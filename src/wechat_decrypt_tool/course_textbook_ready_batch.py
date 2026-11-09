"""Write the next complete, unattempted courses after the exact live controller.

The finite batch preserves catalog scope and every original record. Shorter
members run first; the first write already uses the count-bound schema. Ledger
and writer roles keep their original budgets. Private drafts are never published.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import time

import psutil

from . import course_textbook_corpus_batch as original
from . import course_textbook_corpus_unused_tail as previous
from . import course_textbook_read_lease as reads
from . import course_textbook_unused_writer_dispatch as recovered
from .course_knowledge import atomic_json, schema_lease

whole, fresh, writing = original.whole, original.fresh, original.writing
guard, digest, reviewed = original.private_guard, original.digest, previous.reviewed
POLICY = "first-complete-ready-course-batch-count-bound-v1"
COUNT_CONTRACT = "first-ready-count-bound-execution.json"
DIRECTORY = "ready-course-batches"
SUCCESS = "finite_ready_private_manuscripts_require_coverage_and_editorial_acceptance"


def require(ok, code):
    if not ok:
        raise ValueError("requires_ready_course_batch_" + code)


def bindings():
    return {"entrypoints": {Path(m.__file__).stem: whole.sha(Path(m.__file__).read_bytes())
        for m in (sys.modules[__name__], previous, reviewed, reads, recovered)},
        "original": original.bindings(), "previous": previous.bindings()}


def folder_for(root, key):
    require(isinstance(key, str) and re.fullmatch(r"[a-f0-9]{32}", key), "batch_id")
    return Path(root).resolve() / "wiki" / DIRECTORY / key


def source_path(root, name):
    path = (root / name).resolve()
    require(path.is_relative_to(root) and path.is_file() and not path.is_symlink(), "existing_contained_input")
    return path


def select(root, limit):
    # The original selector authenticates complete originals and excludes every
    # delivery, reservation and attempted ledger, including unsuccessful ones.
    items = original.selected_inputs(root, limit)
    return sorted(items, key=lambda row: (row["groups"], row["characters"], row["lesson"]))


def recovered_predecessor(root, key):
    _, folder, execution = recovered.load(root, key)
    status = whole.read(folder / "guard-progress.json")
    worker = whole.read(root / "wiki/textbook-whole-authoring-jobs" / key / "progress.json")
    require(status["state"] == "running" and writing.identity_live(status) and writing.identity_live(worker)
        and worker["job"] == key and worker["state"] not in {"stopped", "paused"}, "exact_healthy_original_unspent_writer")
    files = [folder / "execution.json", folder / "guard-started.json", folder / "worker-started.json"]
    return {"predecessor_kind": "unspent-writer-dispatch", "predecessor_writer": key,
        "predecessor_batch": execution["batch"], "predecessor_directory": folder.relative_to(root).as_posix(),
        "predecessor_controller": {k: status[k] for k in ("pid", "created")},
        "predecessor_worker": {k: worker[k] for k in ("pid", "created")},
        "predecessor_execution_sha256": digest(execution),
        "predecessor_files": {p.relative_to(root).as_posix(): whole.sha(p.read_bytes()) for p in files}}


def prepare(root, tokenizer, predecessor_batch=None, *, limit=3, predecessor_dispatch=None):
    root, tokenizer = Path(root).resolve(), Path(tokenizer).resolve()
    guard.checkpoint(root)
    require(tokenizer.is_relative_to(root) and tokenizer.is_file(), "local_tokenizer")
    with schema_lease(root / "wiki/full-course-batch-prepare.lock", timeout=0):
        if predecessor_dispatch is not None:
            require(predecessor_batch is None, "one_explicit_predecessor_provider")
            predecessor = recovered_predecessor(root, predecessor_dispatch)
        else:
            with reads.bounded_ledger_reads(writing):
                _, old, target, _, _, _, _, execution = previous.load(root, predecessor_batch)
            status, started = whole.read(target / "progress.json"), whole.read(target / "started.json")
            require(writing.identity_live(status) and status["batch"] == predecessor_batch
                and status["state"] in {"waiting_for_exact_recovered_first_writer", "building_original_unused_second_ledger",
                    "writing_original_unused_second_article"}
                and all(status[k] == started[k] for k in ("pid", "created")), "exact_healthy_original_controller")
            files = [old / "plan.json", old / "preflights.json", target / "execution.json", target / "started.json"]
            predecessor = {"predecessor_kind": "original-two-course-controller", "predecessor_batch": predecessor_batch,
                "predecessor_directory": target.relative_to(root).as_posix(),
                "predecessor_controller": {k: status[k] for k in ("pid", "created")},
                "predecessor_execution_sha256": digest(execution),
                "predecessor_files": {p.relative_to(root).as_posix(): whole.sha(p.read_bytes()) for p in files}}
        items = select(root, limit)
        plan = {"policy": POLICY, "code": bindings(), "items": items,
            "scope": {"audio_courses": 340, "silent_sources": 2, "all_recoverable_courseware_required": True},
            **predecessor,
            "tokenizer": tokenizer.relative_to(root).as_posix(), "tokenizer_sha256": whole.sha(tokenizer.read_bytes()),
            "models": {name: writing.local_model_digest(name) for name in ("qwen3.5:9b", "course-defiant:q4")},
            "ordering": "shortest_complete_member_of_next_catalog_batch_first",
            "first_write_format": "tagged_actual_paragraph_count_2_to_12_and_bounded_indices",
            "original_stage_attempt_budget": 2, "semantic_repairs_per_unit": 1,
            "automatic_retry_allowed": False, "automatic_thermal_resume_allowed": False,
            "maximum_temperature_c": 70, "maximum_power_w": 200, "minimum_free_gib_each_volume": 32,
            "reader_publication_allowed": False, "source_deletion_allowed": False}
        key = digest(plan)[:32]
        folder = folder_for(root, key)
        require(not folder.exists(), "first_preparation_only")
        fresh.retain(folder / "plan.json", plan)
        try:
            flights = []
            for item in items:
                guard.checkpoint(root)
                original.check_item(root, item)
                flights.append(whole.run(root, item["inventory"], tokenizer, preflight=True))
            fresh.retain(folder / "preflights.json", flights)
        except BaseException as exc:
            fresh.retain(folder / "preparation-failure.json", whole.failure_status(exc))
            raise
        return {"state": "next_complete_courses_prepared_before_any_ledger_role", "batch": key,
            "courses": len(items), "records": sum(r["records"] for r in items),
            "characters": sum(r["characters"] for r in items), "groups": sum(r["groups"] for r in items),
            "model_calls": 0, "reader_publication_allowed": False}


def load(root, key):
    root = Path(root).resolve()
    folder = folder_for(root, key)
    plan, flights = whole.read(folder / "plan.json"), whole.read(folder / "preflights.json")
    require(plan["policy"] == POLICY and digest(plan)[:32] == key and plan["code"] == bindings(), "frozen_plan_and_code")
    require(plan["scope"] == {"audio_courses": 340, "silent_sources": 2, "all_recoverable_courseware_required": True}
        and 1 <= len(plan["items"]) <= 3 and len(plan["items"]) == len(flights)
        and len({r["lesson"] for r in plan["items"]}) == len(flights), "finite_complete_course_scope")
    tokenizer = source_path(root, plan["tokenizer"])
    require(whole.sha(tokenizer.read_bytes()) == plan["tokenizer_sha256"], "same_tokenizer_bytes")
    require(all(whole.sha(source_path(root, name).read_bytes()) == sha for name, sha in plan["predecessor_files"].items()),
        "original_predecessor_execution_preserved")
    require(plan["models"] == {name: writing.local_model_digest(name) for name in plan["models"]}, "same_local_model_weights")
    for item, flight in zip(plan["items"], flights, strict=True):
        original.check_item(root, item)
        manifest = whole.read(root / "wiki/textbook-keyed-jobs" / flight["job"] / "manifest.json")
        bound = manifest["binding"]
        require(manifest["id"] == flight["job"] == digest(bound)[:32] and bound["inventory"] == item["inventory"]
            and bound["module_sha256"] == whole.module_bindings() and bound["models"] == plan["models"]
            and bound["tokenizer_sha256"] == plan["tokenizer_sha256"], "original_unused_ledger_binding")
    return root, folder, plan, tokenizer, flights


def unused(root, folder, flights):
    require(not list(folder.glob("item-*-started.json")) and not list(folder.glob("item-*-result.json")),
        "no_previous_item_execution")
    for flight in flights:
        job = root / "wiki/textbook-keyed-jobs" / flight["job"]
        require(not (job / "progress.json").exists() and not (job / "ledger.json").exists()
            and not list(job.glob("group-*.json")) and not list((job / "attempts").glob("*.json"))
            and not (job / "whole-source-authoring-execution.json").exists(), "all_original_course_roles_unused")


def wait(root, plan, *, live=writing.identity_live, sleep=time.sleep):
    target = root / plan["predecessor_directory"]
    expected = plan["predecessor_controller"]
    if plan.get("predecessor_kind") == "unspent-writer-dispatch":
        while True:
            guard.checkpoint(root)
            state = whole.read(target / "guard-progress.json")
            require(all(state[k] == v for k, v in expected.items()), "same_exact_recovered_guard_identity")
            require(state["state"] not in {"stopped", "paused", "pausing"}, "recovered_predecessor_failure_or_pause")
            if not any(live(row) for row in [expected, plan["predecessor_worker"]]):
                require(state["state"] == "worker_exited" and state["returncode"] == 0, "recovered_original_writer_successful_exit")
                actual = recovered.verify(root, plan["predecessor_writer"])
                return {"original_second": actual["proof"], "model_calls": 0,
                    "semantic_acceptance": False, "reader_publication_allowed": False}
            sleep(15)
    while True:
        guard.checkpoint(root)
        state = whole.read(target / "progress.json")
        require(all(state[k] == v for k, v in expected.items()), "same_exact_controller_identity")
        require(state["state"] not in {"stopped", "paused", "pausing"}, "predecessor_failure_or_pause_no_automatic_resume")
        if not live(expected):
            require(state["state"] == "original_two_course_private_manuscripts_require_coverage_and_editorial_review",
                "actual_successful_controller_exit")
            break
        sleep(15)
    with reads.bounded_ledger_reads(writing):
        _, _, actual_target, _, _, _, first, contract = previous.load(root, plan["predecessor_batch"])
        require(actual_target == target and digest(contract) == plan["predecessor_execution_sha256"], "actual_original_controller_contract")
        result = whole.read(target / "result.json")
        first_book = reviewed.reconstruct(root, first.job.key)
        second_book = previous.verify_writer(root, plan["predecessor_batch"])
    require(result["original_courses_completed"] == 2 and result["original_unused_second_completed"] == 1
        and result["second_writer"] == second_book["summary"]["job"], "actual_both_predecessor_manuscripts")
    for manuscript in (first_book, second_book):
        require(manuscript["private"] is not None
            and manuscript["summary"]["verified_units"] == manuscript["summary"]["total_units"], "entire_predecessor_manuscript_reconstructed")
    return {"first": first_book["proof"], "second": second_book["proof"], "model_calls": 0,
        "semantic_acceptance": False, "reader_publication_allowed": False}


def item_at(plan, flights, number):
    require(type(number) is int and 0 <= number < len(plan["items"]), "item_number")
    return plan["items"][number], flights[number]


def ledger_worker(root, key, number):
    root, folder, plan, tokenizer, flights = load(root, key)
    item, flight = item_at(plan, flights, number)
    job = root / "wiki/textbook-keyed-jobs" / flight["job"]
    require(not (job / "progress.json").exists() and not (job / "ledger.json").exists()
        and not list((job / "attempts").glob("*.json")), "first_ledger_worker_only")
    with schema_lease(folder / f"item-{number:02d}-ledger-worker.lock", timeout=0):
        fresh.retain(folder / f"item-{number:02d}-ledger-started.json", {"pid": psutil.Process().pid,
            "created": psutil.Process().create_time()})
        (job / "failures").mkdir(exist_ok=True)
        return whole.run(root, item["inventory"], tokenizer)


def writer_execution(job, contract, key, number):
    return {"policy": POLICY, "batch": key, "item": number, "writer": job.key,
        "fresh_contract_sha256": digest(contract), "code": bindings(),
        "first_write_format": "tagged_actual_paragraph_count_2_to_12_and_bounded_indices",
        "original_budgets_reset": False, "reader_publication_allowed": False}


def writer_type(contract, extra, key, number, *, read_only):
    base = original.mapped.ORIGINAL_FACTORY(contract, read_only=read_only)

    class FirstCountBoundJob(base):
        def __init__(self, *args):
            super().__init__(*args)
            require(extra == writer_execution(self, contract, key, number), "same_first_write_format")
            self.reader.paths.add(self.root / COUNT_CONTRACT)

        def stage(self, name, prompt, schema, context, validate, **kwargs):
            match = re.fullmatch(fresh.PATTERN, name)
            if match and match[2] in {"write", "repair"}:
                with reviewed.future_format():
                    return super().stage(name, prompt, schema, context, validate, **kwargs)
            return super().stage(name, prompt, schema, context, validate, **kwargs)

    return FirstCountBoundJob


def writer_bound(root, key, number):
    root, folder, plan, tokenizer, flights = load(root, key)
    item, flight = item_at(plan, flights, number)
    with reads.bounded_ledger_reads(writing):
        _, _, _, job, contract = fresh.bound(root, item["inventory"], flight["job"], tokenizer)
    extra = whole.read(job.root / COUNT_CONTRACT)
    require(extra == writer_execution(job, contract, key, number), "original_first_writer_contract")
    return root, folder, item, flight, tokenizer, job, contract, extra


def writer_worker(root, key, number):
    root, _, item, flight, tokenizer, job, contract, extra = writer_bound(root, key, number)
    guard.checkpoint(root)
    require(not (job.root / "progress.json").exists() and not list((job.root / "attempts").glob("*.json")), "first_writer_worker_only")
    with reads.bounded_ledger_reads(writing), fresh.installed(writer_type(contract, extra, key, number, read_only=False)):
        return writing.run(root, item["inventory"], flight["job"], tokenizer)


def verify_writer(root, key, number):
    root, _, item, flight, tokenizer, _, contract, extra = writer_bound(root, key, number)
    factory = fresh.job_type
    fresh.job_type = lambda actual, *, read_only: writer_type(actual, extra, key, number, read_only=read_only)
    try:
        with reads.bounded_ledger_reads(writing):
            result = fresh.reconstruct(root, item["inventory"], flight["job"], tokenizer)
    finally:
        fresh.job_type = factory
    result["proof"].update(policy=POLICY, ready_batch=key, ready_item=number, first_writer_contract_sha256=digest(extra))
    return result


def run(root, key):
    root, folder, plan, tokenizer, flights = load(root, key)
    owner, completed = psutil.Process(), []

    def progress(state, **extra):
        atomic_json(folder / "progress.json", {"pid": owner.pid, "created": owner.create_time(), "updated": time.time(),
            "state": state, "batch": key, "completed_courses": len(completed), "total_courses": len(flights),
            "reader_publication_allowed": False, "source_deletion_allowed": False, **extra})

    with schema_lease(folder / "controller.lock", timeout=0):
        require(not (folder / "started.json").exists(), "controller_once_no_automatic_retry")
        restored = whole.read(folder / "independent-input-restore.json")
        require(restored["state"] == "ready_course_inputs_independently_restored" and restored["batch"] == key
            and restored["model_calls"] == restored["original_root_reads"] == 0, "independent_actual_inputs_restore_before_dispatch")
        unused(root, folder, flights)
        fresh.retain(folder / "started.json", {"pid": owner.pid, "created": owner.create_time()})
        try:
            progress("waiting_for_exact_original_two_course_controller")
            fresh.retain(folder / "predecessor-proof.json", wait(root, plan))
            # The active predecessor owns this lock until its real exit. Waiting
            # controllers never compete for it or for the shared GPU lock.
            with schema_lease(root / "wiki/full-course-batch-worker.lock", timeout=0):
                for number, (item, flight) in enumerate(zip(plan["items"], flights, strict=True)):
                    guard.checkpoint(root)
                    original.check_item(root, item)
                    progress("building_first_complete_original_ledger", item=number, lesson=item["lesson"], ledger=flight["job"])
                    ledger_guard = folder / f"item-{number:02d}-ledger-guard"
                    ledger_guard.mkdir()
                    outcome = guard.run(root, ledger_guard, [sys.executable, "-m", __spec__.name,
                        "--root", str(root), "--batch", key, "--item", str(number), "ledger-worker"])
                    require(outcome["state"] == "private_worker_exited", "ledger_successful_exit")
                    writing.load_inputs(root, item["inventory"], flight["job"], tokenizer)
                    fresh.prepare(root, item["inventory"], flight["job"], tokenizer)
                    _, _, _, job, contract = fresh.bound(root, item["inventory"], flight["job"], tokenizer)
                    require(not (job.root / "progress.json").exists()
                        and not list((job.root / "attempts").glob("*.json")), "first_write_roles_unused")
                    fresh.retain(job.root / COUNT_CONTRACT, writer_execution(job, contract, key, number))
                    progress("writing_first_complete_conceptual_article", item=number, lesson=item["lesson"], writer=job.key)
                    writer_guard = folder / f"item-{number:02d}-authoring-guard"
                    writer_guard.mkdir()
                    outcome = guard.run(root, writer_guard, [sys.executable, "-m", __spec__.name,
                        "--root", str(root), "--batch", key, "--item", str(number), "writer-worker"])
                    require(outcome["state"] == "private_worker_exited", "writer_successful_exit")
                    actual = verify_writer(root, key, number)
                    snapshot = original.mapped.snapshot.save(root, actual)
                    row = {"lesson": item["lesson"], "inventory": item["inventory"], "ledger": flight["job"],
                        "writer": job.key, "snapshot": snapshot.relative_to(root).as_posix(),
                        "unresolved_issues": len(actual["issues"]), "reader_publication_allowed": False}
                    fresh.retain(folder / f"item-{number:02d}-result.json", row)
                    completed.append(row)
            result = {"state": SUCCESS, "batch": key, "completed_courses": len(completed), "total_courses": len(flights),
                "items": completed, "reader_publication_allowed": False, "source_deletion_allowed": False}
            fresh.retain(folder / "result.json", result)
            progress(SUCCESS)
            return {k: v for k, v in result.items() if k != "items"}
        except BaseException as exc:
            failure = whole.failure_status(exc)
            fresh.retain(folder / ("failure-" + str(time.time_ns()) + ".json"), failure)
            progress(failure["state"], reason_code=failure["reason_code"])
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--batch")
    parser.add_argument("--after-batch")
    parser.add_argument("--after-dispatch")
    parser.add_argument("--tokenizer", type=Path)
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--item", type=int)
    parser.add_argument("command", choices=["prepare", "load", "run", "ledger-worker", "writer-worker", "verify-writer"])
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = prepare(args.root, args.tokenizer, args.after_batch, limit=args.limit, predecessor_dispatch=args.after_dispatch)
        elif args.command == "load":
            _, _, plan, _, _ = load(args.root, args.batch)
            result = {"state": "complete_ready_batch_inputs_authenticated", "batch": args.batch, "courses": len(plan["items"]), "model_calls": 0}
        elif args.command == "verify-writer":
            result = verify_writer(args.root, args.batch, args.item)["summary"]
        elif args.command in {"ledger-worker", "writer-worker"}:
            result = globals()[args.command.replace("-", "_")](args.root, args.batch, args.item)
        else:
            result = run(args.root, args.batch)
        print(json.dumps(result))
    except BaseException as exc:
        print(json.dumps(whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
