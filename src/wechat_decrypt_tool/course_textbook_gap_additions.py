"""Finite teaching additions for omissions found in the actual whole article."""
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

from . import course_textbook_gap_final_review as final
from .course_knowledge import atomic_json, schema_lease
from .course_library import canonical
from .course_pdf_ocr import local_model_digest
from .course_textbook_evidence import sha
from .course_textbook_ledger import TextbookStore
from .course_wiki import object_schema
from .course_wiki_writer import DraftJob

POLICY = "original-teaching-omissions-in-actual-article-bounded-additions-v1"
TEXT = {"type": "string", "minLength": 1, "maxLength": 400}
WRITE = final.writing.planmod.COMMON + """
本任务交付连贯的课程文章补充正文，解决 targets 中实际整篇稿仍遗漏的课堂教学。
article 是现有实际文章，all_original_sources 是全部原始语音、画面文字和候选课件。
targets 的负面审查只是线索；阅读全部上下文确认原文真正讲了什么，不能为疑点编造知识。
按知识逻辑合并同一主题，给已有 section 补写独立可读的解释，不按来源或老师时间顺序排列。
明确原理、前置条件、操作步骤、具体例子和限制中原课真正涉及但文章未充分解释的部分。
补充段落接在对应 section 的现有讲解之后，应自然衔接且避免重复已有内容。一个段落可解决多条来源。
只写有依据的教学正文，通常每段 200 至 800 字；代码可用 Markdown，保持缩进。
不写审稿说明、来源编号列表、补写进度或占位句。不要重写已经写好的整篇文章。
每个 additions 项的 source_keys 必须是真实依据，不能仅给宽泛相关的来源。
每条目标都必须分配：added 指向真正解释它的新增段落，non_teaching 要附原文的连续引文及理由，
实在无法从原文理解就 unresolved，不能靠泛泛而谈或虚构细节消除遗漏。
原文的有效概念、示例、必要条件和限制不得作为口头填充排除。原负面意见和全部旧预算保留。
若提供 prior_candidate 和 negative_review，这是唯一一次语义修改机会。只输出严格 JSON。
"""
REVIEW = final.writing.planmod.COMMON + """
独立核对 proposed_article 中的新增讲解，不把 writer 的分类或旧审查意见当作事实。
all_original_sources 为完整课程原文，targets 是实际整篇稿留下的原始覆盖疑点。
逐段核查是否有完整原文依据、是否把真正欠缺的教学讲清、是否与已有 section 自然衔接。
不能把关键词命中、重复旧话、抽象概括或无依据的细节判为充分讲解。
逐个 target 核查真实教学是否由所列新增段落充分解释，尤其注意示例、条件和限制。
若原文实际是非教学内容，可教学=false，但需有连续原文引文及可复核理由；不排除有效教学。
未解决就如实 covered=false。通过的段落必须引用真实支持来源。不要补写或为文章辩护。
此检查只核对这些新增讲解，不宣布全课事实、可读性、附件关联或最终验收通过。
"""


def require(value, reason):
    if not value:
        raise ValueError("requires_gap_additions_" + reason)


def normalize(text):
    return "".join(text.split())


def sections(book):
    return {s["id"]: s for c in book["chapters"] for s in c["sections"]}


def candidate_schema(book, targets, sources):
    ids = {"type": "array", "uniqueItems": True,
           "items": {"type": "string", "pattern": "^add[0-9]{3}$"}}
    return object_schema({
        "additions": {"type": "array", "maxItems": 12, "items": object_schema({
            "id": {"type": "string", "pattern": "^add[0-9]{3}$"},
            "section": {"enum": list(sections(book))},
            "text": {"type": "string", "minLength": 80, "maxLength": 2600},
            "source_keys": {"type": "array", "minItems": 1, "uniqueItems": True,
                            "items": {"enum": list(sources)}}})},
        "targets": object_schema({ref: object_schema({
            "kind": {"enum": ["added", "non_teaching", "unresolved"]},
            "addition_ids": ids, "source_quote": TEXT, "reason": TEXT}) for ref in targets})})


def real_quote(quote, source):
    value, text = normalize(quote), normalize(source["text"])
    require(bool(value) and len(value) >= min(12, len(text)) and value in text, "actual_original_quote")


def check_candidate(value, book, targets, sources):
    jsonschema.validate(value, candidate_schema(book, targets, sources))
    additions = {p["id"]: p for p in value["additions"]}
    require(len(additions) == len(value["additions"]), "unique_addition_ids")
    used = set()
    for ref, disposition in value["targets"].items():
        selected = disposition["addition_ids"]
        require(set(selected) <= set(additions), "real_target_additions")
        real_quote(disposition["source_quote"], sources[ref])
        if disposition["kind"] == "added":
            require(bool(selected) and all(ref in additions[k]["source_keys"] for k in selected),
                    "new_explanation_cites_its_actual_target")
            used.update(selected)
        else:
            require(not selected, "non_added_targets_do_not_claim_new_prose")
    require(used == set(additions), "no_unassigned_prose")
    return value


