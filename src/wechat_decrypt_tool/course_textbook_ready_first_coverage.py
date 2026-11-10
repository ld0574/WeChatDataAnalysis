"""First full-source coverage for the original completed ready-course manuscript.

The first manuscript is reconstructed once into an authenticated immutable
snapshot. Later loads check all original bytes without repeating model roles or
replaying the same author at every coverage batch. Running authors and the
failed placement job are unrelated and remain untouched.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import copy
import json
from pathlib import Path
import re
import sys
import time

from . import course_textbook_first_boundary_coverage as first
from . import course_textbook_tail_ready_resume as ready
from .course_knowledge import schema_lease

whole, guard, digest, retained = first.whole, first.guard, first.digest, first.retained
POLICY = "first-original-completed-ready-manuscript-full-source-coverage-v1"
PROVIDER = "original-ready-first-complete"
BASE_BINDINGS = first.bindings


def require(ok, code):
    if not ok:
        raise ValueError("requires_ready_first_coverage_" + code)


def snapshot_path(root, key):
    require(isinstance(key, str) and re.fullmatch(r"[a-f0-9]{32}", key), "parent_writer_id")
    return Path(root).resolve() / "wiki/ready-first-coverage-inputs" / key / "snapshot.json"


def bindings():
    return {"module_sha256": whole.sha(Path(__file__).read_bytes()),
        "ready": ready.bindings(), "first_coverage": BASE_BINDINGS()}


def check_complete(actual, key, saved_proof, restore, protected):
    summary = actual["summary"]
    require(summary["job"] == key and actual["private"] is not None
        and summary["verified_units"] == summary["total_units"] > 0 and summary["model_calls"] == 0,
        "actual_entire_original_manuscript")
    require(actual["proof"] == saved_proof and restore["state"] == "ready_guard_exit_inputs_independently_restored"
        and restore["first_manuscript_fully_reconstructed"] is True and restore["first_writer"] == key
        and restore["first_proof_sha256"] == digest(saved_proof)
        and restore["first_verified_units"] == restore["first_total_units"] == summary["total_units"]
        and restore["first_unresolved_issues"] == summary["unresolved_issues"] == len(actual["issues"])
        and restore["original_root_reads"] == restore["model_calls"] == 0,
        "same_actual_independently_restored_complete_manuscript_and_issues")
    require(protected["state"] == "worker_exited" and protected["returncode"] == 0
        and not first.coverage.writing.identity_live(protected), "actual_successfully_exited_original_writer_guard")


def capture(root, key, batch):
    root = Path(root).resolve()
    path = snapshot_path(root, key)
    require(not path.exists(), "one_original_complete_snapshot")
    actual = ready.verify_writer(root, batch, 0)
    folder = root / "wiki/ready-guard-exit-continuations" / batch
    proof_path = folder / "first-manuscript-proof.json"
    restore_path = folder / "independent-input-restore.json"
    guard_path = ready.folder_for(root, batch) / "item-00-writer-guard/guard-progress.json"
    saved, restore, protected = [whole.read(p) for p in (proof_path, restore_path, guard_path)]
    check_complete(actual, key, saved, restore, protected)
    actual = copy.deepcopy(actual)
    actual["proof"]["ready_first_coverage_precondition"] = {"first_proof_sha256": digest(saved),
        "independent_result_restore_sha256": digest(restore), "original_guard_sha256": digest(protected)}
    actual["proof"]["files"].update({p.relative_to(root).as_posix(): whole.sha(p.read_bytes())
        for p in (proof_path, restore_path, guard_path)})
    value = {"policy": POLICY, "parent_writer": key, "batch": batch, "code": bindings(),
        "manuscript": actual, "manuscript_sha256": digest(actual)}
    retained(path, value)
    return value


def manuscript(root, key, batch):
    root = Path(root).resolve()
    value = whole.read(snapshot_path(root, key))
    require(value["policy"] == POLICY and value["parent_writer"] == key and value["batch"] == batch
        and value["code"] == bindings() and digest(value["manuscript"]) == value["manuscript_sha256"],
        "same_frozen_original_manuscript_snapshot")
    actual = value["manuscript"]
    require(actual["summary"]["job"] == key and actual["private"] is not None
        and digest(actual["inventory"]) == actual["summary"]["inventory"]
        and digest(actual["private"]) == actual["proof"]["private_article_sha256"]
        and digest(actual["book"]) == actual["proof"]["article_sha256"]
        and actual["summary"]["verified_units"] == actual["summary"]["total_units"] > 0
        and len(actual["issues"]) == actual["summary"]["unresolved_issues"], "complete_inventory_body_and_all_issues")
    for name, expected in actual["proof"]["files"].items():
        p = (root / name).resolve()
        require(p.is_relative_to(root) and whole.sha(p.read_bytes()) == expected, "all_original_authenticated_bytes_unchanged")
    return actual


@contextmanager
def installed():
    """Scoped provider injection; never edit a frozen original module."""
    parent, previous_bindings = first.parent, first.bindings

    def provider(root, key, kind, batch):
        if kind == PROVIDER:
            return manuscript(root, key, batch)
        return parent(root, key, kind, batch)

    first.parent = provider
    first.bindings = lambda: {"original": previous_bindings(), "ready_first_provider": bindings()}
    try:
        yield
    finally:
        first.parent, first.bindings = parent, previous_bindings


def prepare(root, key, batch, tokenizer):
    root = Path(root).resolve()
    guard.checkpoint(root)
    require(not (root / "wiki/textbook-whole-authoring-jobs" / key / first.coverage.CONTRACT).exists(),
        "no_existing_coverage_budget_for_this_parent")
    require(isinstance(batch, str) and re.fullmatch(r"[a-f0-9]{32}", batch), "original_ready_batch")
    with schema_lease(root / "wiki/ready-first-coverage-prepare.lock", timeout=0):
        if not snapshot_path(root, key).exists():
            capture(root, key, batch)
        else:
            manuscript(root, key, batch)
        with installed():
            return first.prepare(root, key, tokenizer, kind=PROVIDER, batch=batch)


def load(root, key, tokenizer, *, read_only=True):
    with installed():
        return first.load(root, key, tokenizer, read_only=read_only)


def restored_input(bundle):
    value = whole.read(bundle.folder / "independent-input-restore.json")
    require(value["state"] == "ready_first_full_coverage_inputs_independently_restored"
        and value["parent_writer"] == bundle.contract["parent_writer"] and value["job"] == bundle.job.key
        and value["plan_sha256"] == digest(bundle.plan)
        and value["original_root_reads"] == value["new_model_calls"] == 0,
        "actual_independent_full_input_restore_before_first_role")


def worker(root, key, tokenizer):
    bundle = load(root, key, tokenizer, read_only=False)
    restored_input(bundle)
    with installed():
        return first.run(root, key, tokenizer)


def guarded(root, key, tokenizer):
    bundle = load(root, key, tokenizer)
    restored_input(bundle)
    with schema_lease(bundle.folder / "dispatch.lock", timeout=0):
        require(not (bundle.folder / "dispatch-started.json").exists(), "first_dispatch_without_automatic_retry")
        retained(bundle.folder / "dispatch-started.json", bundle.job.identity)
        return guard.run(bundle.root, bundle.folder, [sys.executable, "-m", __spec__.name,
            "--root", str(bundle.root), "--parent", key, "--tokenizer", str(Path(tokenizer).resolve()), "worker"])


def verify(root, key, tokenizer):
    with installed():
        return first.verify(root, key, tokenizer)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--parent", required=True)
    parser.add_argument("--batch")
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("command", choices=["prepare", "load", "worker", "guard", "verify"])
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = prepare(args.root, args.parent, args.batch, args.tokenizer)
        elif args.command == "load":
            bundle = load(args.root, args.parent, args.tokenizer)
            result = {"state": "same_first_ready_full_coverage_inputs_authenticated", "job": bundle.job.key,
                "parent_writer": args.parent, "batches": len(bundle.plan["batches"]),
                "clauses": bundle.plan["clauses"], "sources": bundle.plan["sources"], "new_model_calls": 0}
        else:
            result = {"worker": worker, "guard": guarded, "verify": verify}[args.command](args.root, args.parent, args.tokenizer)
        print(json.dumps(result))
    except BaseException as exc:
        failure = whole.failure_status(exc)
        if args.command == "prepare" and re.fullmatch(r"[a-f0-9]{32}", args.parent):
            retained(args.root.resolve() / "wiki/ready-first-coverage-preparations" / args.parent
                / ("failure-" + str(time.time_ns()) + ".json"), failure)
        print(json.dumps(failure))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
