"""Bounded full-record ledger trial with three groups scheduled by model role.

The full lesson inventory stays intact. Trial completion stops after at most
three groups, never publishes a partial ledger, and retains the same budgets
when explicitly continuing the remaining lesson groups later.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

import psutil

from . import course_textbook_evidence as evidence
from . import course_textbook_whole_sources as whole
from .course_knowledge import atomic_json, schema_lease
from .course_library import canonical

POLICY = "full-original-18000-character-three-group-role-concentration-trial-v1"
GROUP_CHARACTERS = 18000
BATCH_GROUPS = 3


def require(ok, code):
    if not ok:
        raise ValueError("requires_source_pilot_" + code)


def prepare(root, lesson, tokenizer):
    root = Path(root).resolve()
    whole.TextbookStore(root).checkpoint()
    existing = list((root / "wiki/textbook-whole-record-evidence").glob("*/manifest.json"))
    require(not any(whole.read(p)["lesson"] == lesson for p in existing), "new_full_record_lesson_only")
    parent_key, parent = evidence.prepare(root, lesson)
    parent_folder = root / "wiki/textbook-evidence" / parent_key
    require(not (parent_folder / "whole-source-ledger-execution.json").exists(), "existing_execution_must_not_be_refreshed")
    value = whole.build(parent_key, parent)
    value.update(group_character_limit=GROUP_CHARACTERS, pages=whole.groups_for(value["sources"], GROUP_CHARACTERS))
    value["throughput_trial"] = {"policy": POLICY, "role_batch_groups": BATCH_GROUPS,
        "module_sha256": whole.sha(Path(__file__).read_bytes()), "all_courseware_still_requires_specific_association": True}
    whole.validate(value, parent)
    key = whole.sha(canonical(value))
    folder = root / "wiki/textbook-whole-record-evidence" / key
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "manifest.json"
    require(not path.exists(), "trial_inventory_already_prepared")
    atomic_json(path, value)
    binding = make_binding(value, tokenizer)
    job_key = whole.sha(canonical(binding))[:32]
    job_folder = root / "wiki/textbook-keyed-jobs" / job_key
    job_folder.mkdir(parents=True, exist_ok=True)
    atomic_json(job_folder / "manifest.json", {"id": job_key, "binding": binding})
    extractor = whole.Job(whole.TextbookStore(root), value, binding, "qwen3.5:9b", tokenizer)
    capacities = []
    for refs in value["pages"]:
        context = whole.context_for(value, refs)
        count, output, capacity = extractor.budget([{"content": whole.EXTRACT},
            {"content": json.dumps({**context, "previous_validation_failure": ""}, ensure_ascii=False)}], "extract")
        capacities.append({"input_tokens": count, "output_reserved": output, "context": capacity})
    atomic_json(job_folder / "request-preflight.json", capacities)
    backup = Path.home() / "Documents/CourseKnowledgeBackups/whole-source-pilot-inputs" / job_key
    for source, relative in [(path, "whole-inventory.json"), (parent_folder / "manifest.json", "parent-inventory.json"),
        (job_folder / "manifest.json", "execution-manifest.json"), (job_folder / "request-preflight.json", "request-preflight.json"),
        (Path(tokenizer), "tokenizer.json"), (Path(__file__), "code/" + Path(__file__).name)]:
        whole.backup(source, backup / relative)
    return {"state": "full_record_trial_prepared", "inventory": key, "job": job_key, "lesson": lesson,
        "complete_original_records": len(value["sources"]), "complete_original_characters": value["original_characters"],
        "full_lesson_groups": len(value["pages"]), "trial_groups": min(BATCH_GROUPS, len(value["pages"])),
        "group_character_limit": GROUP_CHARACTERS, "input_tokens_max": max(c["input_tokens"] for c in capacities),
        "model_calls": 0, "reader_article_published": False, "source_deletion_allowed": False}


def make_binding(value, tokenizer):
    require(value["throughput_trial"] == {"policy": POLICY, "role_batch_groups": BATCH_GROUPS,
        "module_sha256": whole.sha(Path(__file__).read_bytes()), "all_courseware_still_requires_specific_association": True}, "bound_trial_revision")
    models = ["qwen3.5:9b", "course-defiant:q4"]
    return {"inventory": whole.sha(canonical(value)), "module_sha256": whole.module_bindings(),
        "models": {name: whole.local_model_digest(name) for name in models},
        "tokenizer_sha256": whole.sha(Path(tokenizer).read_bytes()), "maximum_semantic_repairs_per_group": 1,
        "maximum_structural_attempts": 2, "old_budgets_reset": False, "throughput_trial": value["throughput_trial"]}


def process_batch(extractor, reviewer, inventory, numbers, *, checkpoint=lambda: None, cached_groups=()):
    """Original requests and stage names; only cross-group scheduling changes."""
    contexts, selected, candidates, audits = {}, {}, {}, {}
    for number in numbers:
        refs = inventory["pages"][number]
        contexts[number] = whole.context_for(inventory, refs)
        selected[number] = {ref: inventory["sources"][ref] for ref in refs}

    def extract(number, suffix, extra=None):
        checkpoint()
        return whole.checked_stage(extractor, f"group-{number:04d}" + suffix, whole.EXTRACT,
            whole.keyed.source_schema(selected[number]), {**contexts[number], **(extra or {})}, selected[number],
            lambda row: whole.keyed.normalize_sources(row, selected[number]), cached_only=number in cached_groups)[0]

    def review(number, suffix):
        checkpoint()
        return whole.checked_stage(reviewer, f"group-{number:04d}" + suffix, whole.REVIEW,
            whole.keyed.review_schema(candidates[number]), whole.review_context(contexts[number], candidates[number]), selected[number],
            lambda row: whole.keyed.normalize_review(row, candidates[number], selected[number]), cached_only=number in cached_groups)[0]

    for number in numbers:
        candidates[number] = extract(number, "-extract")
    for number in numbers:
        audits[number] = review(number, "-review")
    repair_numbers = [number for number in numbers if not whole.audit_passed(audits[number])]
    for number in repair_numbers:
        candidates[number] = extract(number, "-repair", {"candidate": candidates[number], "review": audits[number]})
    for number in repair_numbers:
        audits[number] = review(number, "-repair-review")
    return {number: whole.process_group(extractor, reviewer, contexts[number], selected[number],
        f"group-{number:04d}", cached_only=True) for number in numbers}


def run(root, inventory_key, tokenizer, *, complete=False):
    root = Path(root).resolve()
    store, inventory = whole.TextbookStore(root), whole.load(root, inventory_key)
    store.checkpoint()
    binding = make_binding(inventory, tokenizer)
    key = whole.sha(canonical(binding))[:32]
    folder = root / "wiki/textbook-keyed-jobs" / key
    require(whole.read(folder / "manifest.json") == {"id": key, "binding": binding}, "unchanged_trial_job_and_budget")
    parent = root / "wiki/textbook-evidence" / inventory["parent_inventory_sha256"]
    target = len(inventory["pages"]) if complete else min(BATCH_GROUPS, len(inventory["pages"]))
    identity = {"pid": psutil.Process().pid, "created": psutil.Process().create_time()}
    state = {"job": key, "inventory": inventory_key, "target_groups": target, "full_lesson_groups": len(inventory["pages"]),
        "completed_groups": 0, "knowledge_points": 0, "reader_article_published": False, "source_deletion_allowed": False}

    def progress(phase, **extra):
        state.update(extra)
        value = {**identity, **state, "state": phase, "updated": time.time()}
        atomic_json(folder / "progress.json", value)
        atomic_json(root / "wiki/whole-source-pilot-current.json", value)

    with schema_lease(parent / "ledger-worker.lock", timeout=0):
        contract = parent / "whole-source-ledger-execution.json"
        require(not contract.exists() or whole.read(contract) == binding, "preserve_original_trial_execution")
        if not contract.exists():
            atomic_json(contract, binding)
        folder.joinpath("failures").mkdir(exist_ok=True)
        if (folder / "ledger.json").exists():
            saved = whole.read(folder / "ledger.json")
            require(saved["data_sha256"] == whole.sha(canonical(saved["data"])), "completed_ledger_checksum")
            whole.validate_result(saved["data"], inventory, key)
            return {"state": saved["data"]["state"], "job": key, "existing_result_retained": True}
        extractor, reviewer = [whole.Job(store, inventory, binding, model, tokenizer) for model in ["qwen3.5:9b", "course-defiant:q4"]]
        outputs, started = [], time.time()
        destination = Path.home() / "Documents/CourseKnowledgeBackups/whole-source-ledgers" / key
        try:
            progress("trial_running_with_original_stage_budgets")
            for start in range(0, target, BATCH_GROUPS):
                numbers = list(range(start, min(start + BATCH_GROUPS, target)))
                cached_groups = set()
                for number in numbers:
                    path = folder / f"group-{number:04d}.json"
                    if not path.exists():
                        continue
                    saved = whole.read(path)
                    context = whole.context_for(inventory, inventory["pages"][number])
                    require(saved["input_sha256"] == whole.sha(canonical(context))
                        and saved["data_sha256"] == whole.sha(canonical(saved["data"]))
                        and all(Path(p).is_file() and whole.sha(Path(p).read_bytes()) == digest
                            for p, digest in saved["stage_files"].items()), "preserved_completed_group_changed")
                    cached_groups.add(number)
                results = process_batch(extractor, reviewer, inventory, numbers, checkpoint=store.checkpoint, cached_groups=cached_groups)
                for number, (data, used) in results.items():
                    context = whole.context_for(inventory, inventory["pages"][number])
                    proof = {"input_sha256": whole.sha(canonical(context)), "data": data, "data_sha256": whole.sha(canonical(data)),
                        "stage_files": {str(path): whole.sha(path.read_bytes()) for path in used}}
                    path = folder / f"group-{number:04d}.json"
                    require(not path.exists() or whole.read(path) == proof, "unchanged_completed_original_group")
                    if not path.exists():
                        atomic_json(path, proof)
                    whole.backup(path, destination / path.name)
                    outputs.append((data["candidate"], data["audit"]))
                for job in (extractor, reviewer):
                    for path in job.root.rglob("*.json"):
                        if path.name != "progress.json":
                            whole.backup(path, destination / job.key / path.relative_to(job.root))
                progress("audited_role_batch_checkpoint", completed_groups=len(outputs),
                    knowledge_points=sum(len(row[0]["points"]) for row in outputs),
                    groups_with_unresolved_reviews=sum(not whole.audit_passed(row[1]) for row in outputs))
            summary = {"job": key, "inventory": inventory_key, "completed_groups": len(outputs),
                "knowledge_points": state["knowledge_points"], "elapsed_seconds_this_run": time.time() - started,
                "full_lesson_groups": len(inventory["pages"]), "unresolved_groups": state["groups_with_unresolved_reviews"],
                "reader_article_published": False, "source_deletion_allowed": False}
            if target == len(inventory["pages"]):
                ledger = whole.compile_ledger(inventory, key, outputs)
                atomic_json(folder / "ledger.json", {"data": ledger, "data_sha256": whole.sha(canonical(ledger))})
                result = {**summary, "state": ledger["state"]}
                atomic_json(folder / "result.json", result)
                whole.backup(folder / "ledger.json", destination / "ledger.json")
                whole.backup(folder / "result.json", destination / "result.json")
            else:
                result = {**summary, "state": "first_role_batch_completed_quality_and_throughput_review_pending",
                    "partial_trial_only": True, "speedup_proven": False}
                atomic_json(folder / "trial-result.json", result)
                whole.backup(folder / "trial-result.json", destination / "trial-result.json")
            progress(result["state"])
            return result
        except BaseException as exc:
            failure = whole.failure_status(exc)
            atomic_json(folder / "failures" / (str(time.time_ns()) + ".json"), failure)
            progress(failure["state"], **{k: v for k, v in failure.items() if k != "state"})
            raise


def guard(root, inventory_key, tokenizer, predecessors):
    from .course_archive_gpu import checked_sample
    from .course_gpu_guard import run as guarded

    root = Path(root).resolve()
    value = whole.load(root, inventory_key)
    make_binding(value, tokenizer)
    identity = {"pid": psutil.Process().pid, "created": psutil.Process().create_time()}
    with schema_lease(root / "wiki/whole-source-pilot-dispatch.lock", timeout=0):
        while any(whole_identity_live(p) for p in predecessors):
            whole.TextbookStore(root).checkpoint()
            atomic_json(root / "wiki/whole-source-pilot-dispatch-current.json", {**identity, "state": "waiting_for_exact_predecessor",
                "predecessors": predecessors, "inventory": inventory_key, "updated": time.time(), "model_calls": 0})
            time.sleep(20)
        prior = root / "wiki-throughput-thermal-guard.json"
        if prior.exists():
            old = whole.read(prior)
            require(not whole_identity_live(old), "prior_pilot_guard_exited")
            whole.backup(prior, root / "wiki/whole-source-pilot-guard-history" / (whole.sha(prior.read_bytes()) + ".json"))
        guarded(root, [sys.executable, "-m", __spec__.name, "--root", str(root), "--inventory", inventory_key,
            "--tokenizer", str(Path(tokenizer).resolve()), "worker"], maximum_temperature=70, maximum_power=200,
            guard_name="wiki-throughput-thermal-guard", sampler=lambda: checked_sample(root))


def whole_identity_live(row):
    try:
        return abs(psutil.Process(row["pid"]).create_time() - row["created"]) < .01
    except psutil.NoSuchProcess:
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("--inventory")
    parser.add_argument("--lesson")
    parser.add_argument("--predecessors", type=Path)
    parser.add_argument("--complete", action="store_true")
    parser.add_argument("command", choices=["prepare", "worker", "guard"])
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            require(args.lesson is not None and args.inventory is None, "explicit_new_lesson_required")
            result = prepare(args.root, args.lesson, args.tokenizer)
        else:
            require(args.inventory is not None, "prepared_inventory_required")
            if args.command == "worker":
                result = run(args.root, args.inventory, args.tokenizer, complete=args.complete)
            else:
                require(not args.complete and args.predecessors is not None, "bounded_trial_and_predecessors_required")
                result = guard(args.root, args.inventory, args.tokenizer, whole.read(args.predecessors))
        print(json.dumps(result or {"state": "guard_returned"}))
    except Exception as exc:
        print(json.dumps(whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
