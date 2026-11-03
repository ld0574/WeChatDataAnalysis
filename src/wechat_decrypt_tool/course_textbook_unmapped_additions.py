"""Review saved, unmapped prose using the parent's unused final-review slot.

No new writing is permitted. The earliest complete failed repair is retained
verbatim, including every classification and mapping failure. This is only an
internal review; it cannot make that failed writing contract pass.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import time
from pathlib import Path

from . import course_textbook_gap_additions as original
from . import course_textbook_gap_additions_continue as continuation
from .course_knowledge import atomic_json, schema_lease
from .course_library import canonical
from .course_textbook_evidence import sha
from .course_textbook_ledger import TextbookStore

NOTICE = """
这是对已保存失败候选的独立审核，不是写作或修补。
候选中的 unassigned_addition_ids 没有映射到目标，原写作契约仍然失败，不能自动宣布解决。
complete_sources_for_this_check 包含每个目标、每个新增段落引用的完整原文及同类相邻上下文，
这是明确限定的审核范围，不代表重新审核全课。原负面判断只是线索，不是事实。
逐段检查真实依据、解释是否充分及与所在小节的衔接，不能因没有目标映射就跳过事实审核。
逐目标判断有效教学是否被解释；不能因 writer 全部排除就认定它们不是教学。
source_quote 是程序提供的原文身份，不能证明任何教学分类或段落断言正确。
没有真实依据或未解决就如实给负面结果；不要重写、补造映射或为候选辩护。
"""


def require(value, reason):
    if not value:
        raise ValueError("requires_unmapped_additions_" + reason)


def select_candidate(folder, book, targets, sources):
    files = sorted((folder / "attempts").glob("additions-repair-*.json"))
    records = [json.loads(p.read_bytes()) for p in files]
    require(len(records) == 2 and all(not r.get("accepted_structure") and
            r.get("validation_error") == "requires_gap_additions_no_unassigned_prose" for r in records),
            "only_exhausted_unassigned_prose_failures")
    candidate = json.loads(records[0]["raw_response"])
    used = {k for row in candidate["targets"].values() if row["kind"] == "added" for k in row["addition_ids"]}
    unassigned = [row["id"] for row in candidate["additions"] if row["id"] not in used]
    require(bool(unassigned), "actual_unassigned_prose")
    # Removing only unassigned prose in a validation copy proves that no other
    # structural condition is being waived. The stored candidate is unchanged.
    validation_copy = copy.deepcopy(candidate)
    validation_copy["additions"] = [row for row in candidate["additions"] if row["id"] in used]
    original.check_candidate(validation_copy, book, targets, sources)
    return candidate, unassigned, {str(p.relative_to(folder)): sha(p.read_bytes()) for p in files}


def source_scope(sources, targets, candidate):
    required = set(targets) | {k for row in candidate["additions"] for k in row["source_keys"]}
    require(required <= set(sources), "all_cited_sources_present")
    scope = set(required)
    for kind in {row["kind"] for row in sources.values()}:
        refs = [k for k, row in sources.items() if row["kind"] == kind]
        for number, ref in enumerate(refs):
            if ref in required:
                scope.update(refs[max(0, number - 2):number + 3])
    return {k: v for k, v in sources.items() if k in scope}


class UnmappedJob(continuation.ContinuationJob):
    def progress(self, state, stage, **extra):
        self.current = stage
        value = {**self.identity, "job": self.key, "state": state, "stage": stage, "updated": time.time(),
            "original_mapping_failure_retained": True, "reader_publication_allowed": False,
            "source_deletion_allowed": False, **extra}
        atomic_json(self.root / "unmapped-review-progress.json", value)
        atomic_json(self.store.root / "wiki/textbook-unmapped-additions-current.json", value)


def prepare(root, key, tokenizer):
    inputs, book, audit, targets, _, _, prior = continuation.prepare(root, key, tokenizer)
    folder = Path(root) / "wiki/textbook-gap-addition-jobs" / key
    progress = json.loads((folder / "continuation-progress.json").read_bytes())
    require(not original.final.writing.planmod.identity_live(progress)
            and progress["state"] == "stopped" and progress["stage"] == "additions-repair"
            and progress.get("reason_code") == "wiki_stage_attempt_budget_exhausted_additions-repair",
            "original_repair_finished_without_restart")
    require(json.loads((folder / "continuation-binding.json").read_bytes()) == prior, "original_recovery_binding")
    candidate, unassigned, failures = select_candidate(folder, book, targets, inputs["sources"])
    sources = source_scope(inputs["sources"], targets, candidate)
    proposed = original.assemble(book, candidate)
    schema = continuation.fixed_quotes(original.review_schema(candidate, targets, sources), targets, sources)
    context = {"complete_sources_for_this_check": sources, "proposed_article": proposed, "candidate": candidate,
        "targets": targets, "unassigned_addition_ids": unassigned,
        "target_original_sources": {k: inputs["sources"][k] for k in targets},
        "source_scope_is_whole_lesson": False, "original_mapping_failure_retained": True}
    binding = {"policy": "earliest-failed-repair-prose-unused-final-review-v1", "parent": key,
        "original_recovery_binding_sha256": sha(canonical(prior)), "failed_repairs": failures,
        "selection_rule": "earliest_complete_failed_repair_unchanged", "candidate_sha256": sha(canonical(candidate)),
        "unassigned_addition_ids": unassigned, "source_scope": list(sources),
        "schema_sha256": sha(canonical(schema)), "context_sha256": sha(canonical(context)),
        "prompt_sha256": sha((original.REVIEW + NOTICE).encode()), "module_sha256": sha(Path(__file__).read_bytes()),
        "stage": "additions-review-1", "additional_writing_or_repair_calls": 0,
        "original_mapping_failure_retained": True, "reader_publication_allowed": False}
    return inputs, candidate, schema, context, binding


def run(root, key, tokenizer):
    store = TextbookStore(root)
    store.checkpoint()
    with schema_lease(store.root / "wiki/textbook-gap-additions.lock", timeout=0):
        inputs, candidate, schema, context, binding = prepare(root, key, tokenizer)
        folder = store.root / "wiki/textbook-gap-addition-jobs" / key
        extra = json.loads((folder / "manifest.json").read_bytes())["recipe"]["extra"]
        job = UnmappedJob(store, inputs, extra["writer"], extra["writer_bindings"], tokenizer)
        require(job.key == key, "same_parent_review_budget")
        continuation.bind_once(folder / "unmapped-review-binding.json", binding)
        backup = Path.home() / "Documents/CourseKnowledgeBackups/first-lesson-gap-additions" / key / "unmapped-review"
        backup.mkdir(parents=True, exist_ok=True)
        for path in [folder / "unmapped-review-binding.json", Path(__file__)]:
            target = backup / path.name
            require(not target.exists() or target.read_bytes() == path.read_bytes(), "backup_unchanged")
            if not target.exists():
                target.write_bytes(path.read_bytes())
            require(target.read_bytes() == path.read_bytes(), "backup_byte_match")
        try:
            review = job.stage("additions-review-1", original.REVIEW + NOTICE, schema, context,
                lambda v: original.check_review(v, candidate, context["targets"], context["complete_sources_for_this_check"]),
                evidence_packet={"sources": context["complete_sources_for_this_check"]})
            atomic_json(folder / "private-unmapped-additions.json", {"candidate": candidate, "review": review,
                "unassigned_addition_ids": binding["unassigned_addition_ids"], "original_mapping_failure_retained": True})
            result = {"state": "unmapped_prose_reviewed_internally", "job": key,
                "new_paragraphs": len(candidate["additions"]),
                "paragraphs_passed": sum(all(row[k] for k in ["supported", "fully_explained", "fits_section"])
                                          for row in review["paragraphs"].values()),
                "target_sources": len(context["targets"]),
                "resolved_target_classifications": len(original.resolved(candidate, review)),
                "checked_source_scope": len(context["complete_sources_for_this_check"]),
                "original_mapping_failure_retained": True, "whole_lesson_reviewed": False,
                "reader_publication_allowed": False, "semantic_acceptance": False,
                "source_deletion_allowed": False, "goal_complete": False}
            atomic_json(folder / "unmapped-review-result.json", result)
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
    p.add_argument("--job", required=True)
    p.add_argument("--tokenizer", type=Path, required=True)
    p.add_argument("command", choices=["preflight", "worker", "guard"])
    args = p.parse_args()
    if args.command == "preflight":
        *_, binding = prepare(args.root, args.job, args.tokenizer)
        print(json.dumps({"state": "unused_review_ready", "source_inputs": len(binding["source_scope"]),
                          "unassigned_paragraphs": len(binding["unassigned_addition_ids"])}))
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
                require(not original.final.writing.planmod.identity_live(json.loads(data)), "guard_slot_free")
                history = args.root / "wiki/textbook-gap-addition-guard-history" / (sha(data) + ".json")
                history.parent.mkdir(parents=True, exist_ok=True)
                require(not history.exists() or history.read_bytes() == data, "guard_history_unchanged")
                if not history.exists():
                    history.write_bytes(data)
            guarded(args.root, [sys.executable, "-m", "wechat_decrypt_tool.course_textbook_unmapped_additions",
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
