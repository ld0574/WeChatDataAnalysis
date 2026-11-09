"""Continue untouched ready courses after a separately authenticated writer.

The stopped ready controller is never restarted or relabelled. Its original
reserved courses, ledgers and role budgets continue through a new execution.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import time

import psutil

from . import course_textbook_ready_batch as ready
from . import course_textbook_one_based_writer as predecessor
from . import course_textbook_first_plan_format as planning
from .course_knowledge import atomic_json, schema_lease

whole, fresh, writing, guard, digest = (
    ready.whole,
    ready.fresh,
    ready.writing,
    ready.guard,
    ready.digest,
)
POLICY = "untouched-original-ready-courses-after-explicit-one-based-writer-v1"
DIRECTORY = "ready-course-continuations"
WRITER = "original-ready-first-planning-format.json"


def require(ok, code):
    if not ok:
        raise ValueError("requires_ready_resume_" + code)


def bindings():
    return {
        "module_sha256": whole.sha(Path(__file__).read_bytes()),
        "original": ready.bindings(),
        "predecessor": predecessor.bindings(),
        "planning": planning.binding(),
    }


def folder_for(root, key):
    require(
        isinstance(key, str) and re.fullmatch(r"[a-f0-9]{32}", key), "original_batch_id"
    )
    return Path(root).resolve() / "wiki" / DIRECTORY / key


def prepare(root, key):
    root, old, plan, _, flights = ready.load(root, key)
    guard.checkpoint(root)
    with schema_lease(root / "wiki/full-course-batch-prepare.lock", timeout=0):
        status = whole.read(old / "progress.json")
        require(
            status["state"] == "stopped"
            and not writing.identity_live(status)
            and status["completed_courses"] == 0
            and status["total_courses"] == len(flights)
            and status["reason_code"]
            == "requires_ready_course_batch_recovered_original_writer_successful_exit",
            "actual_stopped_controller_before_any_course_role",
        )
        ready.unused(root, old, flights)
        writer = plan["predecessor_writer"]
        b = predecessor.load(root, writer)
        require(
            plan["predecessor_kind"] == "unspent-writer-dispatch"
            and plan["predecessor_batch"] == b.execution["batch"],
            "same_original_predecessor_and_reserved_courses",
        )
        protected = whole.read(b.folder / "guard/guard-progress.json")
        worker = whole.read(b.job.root / "progress.json")
        require(
            protected["state"] == "running"
            and worker["state"] not in {"stopped", "paused", "pausing"}
            and writing.identity_live(protected)
            and writing.identity_live(worker),
            "exact_live_explicit_writer_continuation",
        )
        files = [
            old / "plan.json",
            old / "preflights.json",
            old / "progress.json",
            old / "started.json",
            old / "independent-input-restore.json",
            *old.glob("failure-*.json"),
            b.folder / "execution.json",
            b.folder / "recovery.json",
            b.folder / "worker-started.json",
            b.folder / "guard/guard-started.json",
        ]
        execution = {
            "policy": POLICY,
            "batch": key,
            "code": bindings(),
            "original_plan_sha256": digest(plan),
            "original_preflights_sha256": digest(flights),
            "original_controller_failure": status,
            "predecessor_writer": writer,
            "predecessor_directory": b.folder.relative_to(root).as_posix(),
            "predecessor_execution_sha256": digest(b.execution),
            "predecessor_guard": {k: protected[k] for k in ["pid", "created"]},
            "predecessor_worker": {k: worker[k] for k in ["pid", "created"]},
            "original_files": {
                p.relative_to(root).as_posix(): whole.sha(p.read_bytes()) for p in files
            },
            "courses": len(flights),
            "original_ledger_and_writer_roles_unused": True,
            "original_stage_attempt_budget": 2,
            "semantic_repairs_per_unit": 1,
            "original_budgets_reset": False,
            "first_planning_format": planning.binding(),
            "automatic_retry_allowed": False,
            "automatic_thermal_resume_allowed": False,
            "reader_publication_allowed": False,
            "source_deletion_allowed": False,
        }
        folder = folder_for(root, key)
        require(not folder.exists(), "one_explicit_ready_continuation")
        fresh.retain(folder / "execution.json", execution)
        return {
            "state": "original_unused_ready_courses_continuation_prepared",
            "batch": key,
            "courses": len(flights),
            "records": sum(i["records"] for i in plan["items"]),
            "characters": sum(i["characters"] for i in plan["items"]),
            "model_calls": 0,
            "reader_publication_allowed": False,
        }


def load(root, key):
    root, old, plan, tokenizer, flights = ready.load(root, key)
    folder = folder_for(root, key)
    execution = whole.read(folder / "execution.json")
    require(
        execution["policy"] == POLICY
        and execution["batch"] == key
        and execution["code"] == bindings()
        and execution["original_plan_sha256"] == digest(plan)
        and execution["original_preflights_sha256"] == digest(flights),
        "same_original_courses_and_frozen_continuation",
    )
    for name, expected in execution["original_files"].items():
        path = ready.source_path(root, name)
        require(
            whole.sha(path.read_bytes()) == expected,
            "old_failure_and_explicit_predecessor_records_preserved",
        )
    return root, folder, old, plan, tokenizer, flights, execution


def wait(root, execution, *, live=writing.identity_live, sleep=time.sleep):
    folder = root / execution["predecessor_directory"]
    expected_guard, expected_worker = (
        execution["predecessor_guard"],
        execution["predecessor_worker"],
    )
    while True:
        guard.checkpoint(root)
        state = whole.read(folder / "guard/guard-progress.json")
        worker = whole.read(
            root
            / "wiki/textbook-whole-authoring-jobs"
            / execution["predecessor_writer"]
            / "progress.json"
        )
        require(
            all(state.get(k) == v for k, v in expected_guard.items())
            and all(worker.get(k) == v for k, v in expected_worker.items()),
            "unchanged_exact_predecessor_process_identities",
        )
        require(
            state["state"] not in {"paused", "pausing", "stopped"}
            and worker["state"] not in {"paused", "pausing", "stopped"},
            "predecessor_failed_or_paused_no_automatic_resume",
        )
        if not any(live(v) for v in [expected_guard, expected_worker]):
            require(
                state["state"] == "worker_exited"
                and state["returncode"] == 0
                and worker["stage"] == "private_article_assembled",
                "actual_successful_original_writer_exit",
            )
            result = predecessor.verify(root, execution["predecessor_writer"])
            require(
                result["private"] is not None
                and result["summary"]["job"] == execution["predecessor_writer"]
                and result["summary"]["verified_units"]
                == result["summary"]["total_units"],
                "entire_actual_predecessor_manuscript",
            )
            return {
                "original_predecessor": result["proof"],
                "model_calls": 0,
                "semantic_acceptance": False,
                "reader_publication_allowed": False,
            }
        sleep(15)


def writer_execution(job, key, number, contract, extra, execution):
    return {
        "policy": POLICY,
        "batch": key,
        "item": number,
        "writer": job.key,
        "code": bindings(),
        "fresh_contract_sha256": digest(contract),
        "original_count_contract_sha256": digest(extra),
        "continuation_execution_sha256": digest(execution),
        "original_roles_unused_at_registration": True,
        "original_budgets_reset": False,
        "reader_publication_allowed": False,
    }


def writer_bound(root, key, number):
    root, folder, _, plan, _, _, execution = load(root, key)
    _, _, item, flight, tokenizer, job, contract, extra = ready.writer_bound(
        root, key, number
    )
    bound = whole.read(job.root / WRITER)
    require(
        bound == writer_execution(job, key, number, contract, extra, execution),
        "first_original_writer_planning_registration",
    )
    require(item == plan["items"][number], "same_original_catalog_member")
    return root, folder, item, flight, tokenizer, job, contract, extra, bound


def writer_type(contract, extra, key, number, bound, *, read_only):
    base = ready.writer_type(contract, extra, key, number, read_only=read_only)
    counted = planning.job_type(base, bound, read_only=read_only)

    class FirstPlannedWriter(counted):
        def __init__(self, *args):
            super().__init__(*args)
            require(
                whole.read(self.root / WRITER) == bound,
                "same_first_writer_format_contract",
            )
            self.reader.paths.add(self.root / WRITER)

    return FirstPlannedWriter


def writer_worker(root, key, number):
    root, _, item, flight, tokenizer, job, contract, extra, bound = writer_bound(
        root, key, number
    )
    guard.checkpoint(root)
    require(
        not (job.root / "progress.json").exists()
        and not list((job.root / "attempts").glob("*.json")),
        "original_writer_first_role_only",
    )
    with (
        ready.reads.bounded_ledger_reads(writing),
        fresh.installed(
            writer_type(contract, extra, key, number, bound, read_only=False)
        ),
    ):
        return writing.run(root, item["inventory"], flight["job"], tokenizer)


def verify_writer(root, key, number):
    root, _, item, flight, tokenizer, _, contract, extra, bound = writer_bound(
        root, key, number
    )
    factory = fresh.job_type
    fresh.job_type = lambda value, *, read_only: writer_type(
        value, extra, key, number, bound, read_only=read_only
    )
    try:
        with ready.reads.bounded_ledger_reads(writing):
            result = fresh.reconstruct(
                root, item["inventory"], flight["job"], tokenizer
            )
    finally:
        fresh.job_type = factory
    result["proof"].update(ready_continuation_first_plan_sha256=digest(bound))
    return result


def run(root, key):
    root, folder, old, plan, tokenizer, flights, execution = load(root, key)
    owner, completed = psutil.Process(), []

    def progress(state, **extra):
        atomic_json(
            folder / "progress.json",
            {
                "pid": owner.pid,
                "created": owner.create_time(),
                "updated": time.time(),
                "state": state,
                "batch": key,
                "completed_courses": len(completed),
                "total_courses": len(flights),
                "reader_publication_allowed": False,
                "source_deletion_allowed": False,
                **extra,
            },
        )

    with schema_lease(folder / "controller.lock", timeout=0):
        require(
            not (folder / "started.json").exists(), "controller_once_no_automatic_retry"
        )
        restored = whole.read(folder / "independent-input-restore.json")
        require(
            restored["state"] == "ready_continuation_inputs_independently_restored"
            and restored["batch"] == key
            and restored["original_root_reads"] == restored["model_calls"] == 0,
            "independent_actual_inputs_before_dispatch",
        )
        ready.unused(root, old, flights)
        fresh.retain(
            folder / "started.json", {"pid": owner.pid, "created": owner.create_time()}
        )
        try:
            progress("waiting_for_exact_explicit_writer_continuation")
            fresh.retain(folder / "predecessor-proof.json", wait(root, execution))
            with schema_lease(root / "wiki/full-course-batch-worker.lock", timeout=0):
                ready.unused(root, old, flights)
                for number, (item, flight) in enumerate(
                    zip(plan["items"], flights, strict=True)
                ):
                    guard.checkpoint(root)
                    ready.original.check_item(root, item)
                    progress(
                        "building_original_unused_ledger",
                        item=number,
                        lesson=item["lesson"],
                        ledger=flight["job"],
                    )
                    ledger_guard = folder / f"item-{number:02d}-ledger-guard"
                    ledger_guard.mkdir()
                    outcome = guard.run(
                        root,
                        ledger_guard,
                        [
                            sys.executable,
                            "-m",
                            ready.__name__,
                            "--root",
                            str(root),
                            "--batch",
                            key,
                            "--item",
                            str(number),
                            "ledger-worker",
                        ],
                    )
                    require(
                        outcome["state"] == "private_worker_exited",
                        "original_ledger_successful_exit",
                    )
                    with ready.reads.bounded_ledger_reads(writing):
                        writing.load_inputs(
                            root, item["inventory"], flight["job"], tokenizer
                        )
                        fresh.prepare(root, item["inventory"], flight["job"], tokenizer)
                        _, _, _, job, contract = fresh.bound(
                            root, item["inventory"], flight["job"], tokenizer
                        )
                    require(
                        not (job.root / "progress.json").exists()
                        and not list((job.root / "attempts").glob("*.json")),
                        "all_original_writer_roles_unused_before_format_binding",
                    )
                    extra = ready.writer_execution(job, contract, key, number)
                    fresh.retain(job.root / ready.COUNT_CONTRACT, extra)
                    fresh.retain(
                        job.root / WRITER,
                        writer_execution(job, key, number, contract, extra, execution),
                    )
                    progress(
                        "writing_original_unused_course",
                        item=number,
                        lesson=item["lesson"],
                        writer=job.key,
                    )
                    writer_guard = folder / f"item-{number:02d}-writer-guard"
                    writer_guard.mkdir()
                    outcome = guard.run(
                        root,
                        writer_guard,
                        [
                            sys.executable,
                            "-m",
                            __spec__.name,
                            "--root",
                            str(root),
                            "--batch",
                            key,
                            "--item",
                            str(number),
                            "writer-worker",
                        ],
                    )
                    require(
                        outcome["state"] == "private_worker_exited",
                        "original_writer_successful_exit",
                    )
                    actual = verify_writer(root, key, number)
                    snapshot = ready.original.mapped.snapshot.save(root, actual)
                    row = {
                        "lesson": item["lesson"],
                        "inventory": item["inventory"],
                        "ledger": flight["job"],
                        "writer": job.key,
                        "snapshot": snapshot.relative_to(root).as_posix(),
                        "unresolved_issues": len(actual["issues"]),
                        "reader_publication_allowed": False,
                    }
                    fresh.retain(folder / f"item-{number:02d}-result.json", row)
                    completed.append(row)
            result = {
                "state": ready.SUCCESS,
                "batch": key,
                "completed_courses": len(completed),
                "total_courses": len(flights),
                "items": completed,
                "reader_publication_allowed": False,
                "source_deletion_allowed": False,
            }
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
    parser.add_argument("--item", type=int)
    parser.add_argument(
        "command", choices=["prepare", "load", "run", "writer-worker", "verify-writer"]
    )
    args = parser.parse_args()
    try:
        if args.command == "load":
            _, _, _, plan, _, _, _ = load(args.root, args.batch)
            result = {
                "state": "original_ready_continuation_authenticated",
                "batch": args.batch,
                "courses": len(plan["items"]),
                "model_calls": 0,
            }
        elif args.command == "writer-worker":
            result = writer_worker(args.root, args.batch, args.item)
        elif args.command == "verify-writer":
            actual = verify_writer(args.root, args.batch, args.item)
            result = {
                **actual["summary"],
                "snapshot": str(fresh.snapshot.save(args.root, actual)),
            }
        else:
            result = globals()[args.command](args.root, args.batch)
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
