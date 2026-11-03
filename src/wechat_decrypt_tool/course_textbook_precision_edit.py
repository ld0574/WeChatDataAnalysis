"""Bounded factual edits after complete-article and full-context review.

Each accepted operation changes only its exact original item(s). Original books,
negative reviews and exhausted budgets remain immutable. These provisional
edits do not resolve coverage, reading or scope issues by assertion.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import time
from pathlib import Path

import jsonschema

from . import course_textbook_article as article
from . import course_textbook_gap_final_review as final
from .course_knowledge import atomic_json, schema_lease
from .course_library import canonical
from .course_pdf_ocr import local_model_digest
from .course_textbook_evidence import sha
from .course_textbook_ledger import TextbookStore
from .course_wiki import object_schema
from .course_wiki_writer import DraftJob

POLICY = "precise-factual-item-edits-after-integrated-review-v1"
TEXT = {"type": "string", "minLength": 1, "maxLength": 240}
WRITE = final.writing.planmod.COMMON + """
只修改 task.actual_content 指定的具体段落、图或自测；其余文章保持原样。
all_original_sources 是完整课程原文，article 是包含此前已通过局部检查修订的当前文章。
依据原文修正负面事实核查发现的真实问题，保留原内容中正确且重要的知识。
不要扩写成另一章，不引入课程外事实、版本、API、案例或无依据的因果。写清必要条件和限制。
段落应是连贯的读者正文；代码可以使用 Markdown 代码块。source_keys 指向真正支持修改内容的原文。
只有图或自测可以选 omit；仅当其没有必要保留的有效教学，或有效教学已在正文充分讲清时才省略。
不能通过删掉重要知识消除错误。replace 必须给完整替代内容，不写改动说明或占位句。
若有 prior_candidate 和 negative_review，这是本项目仅有的一轮语义修订，解决实际问题并保留正确内容。
"""
REVIEW = final.writing.planmod.COMMON + """
独立核查这一次局部修订。all_original_sources 为完整原文；article 为修改前当前整篇文章。
判断 candidate 是否真正解决 task.negative_checks 的问题，全部新断言是否受原文支持，
原内容中正确的重要知识是否保留。不要要求原文有同样标题或措辞，允许有依据的解释和重绘。
如果省略图或题目，核实有效教学已在正文讲清，或原项确实没有可保留的有效教学。
不能把删掉必要讲解、只换说法或另写一个无关题目判作解决；不能为修订稿辩护。
passed 需要 facts、issue_resolved、valid_teaching_retained 全部成立。
replace 通过必须给真实支持来源；omit 通过须给保留知识的真实正文位置及来源，
或明确 no_valid_original_teaching=true 并说明。只输出严格 JSON。
"""


def require(value, reason):
    if not value:
        raise ValueError("requires_precision_edit_" + reason)


def locations(book):
    result, p, d = {}, 0, 0
    for ci, chapter in enumerate(book["chapters"]):
        for si, section in enumerate(chapter["sections"]):
            for pi, _ in enumerate(section["paragraphs"]):
                result[f"p{p:03d}"] = ("chapters", ci, "sections", si, "paragraphs", pi)
                p += 1
            for di, _ in enumerate(section["diagrams"]):
                result[f"d{d:02d}"] = ("chapters", ci, "sections", si, "diagrams", di)
                d += 1
    result.update({f"q{i:02d}": ("quiz", i) for i in range(len(book["quiz"]))})
    return result


def tasks_from(book, pending, reviews):
    require(set(pending) == set(reviews), "every_integrated_judgment_present")
    actual, groups = final.all_items(book), {}
    for key, value in pending.items():
        if value["kind"] != "facts" or reviews[key]["passed"]:
            continue
        content = value["actual_content"]
        ids = value["actual_article_items"]
        require(bool(ids) and all(actual.get(ref) == content for ref in ids), "same_actual_negative_fact")
        group = groups.setdefault(sha(canonical(content)), {"actual_content": content,
            "target_ids": set(), "negative_checks": {}, "kind": ids[0][0]})
        require(all(ref[0] == group["kind"] for ref in ids), "same_fact_item_kind")
        group["target_ids"].update(ids)
        group["negative_checks"][key] = {"original": value["original_negative_review"], "integrated": reviews[key]}
    return [{**g, "task": f"fix{i:03d}", "target_ids": sorted(g["target_ids"])} for i, g in enumerate(groups.values())]


def load(root, key):
    require(re.fullmatch(r"[a-f0-9]{32}", key), "review_job_id")
    folder = Path(root) / "wiki/textbook-integrated-review-jobs" / key
    progress = final.writing.read(folder / "progress.json")
    require(progress["stage"] == "complete" and not final.writing.planmod.identity_live(progress), "review_finished_and_exited")
    manifest, result, proof = [final.writing.read(folder / name) for name in
                              ["manifest.json", "result.json", "integrated-review.json"]]
    require(result["state"] == "integrated_pending_review_finished", "review_result_complete")
    require(manifest["id"] == key == sha(canonical(manifest["recipe"]))[:32], "review_manifest")
    writer = manifest["recipe"]["extra"]["writer"]
    inputs, plan, book, audit, previous, binding = final.inputs_for(root, writer)
    require(binding == manifest["recipe"]["extra"]["writer_bindings"], "same_writer_bindings")
    actual, pending = final.pending_items(inputs, plan, book, audit["unit_proofs"])
    reviews = proof["integrated_reviews"]
    require(proof["article_sha256"] == result["article_sha256"] == sha(canonical(book))
            and proof["prior_pending"] == pending, "same_actual_integrated_article")
    final.validate({"items": reviews}, final.schema_for(pending, inputs["sources"], actual))
    require(proof["whole_article_source_coverage_unchanged"] == audit["source_coverage"]
            and proof["whole_article_reading_review_unchanged"] == audit["reading"], "all_global_negatives_retained")
    return inputs, book, tasks_from(book, pending, reviews), {
        "review_job": key, "writer": writer, "writer_bindings": binding,
        "review_bindings": {n: sha((folder / n).read_bytes()) for n in
                            ["manifest.json", "result.json", "integrated-review.json"]},
        "all_prior_pending": pending, "all_integrated_reviews": reviews,
        "whole_source_coverage": audit["source_coverage"], "whole_reading_review": audit["reading"]}


def can_omit(book, task):
    count = len(task["target_ids"])
    return ((task["kind"] == "d" and len(final.writing.planmod.body_index(book)[2]) - count >= 2)
            or (task["kind"] == "q" and len(book["quiz"]) - count >= 4))


def candidate_schema(book, task, sources):
    kind = task["kind"]
    value = ({"type": "string", "minLength": 1, "maxLength": max(2400, len(task["actual_content"]) + 600)}
             if kind == "p" else article.GRAPH if kind == "d" else article.QUIZ)
    replace = object_schema({"action": {"const": "replace"}, "value": value,
        "source_keys": {"type": "array", "minItems": 1, "uniqueItems": True, "items": {"enum": list(sources)}}})
    return {"anyOf": [replace, object_schema({"action": {"const": "omit"}, "reason": TEXT})]} if can_omit(book, task) else replace


def check_candidate(value, book, task, sources):
    jsonschema.validate(value, candidate_schema(book, task, sources))
    if value["action"] == "replace":
        if task["kind"] == "p":
            require(bool(value["value"].strip()), "nonempty_corrected_paragraph")
        elif task["kind"] == "d":
            article.validate_graph(value["value"])
    return value


def review_schema(book, sources):
    return object_schema({"facts": {"type": "boolean"}, "issue_resolved": {"type": "boolean"},
        "valid_teaching_retained": {"type": "boolean"}, "no_valid_original_teaching": {"type": "boolean"},
        "reason": TEXT, "source_keys": {"type": "array", "uniqueItems": True, "items": {"enum": list(sources)}},
        "article_items": {"type": "array", "uniqueItems": True, "items": {"enum": list(final.all_items(book))}}})


def accepted(review):
    return all(review[k] for k in ["facts", "issue_resolved", "valid_teaching_retained"])


def check_review(value, candidate, book, sources):
    jsonschema.validate(value, review_schema(book, sources))
    if accepted(value):
        if candidate["action"] == "replace":
            require(bool(value["source_keys"]), "positive_replacement_support")
        else:
            require(value["no_valid_original_teaching"] or bool(value["source_keys"]) and bool(value["article_items"]),
                    "omission_preserves_actual_teaching")


def apply_operations(book, operations):
    result, paths, targeted, deletes = copy.deepcopy(book), locations(book), set(), []
    for operation in operations:
        require(operation["accepted"] is True, "only_checked_edits_applied")
        task, candidate = operation["task"], operation["candidate"]
        require(candidate["action"] in {"replace", "omit"}, "known_edit_action")
        require(not targeted.intersection(task["target_ids"]), "nonoverlapping_edits")
        targeted.update(task["target_ids"])
        for ref in task["target_ids"]:
            require(ref in paths and final.all_items(book)[ref] == task["actual_content"], "exact_original_edit_target")
            path = paths[ref]
            container = result
            for step in path[:-1]:
                container = container[step]
            if candidate["action"] == "replace":
                container[path[-1]] = copy.deepcopy(candidate["value"])
            else:
                require(task["kind"] in {"d", "q"} and can_omit(book, task), "permitted_omission")
                deletes.append((path, container, path[-1]))
    # Replacements use original positions; remove list entries only afterwards.
    for _, container, index in sorted(deletes, key=lambda row: row[0], reverse=True):
        del container[index]
    require(len(final.writing.planmod.body_index(result)[2]) >= min(2, len(final.writing.planmod.body_index(book)[2]))
            and len(result["quiz"]) >= min(4, len(book["quiz"])), "aggregate_omission_floors")
    article.validate_book(result)
    return result


class PrecisionJob(final.ReviewJob):
    def __init__(self, store, inputs, binding, tokenizer):
        from tokenizers import Tokenizer
        self.inputs, self.tokenizer = inputs, Tokenizer.from_file(str(tokenizer))
        DraftJob.__init__(self, store, {"sources": inputs["sources"]}, "qwen3.5:9b", inputs["model_digest"], 21600,
            pipeline=POLICY, job_directory="textbook-precision-edit-jobs", max_attempts=2,
            recipe_extra={"parent_bindings": binding, "module_sha256": sha(Path(__file__).read_bytes()),
                "tokenizer_sha256": sha(Path(tokenizer).read_bytes()), "prompts": [WRITE, REVIEW],
                "maximum_semantic_repairs_per_exact_item": 1, "old_budgets_reset": False})

    def progress(self, state, stage, **extra):
        self.current = stage
        value = {**self.identity, "job": self.key, "job_folder": str(self.root), "state": state,
            "stage": stage, "updated": time.time(), "reader_publication_allowed": False,
            "source_deletion_allowed": False, **extra}
        atomic_json(self.root / "progress.json", value)
        atomic_json(self.store.root / "wiki/textbook-precision-current.json", value)


def edit_task(job, task, current):
    sources = job.inputs["sources"]
    context = {"article": current, "task": task, "all_original_sources": sources}
    schema = candidate_schema(current, task, sources)
    candidate = job.stage(task["task"] + "-write", WRITE, schema, context,
                          lambda v: check_candidate(v, current, task, sources))
    assessment = None
    for round_number in range(2):
        assessment = job.stage(task["task"] + f"-review-{round_number}", REVIEW,
            review_schema(current, sources), {**context, "candidate": candidate},
            lambda v: check_review(v, candidate, current, sources))
        if accepted(assessment):
            break
        if round_number == 0:
            candidate = job.stage(task["task"] + "-repair", WRITE, schema,
                {**context, "prior_candidate": candidate, "negative_review": assessment},
                lambda v: check_candidate(v, current, task, sources))
    return {"task": task, "candidate": candidate, "review": assessment,
            "accepted": accepted(assessment), "global_revalidation_required": True}


def run(root, key, tokenizer):
    store = TextbookStore(root)
    store.checkpoint()
    inputs, book, tasks, binding = load(root, key)
    require(local_model_digest("qwen3.5:9b") == inputs["model_digest"], "same_verified_model")
    with schema_lease(store.root / "wiki/textbook-precision-edit.lock", timeout=0):
        job = PrecisionJob(store, inputs, binding, tokenizer)
        bound = store.root / "wiki/textbook-integrated-review-jobs" / key / "precision-edit-binding.json"
        binding_value = {"job": job.key, "manifest_sha256": sha((job.root / "manifest.json").read_bytes())}
        require(not bound.exists() or final.writing.read(bound) == binding_value, "existing_precision_budget_binding")
        if not bound.exists():
            atomic_json(bound, binding_value)
        operations, outcomes = [], {}
        try:
            for task in tasks:
                current = apply_operations(book, operations)
                try:
                    proof = edit_task(job, task, current)
                except ValueError as exc:
                    if not str(exc).startswith("wiki_stage_attempt_budget_exhausted_" + task["task"] + "-"):
                        raise
                    proof = {"task": task, "accepted": False, "error_code": str(exc),
                             "original_attempt_budget_retained": True, "global_revalidation_required": True}
                outcomes[task["task"]] = proof
                atomic_json(job.root / (task["task"] + "-private-result.json"), proof)
                if proof["accepted"]:
                    operations.append(proof)
                corrected = apply_operations(book, operations)
                atomic_json(job.root / "provisional-reading.json", corrected)
                atomic_json(job.root / "private-operations.json", operations)
                job.progress("editing", task["task"] + "-done", exact_items_processed=len(outcomes),
                             total_exact_items=len(tasks), edits_applied=len(operations))
            corrected = apply_operations(book, operations)
            atomic_json(job.root / "provisional-reading.json", corrected)
            atomic_json(job.root / "private-operations.json", operations)
            atomic_json(job.root / "all-prior-reviews.json", binding)
            result = {"state": "precise_edits_require_global_revalidation", "job": job.key,
                "parent_review": key, "article_sha256": sha(canonical(corrected)),
                "exact_items_processed": len(tasks), "edits_applied": len(operations),
                "items_still_unresolved": len(tasks) - len(operations),
                "all_global_source_and_reading_issues_retained": True, "scope_verified": False,
                "reader_publication_allowed": False, "source_deletion_allowed": False, "goal_complete": False}
            atomic_json(job.root / "result.json", result)
            job.progress(result["state"], "complete")
            return result
        except Exception as exc:
            msg = str(exc)
            job.progress("paused" if isinstance(exc, InterruptedError) else "stopped", job.current,
                error_type=type(exc).__name__, reason_code=msg if re.fullmatch(r"[a-zA-Z0-9_-]{1,180}", msg) else None,
                error_sha256=sha(msg.encode()))
            raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--review-job")
    p.add_argument("--writer-job")
    p.add_argument("--tokenizer", type=Path, required=True)
    p.add_argument("command", choices=["worker", "guard"])
    a = p.parse_args()
    if a.command == "worker":
        print(json.dumps(run(a.root, a.review_job, a.tokenizer)))
        return
    from .course_archive_gpu import checked_sample
    from .course_gpu_guard import run as guarded
    store = TextbookStore(a.root)
    with schema_lease(store.root / "wiki/textbook-precision-dispatch.lock", timeout=0):
        deadline = time.monotonic() + 14400
        while True:
            store.checkpoint()
            path = store.root / "wiki/textbook-integrated-review-current.json"
            state = final.writing.read(path) if path.exists() else None
            if state:
                require(re.fullmatch(r"[a-f0-9]{32}", state["job"]), "valid_dispatched_review_job")
                manifest = final.writing.read(store.root / "wiki/textbook-integrated-review-jobs" / state["job"] / "manifest.json")
                if not a.writer_job or manifest["recipe"]["extra"]["writer"] == a.writer_job:
                    if not final.writing.planmod.identity_live(state):
                        require(state["stage"] == "complete", "integrated_review_stopped_no_restart")
                        key = state["job"]
                        break
            require(time.monotonic() < deadline, "review_wait_timeout_no_restart")
            time.sleep(5)
        guard = store.root / "review-experiment-thermal-guard.json"
        while guard.exists() and final.writing.planmod.identity_live(final.writing.read(guard)):
            store.checkpoint()
            require(time.monotonic() < deadline, "review_guard_exit_timeout")
            time.sleep(1)
        guarded(store.root, [sys.executable, "-m", "wechat_decrypt_tool.course_textbook_precision_edit",
            "--root", str(store.root), "--review-job", key, "--tokenizer", str(a.tokenizer.resolve()), "worker"],
            maximum_temperature=70, maximum_power=200, guard_name="review-experiment-thermal-guard",
            sampler=lambda: checked_sample(store.root))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        msg = str(exc)
        print(json.dumps({"state": "stopped", "error_type": type(exc).__name__,
            "reason_code": msg if re.fullmatch(r"[a-zA-Z0-9_-]{1,180}", msg) else None, "error_sha256": sha(msg.encode())}))
        raise SystemExit(1) from None
