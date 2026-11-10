"""Finish the two untouched reservations after an actual self-guard exit error.

The completed first private manuscript and stopped controller stay immutable.
Each remaining original guard runs in its own process and is joined before
readonly reconstruction. No completed model role or spent budget is repeated.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import psutil

from . import course_textbook_tail_ready_resume as previous
from .course_knowledge import atomic_json, schema_lease

whole, ready, fresh, writing, guard, digest = (
    previous.whole, previous.ready, previous.fresh, previous.writing, previous.guard, previous.digest)
POLICY = "same-two-untouched-course-reservations-after-actual-self-guard-exit-v1"
DIRECTORY = "ready-guard-exit-continuations"
MODULE = "wechat_decrypt_tool.course_textbook_ready_guard_exit"


def require(ok, code):
    if not ok:
        raise ValueError("requires_ready_guard_exit_" + code)


def folder_for(root, key):
    previous.folder_for(root, key)
    return Path(root).resolve() / "wiki" / DIRECTORY / key


def bindings():
    return {"module_sha256": whole.sha(Path(__file__).read_bytes()), "previous": previous.bindings()}


def unused(root, original, old, flights):
    require(len(flights) == 3, "same_three_original_reservations")
    for number in (1, 2):
        job = root / "wiki/textbook-keyed-jobs" / flights[number]["job"]
        require(not list(old.glob(f"item-{number:02d}-*started.json"))
            and not list(old.glob(f"item-{number:02d}-*result.json"))
            and not (job / "progress.json").exists() and not (job / "ledger.json").exists()
            and not list(job.glob("group-*.json")) and not list((job / "attempts").glob("*.json"))
            and not (job / "whole-source-authoring-execution.json").exists(),
            "remaining_original_ledger_and_writer_roles_unused")
        require(all(not (original / f"item-{number:02d}-{role}-guard").exists()
            for role in ("ledger", "writer")), "two_remaining_original_guards_never_launched")


def inspect(root, key):
    root, original, _, old, plan, tokenizer, flights, _, execution = previous.load(root, key)
    state, started = [whole.read(original / n) for n in ("progress.json", "started.json")]
    protected = whole.read(original / "item-00-writer-guard/guard-progress.json")
    require(state["state"] == "stopped" and state["completed_courses"] == 0 and state["total_courses"] == 3
        and state["reason_code"] == "requires_tail_ready_resume_actual_original_course_writer_guard_success"
        and all(state[k] == started[k] == protected[k] for k in ("pid", "created"))
        and not writing.identity_live(state) and not writing.identity_live(protected)
        and protected["state"] == "worker_exited" and protected["returncode"] == 0,
        "actual_exited_controller_was_its_own_successful_first_writer_guard")
    failures = sorted(original.glob("failure-*.json"))
    require(bool(failures) and all(whole.read(p)["reason_code"] == state["reason_code"]
        and whole.read(p)["error_type"] == "ValueError" for p in failures), "same_actual_self_guard_failure_records")
    unused(root, original, old, flights)
    actual = previous.verify_writer(root, key, 0)
    require(actual["private"] is not None and actual["summary"]["verified_units"]
        == actual["summary"]["total_units"] > 0 and actual["summary"]["model_calls"] == 0,
        "entire_first_original_manuscript_reconstructed_without_new_roles")
    paths = [root / name for name in actual["proof"]["files"]]
    paths += [original / n for n in ("execution.json", "progress.json", "started.json", "independent-input-restore.json",
        "predecessor-proof.json", "item-00-ledger-guard/guard-started.json", "item-00-ledger-guard/guard-progress.json",
        "item-00-writer-guard/guard-started.json", "item-00-writer-guard/guard-progress.json")]
    paths += failures
    paths += list(old.glob("item-00-*.json"))
    history = root / "wiki/ready-guard-exit-preparation-history" / key
    paths += list(history.glob("*")) if history.exists() else []
    proof = {"policy": POLICY, "batch": key, "code": bindings(), "original_execution_sha256": digest(execution),
        "first_summary": actual["summary"], "first_proof_sha256": digest(actual["proof"]),
        "controller_and_first_guard": {k: state[k] for k in ("pid", "created")},
        "remaining_original_items": [1, 2],
        "remaining_original_ledger_groups": [flights[n]["processing_groups"] for n in (1, 2)],
        "files": {p.relative_to(root).as_posix(): whole.sha(p.read_bytes()) for p in paths},
        "separate_guard_process_required": True, "original_budgets_reset": False,
        "automatic_retry_allowed": False, "automatic_thermal_resume_allowed": False,
        "reader_publication_allowed": False, "source_deletion_allowed": False}
    return root, original, old, plan, tokenizer, flights, actual, proof


def prepare(root, key):
    guard.checkpoint(Path(root).resolve())
    with schema_lease(Path(root) / "wiki/full-course-batch-prepare.lock", timeout=0):
        root, _, _, _, _, _, actual, proof = inspect(root, key)
        folder = folder_for(root, key)
        require(not folder.exists(), "single_original_unused_two_course_continuation")
        fresh.retain(folder / "first-manuscript-proof.json", actual["proof"])
        fresh.retain(folder / "execution.json", proof)
        return {"state": "original_two_course_separate_guard_continuation_prepared", "batch": key,
            "first_private_manuscript_units": proof["first_summary"]["verified_units"],
            "first_unresolved_issues": proof["first_summary"]["unresolved_issues"],
            "remaining_original_courses": 2, "model_calls": 0}


def load(root, key):
    root, original, _, old, plan, tokenizer, flights, _, execution = previous.load(root, key)
    folder = folder_for(root, key)
    proof = whole.read(folder / "execution.json")
    require(proof["policy"] == POLICY and proof["batch"] == key and proof["code"] == bindings()
        and proof["original_execution_sha256"] == digest(execution) and proof["remaining_original_items"] == [1, 2],
        "same_frozen_original_two_course_continuation")
    for name, checksum in proof["files"].items():
        path = (root / name).resolve()
        require(path.is_relative_to(root) and whole.sha(path.read_bytes()) == checksum,
            "original_failures_first_manuscript_and_spent_roles_preserved")
    require(digest(whole.read(folder / "first-manuscript-proof.json")) == proof["first_proof_sha256"],
        "same_complete_first_manuscript_and_all_original_issues")
    return root, folder, original, old, plan, tokenizer, flights, proof


def guard_role(root, key, number, role):
    root, _, original, _, _, _, _, _ = load(root, key)
    require(number in (1, 2) and role in ("ledger", "writer"), "only_original_remaining_roles")
    module, command = (ready.__name__, "ledger-worker") if role == "ledger" else (previous.__name__, "writer-worker")
    return guard.run(root, original / f"item-{number:02d}-{role}-guard", [sys.executable, "-m", module,
        "--root", str(root), "--batch", key, "--item", str(number), command])


def run_guard(root, key, number, role, *, launch=subprocess.Popen, process=psutil.Process, sleep=time.sleep):
    root, folder, original, _, _, _, _, _ = load(root, key)
    require(number in (1, 2) and role in ("ledger", "writer"), "only_original_remaining_roles")
    target = original / f"item-{number:02d}-{role}-guard"
    require(not target.exists(), "one_original_guard_launch_without_retry")
    target.mkdir()
    prefix = f"item-{number:02d}-{role}-guard"
    with (folder / (prefix + ".stdout.log")).open("xb") as out, (folder / (prefix + ".stderr.log")).open("xb") as err:
        child = launch([sys.executable, "-m", MODULE, "--root", str(root), "--batch", key,
            "--item", str(number), "--role", role, "guard-role"], stdout=out, stderr=err,
            env={**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"},
            creationflags=0x08000000 if os.name == "nt" else 0)
        identity = {"pid": child.pid, "created": process(child.pid).create_time()}
        require(identity["pid"] != psutil.Process().pid, "guard_is_distinct_from_the_verifying_controller")
        fresh.retain(folder / (prefix + "-process.json"), identity)
        # The child guard checks temperature, power, disk and pause controls.
        # Join it before verification instead of testing this controller's PID.
        while child.poll() is None:
            sleep(5)
        returncode = child.wait()
    status = whole.read(target / "guard-progress.json")
    require(returncode == 0 and status["state"] == "worker_exited" and status["returncode"] == 0
        and all(status[k] == v for k, v in identity.items()) and not writing.identity_live(status),
        "actual_distinct_original_guard_successfully_exited_before_reconstruction")
    return identity


def prepare_writer(root, key, number, plan, flights, tokenizer):
    item, flight = plan["items"][number], flights[number]
    with ready.reads.bounded_ledger_reads(writing):
        writing.load_inputs(root, item["inventory"], flight["job"], tokenizer)
        fresh.prepare(root, item["inventory"], flight["job"], tokenizer)
        _, _, _, job, contract = fresh.bound(root, item["inventory"], flight["job"], tokenizer)
    require(not (job.root / "progress.json").exists() and not list((job.root / "attempts").glob("*.json")),
        "first_original_writer_only")
    _, _, _, _, _, _, _, prior, execution = previous.load(root, key)
    extra = ready.writer_execution(job, contract, key, number)
    bound = previous.original.writer_execution(job, key, number, contract, extra, prior)
    fresh.retain(job.root / ready.COUNT_CONTRACT, extra)
    fresh.retain(job.root / previous.original.WRITER, bound)
    fresh.retain(job.root / previous.graphs.CONTRACT, previous.writer_execution(job, key, number, bound, execution))
    return job.key


def run(root, key):
    root, folder, original, old, plan, tokenizer, flights, proof = load(root, key)
    owner = psutil.Process()
    completed = [{"item": 0, "writer": proof["first_summary"]["job"], "proof_sha256": proof["first_proof_sha256"],
        "unresolved_issues": proof["first_summary"]["unresolved_issues"]}]

    def progress(state, **extra):
        atomic_json(folder / "progress.json", {"pid": owner.pid, "created": owner.create_time(), "updated": time.time(),
            "batch": key, "state": state, "completed_courses": len(completed), "total_courses": 3,
            "reader_publication_allowed": False, "source_deletion_allowed": False, **extra})

    with schema_lease(folder / "controller.lock", timeout=0), schema_lease(root / "wiki/full-course-batch-worker.lock", timeout=0):
        require(not (folder / "started.json").exists(), "single_controller_without_automatic_retry")
        restored = whole.read(folder / "independent-input-restore.json")
        require(restored["state"] == "ready_guard_exit_inputs_independently_restored" and restored["batch"] == key
            and restored["original_root_reads"] == restored["model_calls"] == 0
            and restored["first_manuscript_fully_reconstructed"] and restored["remaining_original_courses_unused"] == 2,
            "actual_independent_first_result_and_remaining_input_restore")
        unused(root, original, old, flights)
        fresh.retain(folder / "started.json", {"pid": owner.pid, "created": owner.create_time()})
        try:
            for number in proof["remaining_original_items"]:
                guard.checkpoint(root)
                item, flight = plan["items"][number], flights[number]
                ready.original.check_item(root, item)
                progress("building_original_unused_ledger", item=number, ledger=flight["job"])
                run_guard(root, key, number, "ledger")
                writer = prepare_writer(root, key, number, plan, flights, tokenizer)
                progress("writing_original_unused_course", item=number, writer=writer)
                run_guard(root, key, number, "writer")
                actual = previous.verify_writer(root, key, number)
                require(actual["private"] is not None and actual["summary"]["verified_units"]
                    == actual["summary"]["total_units"], "entire_original_course_manuscript")
                fresh.retain(folder / f"item-{number:02d}-writer-proof.json", actual["proof"])
                snapshot = fresh.snapshot.save(root, actual)
                completed.append({"item": number, "writer": writer, "proof_sha256": digest(actual["proof"]),
                    "snapshot": snapshot.relative_to(root).as_posix(), "unresolved_issues": len(actual["issues"])})
                progress("original_complete_private_manuscript_reconstructed")
            result = {"state": "three_original_private_manuscripts_reconstructed_require_full_quality_review",
                "batch": key, "completed_courses": len(completed), "total_courses": 3, "items": completed,
                "original_failed_controller_preserved": True, "original_budgets_reset": False,
                "semantic_acceptance": False, "reader_publication_allowed": False, "source_deletion_allowed": False}
            fresh.retain(folder / "result.json", result)
            progress(result["state"])
            return {k: v for k, v in result.items() if k != "items"}
        except BaseException as exc:
            failure = whole.failure_status(exc)
            fresh.retain(folder / ("failure-" + str(time.time_ns()) + ".json"), failure)
            progress(failure["state"], reason_code=failure["reason_code"])
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--batch", required=True)
    parser.add_argument("--item", type=int)
    parser.add_argument("--role", choices=["ledger", "writer"])
    parser.add_argument("command", choices=["prepare", "load", "run", "guard-role"])
    args = parser.parse_args()
    try:
        if args.command == "guard-role":
            result = guard_role(args.root, args.batch, args.item, args.role)
        elif args.command == "load":
            load(args.root, args.batch)
            result = {"state": "same_original_self_guard_failure_and_remaining_inputs", "batch": args.batch, "model_calls": 0}
        else:
            result = {"prepare": prepare, "run": run}[args.command](args.root, args.batch)
        print(json.dumps(result))
    except BaseException as exc:
        print(json.dumps(whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
