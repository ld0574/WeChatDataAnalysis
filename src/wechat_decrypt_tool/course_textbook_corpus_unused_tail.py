"""Continue the original unattempted second course after the recovered first.

The active predecessor is never restarted. Its exact guard and worker must exit
successfully and its complete real manuscript must reconstruct before the
original unused ledger and writer roles run. No accepted batch is rerun.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import time

import psutil

from . import course_textbook_corpus_index_review as reviewed
from .course_knowledge import atomic_json, schema_lease

prior, fresh, writing, whole = reviewed.prior.original, reviewed.fresh, reviewed.writing, reviewed.whole
original, guard, digest = prior.original, reviewed.guard, reviewed.digest
POLICY = "original-unattempted-second-corpus-course-after-recovered-first-v1"
DIRECTORY = "original-unused-second-course"
WRITER_CONTRACT = "count-bound-first-execution.json"


def require(ok, code):
    if not ok:
        raise ValueError("requires_corpus_unused_tail_" + code)


def bindings():
    return {Path(module.__file__).stem: whole.sha(Path(module.__file__).read_bytes())
        for module in (sys.modules[__name__], reviewed, prior, original)}


def inputs(root, key):
    root, folder, target, plan, tokenizer, flights, parent = prior.load(root, key)
    require(len(plan["items"]) == 2 and len(flights) == 2, "original_two_course_batch")
    ledger = root / "wiki/textbook-keyed-jobs" / flights[0]["job"] / "whole-source-authoring-execution.json"
    first = whole.read(ledger)["job"]
    bundle = reviewed.load(root, first)
    require(bundle.batch == key and digest(bundle.inventory) == plan["items"][0]["inventory"]
        and bundle.working["job"] == flights[0]["job"], "actual_first_course_in_original_plan")
    final = reviewed.final_review(bundle, read_only=True)
    require(whole.read(bundle.folder / "execution.json") == reviewed.execution(bundle), "actual_bound_recovered_first")
    return root, folder, target, plan, tokenizer, flights, parent, bundle, final


def unused(root, folder, plan, flights):
    job = root / "wiki/textbook-keyed-jobs" / flights[1]["job"]
    require(not list(folder.glob("item-01-*.json"))
        and not (job / "progress.json").exists() and not (job / "ledger.json").exists()
        and not list(job.glob("group-*.json")) and not list((job / "attempts").glob("*.json"))
        and not (job / "whole-source-authoring-execution.json").exists(), "original_second_course_all_roles_unused")
    original.check_item(root, plan["items"][1])


def execution(root, key, folder, target, plan, flights, parent, bundle, final, previous, live_guard, worker):
    files = {folder / "plan.json", folder / "preflights.json", target / "execution.json",
        target / "original-status.json", target / "started.json", target / "predecessor-proof.json",
        bundle.folder / "execution.json", bundle.folder / "final-review-result.json"}
    files.update(target.glob("failure-*.json"))
    return {"policy": POLICY, "batch": key, "code": bindings(), "parent_execution_sha256": digest(parent),
        "original_plan_sha256": digest(plan), "first_writer": bundle.job.key,
        "first_final_review_sha256": digest(final), "first_execution_sha256": digest(reviewed.execution(bundle)),
        "first_guard_path": (bundle.folder / "guard-progress.json").relative_to(root).as_posix(),
        "first_guard": {k: live_guard[k] for k in ("pid", "created")},
        "first_worker": {k: worker[k] for k in ("pid", "created")},
        "second_inventory": plan["items"][1]["inventory"], "second_ledger": flights[1]["job"],
        "original_second_item": 1, "previous_status_sha256": digest(previous),
        "files": {p.relative_to(root).as_posix(): whole.sha(p.read_bytes()) for p in sorted(files)},
        "original_budgets_reset": False, "new_model_roles": 0, "original_second_writer_format": "tagged_count_2_to_12_bound_indices",
        "automatic_retry_allowed": False, "automatic_thermal_resume_allowed": False,
        "reader_publication_allowed": False, "source_deletion_allowed": False}


def prepare(root, key):
    root, folder, target, plan, tokenizer, flights, parent, bundle, final = inputs(root, key)
    guard.checkpoint(root)
    with schema_lease(root / "wiki/full-course-batch-prepare.lock", timeout=0):
        previous = whole.read(target / "progress.json")
        require(previous["state"] == "stopped" and previous["completed_courses"] == 0
            and not writing.identity_live(previous), "original_failed_controller_exited")
        require(not (target / "item-00-result.json").exists() and not (target / "item-01-result.json").exists(),
            "no_prior_completed_course_result_reexecution")
        live_guard = whole.read(bundle.folder / "guard-progress.json")
        worker = whole.read(bundle.job.root / "progress.json")
        require(live_guard["state"] == "running" and writing.identity_live(live_guard)
            and writing.identity_live(worker) and worker["job"] == bundle.job.key
            and worker["state"] not in {"stopped", "paused"}, "exact_healthy_recovered_first")
        unused(root, folder, plan, flights)
        destination = target / DIRECTORY
        require(not destination.exists(), "prepare_once_no_original_budget_refresh")
        contract = execution(root, key, folder, target, plan, flights, parent, bundle, final, previous, live_guard, worker)
        fresh.retain(destination / "execution.json", contract)
        fresh.retain(destination / "prior-controller-status.json", previous)
        return {"state": "original_unused_second_course_prepared_after_live_first", "batch": key,
            "remaining_courses": 1, "model_calls": 0, "reader_publication_allowed": False}


def load(root, key):
    root, folder, target, plan, tokenizer, flights, parent, bundle, final = inputs(root, key)
    destination = target / DIRECTORY
    contract = whole.read(destination / "execution.json")
    previous = whole.read(destination / "prior-controller-status.json")
    require(previous == whole.read(target / "progress.json") and contract == execution(root, key, folder, target,
        plan, flights, parent, bundle, final, previous, whole.read(bundle.folder / "guard-progress.json"),
        whole.read(bundle.job.root / "progress.json")),
        "frozen_same_original_two_course_execution")
    require(all(whole.sha((root / name).read_bytes()) == checksum for name, checksum in contract["files"].items()),
        "old_failures_and_actual_first_review_unchanged")
    return root, folder, destination, plan, tokenizer, flights, bundle, contract


def wait(root, contract, *, live=writing.identity_live, sleep=time.sleep):
    while True:
        guard.checkpoint(root)
        status = whole.read(root / contract["first_guard_path"])
        require(all(status[k] == v for k, v in contract["first_guard"].items()), "same_exact_predecessor_guard")
        require(status["state"] not in {"paused", "pausing"}, "predecessor_paused_without_automatic_resume")
        if not any(live(contract[name]) for name in ("first_guard", "first_worker")):
            require(status["state"] == "worker_exited" and status.get("returncode") == 0,
                "actual_successful_predecessor_guard_and_worker_exit")
            return
        sleep(15)


def writer_execution(job, fresh_contract, contract):
    return {"policy": POLICY, "writer": job.key, "fresh_contract_sha256": digest(fresh_contract),
        "tail_execution_sha256": digest(contract), "code": bindings(), "format": "tagged_actual_count_and_bounded_indices",
        "original_budgets_reset": False, "reader_publication_allowed": False}


def writer_type(fresh_contract, extra, contract, *, read_only):
    base = original.mapped.ORIGINAL_FACTORY(fresh_contract, read_only=read_only)

    class FirstCountBoundJob(base):
        def __init__(self, *args):
            super().__init__(*args)
            require(extra == writer_execution(self, fresh_contract, contract), "same_first_writer_format_and_budget")
            self.reader.paths.add(self.root / WRITER_CONTRACT)

        def stage(self, name, prompt, schema, context, validate, **kwargs):
            match = re.fullmatch(fresh.PATTERN, name)
            if match and match[2] in {"write", "repair"}:
                with reviewed.future_format():
                    return super().stage(name, prompt, schema, context, validate, **kwargs)
            return super().stage(name, prompt, schema, context, validate, **kwargs)

    return FirstCountBoundJob


def writer_bound(root, key):
    root, _, target, plan, tokenizer, flights, _, contract = load(root, key)
    inventory, ledger = plan["items"][1]["inventory"], flights[1]["job"]
    root, _, _, job, fresh_contract = fresh.bound(root, inventory, ledger, tokenizer)
    extra = whole.read(job.root / WRITER_CONTRACT)
    require(extra == writer_execution(job, fresh_contract, contract), "same_original_second_writer")
    return root, target, inventory, ledger, tokenizer, job, fresh_contract, extra, contract


def writer_worker(root, key):
    root, _, inventory, ledger, tokenizer, job, fresh_contract, extra, contract = writer_bound(root, key)
    guard.checkpoint(root)
    require(not (job.root / "progress.json").exists() and not list((job.root / "attempts").glob("*.json")),
        "first_second_writer_worker_only")
    with fresh.installed(writer_type(fresh_contract, extra, contract, read_only=False)):
        return writing.run(root, inventory, ledger, tokenizer)


def verify_writer(root, key):
    root, _, inventory, ledger, tokenizer, _, fresh_contract, extra, contract = writer_bound(root, key)
    previous = fresh.job_type
    fresh.job_type = lambda value, *, read_only: writer_type(value, extra, contract, read_only=read_only)
    try:
        result = fresh.reconstruct(root, inventory, ledger, tokenizer)
    finally:
        fresh.job_type = previous
    result["proof"].update(policy=POLICY, unused_tail_execution_sha256=digest(contract),
        first_count_bound_writer_sha256=digest(extra), actual_fresh_contract_sha256=digest(fresh_contract))
    return result


def run(root, key):
    root, folder, target, plan, tokenizer, flights, bundle, contract = load(root, key)
    owner = psutil.Process()

    def progress(state, **extra):
        atomic_json(target / "progress.json", {"pid": owner.pid, "created": owner.create_time(), "updated": time.time(),
            "state": state, "batch": key, "original_total_courses": 2, "remaining_courses": 1,
            "reader_publication_allowed": False, "source_deletion_allowed": False, **extra})

    with schema_lease(root / "wiki/full-course-batch-worker.lock", timeout=0):
        require(not (target / "started.json").exists(), "controller_once_no_automatic_retry")
        unused(root, folder, plan, flights)
        fresh.retain(target / "started.json", {"pid": owner.pid, "created": owner.create_time()})
        try:
            progress("waiting_for_exact_recovered_first_writer")
            wait(root, contract)
            first = reviewed.reconstruct(root, bundle.job.key)
            require(first["summary"]["verified_units"] == first["summary"]["total_units"] and first["private"] is not None,
                "actual_complete_first_course_before_successor")
            snapshot = original.mapped.snapshot.save(root, first)
            fresh.retain(target / "first-course-proof.json", first["proof"])
            fresh.retain(target / "first-course-result.json", {"writer": bundle.job.key,
                "snapshot": snapshot.relative_to(root).as_posix(), "unresolved_issues": len(first["issues"]),
                "reader_publication_allowed": False})
            unused(root, folder, plan, flights)
            progress("building_original_unused_second_ledger", ledger=flights[1]["job"])
            ledger_guard = target / "item-01-ledger-guard"
            ledger_guard.mkdir()
            outcome = guard.run(root, ledger_guard, [sys.executable, "-m", original.__name__, "--root", str(root),
                "--batch", key, "--item", "1", "ledger-worker"])
            require(outcome["state"] == "private_worker_exited", "original_second_ledger_successful_exit")
            writing.load_inputs(root, plan["items"][1]["inventory"], flights[1]["job"], tokenizer)
            fresh.prepare(root, plan["items"][1]["inventory"], flights[1]["job"], tokenizer)
            _, _, _, job, fresh_contract = fresh.bound(root, plan["items"][1]["inventory"], flights[1]["job"], tokenizer)
            require(not (job.root / "progress.json").exists() and not list((job.root / "attempts").glob("*.json")),
                "all_original_second_writer_roles_unused")
            fresh.retain(job.root / WRITER_CONTRACT, writer_execution(job, fresh_contract, contract))
            backup = Path.home() / "Documents/CourseKnowledgeBackups/count-bound-first-writers" / job.key
            for path in (job.root / WRITER_CONTRACT, Path(__file__), Path(reviewed.__file__)):
                whole.backup(path, backup / path.name)
            progress("writing_original_unused_second_article", writer=job.key)
            writer_guard = target / "item-01-authoring-guard"
            writer_guard.mkdir()
            outcome = guard.run(root, writer_guard, [sys.executable, "-m", __spec__.name,
                "--root", str(root), "--batch", key, "writer-worker"])
            require(outcome["state"] == "private_worker_exited", "original_second_writer_successful_exit")
            second = verify_writer(root, key)
            snapshot = original.mapped.snapshot.save(root, second)
            result = {"state": "original_two_course_private_manuscripts_require_coverage_and_editorial_review", "batch": key,
                "original_courses_completed": 2, "original_unused_second_completed": 1,
                "second_writer": job.key, "second_snapshot": snapshot.relative_to(root).as_posix(),
                "unresolved_issues": len(second["issues"]), "reader_publication_allowed": False,
                "source_deletion_allowed": False}
            fresh.retain(target / "result.json", result)
            progress(result["state"], original_courses_completed=2)
            return {k: v for k, v in result.items() if k != "second_snapshot"}
        except BaseException as exc:
            failure = whole.failure_status(exc)
            atomic_json(target / ("failure-" + str(time.time_ns()) + ".json"), failure)
            progress(failure["state"], reason_code=failure["reason_code"])
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--batch", required=True)
    parser.add_argument("command", choices=["prepare", "load", "run", "writer-worker", "verify-writer"])
    args = parser.parse_args()
    try:
        if args.command == "load":
            _, _, _, _, _, _, _, contract = load(args.root, args.batch)
            result = {"state": "original_unused_second_course_execution_authenticated", "batch": args.batch,
                "second_ledger": contract["second_ledger"], "remaining_courses": 1, "model_calls": 0}
        elif args.command == "writer-worker":
            result = writer_worker(args.root, args.batch)
        elif args.command == "verify-writer":
            value = verify_writer(args.root, args.batch)
            result = {**value["summary"], "snapshot": str(original.mapped.snapshot.save(args.root, value))}
        else:
            result = globals()[args.command](args.root, args.batch)
        print(json.dumps(result))
    except BaseException as exc:
        print(json.dumps(whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
