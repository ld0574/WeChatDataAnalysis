"""Consume the unused repair slot after a stopped additions write.

The exhausted write stage, original manifest and failures stay unchanged.
Quotes are supplied verbatim by the program, not reconstructed by the model.
Only the parent's existing repair/review-1 slots may be used, once each.
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

from . import course_textbook_gap_additions as original
from .course_knowledge import atomic_json, schema_lease
from .course_library import canonical
from .course_textbook_evidence import sha
from .course_textbook_ledger import TextbookStore

NOTICE = """
这是原任务尚未使用的一次修补，不是新一轮写作。prior_unvalidated_candidate
曾因引用不匹配而失败，它的引文、分类及新增段落均未验证，不作为知识依据。
target_original_sources 在输入末尾重复给出每个目标的完整真实原文，避免混淆编号。
source_quote 已由程序锁定为对应原文，照原样输出；它只证明原文身份，不证明分类正确。
逐个核实目标是否包含有效教学。确有遗漏则按知识逻辑写出完整解释并标 added，
把新增段落明确分配给相应目标；不能把全部目标标为非教学却添加无对应段落。
无关口头内容可以排除，有效例子、操作、条件或限制不能排除。无法判断就 unresolved。
所有旧负面意见保留；此任务不能声称整篇文章通过。
"""


def require(value, reason):
    if not value:
        raise ValueError("requires_additions_continuation_" + reason)


def fixed_quotes(schema, targets, sources):
    """Constrain citation text without changing any semantic verdict field."""
    result = copy.deepcopy(schema)
    for ref in targets:
        text = sources[ref]["text"]
        quote = text[:400]
        original.real_quote(quote, sources[ref])
        result["properties"]["targets"]["properties"][ref]["properties"]["source_quote"] = {"const": quote}
    return result


def bind_once(path, value):
    if path.exists():
        require(json.loads(path.read_bytes()) == value, "frozen_recovery_binding_unchanged")
    else:
        atomic_json(path, value)


class ContinuationJob(original.AdditionJob):
    def progress(self, state, stage, **extra):
        self.current = stage
        value = {**self.identity, "job": self.key, "state": state, "stage": stage,
                 "updated": time.time(), "reader_publication_allowed": False,
                 "source_deletion_allowed": False, **extra}
        atomic_json(self.root / "continuation-progress.json", value)
        atomic_json(self.store.root / "wiki/textbook-gap-additions-continue-current.json", value)


def prepare(root, key, tokenizer):
    require(re.fullmatch(r"[a-f0-9]{32}", key), "valid_parent_id")
    folder = Path(root) / "wiki/textbook-gap-addition-jobs" / key
    manifest = json.loads((folder / "manifest.json").read_bytes())
    require(manifest["id"] == key == sha(canonical(manifest["recipe"]))[:32], "original_manifest_integrity")
    extra = manifest["recipe"]["extra"]
    require(extra["module_sha256"] == sha(Path(original.__file__).read_bytes()), "original_code_unchanged")
    require(extra["tokenizer_sha256"] == sha(Path(tokenizer).read_bytes()), "original_tokenizer_unchanged")
    progress = json.loads((folder / "progress.json").read_bytes())
    require(not original.final.writing.planmod.identity_live(progress), "original_worker_exited")
    require(progress["state"] == "stopped" and progress["stage"] == "additions-write"
            and progress.get("reason_code") == "wiki_stage_attempt_budget_exhausted_additions-write",
            "only_stopped_exhausted_write")
    inputs, _, book, audit, _, bindings = original.final.inputs_for(root, extra["writer"])
    require(bindings == extra["writer_bindings"], "original_article_inputs_unchanged")
    targets = {k: v for k, v in audit["source_coverage"].items() if v["kind"] == "missing"}
    failures = sorted((folder / "attempts").glob("additions-write-*.json"))
    require(manifest["recipe"]["stage_attempt_budget"] == 2 and len(failures) == 2,
            "original_write_budget_still_exhausted")
    records = [json.loads(p.read_bytes()) for p in failures]
    require(all(not r.get("accepted_structure")
                and r.get("validation_error") == "requires_gap_additions_actual_original_quote" for r in records),
            "known_quote_validation_failures")
    require(not (folder / "additions-review-0.json").exists()
            and not list((folder / "attempts").glob("additions-review-0-*.json")), "no_prior_semantic_review")
    candidate = json.loads(records[-1]["raw_response"])
    jsonschema.validate(candidate, original.candidate_schema(book, targets, inputs["sources"]))
    source_bytes = {str(p.relative_to(folder)): sha(p.read_bytes()) for p in
                    [folder / "manifest.json", folder / "progress.json", *failures]}
    context = {"all_original_sources": inputs["sources"], "article": book,
               "targets": targets, "whole_article_reading_problems_retained": audit["reading"],
               "prior_unvalidated_candidate": candidate,
               "target_original_sources": {k: inputs["sources"][k] for k in targets}}
    schema = fixed_quotes(original.candidate_schema(book, targets, inputs["sources"]), targets, inputs["sources"])
    binding = {"policy": "unused-additions-repair-with-program-bound-quotes-v1", "parent": key,
               "parent_files": source_bytes, "original_writer_bindings": bindings,
               "continuation_code_sha256": sha(Path(__file__).read_bytes()),
               "tokenizer_sha256": extra["tokenizer_sha256"],
               "repair_prompt_sha256": sha((original.WRITE + NOTICE).encode()),
               "repair_context_sha256": sha(canonical(context)), "repair_schema_sha256": sha(canonical(schema)),
               "repair_slot": "additions-repair", "review_slot": "additions-review-1",
               "maximum_structural_attempts_per_slot": 2, "additional_semantic_repairs": 0,
               "write_budget_reset": False, "quotes_supplied_by_program_not_model": True}
    return inputs, book, audit, targets, context, schema, binding


def backup_inputs(job, tokenizer, binding):
    folder = Path.home() / "Documents/CourseKnowledgeBackups/first-lesson-gap-additions" / job.key / "continuation"
    folder.mkdir(parents=True, exist_ok=True)
    files = [job.root / name for name in binding["parent_files"]]
    files += [job.root / "continuation-binding.json", Path(__file__), Path(tokenizer)]
    entries = []
    for p in files:
        name = str(p.relative_to(job.root)) if p.is_relative_to(job.root) else p.name
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        data = p.read_bytes()
        require(not target.exists() or target.read_bytes() == data, "prior_backup_unchanged")
        if not target.exists():
            target.write_bytes(data)
        require(target.read_bytes() == data, "backup_byte_match")
        entries.append({"path": name, "sha256": sha(data), "bytes": len(data)})
    bind_once(folder / "receipt.json", {"files": entries, "complete_processing_restore": False})


def run(root, key, tokenizer):
    store = TextbookStore(root)
    store.checkpoint()
    with schema_lease(store.root / "wiki/textbook-gap-additions.lock", timeout=0):
        inputs, book, audit, targets, context, schema, binding = prepare(root, key, tokenizer)
        extra = json.loads((store.root / "wiki/textbook-gap-addition-jobs" / key / "manifest.json").read_bytes())["recipe"]["extra"]
        job = ContinuationJob(store, inputs, extra["writer"], extra["writer_bindings"], tokenizer)
        require(job.key == key, "same_original_budget_identity")
        bind_once(job.root / "continuation-binding.json", binding)
        backup_inputs(job, tokenizer, binding)
        try:
            candidate = job.stage("additions-repair", original.WRITE + NOTICE, schema, context,
                                  lambda v: original.check_candidate(v, book, targets, inputs["sources"]))
            proposed = original.assemble(book, candidate)
            review = job.stage("additions-review-1", original.REVIEW,
                fixed_quotes(original.review_schema(candidate, targets, inputs["sources"]), targets, inputs["sources"]),
                {"all_original_sources": inputs["sources"], "proposed_article": proposed,
                 "candidate": candidate, "targets": targets,
                 "target_original_sources": {k: inputs["sources"][k] for k in targets},
                 "quote_origin": "Program-supplied source identity; not evidence of a correct classification"},
                lambda v: original.check_review(v, candidate, targets, inputs["sources"]))
            atomic_json(job.root / "private-additions-continued.json", {"candidate": candidate, "review": review})
            atomic_json(job.root / "proposed-reading-continued.json", proposed)
            result = {"state": "unused_additions_repair_checked", "job": key,
                "target_sources": len(targets), "resolved_targets": len(original.resolved(candidate, review)),
                "new_paragraphs": len(candidate["additions"]),
                "new_characters": sum(len(p["text"]) for p in candidate["additions"]),
                "paragraphs_with_issues": sum(not all(p[k] for k in ("supported", "fully_explained", "fits_section"))
                                              for p in review["paragraphs"].values()),
                "whole_article_reading_issues_retained": sum(not v["passed"] for v in audit["reading"].values()),
                "proposed_article_sha256": sha(canonical(proposed)), "original_write_budget_reset": False,
                "integration_with_factual_edits_pending": True, "semantic_acceptance": False,
                "reader_publication_allowed": False, "source_deletion_allowed": False, "goal_complete": False}
            atomic_json(job.root / "continuation-result.json", result)
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
    parser.add_argument("--job", required=True)
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("command", choices=["preflight", "worker", "guard"])
    args = parser.parse_args()
    if args.command == "preflight":
        *_, binding = prepare(args.root, args.job, args.tokenizer)
        print(json.dumps({"state": "unused_repair_ready", "job": args.job, "binding_sha256": sha(canonical(binding))}))
    elif args.command == "worker":
        print(json.dumps(run(args.root, args.job, args.tokenizer)))
    else:
        from .course_archive_gpu import checked_sample
        from .course_gpu_guard import run as guarded
        with schema_lease(args.root / "wiki/textbook-gap-additions-dispatch.lock", timeout=0):
            prepare(args.root, args.job, args.tokenizer)
            previous = args.root / "wiki-revision-thermal-guard.json"
            if previous.exists():
                data = previous.read_bytes()
                require(not original.final.writing.planmod.identity_live(json.loads(data)), "guard_slot_not_in_use")
                history = args.root / "wiki/textbook-gap-addition-guard-history" / (sha(data) + ".json")
                history.parent.mkdir(parents=True, exist_ok=True)
                require(not history.exists() or history.read_bytes() == data, "old_guard_history_unchanged")
                if not history.exists():
                    history.write_bytes(data)
            guarded(args.root, [sys.executable, "-m", "wechat_decrypt_tool.course_textbook_gap_additions_continue",
                "--root", str(args.root), "--job", args.job, "--tokenizer", str(args.tokenizer), "worker"],
                maximum_temperature=70, maximum_power=200, guard_name="wiki-revision-thermal-guard",
                sampler=lambda: checked_sample(args.root))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        msg = str(exc)
        print(json.dumps({"state": "stopped", "error_type": type(exc).__name__,
            "reason_code": msg if re.fullmatch(r"[a-zA-Z0-9_-]{1,180}", msg) else None, "error_sha256": sha(msg.encode())}))
        raise SystemExit(1) from None