def assemble(book, candidate):
    result = copy.deepcopy(book)
    targets = sections(result)
    for row in candidate["additions"]:
        require(row["section"] in targets, "real_section_at_integration")
        targets[row["section"]]["paragraphs"].append(row["text"])
    final.writing.article.validate_book(result)
    return result


def review_schema(candidate, targets, sources):
    ids = [p["id"] for p in candidate["additions"]]
    return object_schema({
        "paragraphs": object_schema({p["id"]: object_schema({
            "supported": {"type": "boolean"}, "fully_explained": {"type": "boolean"},
            "fits_section": {"type": "boolean"}, "reason": TEXT,
            "source_keys": {"type": "array", "uniqueItems": True,
                            "items": {"enum": list(sources)}}}) for p in candidate["additions"]}),
        "targets": object_schema({ref: object_schema({
            "teaching": {"type": "boolean"}, "covered": {"type": "boolean"},
            "addition_ids": {"type": "array", "uniqueItems": True, "maxItems": len(ids),
                             "items": {"enum": ids} if ids else {"type": "string"}},
            "source_quote": TEXT, "reason": TEXT}) for ref in targets})})


def check_review(value, candidate, targets, sources):
    jsonschema.validate(value, review_schema(candidate, targets, sources))
    for row in value["paragraphs"].values():
        require(not row["supported"] or bool(row["source_keys"]), "positive_paragraph_has_evidence")
    for ref, row in value["targets"].items():
        real_quote(row["source_quote"], sources[ref])
        require(not row["covered"] or row["teaching"], "consistent_teaching_coverage")
        require(bool(row["addition_ids"]) == (row["teaching"] and row["covered"]), "actual_coverage_positions")
        require(set(row["addition_ids"]) <= set(candidate["targets"][ref]["addition_ids"]), "actual_target_mapping")


def resolved(candidate, review):
    good = {k for k, p in review["paragraphs"].items()
            if all(p[x] for x in ("supported", "fully_explained", "fits_section"))}
    return {k for k, row in review["targets"].items() if
            (candidate["targets"][k]["kind"] == "added" and row["teaching"] and row["covered"]
             and bool(row["addition_ids"]) and set(row["addition_ids"]) <= good)
            or (candidate["targets"][k]["kind"] == "non_teaching" and not row["teaching"])}


class AdditionJob(final.ReviewJob):
    def __init__(self, store, inputs, writer, binding, tokenizer):
        self.inputs, self.tokenizer = inputs, Tokenizer.from_file(str(tokenizer))
        DraftJob.__init__(self, store, {"sources": inputs["sources"]}, "qwen3.5:9b",
            inputs["model_digest"], 21600, pipeline=POLICY, job_directory="textbook-gap-addition-jobs", max_attempts=2,
            recipe_extra={"writer": writer, "writer_bindings": binding,
                "module_sha256": sha(Path(__file__).read_bytes()), "tokenizer_sha256": sha(Path(tokenizer).read_bytes()),
                "prompts": [WRITE, REVIEW], "maximum_semantic_repairs": 1,
                "old_budgets_reset": False, "whole_article_not_yet_accepted": True})

    def budget(self, messages, stage):
        expected = sum(len(self.tokenizer.encode(m["content"]).ids) for m in messages)
        output = 12288
        context = max(16384, math.ceil((expected + output + 1024) / 16384) * 16384)
        require(context <= 131072, "complete_context_fits_before_call")
        return expected, output, context

    def progress(self, state, stage, **extra):
        self.current = stage
        value = {**self.identity, "job": self.key, "state": state, "stage": stage,
                 "updated": time.time(), "reader_publication_allowed": False, **extra}
        atomic_json(self.root / "progress.json", value)
        atomic_json(self.store.root / "wiki/textbook-gap-additions-current.json", value)


