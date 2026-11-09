"""Continue the same three unused reservations after the exact graph recovery.

Old controllers remain stopped. A successful whole recovered manuscript and a
real finite numbered-graph calibration precede any original course model role.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import re
import sys
import time

import psutil

from . import course_textbook_ready_resume as original
from . import course_textbook_graph_write_resume as predecessor
from . import course_textbook_numbered_graphs as graphs
from . import course_schema_capabilities as probes
from .course_knowledge import atomic_json, schema_lease

ready, whole, fresh, writing, guard, digest = (
    original.ready, original.whole, original.fresh, original.writing, original.guard, original.digest)
POLICY = "same-original-ready-reservations-after-exact-graph-recovery-v1"
DIRECTORY = "graph-ready-continuations"


def require(ok, code):
    if not ok:
        raise ValueError("requires_graph_ready_resume_" + code)


def folder_for(root, key):
    require(isinstance(key, str) and re.fullmatch(r"[a-f0-9]{32}", key), "original_batch_id")
    return Path(root).resolve() / "wiki" / DIRECTORY / key


def bindings():
    return {"module_sha256": whole.sha(Path(__file__).read_bytes()), "original": original.bindings(),
        "predecessor": predecessor.bindings(), "numbered_graphs": graphs.binding(), "probes": probes.bindings()}


def calibration(root, key):
    whole.checked_sha(key)
    folder = root / "wiki/schema-numbered-graph-probes" / key
    plan = whole.read(folder / "manifest.json")
    driver = root / "wiki/probe_numbered_graph_20261110.py"
    require(digest(plan) == key and plan["policy"] == "actual-writer-two-fixed-numbered-graph-probes-once-v1"
        and plan["model"] == "qwen3.5:9b" and plan["maximum_calls"] == 2 and plan["attempts_per_case"] == 1
        and plan["code"]["driver"] == whole.sha(driver.read_bytes())
        and plan["code"]["numbered_graphs"] == graphs.binding()
        and plan["code"]["baseline"] == probes.bindings(), "exact_frozen_two_synthetic_cases")
    state = whole.read(folder / "guard-progress.json")
    require(state["state"] == "worker_exited" and state["returncode"] == 0
        and not writing.identity_live(state), "actual_calibration_successful_exit")
    spec = importlib.util.spec_from_file_location("private_numbered_graph_calibration", driver)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.verify(root, key)
    require(result["completed"] == result["total"] == result["model_calls"] == 2
        and all(row["schema_valid"] and row["compiled_graph_valid"] for row in result["outcomes"]),
        "both_actual_cases_pass_without_retry")
    files = [driver, *folder.glob("*.json"), *folder.glob("*-wire-response.bin")]
    # Driver logs can still be flushed as its guard exits. They are not evidence.
    files = [path for path in files if path.name != "progress.json"]
    baseline = root / "wiki/schema-capability-probes" / module.BASELINE / "result.json"
    files.append(baseline)
    return {"probe": key, "model_digest": plan["model_digest"], "actual_original_calls": 2,
        "replay_calls": 0, "result_sha256": digest(result), "course_semantic_quality_proven": False}, files


def prepare(root, key, probe):
    root, stopped, old, plan, _, flights, prior = original.load(root, key)
    guard.checkpoint(root)
    with schema_lease(root / "wiki/full-course-batch-prepare.lock", timeout=0):
        state = whole.read(stopped / "progress.json")
        require(state["state"] == "stopped" and not writing.identity_live(state)
            and state["completed_courses"] == 0 and state["total_courses"] == len(flights)
            and state["reason_code"] == "requires_ready_resume_predecessor_failed_or_paused_no_automatic_resume",
            "actual_stopped_continuation_before_any_course_role")
        ready.unused(root, old, flights)
        writer = plan["predecessor_writer"]
        _, target, graph = predecessor.contract(root, writer)
        require(prior["predecessor_writer"] == writer
            and graph["original_continuation_sha256"] == prior["predecessor_execution_sha256"],
            "same_original_writer_and_all_reserved_courses")
        protected = whole.read(target / "guard/guard-progress.json")
        worker = whole.read(root / "wiki/textbook-whole-authoring-jobs" / writer / "progress.json")
        require(protected["state"] == "running" and worker["state"] not in {"stopped", "paused", "pausing"}
            and writing.identity_live(protected) and writing.identity_live(worker), "exact_live_graph_continuation")
        checked, paths = calibration(root, probe)
        require(checked["model_digest"] == plan["models"]["qwen3.5:9b"], "same_actual_original_writer_weights")
        paths += [stopped / name for name in ["execution.json", "progress.json", "started.json", "independent-input-restore.json"]]
        paths += list(stopped.glob("failure-*.json"))
        paths += [target / name for name in ["execution.json", "started.json", "independent-input-restore.json", "guard/guard-started.json"]]
        execution = {"policy": POLICY, "batch": key, "code": bindings(), "original_plan_sha256": digest(plan),
            "original_preflights_sha256": digest(flights), "original_stopped_continuation": state,
            "predecessor_writer": writer, "predecessor_directory": target.relative_to(root).as_posix(),
            "predecessor_execution_sha256": digest(graph),
            "predecessor_guard": {k: protected[k] for k in ["pid", "created"]},
            "predecessor_worker": {k: worker[k] for k in ["pid", "created"]},
            "calibration": checked, "courses": len(flights), "original_roles_unused": True,
            "files": {path.relative_to(root).as_posix(): whole.sha(path.read_bytes()) for path in paths},
            "original_stage_attempt_budget": 2, "semantic_repairs_per_unit": 1, "original_budgets_reset": False,
            "automatic_retry_allowed": False, "automatic_thermal_resume_allowed": False,
            "reader_publication_allowed": False, "source_deletion_allowed": False}
        folder = folder_for(root, key)
        require(not folder.exists(), "single_explicit_continuation_registration")
        fresh.retain(folder / "execution.json", execution)
        return {"state": "same_original_three_courses_graph_continuation_prepared", "batch": key,
            "courses": len(flights), "records": sum(item["records"] for item in plan["items"]),
            "characters": sum(item["characters"] for item in plan["items"]), "model_calls": 0}


def load(root, key):
    root, stopped, old, plan, tokenizer, flights, prior = original.load(root, key)
    folder = folder_for(root, key)
    execution = whole.read(folder / "execution.json")
    require(execution["policy"] == POLICY and execution["batch"] == key and execution["code"] == bindings()
        and execution["original_plan_sha256"] == digest(plan)
        and execution["original_preflights_sha256"] == digest(flights), "same_complete_original_inputs_and_frozen_code")
    for name, expected in execution["files"].items():
        require(whole.sha(ready.source_path(root, name).read_bytes()) == expected, "preserved_failures_and_evidence")
    return root, folder, stopped, old, plan, tokenizer, flights, prior, execution


def wait(root, execution, *, live=writing.identity_live, sleep=time.sleep):
    target = root / execution["predecessor_directory"]
    while True:
        guard.checkpoint(root)
        protected = whole.read(target / "guard/guard-progress.json")
        worker = whole.read(root / "wiki/textbook-whole-authoring-jobs" / execution["predecessor_writer"] / "progress.json")
        expected = execution["predecessor_guard"], execution["predecessor_worker"]
        require(all(row.get(key) == value for row, identity in zip([protected, worker], expected, strict=True)
            for key, value in identity.items()), "same_exact_guard_and_actual_worker")
        require(all(row["state"] not in {"paused", "pausing", "stopped"} for row in [protected, worker]),
            "predecessor_failed_or_paused_no_automatic_resume")
        if not any(live(identity) for identity in expected):
            require(protected["state"] == "worker_exited" and protected["returncode"] == 0
                and worker["stage"] == "private_article_assembled", "actual_complete_successful_graph_writer_exit")
            actual = predecessor.verify(root, execution["predecessor_writer"])
            require(actual["private"] is not None and actual["summary"]["job"] == execution["predecessor_writer"]
                and actual["summary"]["verified_units"] == actual["summary"]["total_units"], "entire_actual_recovered_manuscript")
            return {"original_predecessor": actual["proof"], "model_calls": 0,
                "semantic_acceptance": False, "reader_publication_allowed": False}
        sleep(15)


def writer_execution(job, key, number, bound, execution):
    return {"policy": POLICY, "writer": job.key, "batch": key, "item": number, "format": graphs.binding(),
        "original_first_plan_sha256": digest(bound), "continuation_execution_sha256": digest(execution),
        "calibration": execution["calibration"], "original_roles_unused_at_registration": True,
        "original_budgets_reset": False, "reader_publication_allowed": False}


def writer_bound(root, key, number):
    root, folder, _, _, plan, _, _, _, execution = load(root, key)
    _, _, item, flight, tokenizer, job, contract, extra, bound = original.writer_bound(root, key, number)
    proof = whole.read(job.root / graphs.CONTRACT)
    require(item == plan["items"][number] and proof == writer_execution(job, key, number, bound, execution),
        "same_original_unused_writer_and_numbered_format")
    return root, folder, item, flight, tokenizer, job, contract, extra, bound, proof


def writer_type(contract, extra, key, number, bound, proof, *, read_only):
    parent = original.writer_type(contract, extra, key, number, bound, read_only=read_only)
    return graphs.job_type(parent, proof, read_only=read_only)


def writer_worker(root, key, number):
    root, _, item, flight, tokenizer, job, contract, extra, bound, proof = writer_bound(root, key, number)
    guard.checkpoint(root)
    require(not (job.root / "progress.json").exists() and not list((job.root / "attempts").glob("*.json")),
        "first_original_writer_role_only")
    with ready.reads.bounded_ledger_reads(writing), fresh.installed(writer_type(contract, extra, key, number, bound, proof, read_only=False)):
        return writing.run(root, item["inventory"], flight["job"], tokenizer)


def verify_writer(root, key, number):
    root, folder, item, flight, tokenizer, _, contract, extra, bound, proof = writer_bound(root, key, number)
    state = whole.read(folder / f"item-{number:02d}-writer-guard/guard-progress.json")
    require(state["state"] == "worker_exited" and state["returncode"] == 0 and not writing.identity_live(state),
        "actual_original_course_writer_guard_success")
    previous = fresh.job_type
    fresh.job_type = lambda value, *, read_only: writer_type(contract, extra, key, number, bound, proof, read_only=read_only)
    try:
        with ready.reads.bounded_ledger_reads(writing):
            result = fresh.reconstruct(root, item["inventory"], flight["job"], tokenizer)
    finally:
        fresh.job_type = previous
    result["proof"].update(first_numbered_graph_contract_sha256=digest(proof))
    return result


def run(root, key):
    root, folder, _, old, plan, tokenizer, flights, prior, execution = load(root, key)
    owner, completed = psutil.Process(), []

    def progress(state, **extra):
        atomic_json(folder / "progress.json", {"pid": owner.pid, "created": owner.create_time(), "updated": time.time(),
            "state": state, "batch": key, "completed_courses": len(completed), "total_courses": len(flights),
            "reader_publication_allowed": False, "source_deletion_allowed": False, **extra})

    with schema_lease(folder / "controller.lock", timeout=0):
        require(not (folder / "started.json").exists(), "single_controller_no_automatic_retry")
        restored = whole.read(folder / "independent-input-restore.json")
        require(restored["state"] == "graph_ready_inputs_independently_restored" and restored["batch"] == key
            and restored["original_root_reads"] == restored["model_calls"] == 0, "independently_restored_actual_inputs")
        ready.unused(root, old, flights)
        fresh.retain(folder / "started.json", {"pid": owner.pid, "created": owner.create_time()})
        try:
            progress("waiting_for_exact_complete_graph_writer")
            fresh.retain(folder / "predecessor-proof.json", wait(root, execution))
            with schema_lease(root / "wiki/full-course-batch-worker.lock", timeout=0):
                ready.unused(root, old, flights)
                for number, (item, flight) in enumerate(zip(plan["items"], flights, strict=True)):
                    guard.checkpoint(root)
                    ready.original.check_item(root, item)
                    progress("building_original_unused_ledger", item=number, lesson=item["lesson"], ledger=flight["job"])
                    target = folder / f"item-{number:02d}-ledger-guard"
                    target.mkdir()
                    outcome = guard.run(root, target, [sys.executable, "-m", ready.__name__, "--root", str(root),
                        "--batch", key, "--item", str(number), "ledger-worker"])
                    require(outcome["state"] == "private_worker_exited", "actual_original_ledger_success")
                    with ready.reads.bounded_ledger_reads(writing):
                        writing.load_inputs(root, item["inventory"], flight["job"], tokenizer)
                        fresh.prepare(root, item["inventory"], flight["job"], tokenizer)
                        _, _, _, job, contract = fresh.bound(root, item["inventory"], flight["job"], tokenizer)
                    require(not (job.root / "progress.json").exists() and not list((job.root / "attempts").glob("*.json")),
                        "all_original_writer_roles_unused_before_numbered_format")
                    extra = ready.writer_execution(job, contract, key, number)
                    bound = original.writer_execution(job, key, number, contract, extra, prior)
                    fresh.retain(job.root / ready.COUNT_CONTRACT, extra)
                    fresh.retain(job.root / original.WRITER, bound)
                    fresh.retain(job.root / graphs.CONTRACT, writer_execution(job, key, number, bound, execution))
                    progress("writing_original_unused_course", item=number, lesson=item["lesson"], writer=job.key)
                    target = folder / f"item-{number:02d}-writer-guard"
                    target.mkdir()
                    outcome = guard.run(root, target, [sys.executable, "-m", __spec__.name, "--root", str(root),
                        "--batch", key, "--item", str(number), "writer-worker"])
                    require(outcome["state"] == "private_worker_exited", "actual_original_writer_success")
                    actual = verify_writer(root, key, number)
                    snapshot = fresh.snapshot.save(root, actual)
                    row = {"lesson": item["lesson"], "inventory": item["inventory"], "ledger": flight["job"], "writer": job.key,
                        "snapshot": snapshot.relative_to(root).as_posix(), "unresolved_issues": len(actual["issues"]),
                        "reader_publication_allowed": False}
                    fresh.retain(folder / f"item-{number:02d}-result.json", row)
                    completed.append(row)
            result = {"state": ready.SUCCESS, "batch": key, "completed_courses": len(completed),
                "total_courses": len(flights), "items": completed, "reader_publication_allowed": False, "source_deletion_allowed": False}
            fresh.retain(folder / "result.json", result)
            progress(result["state"])
            return {name: value for name, value in result.items() if name != "items"}
        except BaseException as exc:
            failure = whole.failure_status(exc)
            atomic_json(folder / ("failure-" + str(time.time_ns()) + ".json"), failure)
            progress(failure["state"], reason_code=failure["reason_code"])
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--batch", required=True)
    parser.add_argument("--probe")
    parser.add_argument("--item", type=int)
    parser.add_argument("command", choices=["prepare", "load", "run", "writer-worker", "verify-writer"])
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = prepare(args.root, args.batch, args.probe)
        elif args.command == "load":
            _, _, _, _, plan, _, _, _, _ = load(args.root, args.batch)
            result = {"state": "graph_ready_original_inputs_authenticated", "batch": args.batch,
                "courses": len(plan["items"]), "model_calls": 0}
        elif args.command == "verify-writer":
            actual = verify_writer(args.root, args.batch, args.item)
            result = {**actual["summary"], "snapshot": str(fresh.snapshot.save(args.root, actual))}
        elif args.command == "writer-worker":
            result = writer_worker(args.root, args.batch, args.item)
        else:
            result = run(args.root, args.batch)
        print(json.dumps(result))
    except BaseException as exc:
        failure = whole.failure_status(exc)
        folder = folder_for(args.root, args.batch)
        if folder.exists():
            atomic_json(folder / ("failure-" + str(time.time_ns()) + ".json"), failure)
        print(json.dumps(failure))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
