"""Recheck pending unit judgments against the actual integrated lesson.

This adds the completed article and the full original context which unit checks
did not have. It never retries a whole-article negative, edits prose, publishes,
or changes the original unit attempts or verdicts.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from pathlib import Path

import jsonschema
from tokenizers import Tokenizer

from . import course_textbook_gap_write as writing
from .course_knowledge import atomic_json, schema_lease
from .course_library import canonical
from .course_pdf_ocr import local_model_digest
from .course_textbook_evidence import sha
from .course_textbook_ledger import TextbookStore
from .course_wiki import object_schema
from .course_wiki_writer import DraftJob

POLICY = "pending-unit-checks-in-complete-integrated-lesson-v1"
PROMPT = writing.planmod.COMMON + """
这是对整合后文章的审核，全部原文均已提供。先前 unit 判断时只见一个局部小节及其局部原文。
pending 中 facts 是该小节此前仍未被支持的具体内容，coverage 是小节尚未讲清的教学，
retention 是旧小节正确知识是否保留的问题。现在只审核这些旧问题在实际整合文章中是否解决。
facts 核实 actual_content 的全部断言是否受 all_original_sources 支持；不要把旧审查理由当事实。
coverage 核对真正教学是否已被整篇文章充分解释，必须指出真实文章位置，关键词不等于讲清。
retention 允许正确知识出现在文章其他位置；错误旧表述已按原文纠正时不要求保留错误。
通过时必须给出真正支持的 source_keys 和 article_items；依据不足或仍漏讲就判 false。
允许有依据的重新解释、重绘图及自测，不要求原文有同样的标题或措辞。
不要引入外部常识、补写新文章、替文章辩护、宣布已验收。不能改判整篇反查已发现的其他缺项。
"""


def all_items(book):
    body, _, diagrams = writing.planmod.body_index(book)
    return {**body, **{k: v["graph"] for k, v in diagrams.items()},
            **{f"q{i:02d}": v for i, v in enumerate(book["quiz"])}}


def pending_items(inputs, plan, book, proofs):
    actual = all_items(book)
    by_content = {}
    for key, value in actual.items():
        by_content.setdefault(sha(canonical(value)), []).append(key)
    pending = {}
    for unit in plan["units"]:
        proof = proofs[unit["unit"]]
        candidate = writing.check_candidate(proof["candidate"], unit)
        writing.validate_review(proof["review"], candidate, unit)
        claims = writing.claims_of(candidate)
        gaps = {v["point_id"]: v for v in unit["gap_sources"]}
        for kind, group in proof["review"].items():
            for key, review in group.items():
                if review["passed"]:
                    continue
                row = {"kind": kind, "original_unit": unit["unit"], "original_item": key,
                       "original_negative_review": review}
                if kind == "facts":
                    content = claims[key]
                    content = content["text"] if key.startswith("p") else writing.without_sources(content)
                    refs = by_content.get(sha(canonical(content)), [])
                    writing.require(bool(refs), "pending_fact_exists_in_actual_article")
                    row.update(actual_content=content, actual_article_items=refs)
                elif kind == "coverage":
                    row["missing_teaching"] = gaps[key]
                else:
                    writing.require(re.fullmatch(r"old\d{3}", key), "pending_real_old_paragraph")
                    row["old_paragraph"] = unit["existing_section"]["paragraphs"][int(key[3:])]
                pending[f"pending{len(pending):03d}"] = row
    return actual, pending


def schema_for(refs, sources, actual):
    row = object_schema({"passed": {"type": "boolean"}, "reason": {"type": "string", "minLength": 1, "maxLength": 240},
        "source_keys": {"type": "array", "uniqueItems": True, "items": {"enum": list(sources)}},
        "article_items": {"type": "array", "uniqueItems": True, "items": {"enum": list(actual)}}})
    return object_schema({"items": object_schema({k: row for k in refs})})


def validate(value, schema):
    jsonschema.validate(value, schema)
    for row in value["items"].values():
        writing.require(not row["passed"] or bool(row["source_keys"]) and bool(row["article_items"]),
                        "integrated_positive_actual_sources_and_article")


def inputs_for(root, key):
    writing.require(re.fullmatch(r"[a-f0-9]{32}", key), "integrated_writer_job_id")
    folder = Path(root) / "wiki/textbook-gap-write-jobs" / key
    progress = writing.read(folder / "progress.json")
    writing.require(progress["stage"] == "complete" and not writing.planmod.identity_live(progress), "integrated_writer_finished")
    manifest = writing.read(folder / "manifest.json")
    writing.require(manifest["id"] == key == sha(canonical(manifest["recipe"]))[:32], "integrated_writer_manifest")
    inputs, plan, binding = writing.load_plan(root, manifest["recipe"]["extra"]["plan"])
    writing.require(binding == manifest["recipe"]["extra"]["plan_bindings"], "integrated_same_plan")
    book, audit, result = [writing.read(folder / name) for name in
                           ["expanded-reading.json", "expanded-audit.json", "result.json"]]
    proofs = audit["unit_proofs"]
    assembled = writing.assemble(inputs, plan, {k: p["candidate"] for k, p in proofs.items()})
    writing.require(assembled == book and audit["article_sha256"] == result["article_sha256"] == sha(canonical(book)),
                    "integrated_actual_article_and_audit")
    writing.require(set(audit["source_coverage"]) == set(inputs["sources"]), "integrated_complete_source_check")
    binding = {name: sha((folder / name).read_bytes()) for name in
               ["manifest.json", "expanded-reading.json", "expanded-audit.json", "result.json"]}
    return inputs, plan, book, audit, result, binding


class ReviewJob(writing.planmod.GapPlanJob):
    def __init__(self, store, inputs, key, binding, tokenizer):
        self.inputs, self.tokenizer = inputs, Tokenizer.from_file(str(tokenizer))
        DraftJob.__init__(self, store, {"sources": inputs["sources"]}, "qwen3.5:9b", inputs["model_digest"], 21600,
            pipeline=POLICY, job_directory="textbook-integrated-review-jobs", max_attempts=2,
            recipe_extra={"writer": key, "writer_bindings": binding, "prompt": PROMPT,
                "module_sha256": sha(Path(__file__).read_bytes()), "tokenizer_sha256": sha(Path(tokenizer).read_bytes()),
                "maximum_context": 131072, "maximum_semantic_repairs": 0, "old_verdicts_and_budgets_changed": False})

    def budget(self, messages, stage):
        expected = sum(len(self.tokenizer.encode(m["content"]).ids) for m in messages)
        output = 8192
        context = max(16384, math.ceil((expected + output + 1024) / 16384) * 16384)
        writing.require(context <= 131072, "integrated_complete_input_fits")
        return expected, output, context

    def progress(self, state, stage, **extra):
        self.current = stage
        value = {**self.identity, "job": self.key, "job_folder": str(self.root), "state": state,
            "stage": stage, "updated": time.time(), "reader_publication_allowed": False, "source_deletion_allowed": False, **extra}
        atomic_json(self.root / "progress.json", value)
        atomic_json(self.store.root / "wiki/textbook-integrated-review-current.json", value)


def run(root, key, tokenizer):
    store = TextbookStore(root)
    store.checkpoint()
    inputs, plan, book, audit, previous, binding = inputs_for(root, key)
    writing.require(local_model_digest("qwen3.5:9b") == inputs["model_digest"], "integrated_same_verified_model")
    actual, pending = pending_items(inputs, plan, book, audit["unit_proofs"])
    with schema_lease(store.root / "wiki/textbook-integrated-review.lock", timeout=0):
        job = ReviewJob(store, inputs, key, binding, tokenizer)
        path = store.root / "wiki/textbook-gap-write-jobs" / key / "integrated-review-binding.json"
        expected = {"job": job.key, "manifest_sha256": sha((job.root / "manifest.json").read_bytes())}
        writing.require(not path.exists() or writing.read(path) == expected, "integrated_existing_review_budget")
        if not path.exists():
            atomic_json(path, expected)
        try:
            reviews = {}
            refs = list(pending)
            for number, start in enumerate(range(0, len(refs), 12)):
                selected = refs[start:start + 12]
                schema = schema_for(selected, inputs["sources"], actual)
                value = job.stage(f"integrated-{number:03d}", PROMPT, schema,
                    {"article_items": actual, "all_original_sources": inputs["sources"],
                     "pending": {k: pending[k] for k in selected}}, lambda v, schema=schema: validate(v, schema))
                reviews.update(value["items"])
            proof = {"article_sha256": sha(canonical(book)), "prior_pending": pending, "integrated_reviews": reviews,
                "original_unit_reviews_unchanged": True, "whole_article_source_coverage_unchanged": audit["source_coverage"],
                "whole_article_reading_review_unchanged": audit["reading"], "reader_publication_allowed": False}
            atomic_json(job.root / "integrated-review.json", proof)
            result = {"state": "integrated_pending_review_finished", "job": job.key, "writer": key,
                "article_sha256": sha(canonical(book)), "pending_unit_items_checked": len(reviews),
                "remaining_unit_items": sum(not v["passed"] for v in reviews.values()),
                "whole_article_missing_sources": previous["missing_sources"], "reading_issues": previous["reading_issues"],
                "scope_verified": False, "semantic_acceptance": False, "reader_publication_allowed": False,
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
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--writer-job", required=True)
    p.add_argument("--tokenizer", type=Path, required=True)
    p.add_argument("command", choices=["worker", "guard"])
    a = p.parse_args()
    if a.command == "worker":
        print(json.dumps(run(a.root, a.writer_job, a.tokenizer)))
        return
    from .course_archive_gpu import checked_sample
    from .course_gpu_guard import run as guarded
    store = TextbookStore(a.root)
    writing.require(re.fullmatch(r"[a-f0-9]{32}", a.writer_job), "integrated_writer_job_id")
    path = store.root / "wiki/textbook-gap-write-jobs" / a.writer_job / "progress.json"
    with schema_lease(store.root / "wiki/textbook-integrated-dispatch.lock", timeout=0):
        deadline = time.monotonic() + 10800
        while True:
            store.checkpoint()
            state = writing.read(path)
            if not writing.planmod.identity_live(state):
                writing.require(state["stage"] == "complete", "integrated_writer_failed_no_restart")
                break
            writing.require(time.monotonic() < deadline, "integrated_writer_wait_timeout")
            time.sleep(5)
        guard = store.root / "review-experiment-thermal-guard.json"
        while guard.exists() and writing.planmod.identity_live(writing.read(guard)):
            store.checkpoint()
            writing.require(time.monotonic() < deadline, "integrated_guard_exit_timeout")
            time.sleep(1)
        guarded(store.root, [sys.executable, "-m", "wechat_decrypt_tool.course_textbook_gap_final_review",
            "--root", str(store.root), "--writer-job", a.writer_job, "--tokenizer", str(a.tokenizer.resolve()), "worker"],
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
