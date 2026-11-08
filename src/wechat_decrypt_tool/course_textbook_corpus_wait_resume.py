"""Continue an unstarted two-course batch after its GPU-wait predecessor failed.

The old controller, start record and failure remain. A supplementary execution
binds the replacement coverage identity, then reconstructs its actual complete
result before using the original, still-unused ledger and writing role budgets.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import time

import psutil

from . import course_textbook_bounded_paragraph_resume as bounded
from . import course_textbook_corpus_batch as original
from .course_knowledge import atomic_json, schema_lease

whole, fresh, writing = original.whole, original.fresh, original.writing
digest = original.digest
DIRECTORY = "wait-deadline-continuation"
FIRST = "bounded-first-execution.json"
POLICY = "preserved-unstarted-corpus-batch-after-explicit-coverage-wait-resume-v1"


def require(ok, code):
    if not ok:
        raise ValueError("requires_corpus_wait_resume_" + code)


def bindings():
    return {Path(m.__file__).stem: whole.sha(Path(m.__file__).read_bytes()) for m in (sys.modules[__name__], bounded, original)}


def unstarted(root, folder, preflights):
    require(not list(folder.glob("item-*-started.json")) and not list(folder.glob("item-*-result.json"))
        and not (folder / "predecessor-proof.json").exists(), "no_original_item_started")
    for row in preflights:
        job = root / "wiki/textbook-keyed-jobs" / row["job"]
        require(not (job / "progress.json").exists() and not (job / "ledger.json").exists()
            and not list((job / "attempts").glob("*.json")), "original_ledger_roles_still_unused")


def prepare(root, key):
    root, folder, plan, _, preflights = original.load(root, key)
    previous = whole.read(folder / "progress.json")
    require(plan["predecessor_kind"] == "coverage" and previous["state"] == "stopped"
        and previous["reason_code"] == "requires_corpus_batch_predecessor_successful_exit"
        and previous["completed_courses"] == 0 and not writing.identity_live(previous), "actual_predecessor_failure_before_work")
    unstarted(root, folder, preflights)
    resume = root / "wiki/coverage-deadline-resume-20261108"
    proof, request = whole.read(resume / "preflight.json"), whole.read(resume / "launch-request.json")
    require(proof["job"] == plan["predecessor_job"] and proof["parent_writer"] == plan["predecessor_parent"]
        and proof["model_calls"] == 0 and request["preflight_sha256"] == whole.sha((resume / "preflight.json").read_bytes()),
        "exact_authenticated_deadline_resume")
    guard = whole.read(root / plan["predecessor_guard_path"])
    worker = whole.read(root / "wiki/whole-manuscript-coverage-jobs" / plan["predecessor_job"] / "progress.json")
    require(guard["pid"] != proof["original_guard"]["pid"]
        and ((guard["state"] == "running" and writing.identity_live(guard))
            or (guard["state"] == "worker_exited" and guard.get("returncode") == 0))
        and worker["job"] == plan["predecessor_job"] and worker["pid"] != proof["original_status"]["pid"]
        and worker["state"] not in {"stopped", "paused"}, "actual_replacement_coverage_execution")
    target = folder / DIRECTORY
    require(not (target / "execution.json").exists(), "prepare_once")
    old_files = {p.relative_to(root).as_posix(): whole.sha(p.read_bytes()) for p in [
        folder / "plan.json", folder / "started.json", *folder.glob("failure-*.json"),
        *folder.glob("guard-layout-failure-*.json"), resume / "preflight.json", resume / "launch-request.json"]}
    execution = {"policy": POLICY, "batch": key, "original_plan_sha256": digest(plan), "code": bindings(),
        "old_files": old_files, "replacement_guard": {k: guard[k] for k in ("pid", "created")},
        "replacement_worker": {k: worker[k] for k in ("pid", "created")},
        "old_status_sha256": digest(previous), "original_model_budgets_reset": False,
        "new_model_roles": 0, "guard_layout": "outside_immutable_authoring_payload",
        "future_writer_format": "exact_paragraph_count_and_bounded_coverage_indices",
        "reader_publication_allowed": False, "source_deletion_allowed": False}
    fresh.retain(target / "execution.json", execution)
    fresh.retain(target / "original-status.json", previous)
    return {"state": "unstarted_original_corpus_batch_prepared_after_wait_failure", "batch": key,
        "courses": len(plan["items"]), "model_calls": 0}


def load(root, key):
    root, folder, plan, tokenizer, preflights = original.load(root, key)
    target = folder / DIRECTORY
    execution = whole.read(target / "execution.json")
    require(execution["policy"] == POLICY and execution["batch"] == key and execution["code"] == bindings()
        and execution["original_plan_sha256"] == digest(plan)
        and digest(whole.read(target / "original-status.json")) == execution["old_status_sha256"], "frozen_original_batch_and_continuation")
    require(all(whole.sha((root / name).read_bytes()) == value for name, value in execution["old_files"].items()),
        "old_failures_start_and_resume_proofs_preserved")
    return root, folder, target, plan, tokenizer, preflights, execution


def wait(root, plan, execution, *, live=writing.identity_live, sleep=time.sleep):
    while True:
        original.private_guard.checkpoint(root)
        guard = whole.read(root / plan["predecessor_guard_path"])
        require(all(guard[k] == v for k, v in execution["replacement_guard"].items()), "exact_replacement_guard_identity")
        require(guard["state"] not in {"paused", "pausing"}, "replacement_paused_no_automatic_resume")
        if not any(live(execution[name]) for name in ("replacement_guard", "replacement_worker")):
            require(guard["state"] == "worker_exited" and guard.get("returncode") == 0, "replacement_successful_exit")
            return
        sleep(10)


def first_execution(job, contract, execution):
    return {"policy": POLICY, "writer": job.key, "fresh_contract_sha256": digest(contract),
        "corpus_continuation_sha256": digest(execution), "code": bindings(),
        "format": "exact_paragraph_count_and_bounded_coverage_indices", "original_model_budgets_reset": False,
        "reader_publication_allowed": False}


def first_type(contract, extra, execution, *, read_only):
    base = original.mapped.ORIGINAL_FACTORY(contract, read_only=read_only)

    class BoundedFirst(base):
        def __init__(self, *args):
            super().__init__(*args)
            require(extra == first_execution(self, contract, execution), "same_fresh_bounded_writer")
            self.reader.paths.add(self.root / FIRST)

        def stage(self, name, prompt, schema, context, validate, **kwargs):
            match = re.fullmatch(fresh.PATTERN, name)
            if match and match[2] in {"write", "repair"}:
                with bounded.bounded_format():
                    return super().stage(name, prompt, schema, context, validate, **kwargs)
            return super().stage(name, prompt, schema, context, validate, **kwargs)

    return BoundedFirst


def first_bound(root, key, inventory, ledger):
    root, _, _, plan, tokenizer, preflights, execution = load(root, key)
    require(any(row["inventory"] == inventory and preflight["job"] == ledger
        for row, preflight in zip(plan["items"], preflights, strict=True)), "selected_original_inventory_and_ledger_only")
    _, _, _, job, contract = fresh.bound(root, inventory, ledger, tokenizer)
    extra = whole.read(job.root / FIRST)
    require(extra == first_execution(job, contract, execution), "frozen_first_writer_format")
    return root, tokenizer, job, contract, extra, execution


def first_worker(root, key, inventory, ledger):
    root, tokenizer, job, contract, extra, execution = first_bound(root, key, inventory, ledger)
    original.private_guard.checkpoint(root)
    require(not (job.root / "progress.json").exists(), "first_worker_once")
    with fresh.installed(first_type(contract, extra, execution, read_only=False)):
        return writing.run(root, inventory, ledger, tokenizer)


def first_verify(root, key, inventory, ledger):
    root, tokenizer, _, contract, extra, execution = first_bound(root, key, inventory, ledger)
    old = fresh.job_type
    fresh.job_type = lambda value, *, read_only: first_type(value, extra, execution, read_only=read_only)
    try:
        rebuilt = fresh.reconstruct(root, inventory, ledger, tokenizer)
    finally:
        fresh.job_type = old
    rebuilt["proof"].update(policy=POLICY, corpus_wait_resume_execution_sha256=digest(execution),
        bounded_first_execution_sha256=digest(extra))
    return rebuilt


def run(root, key):
    root, folder, target, plan, tokenizer, preflights, execution = load(root, key)
    owner, completed = psutil.Process(), []

    def progress(state, **extra):
        atomic_json(target / "progress.json", {"pid": owner.pid, "created": owner.create_time(), "state": state,
            "updated": time.time(), "batch": key, "completed_courses": len(completed), "total_courses": len(plan["items"]),
            "reader_publication_allowed": False, "source_deletion_allowed": False, **extra})

    with schema_lease(root / "wiki/full-course-batch-worker.lock", timeout=0):
        require(not (target / "started.json").exists(), "controller_once_no_automatic_retry")
        unstarted(root, folder, preflights)
        fresh.retain(target / "started.json", {"pid": owner.pid, "created": owner.create_time()})
        try:
            progress("waiting_for_exact_replacement_coverage")
            wait(root, plan, execution)
            rebuilt = original.coverage_assignments.reconstruct(root, plan["predecessor_parent"], tokenizer, "section-0007")
            fresh.retain(target / "predecessor-proof.json", rebuilt["proof"])
            for number, (item, preflight) in enumerate(zip(plan["items"], preflights, strict=True)):
                original.private_guard.checkpoint(root)
                original.check_item(root, item)
                progress("building_original_first_ledger", item=number, ledger=preflight["job"])
                guard = target / f"item-{number:02d}-ledger-guard"
                guard.mkdir(exist_ok=True)
                result = original.private_guard.run(root, guard, [sys.executable, "-m", original.__name__,
                    "--root", str(root), "--batch", key, "--item", str(number), "ledger-worker"])
                require(result["state"] == "private_worker_exited", "ledger_guard_success")
                writing.load_inputs(root, item["inventory"], preflight["job"], tokenizer)
                fresh.prepare(root, item["inventory"], preflight["job"], tokenizer)
                _, _, _, job, contract = fresh.bound(root, item["inventory"], preflight["job"], tokenizer)
                require(not (job.root / "progress.json").exists() and not list((job.root / "attempts").glob("*.json")), "fresh_writer_roles_unused")
                fresh.retain(job.root / FIRST, first_execution(job, contract, execution))
                backup = Path.home() / "Documents/CourseKnowledgeBackups/bounded-first-writers" / job.key
                for path in (job.root / FIRST, Path(__file__), Path(bounded.__file__)):
                    whole.backup(path, backup / path.name)
                progress("writing_original_first_conceptual_article", item=number, writer=job.key)
                guard = target / f"item-{number:02d}-authoring-guard"
                guard.mkdir(exist_ok=True)
                result = original.private_guard.run(root, guard, [sys.executable, "-m", __spec__.name,
                    "--root", str(root), "--batch", key, "--inventory", item["inventory"], "--ledger", preflight["job"], "first-worker"])
                require(result["state"] == "private_worker_exited", "first_writer_guard_success")
                checked = first_verify(root, key, item["inventory"], preflight["job"])
                proof = original.mapped.snapshot.save(root, checked)
                completed.append({"lesson": item["lesson"], "inventory": item["inventory"], "ledger": preflight["job"],
                    "writer": job.key, "manuscript_proof": proof.relative_to(root).as_posix(),
                    "unresolved_issues": len(checked["issues"]), "whole_source_coverage_courseware_and_editorial_review_required": True})
                fresh.retain(target / f"item-{number:02d}-result.json", completed[-1])
            result = {"state": "original_finite_private_drafts_require_actual_coverage_and_editorial_review", "batch": key,
                "completed_courses": len(completed), "total_courses": len(plan["items"]), "items": completed,
                "reader_publication_allowed": False, "source_deletion_allowed": False}
            fresh.retain(target / "result.json", result)
            progress(result["state"])
            return {k: v for k, v in result.items() if k != "items"}
        except BaseException as exc:
            failure = whole.failure_status(exc)
            atomic_json(target / ("failure-" + str(time.time_ns()) + ".json"), failure)
            progress(failure["state"], reason_code=failure["reason_code"])
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--batch", required=True)
    parser.add_argument("--inventory")
    parser.add_argument("--ledger")
    parser.add_argument("command", choices=["prepare", "load", "run", "first-worker", "first-verify"])
    args = parser.parse_args()
    try:
        if args.command == "load":
            _, _, _, plan, _, _, _ = load(args.root, args.batch)
            output = {"state": "corpus_wait_continuation_inputs_authenticated", "courses": len(plan["items"]), "model_calls": 0}
        elif args.command == "first-verify":
            value = first_verify(args.root, args.batch, args.inventory, args.ledger)
            output = {**value["summary"], "snapshot": str(original.mapped.snapshot.save(args.root, value))}
        elif args.command == "first-worker":
            output = first_worker(args.root, args.batch, args.inventory, args.ledger)
        else:
            output = globals()[args.command](args.root, args.batch)
        print(json.dumps(output))
    except BaseException as exc:
        print(json.dumps(whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