def run(root, writer, tokenizer):
    store = TextbookStore(root)
    store.checkpoint()
    inputs, _, book, audit, _, binding = final.inputs_for(root, writer)
    targets = {k: v for k, v in audit["source_coverage"].items() if v["kind"] == "missing"}
    require(bool(targets), "actual_whole_article_omissions")
    require(local_model_digest("qwen3.5:9b") == inputs["model_digest"], "same_verified_local_model")
    with schema_lease(store.root / "wiki/textbook-gap-additions.lock", timeout=0):
        job = AdditionJob(store, inputs, writer, binding, tokenizer)
        bound = store.root / "wiki/textbook-gap-write-jobs" / writer / "original-gap-additions-binding.json"
        expected = {"job": job.key, "manifest_sha256": sha((job.root / "manifest.json").read_bytes())}
        require(not bound.exists() or final.writing.read(bound) == expected, "existing_omission_repair_budget")
        if not bound.exists():
            atomic_json(bound, expected)
        backup = Path.home() / "Documents/CourseKnowledgeBackups/first-lesson-gap-additions" / job.key
        backup.mkdir(parents=True, exist_ok=True)
        for path in [job.root / "manifest.json", Path(__file__)]:
            target = backup / path.name
            require(not target.exists() or target.read_bytes() == path.read_bytes(), "existing_input_backup_unchanged")
            if not target.exists():
                target.write_bytes(path.read_bytes())
        context = {"article": book, "all_original_sources": inputs["sources"], "targets": targets,
                   "whole_article_reading_problems_retained": audit["reading"]}
        try:
            candidate = job.stage("additions-write", WRITE, candidate_schema(book, targets, inputs["sources"]), context,
                lambda v: check_candidate(v, book, targets, inputs["sources"]))
            for number in range(2):
                proposed = assemble(book, candidate)
                review = job.stage(f"additions-review-{number}", REVIEW,
                    review_schema(candidate, targets, inputs["sources"]),
                    {"all_original_sources": inputs["sources"], "targets": targets,
                     "candidate": candidate, "proposed_article": proposed},
                    lambda v: check_review(v, candidate, targets, inputs["sources"]))
                all_paragraphs = all(all(p[k] for k in ("supported", "fully_explained", "fits_section"))
                                     for p in review["paragraphs"].values())
                if (len(resolved(candidate, review)) == len(targets) and all_paragraphs) or number == 1:
                    break
                candidate = job.stage("additions-repair", WRITE, candidate_schema(book, targets, inputs["sources"]),
                    {**context, "prior_candidate": candidate, "negative_review": review},
                    lambda v: check_candidate(v, book, targets, inputs["sources"]))
            atomic_json(job.root / "private-additions.json", {"candidate": candidate, "review": review})
            atomic_json(job.root / "proposed-reading.json", proposed)
            result = {"state": "original_teaching_additions_written_and_checked", "job": job.key,
                "writer": writer, "target_sources": len(targets), "resolved_targets": len(resolved(candidate, review)),
                "new_paragraphs": len(candidate["additions"]),
                "new_characters": sum(len(p["text"]) for p in candidate["additions"]),
                "paragraphs_with_issues": sum(not all(p[k] for k in ("supported", "fully_explained", "fits_section"))
                                              for p in review["paragraphs"].values()),
                "whole_article_reading_issues_retained": sum(not v["passed"] for v in audit["reading"].values()),
                "proposed_article_sha256": sha(canonical(proposed)), "integration_with_factual_edits_pending": True,
                "reader_publication_allowed": False, "semantic_acceptance": False,
                "source_deletion_allowed": False, "goal_complete": False}
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--writer-job", required=True)
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("command", choices=["worker", "guard"])
    args = parser.parse_args()
    if args.command == "worker":
        print(json.dumps(run(args.root, args.writer_job, args.tokenizer)))
        return
    from .course_archive_gpu import checked_sample
    from .course_gpu_guard import run as guarded
    with schema_lease(args.root / "wiki/textbook-gap-additions-dispatch.lock", timeout=0):
        previous = args.root / "wiki-revision-thermal-guard.json"
        if previous.exists():
            saved = previous.read_bytes()
            require(not final.writing.planmod.identity_live(json.loads(saved)), "revision_guard_slot_not_in_use")
            backup = args.root / "wiki/textbook-gap-addition-guard-history" / (sha(saved) + ".json")
            backup.parent.mkdir(parents=True, exist_ok=True)
            require(not backup.exists() or backup.read_bytes() == saved, "old_guard_state_preserved")
            if not backup.exists():
                backup.write_bytes(saved)
        guarded(args.root, [sys.executable, "-m", "wechat_decrypt_tool.course_textbook_gap_additions",
            "--root", str(args.root), "--writer-job", args.writer_job, "--tokenizer", str(args.tokenizer), "worker"],
            maximum_temperature=70, maximum_power=200, guard_name="wiki-revision-thermal-guard",
            sampler=lambda: checked_sample(args.root))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        msg = str(exc)
        print(json.dumps({"state": "stopped", "error_type": type(exc).__name__,
            "reason_code": msg if re.fullmatch(r"[a-zA-Z0-9_-]{1,180}", msg) else None,
            "error_sha256": sha(msg.encode())}))
        raise SystemExit(1) from None
