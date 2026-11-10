"""Release the same untouched three courses after the exact original-tail writer.

All three stopped controllers stay stopped. This independent execution binds
their failures, the actual finite tail guard and worker, and the same two real
numbered-graph calibration calls before any original course role can run.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import time

import psutil

from . import course_textbook_graph_ready_resume as previous
from . import course_textbook_graph_tail_roles as predecessor
from . import course_textbook_graph_tail_terminal as terminal
from .course_knowledge import atomic_json, schema_lease

original, ready, whole, fresh, writing, guard, digest, graphs = (
    previous.original, previous.ready, previous.whole, previous.fresh,
    previous.writing, previous.guard, previous.digest, previous.graphs)
POLICY = "same-three-unused-reservations-after-exact-finite-original-tail-v1"
DIRECTORY = "tail-ready-continuations"
calibration, writer_type = previous.calibration, previous.writer_type


def require(ok, code):
    if not ok:
        raise ValueError("requires_tail_ready_resume_" + code)


def folder_for(root, key):
    require(isinstance(key, str) and re.fullmatch(r"[a-f0-9]{32}", key), "original_batch_id")
    return Path(root).resolve() / "wiki" / DIRECTORY / key


def bindings():
    return {"module_sha256": whole.sha(Path(__file__).read_bytes()), "original": original.bindings(),
        "previous": previous.bindings(), "predecessor": predecessor.bindings(), "terminal": terminal.bindings()}


def retained_terminal(root, writer):
    target = predecessor.folder_for(root, writer)
    actual = terminal.inspect(root, writer)
    restored = whole.read(target / "independent-terminal-restore.json")
    require(restored["state"] == "original_graph_tail_terminal_independently_restored"
        and restored["writer"] == writer and restored["terminal_proof_sha256"] == digest(actual)
        and restored["original_root_reads"] == restored["model_calls"] == 0
        and restored["verified_prefix_units"] == actual["verified_prefix_units"] < actual["total_units"]
        and restored["complete_manuscript"] is actual["complete_manuscript"] is False,
        "actual_independent_terminal_restore_stays_explicitly_incomplete")
    require(whole.read(target / "terminal-authenticated-proof.json") == actual, "same_original_incomplete_terminal_proof")
    return actual


def prepare(root, key, probe, *, retain_incomplete=False):
    root, stopped, old, plan, _, flights, prior = original.load(root, key)
    guard.checkpoint(root)
    with schema_lease(root / "wiki/full-course-batch-prepare.lock", timeout=0):
        previous_folder = previous.folder_for(root, key)
        state = whole.read(previous_folder / "progress.json")
        require(state["state"] == "stopped" and not writing.identity_live(state)
            and state["completed_courses"] == 0 and state["total_courses"] == len(flights)
            and state["reason_code"] == "requires_graph_ready_resume_predecessor_failed_or_paused_no_automatic_resume",
            "actual_third_controller_stopped_before_original_roles")
        ready.unused(root, old, flights)
        writer = plan["predecessor_writer"]
        target = predecessor.folder_for(root, writer)
        tail = whole.read(target / "execution.json")
        graph = whole.read(predecessor.prior.folder_for(root, writer) / "execution.json")
        require(tail["policy"] == predecessor.POLICY and tail["writer"] == writer
            and tail["code"] == predecessor.bindings()
            and tail["previous_execution_sha256"] == digest(graph)
            and graph["writer"] == writer
            and graph["original_continuation_sha256"] == prior["predecessor_execution_sha256"],
            "same_original_writer_and_finite_tail")
        protected = whole.read(target / "guard/guard-progress.json")
        worker = whole.read(root / "wiki/textbook-whole-authoring-jobs" / writer / "progress.json")
        incomplete = retained_terminal(root, writer) if retain_incomplete else None
        if incomplete is None:
            require(protected["state"] == "running" and worker["state"] not in {"stopped", "paused", "pausing"}
                and writing.identity_live(protected) and writing.identity_live(worker), "exact_live_original_tail_guard_and_actual_worker")
        checked, paths = calibration(root, probe)
        require(checked["model_digest"] == plan["models"]["qwen3.5:9b"], "same_original_numbered_graph_writer_weights")
        paths += [previous_folder / name for name in ["execution.json", "progress.json", "started.json", "independent-input-restore.json"]]
        paths += list(previous_folder.glob("failure-*.json"))
        paths += [target / name for name in ["execution.json", "started.json", "independent-input-restore.json", "guard/guard-started.json"]]
        if incomplete is not None:
            paths += [root / name for name in incomplete["files"]]
            paths += [target / name for name in ["terminal-authenticated-proof.json", "independent-terminal-restore.json"]]
        history = root / "wiki/tail-ready-prepare-history" / key
        paths += list(history.glob("*")) if history.exists() else []
        execution = {"policy": POLICY, "batch": key, "code": bindings(),
            "original_plan_sha256": digest(plan), "original_preflights_sha256": digest(flights),
            "original_stopped_continuation": whole.read(stopped / "progress.json"),
            "original_graph_controller_failure": state,
            "predecessor_writer": writer, "predecessor_directory": target.relative_to(root).as_posix(),
            "predecessor_execution_sha256": digest(tail),
            "predecessor_total_units": tail["total_units"],
            "predecessor_mode": "complete_original_tail" if incomplete is None else "retained_incomplete_original_tail",
            "terminal_proof_sha256": None if incomplete is None else digest(incomplete),
            "predecessor_guard": {k: protected[k] for k in ["pid", "created"]},
            "predecessor_worker": {k: worker[k] for k in ["pid", "created"]},
            "calibration": checked, "courses": len(flights), "original_roles_unused": True,
            "files": {p.relative_to(root).as_posix(): whole.sha(p.read_bytes()) for p in paths},
            "original_stage_attempt_budget": 2, "semantic_repairs_per_unit": 1,
            "original_budgets_reset": False, "automatic_retry_allowed": False,
            "automatic_thermal_resume_allowed": False, "reader_publication_allowed": False, "source_deletion_allowed": False}
        folder = folder_for(root, key)
        require(not folder.exists(), "single_explicit_original_three_course_registration")
        fresh.retain(folder / "execution.json", execution)
        return {"state": "same_original_three_courses_after_finite_tail_prepared", "batch": key,
            "courses": len(flights), "records": sum(i["records"] for i in plan["items"]),
            "characters": sum(i["characters"] for i in plan["items"]), "model_calls": 0}


def load(root, key):
    root, stopped, old, plan, tokenizer, flights, prior = original.load(root, key)
    folder = folder_for(root, key)
    execution = whole.read(folder / "execution.json")
    require(execution["policy"] == POLICY and execution["batch"] == key and execution["code"] == bindings()
        and execution["original_plan_sha256"] == digest(plan)
        and execution["original_preflights_sha256"] == digest(flights), "same_complete_original_inputs_and_frozen_code")
    for name, expected in execution["files"].items():
        require(whole.sha(ready.source_path(root, name).read_bytes()) == expected, "preserved_original_failures_and_tail_inputs")
    return root, folder, stopped, old, plan, tokenizer, flights, prior, execution


def wait(root, execution, *, live=writing.identity_live, sleep=time.sleep):
    target = root / execution["predecessor_directory"]
    mode = execution.get("predecessor_mode", "complete_original_tail")
    require(mode in {"complete_original_tail", "retained_incomplete_original_tail"}, "known_explicit_predecessor_disposition")
    if mode == "retained_incomplete_original_tail":
        guard.checkpoint(root)
        protected = whole.read(target / "guard/guard-progress.json")
        worker = whole.read(root / "wiki/textbook-whole-authoring-jobs" / execution["predecessor_writer"] / "progress.json")
        require(all(row.get(k) == v for row, identity in zip([protected, worker],
                [execution["predecessor_guard"], execution["predecessor_worker"]], strict=True) for k, v in identity.items())
            and not live(execution["predecessor_guard"]) and not live(execution["predecessor_worker"]),
            "same_exact_exited_incomplete_predecessor_not_a_resume")
        actual = retained_terminal(root, execution["predecessor_writer"])
        require(digest(actual) == execution["terminal_proof_sha256"]
            and actual["original_tail_execution_sha256"] == execution["predecessor_execution_sha256"],
            "same_independently_retained_original_terminal")
        return {"retained_incomplete_predecessor": actual, "original_predecessor_complete": False,
            "model_calls": 0, "semantic_acceptance": False, "reader_publication_allowed": False}
    while True:
        guard.checkpoint(root)
        protected = whole.read(target / "guard/guard-progress.json")
        worker = whole.read(root / "wiki/textbook-whole-authoring-jobs" / execution["predecessor_writer"] / "progress.json")
        expected = execution["predecessor_guard"], execution["predecessor_worker"]
        require(all(row.get(k) == v for row, identity in zip([protected, worker], expected, strict=True)
            for k, v in identity.items()), "same_exact_finite_tail_guard_and_actual_worker")
        require(all(row["state"] not in {"stopped", "paused", "pausing"} for row in [protected, worker]),
            "predecessor_failed_or_paused_no_automatic_resume")
        if not any(live(identity) for identity in expected):
            require(protected["state"] == "worker_exited" and protected["returncode"] == 0
                and worker["stage"] == "private_article_assembled", "actual_complete_original_tail_successful_exit")
            actual = predecessor.verify(root, execution["predecessor_writer"])
            require(actual["private"] is not None and actual["summary"]["job"] == execution["predecessor_writer"]
                and actual["summary"]["verified_units"] == actual["summary"]["total_units"] == execution["predecessor_total_units"]
                and actual["proof"]["original_graph_tail_sha256"] == execution["predecessor_execution_sha256"],
                "entire_actual_original_tail_manuscript")
            return {"original_predecessor": actual["proof"], "model_calls": 0,
                "semantic_acceptance": False, "reader_publication_allowed": False}
        sleep(15)


def writer_execution(job, key, number, bound, execution):
    return {**previous.writer_execution(job, key, number, bound, execution), "policy": POLICY}


def writer_bound(root, key, number):
    root, folder, _, _, plan, _, _, _, execution = load(root, key)
    _, _, item, flight, tokenizer, job, contract, extra, bound = original.writer_bound(root, key, number)
    proof = whole.read(job.root / graphs.CONTRACT)
    require(item == plan["items"][number] and proof == writer_execution(job, key, number, bound, execution),
        "same_original_unused_writer_and_finite_tail_release")
    return root, folder, item, flight, tokenizer, job, contract, extra, bound, proof


def writer_worker(root, key, number):
    root, _, item, flight, tokenizer, job, contract, extra, bound, proof = writer_bound(root, key, number)
    guard.checkpoint(root)
    require(not (job.root / "progress.json").exists() and not list((job.root / "attempts").glob("*.json")),
        "first_original_writer_role_only")
    with ready.reads.bounded_ledger_reads(writing), fresh.installed(
            writer_type(contract, extra, key, number, bound, proof, read_only=False)):
        return writing.run(root, item["inventory"], flight["job"], tokenizer)


def verify_writer(root, key, number):
    root, folder, item, flight, tokenizer, _, contract, extra, bound, proof = writer_bound(root, key, number)
    state = whole.read(folder / f"item-{number:02d}-writer-guard/guard-progress.json")
    require(state["state"] == "worker_exited" and state["returncode"] == 0 and not writing.identity_live(state),
        "actual_original_course_writer_guard_success")
    factory = fresh.job_type
    fresh.job_type = lambda value, *, read_only: writer_type(contract, extra, key, number, bound, proof, read_only=read_only)
    try:
        with ready.reads.bounded_ledger_reads(writing):
            result = fresh.reconstruct(root, item["inventory"], flight["job"], tokenizer)
    finally:
        fresh.job_type = factory
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
        require(restored["state"] == "tail_ready_inputs_independently_restored" and restored["batch"] == key
            and restored["original_root_reads"] == restored["model_calls"] == 0, "independently_restored_actual_inputs")
        ready.unused(root, old, flights)
        fresh.retain(folder / "started.json", {"pid": owner.pid, "created": owner.create_time()})
        try:
            progress("waiting_for_exact_complete_original_tail")
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
                    outcome = guard.run(root, target, [sys.executable, "-m", __name__, "--root", str(root),
                        "--batch", key, "--item", str(number), "writer-worker"])
                    require(outcome["state"] == "private_worker_exited", "actual_original_writer_success")
                    actual = verify_writer(root, key, number)
                    proof = folder / f"item-{number:02d}-writer-proof.json"
                    fresh.retain(proof, actual["proof"])
                    completed.append({"item": number, "lesson": item["lesson"], "writer": job.key,
                        "snapshot": str(fresh.snapshot.save(root, actual)), "proof_sha256": digest(actual["proof"])})
                    progress("original_course_private_manuscript_reconstructed")
            result = {"state": "same_original_three_private_manuscripts_reconstructed", "batch": key,
                "completed_courses": len(completed), "total_courses": len(flights), "items": completed,
                "semantic_acceptance": False, "reader_publication_allowed": False, "source_deletion_allowed": False}
            fresh.retain(folder / "result.json", result)
            progress(result["state"])
            return {k: v for k, v in result.items() if k != "items"}
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
    parser.add_argument("--retain-incomplete-predecessor", action="store_true")
    parser.add_argument("command", choices=["prepare", "load", "run", "writer-worker", "verify-writer"])
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = prepare(args.root, args.batch, args.probe, retain_incomplete=args.retain_incomplete_predecessor)
        elif args.command == "load":
            _, _, _, _, plan, _, _, _, _ = load(args.root, args.batch)
            result = {"state": "original_tail_ready_inputs_authenticated", "batch": args.batch,
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
