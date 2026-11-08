"""Continue an authenticated final review with versioned runtime backups.

The old immutable backup and its conflict remain. Static evidence retains its
exact original destination; mutable guard/progress states use content-addressed
versions, read once, and never overwrite an earlier state.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import sys
import time

from . import course_textbook_omitted_mapping_review as reviewed
from .course_knowledge import atomic_json

writing, whole, fresh = reviewed.writing, reviewed.whole, reviewed.fresh
digest = reviewed.digest
DIRECTORY = "versioned-backup-continuation"
POLICY = "authenticated-original-final-review-versioned-runtime-backup-continuation-v1"


def require(ok, code):
    if not ok:
        raise ValueError("requires_checked_backup_" + code)


def versioned_runtime(path, destination):
    raw = path.read_bytes()
    target = destination.parent / (whole.sha(raw) + "-" + destination.name)
    target.parent.mkdir(parents=True, exist_ok=True)
    require(not target.exists() or target.read_bytes() == raw, "same_runtime_version_bytes")
    if not target.exists():
        target.write_bytes(raw)
    require(target.read_bytes() == raw, "exact_runtime_version_copy")
    return target


def backup_job(job):
    destination = Path.home() / "Documents/CourseKnowledgeBackups/whole-source-articles" / job.key
    for path in job.root.rglob("*.json"):
        relative = path.relative_to(job.root)
        if path.name.endswith("progress.json"):
            versioned_runtime(path, destination / "runtime-versions" / relative)
        else:
            whole.backup(path, destination / relative)
    for name in writing.bindings():
        path = Path(writing.__file__).with_name(name + ".py")
        whole.backup(path, destination / "code" / path.name)
    for module in (reviewed, sys.modules[__name__]):
        path = Path(module.__file__)
        whole.backup(path, destination / "adapter-code" / (whole.sha(path.read_bytes()) + "-" + path.name))


@contextmanager
def installed_backup():
    previous = writing.backup_job
    writing.backup_job = backup_job
    try:
        yield
    finally:
        writing.backup_job = previous


def inputs(root, key, tokenizer):
    bundle = reviewed.load(root, key, tokenizer)
    result = reviewed.final_review(bundle, tokenizer, read_only=True)
    return bundle, result


def execution(bundle, result):
    failures = {p.relative_to(bundle.root).as_posix(): whole.sha(p.read_bytes())
        for p in bundle.folder.glob("failure-*.json")}
    old_guard = bundle.folder / "guard-progress.json"
    return {"policy": POLICY, "job": bundle.job.key,
        "original_final_review_result_sha256": digest(result), "original_execution_sha256": digest(reviewed.execution(bundle)),
        "old_failures": failures, "old_guard_sha256": whole.sha(old_guard.read_bytes()),
        "module_sha256": whole.sha(Path(__file__).read_bytes()), "review_adapter_sha256": whole.sha(Path(reviewed.__file__).read_bytes()),
        "static_evidence_backup": "unchanged_original_immutable_paths",
        "runtime_backup": "single_read_content_addressed_versions_for_progress_json",
        "old_backups_changed": False, "original_budgets_reset": False,
        "new_model_roles": 0, "original_final_review_reused_without_inference": True,
        "reader_publication_allowed": False, "source_deletion_allowed": False}


def prepare(root, key, tokenizer):
    bundle, result = inputs(root, key, tokenizer)
    status = whole.read(bundle.job.root / "progress.json")
    old_guard = whole.read(bundle.folder / "guard-progress.json")
    require(not writing.identity_live(status) and not writing.identity_live(old_guard)
        and status["state"] == "stopped" and status.get("reason_code") == "whole_sources_immutable_backup"
        and old_guard["state"] == "worker_exited" and old_guard.get("returncode") == 1, "actual_backup_failure_and_exited_workers")
    require(any(whole.read(p).get("reason_code") == "whole_sources_immutable_backup"
        for p in bundle.folder.glob("failure-*.json")), "retained_actual_backup_failure")
    require(all(not list((bundle.job.root / "attempts").glob(unit + "-*.json"))
        for unit in bundle.contract["future_units"]), "later_original_roles_still_unused")
    folder = bundle.folder / DIRECTORY
    require(not (folder / "execution.json").exists(), "prepare_once")
    fresh.retain(folder / "execution.json", execution(bundle, result))
    return {"state": "actual_final_review_authenticated_for_runtime_backup_continuation", "job": key,
        "verified_prefix_units": bundle.count, "final_review_passed": result["final_checks_passed"],
        "omitted_mapping_obligations": len(bundle.metadata["omitted_point_ids"]), "model_calls": 0}


def load(root, key, tokenizer):
    bundle, result = inputs(root, key, tokenizer)
    require(whole.read(bundle.folder / DIRECTORY / "execution.json") == execution(bundle, result), "unchanged_review_failure_and_backup_policy")
    bundle.checked_result = result
    return bundle


def worker(root, key, tokenizer):
    bundle = load(root, key, tokenizer)
    folder = bundle.folder / DIRECTORY
    reviewed.guarder.checkpoint(bundle.root)
    require(not (folder / "started.json").exists(), "worker_once_no_automatic_retry")
    fresh.retain(folder / "started.json", bundle.job.identity)
    try:
        with installed_backup(), reviewed.retained_obligation(bundle), fresh.installed(
                reviewed.resumed_type(bundle, bundle.checked_result, read_only=False)):
            result = writing.run(bundle.root, bundle.contract["inventory"], bundle.contract["ledger_job"], tokenizer)
        fresh.retain(folder / "result.json", result)
        return result
    except BaseException as exc:
        atomic_json(folder / ("failure-" + str(time.time_ns()) + ".json"), whole.failure_status(exc))
        raise


def reconstruct(root, key, tokenizer, *, complete=True):
    bundle = load(root, key, tokenizer)
    result = reviewed.reconstruct(root, key, tokenizer, complete=complete)
    target = bundle.folder / DIRECTORY
    result["proof"]["files"].update({(target / "execution.json").relative_to(bundle.root).as_posix(): whole.sha((target / "execution.json").read_bytes())})
    result["proof"].update(backup_continuation_policy=POLICY, backup_continuation_adapter_sha256=whole.sha(Path(__file__).read_bytes()))
    if complete:
        require(whole.read(target / "result.json") == whole.read(bundle.job.root / "result.json"), "actual_completed_resumption_result")
    return result


def guard(root, key, tokenizer):
    bundle = load(root, key, tokenizer)
    result = reviewed.guarder.run(bundle.root, bundle.folder / DIRECTORY, [sys.executable, "-m", __spec__.name,
        "--root", str(bundle.root), "--job", key, "--tokenizer", str(Path(tokenizer).resolve()), "worker"])
    if result["state"] == "private_worker_exited":
        rebuilt = reconstruct(root, key, tokenizer)
        return {**rebuilt["summary"], "snapshot": str(reviewed.mapped.snapshot.save(root, rebuilt))}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("command", choices=["prepare", "load", "guard", "worker", "verify", "prefix"])
    args = parser.parse_args()
    try:
        if args.command == "load":
            bundle = load(args.root, args.job, args.tokenizer)
            output = {"state": "checked_backup_inputs_authenticated", "job": bundle.job.key, "model_calls": 0}
        elif args.command in {"prefix", "verify"}:
            rebuilt = reconstruct(args.root, args.job, args.tokenizer, complete=args.command == "verify")
            output = {**rebuilt["summary"], "snapshot": str(reviewed.mapped.snapshot.save(args.root, rebuilt))}
        else:
            output = globals()[args.command](args.root, args.job, args.tokenizer)
        print(json.dumps(output))
    except BaseException as exc:
        print(json.dumps(whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
