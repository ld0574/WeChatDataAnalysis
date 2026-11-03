"""One editorial pass for a changed edition's depth, tests and actual gaps.

This is not a restart of an exhausted unit or precision edit. The new parent
edition and its first source audit are immutable; it has one editorial successor,
no semantic retry, and independently checked, exactly located operations.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import re
import sys
import time
from pathlib import Path

import jsonschema
from tokenizers import Tokenizer

from . import course_textbook_edition_sources as sources
from .course_knowledge import atomic_json, schema_lease
from .course_library import canonical
from .course_textbook_evidence import sha
from .course_textbook_ledger import TextbookStore
from .course_wiki import object_schema
from .course_wiki_writer import DraftJob

e = sources.edition
POLICY = "changed-edition-first-depth-and-self-test-editorial-pass-v1"
WRITE = e.additions.final.writing.planmod.COMMON + """
本次对已经独立重组的实际完整文章做一次整篇编辑，不重写全部文章，不追求凑字数。
依据 all_original_sources 的全部原文，解决 remaining_prior_issues、reading_negative_reviews、
current_missing_sources 中真实存在的问题。意见只是线索，不能当作课程事实。
paragraph_changes 可以替换原段落或在对应知识小节追加连贯解释；按知识依赖组织，保留正确教学。
必须修正 required_factual_paragraphs 的实际错误，并保留其中有依据的重要知识、例子、条件及限制。
补足真正缺少的原理、具体步骤或完整实例，明确前提和适用边界，不能只提关键词或同义改写。
不要把读者送回原视频、OCR 或截图，不写时间分段、审核说明、来源编号或占位句。
quiz_changes 至少改进三题，提供能检验理解或应用的具体情境、必要输入、参考答案、推理解释和掌握标准。
优先替换薄弱的原题，不要只增加术语复述题；不考课程外知识，不省略题目成立的必要条件。
每项修改 source_keys 仅列真正支持全部新断言的完整原文。只输出修改后的实际内容，不输出修改说明。
有确切必要才改原段；替换须给完整段落，包含有效的原有教学。图、标题、章节结构保持原样。
不得删除有教学的原段消除问题，不重复已有讲解，不在同一段原位置或同一题上提出多项替换。
本次仅有一次编辑，不会根据负面意见自动再写一轮。全部旧失败和预算保持。
"""
REVIEW = e.additions.final.writing.planmod.COMMON + """
独立审核实际 proposed_article。all_original_sources 是完整原文，不采纳作者或旧审稿作为事实。
逐个 operations 判断新内容的全部断言是否有依据、原有正确教学是否保留、实际问题是否解决、
解释及条件是否充分、与文章是否相容；四项都成立才 accepted，并给真实依据来源。
不能因局部内容有依据就认定原事实问题已经解决，不能因为篇幅长或出现关键词就认定有深度。
逐个 remaining_prior_issues 判断新稿是否真正解决旧问题，resolved 必须指向真实正文位置及支持来源。
通读整篇检查 organisation、depth、coherence、figures、self_tests；题目必须可作答且答案、解释和标准正确。
不要求原文有同样标题、图或同样题目，允许依据材料独立解释重绘；无依据的扩展不可接受。
任何仍欠缺就明确给负面判断，不重写或为候选辩护。只输出严格 JSON。
"""


def require(value, reason):
    if not value:
        raise ValueError("requires_edition_editor_" + reason)


def load(root, audit, tokenizer):
    require(re.fullmatch(r"[a-f0-9]{32}", audit), "source_audit_id")
    folder = Path(root) / "wiki/textbook-edition-source-jobs" / audit
    manifest, progress, result = [e.read(folder / n) for n in ["manifest.json", "progress.json", "result.json"]]
    require(manifest["id"] == audit == sha(canonical(manifest["recipe"]))[:32], "source_audit_manifest")
    extra = manifest["recipe"]["extra"]
    require(extra["module_sha256"] == sha(Path(sources.__file__).read_bytes())
            and extra["tokenizer_sha256"] == sha(Path(tokenizer).read_bytes()), "source_audit_code_and_tokenizer")
    require(progress["stage"] == "complete" and not e.additions.final.writing.planmod.identity_live(progress), "source_audit_finished")
    inputs, book, old, pending, checks, reading, binding = sources.load(root, extra["edition"], tokenizer)
    require(binding == extra["edition_bindings"], "same_changed_edition")
    targets = {k: v for k, v in old.items() if v["kind"] == "missing"}
    scoped = e.unmapped.source_scope(inputs["sources"], targets, {"additions": []})
    require(scoped == manifest["recipe"]["packet"]["sources"], "complete_audited_scope")
    value, stage_binding = e.checked_cache(folder, "changed-edition-source-gaps")
    body = e.additions.final.writing.planmod.body_index(book)[0]
    schema = sources.schema_for(targets, body, scoped)
    original_job = sources.AuditJob(TextbookStore(root), scoped, inputs["model_digest"], extra["edition"], binding, tokenizer)
    require(original_job.key == audit and original_job.stage_input(sources.PROMPT, schema, {
        "article": e.additions.final.writing.planmod.labelled_book(book), "audit_targets": list(targets),
        "complete_context_sources": scoped, "prior_negative_judgments_retained": targets})[1] == stage_binding,
        "actual_source_audit_request_binding")
    sources.validate(value, schema, targets, scoped)
    combined = copy.deepcopy(old)
    combined.update({k: {a: b for a, b in row.items() if a != "source_quote"} for k, row in value["sources"].items()})
    proof = e.read(folder / "private-source-coverage.json")
    require(proof == {"edition": extra["edition"], "article_sha256": sha(canonical(book)),
                     "original_coverage": old, "target_review": value, "combined_coverage": combined}, "actual_source_audit_proof")
    require(result["job"] == audit and result["missing_sources"] == sum(r["kind"] == "missing" for r in combined.values())
            and result["article_sha256"] == sha(canonical(book)), "actual_source_audit_result")
    remaining = {k: {**pending[k], "latest_negative_review": v} for k, v in checks["items"].items() if not v["passed"]}
    binding = {"source_audit": audit, "edition": extra["edition"], "article_sha256": sha(canonical(book)),
        "source_audit_files": {n: sha((folder / n).read_bytes()) for n in
                               ["manifest.json", "result.json", "private-source-coverage.json"]},
        "edition_files": binding}
    return inputs, book, combined, remaining, {k: v for k, v in reading.items() if not v["passed"]}, binding


def write_schema(book, pending, originals):
    body = e.additions.final.writing.planmod.body_index(book)[0]
    refs = {"type": "array", "minItems": 1, "uniqueItems": True, "items": {"enum": list(originals)}}
    paragraph = {"text": {"type": "string", "minLength": 80, "maxLength": 4000}, "source_keys": refs}
    changes = {"type": "array", "minItems": 1, "maxItems": 8, "items": {"anyOf": [
        object_schema({**paragraph, "kind": {"const": "replace"}, "paragraph_id": {"enum": list(body)}}),
        object_schema({**paragraph, "kind": {"const": "append"}, "section_id": {"enum": list(e.additions.sections(book))}})]}}
    required = {ref for row in pending.values() if row["kind"] == "facts" for ref in row["actual_article_items"]}
    require(required <= set(body) and len(required) <= 8, "bounded_actual_factual_paragraphs")
    if required:
        changes["allOf"] = [{"contains": {"properties": {"kind": {"const": "replace"}, "paragraph_id": {"const": ref}},
                                         "required": ["kind", "paragraph_id"]}} for ref in sorted(required)]
    quiz = {"value": e.additions.final.writing.article.QUIZ, "source_keys": refs}
    return object_schema({"paragraph_changes": changes,
        "quiz_changes": {"type": "array", "minItems": 3, "maxItems": 6, "items": {"anyOf": [
            object_schema({**quiz, "kind": {"const": "replace"}, "question_id": {"enum": [f"q{i:02d}" for i in range(len(book["quiz"]))]}}),
            object_schema({**quiz, "kind": {"const": "append"}})]}}})


def operations(candidate):
    return {f"edit{i:03d}": {"type": kind, **row} for i, (kind, row) in enumerate(
        [("paragraph", row) for row in candidate["paragraph_changes"]] + [("quiz", row) for row in candidate["quiz_changes"]])}


def labelled_article(book):
    value = e.additions.final.writing.planmod.labelled_book(book)
    number = 0
    for chapter in value["chapters"]:
        for section in chapter["sections"]:
            diagrams = []
            for graph in section["diagrams"]:
                diagrams.append({"id": f"d{number:02d}", "graph": graph})
                number += 1
            section["diagrams"] = diagrams
    value["quiz"] = {f"q{i:02d}": v for i, v in enumerate(book["quiz"])}
    return value


def write_context(inputs, book, coverage, pending, reading):
    required = {ref for row in pending.values() if row["kind"] == "facts" for ref in row["actual_article_items"]}
    missing = {k: v for k, v in coverage.items() if v["kind"] == "missing"}
    return {"all_original_sources": inputs["sources"], "article": labelled_article(book),
        "remaining_prior_issues": pending, "reading_negative_reviews": reading,
        "current_missing_sources": {k: {"original": inputs["sources"][k], "review": v} for k, v in missing.items()},
        "required_factual_paragraphs": {k: e.additions.final.all_items(book)[k] for k in required},
        "target_original_context": e.unmapped.source_scope(inputs["sources"], missing, {"additions": []})}


def apply(book, candidate, selected=None):
    result = copy.deepcopy(book)
    paths = e.precision.locations(book)
    sections = e.additions.sections(result)
    ops = operations(candidate)
    chosen = set(ops) if selected is None else set(selected)
    require(chosen <= set(ops), "only_real_operations")
    targets = []
    for ref, row in ops.items():
        if ref not in chosen:
            continue
        if row["kind"] == "replace":
            key = row["paragraph_id"] if row["type"] == "paragraph" else row["question_id"]
            targets.append(key)
            container = result
            for step in paths[key][:-1]:
                container = container[step]
            container[paths[key][-1]] = copy.deepcopy(row["text"] if row["type"] == "paragraph" else row["value"])
        elif row["type"] == "paragraph":
            sections[row["section_id"]]["paragraphs"].append(row["text"])
        else:
            result["quiz"].append(copy.deepcopy(row["value"]))
    require(len(targets) == len(set(targets)), "nonoverlapping_replacements")
    e.additions.final.writing.article.validate_book(result)
    return result


def check_write(candidate, book, schema):
    jsonschema.validate(candidate, schema)
    require(sum(len(row["text"]) for row in candidate["paragraph_changes"]) <= 18000, "bounded_editorial_text")
    quizzes = [sha(canonical(row["value"])) for row in candidate["quiz_changes"]]
    require(len(quizzes) == len(set(quizzes)), "distinct_self_tests")
    require(apply(book, candidate) != book, "real_article_change")
    return candidate


def review_schema(ops, pending, book, originals):
    refs = {"type": "array", "uniqueItems": True, "items": {"enum": list(originals)}}
    actual = e.additions.final.all_items(book)
    reason = {"type": "string", "minLength": 1, "maxLength": 600}
    def verdict(positive, negative, positions=False):
        common = {"reason": reason, "source_keys": refs}
        if positions:
            common["article_items"] = {"type": "array", "uniqueItems": True, "items": {"enum": list(actual)}}
        yes = {**common, "verdict": {"const": positive}, "source_keys": {**refs, "minItems": 1}}
        if positions:
            yes["article_items"] = {**common["article_items"], "minItems": 1}
        return {"anyOf": [object_schema(yes), object_schema({**common, "verdict": {"const": negative}})]}
    return object_schema({"operations": object_schema({k: verdict("accepted", "requires_changes") for k in ops}),
        "prior_issues": object_schema({k: verdict("resolved", "unresolved", True) for k in pending}),
        "reading": object_schema({k: object_schema({"verdict": {"enum": ["passed", "needs_changes"]}, "reason": reason})
                                   for k in ["organisation", "depth", "coherence", "figures", "self_tests"]})})


class EditorJob(sources.AuditJob):
    def __init__(self, store, inputs, binding, tokenizer):
        self.tokenizer = Tokenizer.from_file(str(tokenizer))
        DraftJob.__init__(self, store, {"sources": inputs["sources"]}, "qwen3.5:9b", inputs["model_digest"], 21600,
            pipeline=POLICY, job_directory="textbook-edition-editor-jobs", max_attempts=2,
            recipe_extra={"parent": binding, "module_sha256": sha(Path(__file__).read_bytes()),
                "tokenizer_sha256": sha(Path(tokenizer).read_bytes()), "prompts": [WRITE, REVIEW, sources.PROMPT],
                "maximum_editorial_passes": 1, "maximum_semantic_repairs": 0,
                "old_unit_precision_and_addition_budgets_unchanged": True})

    def budget(self, messages, stage):
        expected = sum(len(self.tokenizer.encode(m["content"]).ids) for m in messages)
        output = 12288 if stage == "editorial-write" else 8192
        context = max(16384, math.ceil((expected + output + 1024) / 16384) * 16384)
        require(context <= 131072, "complete_editorial_input_fits")
        return expected, output, context

    def progress(self, state, stage, **extra):
        self.current = stage
        value = {**self.identity, "job": self.key, "state": state, "stage": stage, "updated": time.time(),
            "reader_publication_allowed": False, "source_deletion_allowed": False, **extra}
        atomic_json(self.root / "progress.json", value)
        atomic_json(self.store.root / "wiki/textbook-edition-editor-current.json", value)


def assess(job, name, book, candidate, ops, inputs, pending):
    schema = review_schema(ops, pending, book, inputs["sources"])
    old = e.additions.final.all_items(job.original_book)
    original = {row["paragraph_id"] if row["type"] == "paragraph" else row["question_id"]:
        old[row["paragraph_id"] if row["type"] == "paragraph" else row["question_id"]]
        for row in ops.values() if row["kind"] == "replace"}
    return job.stage(name, REVIEW, schema, {"all_original_sources": inputs["sources"], "proposed_article": labelled_article(book),
        "operations": ops, "remaining_prior_issues": pending, "complete_original_items_for_replacements": original},
        lambda v: jsonschema.validate(v, schema))


def run(root, audit, tokenizer):
    store = TextbookStore(root)
    store.checkpoint()
    with schema_lease(store.root / "wiki/textbook-edition-editor.lock", timeout=0):
        inputs, book, coverage, pending, reading, binding = load(root, audit, tokenizer)
        require(bool(reading), "unattempted_readability_targets")
        job = EditorJob(store, inputs, binding, tokenizer)
        job.original_book = book
        e.continuation.bind_once(store.root / "wiki/textbook-edition-jobs" / binding["edition"] / "editorial-successor-binding.json",
            {"job": job.key, "manifest_sha256": sha((job.root / "manifest.json").read_bytes())})
        context = write_context(inputs, book, coverage, pending, reading)
        schema = write_schema(book, pending, inputs["sources"])
        backup = Path.home() / "Documents/CourseKnowledgeBackups/first-lesson-editorial-pass" / job.key
        backup.mkdir(parents=True, exist_ok=True)
        atomic_json(job.root / "private-editorial-inputs.json", {"context": context, "schema": schema, "binding": binding})
        for path in [job.root / "manifest.json", job.root / "private-editorial-inputs.json", Path(__file__)]:
            target = backup / path.name
            require(not target.exists() or target.read_bytes() == path.read_bytes(), "backup_unchanged")
            if not target.exists():
                target.write_bytes(path.read_bytes())
            require(target.read_bytes() == path.read_bytes(), "backup_byte_match")
        try:
            candidate = job.stage("editorial-write", WRITE, schema, context, lambda v: check_write(v, book, schema))
            ops = operations(candidate)
            proposed = apply(book, candidate)
            review = assess(job, "editorial-review", proposed, candidate, ops, inputs, pending)
            selected = {k for k, row in review["operations"].items() if row["verdict"] == "accepted"}
            corrected = apply(book, candidate, selected)
            atomic_json(job.root / "private-editorial-proposal.json", {"candidate": candidate, "proposed_review": review,
                "accepted_operation_ids": sorted(selected), "rejected_operation_ids": sorted(set(ops) - selected)})
            if corrected == book:
                result = {"state": "editorial_pass_no_accepted_changes", "job": job.key,
                    "accepted_operations": 0, "reader_publication_allowed": False, "semantic_acceptance": False,
                    "source_deletion_allowed": False, "goal_complete": False}
                atomic_json(job.root / "result.json", result)
                job.progress(result["state"], "complete")
                return result
            if selected != set(ops):
                review = assess(job, "actual-selected-review", corrected, candidate,
                                {k: v for k, v in ops.items() if k in selected}, inputs, pending)
            # Reusing paragraph support requires exact old bytes; changed old
            # paragraphs become missing until independently audited below.
            _, updated, _ = e.merge(book, corrected, {"additions": []}, {"paragraphs": {}, "targets": {}}, coverage)
            targets = {k: v for k, v in updated.items() if v["kind"] == "missing"}
            if targets:
                scoped = e.unmapped.source_scope(inputs["sources"], targets, {"additions": []})
                body = e.additions.final.writing.planmod.body_index(corrected)[0]
                sschema = sources.schema_for(targets, body, scoped)
                checked = job.stage("actual-changed-source-support", sources.PROMPT, sschema,
                    {"article": e.additions.final.writing.planmod.labelled_book(corrected), "audit_targets": list(targets),
                     "complete_context_sources": scoped, "prior_negative_judgments_retained": targets},
                    lambda v: sources.validate(v, sschema, targets, scoped), evidence_packet={"sources": scoped})
                updated.update({k: {a: b for a, b in row.items() if a != "source_quote"} for k, row in checked["sources"].items()})
            atomic_json(job.root / "reading.json", corrected)
            atomic_json(job.root / "private-final-checks.json", {"review": review, "source_coverage": updated, "parent": binding})
            result = {"state": "editorial_edition_checked_scope_pending", "job": job.key,
                "article_sha256": sha(canonical(corrected)), "accepted_operations": len(selected),
                "rejected_operations": len(ops) - len(selected), "total_source_inputs": len(updated),
                "missing_source_inputs": sum(v["kind"] == "missing" for v in updated.values()),
                "remaining_prior_issues": sum(v["verdict"] != "resolved" for v in review["prior_issues"].values()),
                "reading_issues": sum(v["verdict"] != "passed" for v in review["reading"].values()),
                "body_characters": sum(len(v) for v in e.additions.final.writing.planmod.body_index(corrected)[0].values()),
                "chapters": len(corrected["chapters"]), "self_tests": len(corrected["quiz"]),
                "semantic_acceptance": False, "scope_verified": False, "reader_publication_allowed": False,
                "source_deletion_allowed": False, "goal_complete": False}
            atomic_json(job.root / "result.json", result)
            e.export_draft(job.root, corrected, result)
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
    p.add_argument("--source-audit", required=True)
    p.add_argument("--tokenizer", type=Path, required=True)
    p.add_argument("command", choices=["worker", "guard"])
    args = p.parse_args()
    if args.command == "worker":
        print(json.dumps(run(args.root, args.source_audit, args.tokenizer)))
    else:
        from .course_archive_gpu import checked_sample
        from .course_gpu_guard import run as guarded
        with schema_lease(args.root / "wiki/textbook-edition-editor-dispatch.lock", timeout=0):
            previous = args.root / "review-experiment-thermal-guard.json"
            if previous.exists():
                data = previous.read_bytes()
                require(not e.additions.final.writing.planmod.identity_live(json.loads(data)), "guard_slot_free")
                history = args.root / "wiki/textbook-edition-editor-guard-history" / (sha(data) + ".json")
                history.parent.mkdir(parents=True, exist_ok=True)
                require(not history.exists() or history.read_bytes() == data, "guard_history_unchanged")
                if not history.exists():
                    history.write_bytes(data)
            guarded(args.root, [sys.executable, "-m", "wechat_decrypt_tool.course_textbook_edition_editor",
                "--root", str(args.root), "--source-audit", args.source_audit, "--tokenizer", str(args.tokenizer), "worker"],
                maximum_temperature=70, maximum_power=200, guard_name="review-experiment-thermal-guard",
                sampler=lambda: checked_sample(args.root))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        msg = str(exc)
        print(json.dumps({"state": "stopped", "error_type": type(exc).__name__,
            "reason_code": msg if re.fullmatch(r"[a-zA-Z0-9_-]{1,180}", msg) else None, "error_sha256": sha(msg.encode())}))
        raise SystemExit(1) from None
