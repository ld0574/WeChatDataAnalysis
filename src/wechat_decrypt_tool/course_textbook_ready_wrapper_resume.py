"""Keep the running original ledger, then use actual guard process identities.

The prior controller must reach its exact retained launcher-identity failure.
Its healthy ledger is not restarted. Only its unused writer and the final
untouched course remain, with every original model budget and format intact.
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

from . import course_textbook_ready_guard_exit as previous
from . import course_private_guard_identity as identity
from .course_knowledge import atomic_json, schema_lease

whole, ready, fresh, writing, guard, digest = (
    previous.whole, previous.ready, previous.fresh, previous.writing, previous.guard, previous.digest)
POLICY = "original-running-ledger-and-unused-writers-after-observed-venv-guard-identity-v1"
DIRECTORY = "ready-wrapper-continuations"


def require(ok, code):
    if not ok:
        raise ValueError("requires_ready_wrapper_" + code)


def folder_for(root, key):
    previous.folder_for(root, key)
    return Path(root).resolve() / "wiki" / DIRECTORY / key


def bindings():
    return {"module_sha256": whole.sha(Path(__file__).read_bytes()), "previous": previous.bindings(),
        "actual_guard_identity_sha256": whole.sha(Path(identity.__file__).read_bytes())}


def unstarted(root, old, flights):
    ledger = root / "wiki/textbook-keyed-jobs" / flights[1]["job"]
    require(not (ledger / "whole-source-authoring-execution.json").exists()
        and not (old / "item-01-writer-guard").exists(), "second_original_writer_still_unused")
    ledger = root / "wiki/textbook-keyed-jobs" / flights[2]["job"]
    require(not (ledger / "progress.json").exists() and not (ledger / "ledger.json").exists()
        and not list((ledger / "attempts").glob("*.json")) and not list(ledger.glob("group-*.json"))
        and not (ledger / "whole-source-authoring-execution.json").exists()
        and all(not (old / f"item-02-{role}-guard").exists() for role in ("ledger", "writer")),
        "third_original_course_roles_still_unused")


def prepare(root, key):
    root, prior, old, original, plan, tokenizer, flights, execution = previous.load(root, key)
    guard.checkpoint(root)
    with schema_lease(root / "wiki/full-course-batch-prepare.lock", timeout=0):
        observation = whole.read(prior / "windows-wrapper-discovery.json")
        state = whole.read(prior / "progress.json")
        require(observation["state"] == "actual_windows_venv_launcher_guard_mismatch_observed"
            and observation["batch"] == key and observation["actual_guard_descendant_observed_while_live"]
            and observation["actual_guard_command_matches_launcher_arguments"] and observation["launcher_and_guard_differ"]
            and all(state[k] == v for k, v in observation["controller"].items())
            and state["state"] == "building_original_unused_ledger" and state["item"] == 1
            and all(writing.identity_live(observation[k]) for k in ("controller", "launcher", "guard", "worker")),
            "exact_observed_live_original_ledger_not_a_model_retry")
        restored = whole.read(prior / "independent-input-restore.json")
        require(restored["state"] == "ready_guard_exit_inputs_independently_restored"
            and restored["first_proof_sha256"] == execution["first_proof_sha256"]
            and restored["original_root_reads"] == restored["model_calls"] == 0, "previous_actual_entire_first_result_restore")
        unstarted(root, old, flights)
        files = [prior / n for n in ("execution.json", "started.json", "independent-input-restore.json",
            "windows-wrapper-discovery.json", "item-01-ledger-guard-process.json", "first-manuscript-proof.json")]
        files += [old / "item-01-ledger-guard/guard-started.json", original / "item-01-ledger-started.json"]
        proof = {"policy": POLICY, "batch": key, "code": bindings(), "previous_execution_sha256": digest(execution),
            "observation": observation, "first_summary": execution["first_summary"],
            "first_proof_sha256": execution["first_proof_sha256"], "original_plan_sha256": digest(plan),
            "original_flights_sha256": digest(flights), "files": {p.relative_to(root).as_posix(): whole.sha(p.read_bytes()) for p in files},
            "expected_previous_failure": "requires_ready_guard_exit_actual_distinct_original_guard_successfully_exited_before_reconstruction",
            "current_ledger_restarted": False, "remaining_original_writers": [1, 2], "remaining_original_ledgers": [2],
            "original_budgets_reset": False, "automatic_retry_allowed": False, "automatic_thermal_resume_allowed": False,
            "reader_publication_allowed": False, "source_deletion_allowed": False}
        folder = folder_for(root, key)
        require(not folder.exists(), "single_explicit_original_wrapper_continuation")
        fresh.retain(folder / "execution.json", proof)
        return {"state": "original_running_ledger_and_unused_writers_prepared", "batch": key,
            "ledger_restarts": 0, "original_unused_writers": 2, "original_unused_ledgers": 1, "model_calls": 0}


def load(root, key):
    root, prior, old, original, plan, tokenizer, flights, execution = previous.load(root, key)
    folder = folder_for(root, key)
    proof = whole.read(folder / "execution.json")
    require(proof["policy"] == POLICY and proof["batch"] == key and proof["code"] == bindings()
        and proof["previous_execution_sha256"] == digest(execution)
        and proof["original_plan_sha256"] == digest(plan) and proof["original_flights_sha256"] == digest(flights),
        "same_frozen_original_inputs_and_guard_identity_continuation")
    for name, checksum in proof["files"].items():
        path = (root / name).resolve()
        require(path.is_relative_to(root) and whole.sha(path.read_bytes()) == checksum, "original_inputs_first_results_and_process_observation_preserved")
    return root, folder, prior, old, original, plan, tokenizer, flights, proof


def wait_previous(root, key, proof, *, live=writing.identity_live, sleep=time.sleep):
    prior, old = previous.folder_for(root, key), previous.previous.folder_for(root, key)
    observed = proof["observation"]
    while True:
        guard.checkpoint(root)
        state = whole.read(prior / "progress.json")
        protected = whole.read(old / "item-01-ledger-guard/guard-progress.json")
        require(all(state[k] == v for k, v in observed["controller"].items())
            and all(protected[k] == v for k, v in observed["guard"].items()), "same_exact_original_controller_and_actual_guard")
        if not any(live(observed[k]) for k in ("controller", "launcher", "guard", "worker")):
            require(state["state"] == "stopped" and state["reason_code"] == proof["expected_previous_failure"]
                and protected["state"] == "worker_exited" and protected["returncode"] == 0,
                "actual_original_ledger_success_and_retained_exact_launcher_failure")
            failures = sorted(prior.glob("failure-*.json"))
            require(bool(failures) and all(whole.read(p)["reason_code"] == proof["expected_previous_failure"] for p in failures),
                "actual_original_launcher_failure_records_retained")
            return {"previous_failed_controller": state, "successful_original_guard": protected,
                "failure_files": {p.relative_to(root).as_posix(): whole.sha(p.read_bytes()) for p in failures},
                "ledger_restarted": False, "new_model_calls": 0}
        require(state["state"] not in ("paused", "pausing") and protected["state"] not in ("paused", "pausing"),
            "no_automatic_thermal_or_manual_resume")
        sleep(5)


def run_guard(root, key, number, role):
    root, folder, _, old, _, _, _, _, _ = load(root, key)
    require((number, role) in ((1, "writer"), (2, "ledger"), (2, "writer")), "only_still_original_unused_roles")
    target = old / f"item-{number:02d}-{role}-guard"
    require(not target.exists(), "single_original_guard_no_relaunch")
    target.mkdir()
    prefix = f"item-{number:02d}-{role}-guard"
    command = [sys.executable, "-m", previous.MODULE, "--root", str(root), "--batch", key,
        "--item", str(number), "--role", role, "guard-role"]
    with (folder / (prefix + ".stdout.log")).open("xb") as out, (folder / (prefix + ".stderr.log")).open("xb") as err:
        child = subprocess.Popen(command, stdout=out, stderr=err,
            env={**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"},
            creationflags=0x08000000 if os.name == "nt" else 0)
        observed = identity.observe(child, command, target / "guard-progress.json", whole.read)
        fresh.retain(folder / (prefix + "-processes.json"), observed)
        return identity.joined(child, target / "guard-progress.json", observed, whole.read, live=writing.identity_live)


def run(root, key):
    root, folder, _, old, original, plan, tokenizer, flights, proof = load(root, key)
    owner, completed = psutil.Process(), 1

    def progress(state, **extra):
        atomic_json(folder / "progress.json", {"pid": owner.pid, "created": owner.create_time(), "updated": time.time(),
            "state": state, "batch": key, "completed_courses": completed, "total_courses": 3,
            "reader_publication_allowed": False, "source_deletion_allowed": False, **extra})

    with schema_lease(folder / "controller.lock", timeout=0):
        require(not (folder / "started.json").exists(), "single_controller_no_automatic_retry")
        restored = whole.read(folder / "independent-input-restore.json")
        require(restored["state"] == "ready_wrapper_inputs_independently_restored" and restored["batch"] == key
            and restored["original_root_reads"] == restored["model_calls"] == 0, "actual_independent_continuation_input_restore")
        fresh.retain(folder / "started.json", {"pid": owner.pid, "created": owner.create_time()})
        try:
            progress("waiting_for_exact_original_ledger_and_launcher_failure")
            fresh.retain(folder / "previous-terminal.json", wait_previous(root, key, proof))
            with schema_lease(root / "wiki/full-course-batch-worker.lock", timeout=0):
                unstarted(root, old, flights)
                for number in (1, 2):
                    guard.checkpoint(root)
                    if number == 2:
                        progress("building_original_unused_ledger", item=number, ledger=flights[number]["job"])
                        run_guard(root, key, number, "ledger")
                    writer = previous.prepare_writer(root, key, number, plan, flights, tokenizer)
                    progress("writing_original_unused_course", item=number, writer=writer)
                    run_guard(root, key, number, "writer")
                    actual = previous.previous.verify_writer(root, key, number)
                    require(actual["private"] is not None and actual["summary"]["verified_units"] == actual["summary"]["total_units"],
                        "entire_original_manuscript_reconstructed")
                    fresh.retain(folder / f"item-{number:02d}-writer-proof.json", actual["proof"])
                    fresh.snapshot.save(root, actual)
                    completed += 1
                    progress("original_private_manuscript_reconstructed")
            result = {"state": "three_original_private_manuscripts_reconstructed_require_quality_review", "batch": key,
                "completed_courses": completed, "total_courses": 3, "current_ledger_restarted": False,
                "original_budgets_reset": False, "semantic_acceptance": False,
                "reader_publication_allowed": False, "source_deletion_allowed": False}
            fresh.retain(folder / "result.json", result)
            return result
        except BaseException as exc:
            failure = whole.failure_status(exc)
            fresh.retain(folder / ("failure-" + str(time.time_ns()) + ".json"), failure)
            progress(failure["state"], reason_code=failure["reason_code"])
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--batch", required=True)
    parser.add_argument("command", choices=["prepare", "load", "run"])
    args = parser.parse_args()
    try:
        if args.command == "load":
            load(args.root, args.batch)
            result = {"state": "same_original_running_ledger_and_unused_roles", "batch": args.batch, "model_calls": 0}
        else:
            result = {"prepare": prepare, "run": run}[args.command](args.root, args.batch)
        print(json.dumps(result))
    except BaseException as exc:
        print(json.dumps(whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
