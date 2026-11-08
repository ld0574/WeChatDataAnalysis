"""A finite first ledger -> whole article batch after an exact live writer.

Use complete prepared originals and existing local roles, without restarting
healthy work, revisiting attempted courses or publishing unfinished articles.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import time

import psutil

from . import course_textbook_point_mapped_resume as mapped
from . import course_textbook_omitted_mapping_review as omissions
from . import course_textbook_source_intake as intake
from . import course_textbook_whole_coverage_assignments as coverage_assignments
from .course_knowledge import atomic_json, schema_lease

fresh, writing, whole = mapped.fresh, mapped.writing, mapped.writing.whole
private_guard, digest = mapped.private_guard, mapped.digest
POLICY = "finite-complete-original-ledger-and-whole-authoring-batch-v1"
FRESH_CONTRACT = "point-mapped-first-authoring.json"


def require(ok, code):
    if not ok:
        raise ValueError("requires_corpus_batch_" + code)


def bindings():
    return {"batch": whole.sha(Path(__file__).read_bytes()),
        "point_mapping": whole.sha(Path(mapped.__file__).read_bytes()),
        "omitted_mapping": whole.sha(Path(omissions.__file__).read_bytes()),
        "coverage_predecessor": {m.__name__: whole.sha(Path(m.__file__).read_bytes())
            for m in (coverage_assignments, coverage_assignments.checked, coverage_assignments.mapping)},
        "fresh": whole.sha(Path(fresh.__file__).read_bytes()),
        "source_intake": intake.bindings(), "whole": whole.module_bindings(), "writer": writing.bindings()}


def manifest_inventories(root):
    return {whole.read(p)["binding"]["inventory"]
        for p in (root / "wiki/textbook-keyed-jobs").glob("*/manifest.json")
        if "inventory" in whole.read(p).get("binding", {})}


def reserved_lessons(root):
    return {row["lesson"] for p in (root / "wiki/full-course-batches").glob("*/plan.json")
        for row in whole.read(p)["items"]}


def selected_inputs(root, limit):
    require(type(limit) is int and 1 <= limit <= 3, "finite_one_to_three_course_limit")
    catalog = whole.read(root / "library/catalog.json")
    require(len(catalog["lessons"]) == 340 and len({r["id"] for r in catalog["lessons"]}) == 340, "entire_audio_scope")
    attempted, reserved, delivered = manifest_inventories(root), reserved_lessons(root), intake.delivered(root)
    inventories = {}
    for path in (root / "wiki/textbook-whole-record-evidence").glob("*/manifest.json"):
        value = whole.load(root, path.parent.name)
        if path.parent.name not in attempted:
            inventories.setdefault(value["lesson"], []).append(path.parent.name)
    selected = []
    for row in catalog["lessons"]:
        lesson = row["id"]
        if row["state"] != "draft_available" or lesson in reserved | delivered or lesson not in inventories:
            continue
        require(len(inventories[lesson]) == 1, "one_unattempted_inventory_per_lesson")
        key = inventories[lesson][0]
        value = whole.load(root, key)
        parent = intake.evidence.load(root, value["parent_inventory_sha256"])
        equivalence = intake.original_equivalence(root, parent, row["artifact"])
        selected.append({"lesson": lesson, "inventory": key, "source_artifact": row["artifact"],
            "original_equivalence": equivalence, "records": len(value["sources"]),
            "characters": value["original_characters"], "groups": len(value["pages"])})
        if len(selected) == limit:
            break
    require(len(selected) == limit, "enough_distinct_first_attempt_courses")
    return selected


def check_item(root, item):
    inventory = whole.load(root, item["inventory"])
    require(inventory["lesson"] == item["lesson"] and len(inventory["sources"]) == item["records"]
        and inventory["original_characters"] == item["characters"] and len(inventory["pages"]) == item["groups"], "complete_original_input")
    rows = [r for r in whole.read(root / "library/catalog.json")["lessons"] if r["id"] == item["lesson"]]
    require(len(rows) == 1 and rows[0]["state"] == "draft_available"
        and rows[0]["artifact"] == item["source_artifact"], "unchanged_selected_source_version")
    parent = intake.evidence.load(root, inventory["parent_inventory_sha256"])
    require(intake.original_equivalence(root, parent, item["source_artifact"]) == item["original_equivalence"], "unchanged_all_originals")
    return inventory


def prepare(root, tokenizer, predecessor_job, *, limit=2, predecessor_kind="writer"):
    root, tokenizer = Path(root).resolve(), Path(tokenizer).resolve()
    private_guard.checkpoint(root)
    require(re.fullmatch(r"[a-f0-9]{32}", predecessor_job) is not None, "predecessor_id")
    require(predecessor_kind in {"writer", "coverage"}, "predecessor_kind")
    if predecessor_kind == "coverage":
        previous = root / "wiki/whole-manuscript-coverage-jobs" / predecessor_job
        guard_path = root / "wiki-revision-thermal-guard.json"
        predecessor_parent = whole.read(previous / coverage_assignments.CONTRACT)["parent_writer"]
    else:
        previous = root / "wiki/textbook-whole-authoring-jobs" / predecessor_job
        guard_path = previous / mapped.DIRECTORY / "guard-progress.json"
        predecessor_parent = predecessor_job
    guard, worker = whole.read(guard_path), whole.read(previous / "progress.json")
    require(writing.identity_live(guard) and writing.identity_live(worker)
        and guard["state"] == "running" and worker["job"] == predecessor_job, "exact_healthy_predecessor")
    with schema_lease(root / "wiki/full-course-batch-prepare.lock", timeout=0):
        items = selected_inputs(root, limit)
        plan = {"policy": POLICY, "items": items, "scope": {"audio_courses": 340, "silent_sources": 2,
            "all_recoverable_courseware_required": True}, "predecessor_job": predecessor_job,
            "predecessor_kind": predecessor_kind, "predecessor_parent": predecessor_parent,
            "predecessor_guard_path": guard_path.relative_to(root).as_posix(),
            "predecessor_guard": {key: guard[key] for key in ("pid", "created")},
            "predecessor_worker": {key: worker[key] for key in ("pid", "created")},
            "tokenizer": str(tokenizer.relative_to(root)), "tokenizer_sha256": whole.sha(tokenizer.read_bytes()),
            "code": bindings(), "models": {name: writing.local_model_digest(name) for name in ("qwen3.5:9b", "course-defiant:q4")},
            "original_stage_attempt_budget": 2, "semantic_repairs_per_unit": 1,
            "automatic_retry_allowed": False, "automatic_thermal_resume_allowed": False,
            "maximum_temperature_c": 70, "maximum_power_w": 200, "minimum_free_gib_each_volume": 32,
            "reader_publication_allowed": False, "source_deletion_allowed": False}
        key = digest(plan)[:32]
        folder = root / "wiki/full-course-batches" / key
        require(not folder.exists(), "first_batch_preparation_only")
        fresh.retain(folder / "plan.json", plan)
        try:
            preflights = []
            for item in items:
                check_item(root, item)
                preflights.append(whole.run(root, item["inventory"], tokenizer, preflight=True))
            fresh.retain(folder / "preflights.json", preflights)
            return {"state": "finite_whole_courses_prepared_before_first_ledger_role", "batch": key,
                "courses": len(items), "records": sum(r["records"] for r in items),
                "scheduling_groups": sum(r["groups"] for r in items), "model_calls": 0,
                "reader_publication_allowed": False}
        except BaseException as exc:
            atomic_json(folder / "preparation-failure.json", whole.failure_status(exc))
            raise


def load(root, key):
    root = Path(root).resolve()
    require(re.fullmatch(r"[a-f0-9]{32}", key) is not None, "batch_id")
    folder = root / "wiki/full-course-batches" / key
    plan, preflights = whole.read(folder / "plan.json"), whole.read(folder / "preflights.json")
    require(digest(plan)[:32] == key and plan["policy"] == POLICY and plan["code"] == bindings(), "frozen_plan_and_code")
    require(plan["scope"] == {"audio_courses": 340, "silent_sources": 2, "all_recoverable_courseware_required": True}
        and 1 <= len(plan["items"]) <= 3 and len(plan["items"]) == len(preflights), "unchanged_finite_and_full_scope")
    tokenizer = root / plan["tokenizer"]
    require(tokenizer.resolve().is_relative_to(root) and whole.sha(tokenizer.read_bytes()) == plan["tokenizer_sha256"], "frozen_tokenizer")
    for item, preflight in zip(plan["items"], preflights, strict=True):
        check_item(root, item)
        manifest = whole.read(root / "wiki/textbook-keyed-jobs" / preflight["job"] / "manifest.json")
        require(manifest["id"] == preflight["job"] == digest(manifest["binding"])[:32]
            and manifest["binding"]["inventory"] == item["inventory"]
            and manifest["binding"]["module_sha256"] == whole.module_bindings()
            and manifest["binding"]["tokenizer_sha256"] == plan["tokenizer_sha256"]
            and manifest["binding"]["models"] == plan["models"], "original_preflight_ledger_identity")
    return root, folder, plan, tokenizer, preflights


def wait_predecessor(root, plan, *, sleep=time.sleep, live=writing.identity_live):
    previous = root / "wiki/textbook-whole-authoring-jobs" / plan["predecessor_job"]
    while True:
        private_guard.checkpoint(root)
        path = root / plan["predecessor_guard_path"] if "predecessor_guard_path" in plan else previous / mapped.DIRECTORY / "guard-progress.json"
        guard = whole.read(path)
        require(all(guard[k] == v for k, v in plan["predecessor_guard"].items()), "unchanged_predecessor_guard")
        if guard["state"] == "paused":
            raise InterruptedError("requires_corpus_batch_predecessor_paused_no_resume")
        if not any(live(plan[name]) for name in ("predecessor_guard", "predecessor_worker")):
            require(guard["state"] == "worker_exited" and guard.get("returncode") == 0, "predecessor_successful_exit")
            return
        sleep(15)


def first_job_type(contract, execution, *, read_only):
    base = mapped.ORIGINAL_FACTORY(contract, read_only=read_only)

    class MappedFirstJob(base):
        def __init__(self, *args):
            super().__init__(*args)
            require(execution == first_execution(self, contract), "frozen_first_writer_format")
            self.reader.paths.add(self.root / FRESH_CONTRACT)

        def stage(self, name, prompt, schema, context, validate, **kwargs):
            match = re.fullmatch(fresh.PATTERN, name)
            if match and match[2] in {"write", "repair"}:
                with omissions.future_format():
                    return super().stage(name, prompt, schema, context, validate, **kwargs)
            return super().stage(name, prompt, schema, context, validate, **kwargs)

    return MappedFirstJob


def first_execution(job, contract):
    return {"policy": POLICY, "fresh_execution_sha256": digest(contract), "job": job.key,
        "code": bindings(), "paragraph_format": "point_ids_inside_actual_paragraphs",
        "original_budgets_reset": False, "additional_model_roles": 0, "reader_publication_allowed": False}


def prepare_first(root, inventory, ledger, tokenizer):
    fresh.prepare(root, inventory, ledger, tokenizer)
    _, _, _, job, contract = fresh.bound(root, inventory, ledger, tokenizer)
    require(not (job.root / "progress.json").exists() and not list((job.root / "attempts").glob("*.json")), "first_writer_roles_unused")
    fresh.retain(job.root / FRESH_CONTRACT, first_execution(job, contract))
    (job.root / "point-mapped-first-guard").mkdir(exist_ok=True)
    return job.key


def first_bound(root, inventory, ledger, tokenizer):
    root, data, working, job, contract = fresh.bound(root, inventory, ledger, tokenizer)
    execution = whole.read(job.root / FRESH_CONTRACT)
    require(execution == first_execution(job, contract), "same_first_writer_format")
    return root, data, working, job, contract, execution


def first_worker(root, inventory, ledger, tokenizer):
    root, _, _, job, contract, execution = first_bound(root, inventory, ledger, tokenizer)
    private_guard.checkpoint(root)
    require(not (job.root / "progress.json").exists(), "first_writer_once_no_automatic_retry")
    with fresh.installed(first_job_type(contract, execution, read_only=False)):
        return writing.run(root, inventory, ledger, tokenizer)


def first_verify(root, inventory, ledger, tokenizer):
    root, _, _, _, contract, execution = first_bound(root, inventory, ledger, tokenizer)
    old_factory = fresh.job_type
    fresh.job_type = lambda actual, *, read_only: first_job_type(actual, execution, read_only=read_only)
    try:
        rebuilt = fresh.reconstruct(root, inventory, ledger, tokenizer)
    finally:
        fresh.job_type = old_factory
    rebuilt["proof"].update(policy=POLICY, first_writer_format_sha256=digest(execution))
    return rebuilt


def ledger_worker(root, key, number):
    root, folder, plan, tokenizer, preflights = load(root, key)
    require(type(number) is int and 0 <= number < len(plan["items"]), "item_number")
    job = root / "wiki/textbook-keyed-jobs" / preflights[number]["job"]
    require(not (job / "progress.json").exists() and not (job / "ledger.json").exists(), "first_ledger_worker_only")
    with schema_lease(folder / (f"item-{number:02d}-ledger-worker.lock"), timeout=0):
        fresh.retain(folder / (f"item-{number:02d}-ledger-started.json"), {"pid": psutil.Process().pid, "created": psutil.Process().create_time()})
        (job / "failures").mkdir(exist_ok=True)
        return whole.run(root, plan["items"][number]["inventory"], tokenizer)


def run(root, key):
    root, folder, plan, tokenizer, preflights = load(root, key)
    owner, completed = psutil.Process(), []

    def progress(state, **extra):
        atomic_json(folder / "progress.json", {"pid": owner.pid, "created": owner.create_time(), "updated": time.time(),
            "state": state, "batch": key, "completed_courses": len(completed), "total_courses": len(plan["items"]),
            "reader_publication_allowed": False, "source_deletion_allowed": False, **extra})

    with schema_lease(root / "wiki/full-course-batch-worker.lock", timeout=0):
        require(not (folder / "started.json").exists(), "batch_once_no_automatic_retry")
        fresh.retain(folder / "started.json", {"pid": owner.pid, "created": owner.create_time()})
        try:
            progress("waiting_for_exact_whole_writer")
            wait_predecessor(root, plan)
            if plan["predecessor_kind"] == "coverage":
                rebuilt = coverage_assignments.reconstruct(root, plan["predecessor_parent"], tokenizer, "section-0007")
            else:
                rebuilt = mapped.reconstruct(root, plan["predecessor_job"], tokenizer)
            fresh.retain(folder / "predecessor-proof.json", rebuilt["proof"])
            for number, (item, preflight) in enumerate(zip(plan["items"], preflights, strict=True)):
                private_guard.checkpoint(root)
                check_item(root, item)
                progress("building_first_complete_original_ledger", item=number, lesson=item["lesson"], ledger=preflight["job"])
                guard_folder = folder / f"item-{number:02d}-ledger-guard"
                guard_folder.mkdir(exist_ok=True)
                result = private_guard.run(root, guard_folder, [sys.executable, "-m", __spec__.name, "--root", str(root),
                    "--batch", key, "--item", str(number), "ledger-worker"])
                require(result["state"] == "private_worker_exited", "ledger_guard_returned_without_success")
                ledger_progress = whole.read(root / "wiki/textbook-keyed-jobs" / preflight["job"] / "progress.json")
                require(not writing.identity_live(ledger_progress), "ledger_worker_really_exited")
                writing.load_inputs(root, item["inventory"], preflight["job"], tokenizer)
                writer = prepare_first(root, item["inventory"], preflight["job"], tokenizer)
                progress("writing_first_complete_conceptual_article", item=number, lesson=item["lesson"], writer=writer)
                writer_root = root / "wiki/textbook-whole-authoring-jobs" / writer
                result = private_guard.run(root, writer_root / "point-mapped-first-guard", [sys.executable, "-m", __spec__.name,
                    "--root", str(root), "--inventory", item["inventory"], "--ledger", preflight["job"],
                    "--tokenizer", str(tokenizer), "first-worker"])
                require(result["state"] == "private_worker_exited", "writer_guard_returned_without_success")
                checked = first_verify(root, item["inventory"], preflight["job"], tokenizer)
                proof_path = mapped.snapshot.save(root, checked)
                completed.append({"lesson": item["lesson"], "inventory": item["inventory"], "ledger": preflight["job"],
                    "writer": writer, "manuscript_proof": str(proof_path.relative_to(root)),
                    "unresolved_issues": len(checked["issues"]), "whole_source_coverage_and_courseware_review_required": True})
                fresh.retain(folder / f"item-{number:02d}-result.json", completed[-1])
            result = {"state": "finite_whole_course_drafts_require_coverage_courseware_and_final_editing",
                "batch": key, "completed_courses": len(completed), "total_courses": len(plan["items"]),
                "items": completed, "reader_publication_allowed": False, "source_deletion_allowed": False}
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
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--tokenizer", type=Path)
    parser.add_argument("--after-job")
    parser.add_argument("--after-kind", choices=["writer", "coverage"], default="writer")
    parser.add_argument("--limit", type=int, default=2)
    parser.add_argument("--batch")
    parser.add_argument("--item", type=int)
    parser.add_argument("--inventory")
    parser.add_argument("--ledger")
    parser.add_argument("command", choices=["prepare", "load", "run", "ledger-worker", "first-worker", "first-verify"])
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = prepare(args.root, args.tokenizer, args.after_job, limit=args.limit, predecessor_kind=args.after_kind)
        elif args.command == "load":
            _, _, plan, _, _ = load(args.root, args.batch)
            result = {"state": "finite_whole_course_inputs_authenticated", "batch": args.batch,
                "courses": len(plan["items"]), "model_calls": 0}
        elif args.command == "run":
            result = run(args.root, args.batch)
        elif args.command == "ledger-worker":
            result = ledger_worker(args.root, args.batch, args.item)
        elif args.command == "first-worker":
            result = first_worker(args.root, args.inventory, args.ledger, args.tokenizer)
        else:
            rebuilt = first_verify(args.root, args.inventory, args.ledger, args.tokenizer)
            result = {**rebuilt["summary"], "snapshot": str(mapped.snapshot.save(args.root, rebuilt))}
        print(json.dumps(result))
    except BaseException as exc:
        print(json.dumps(whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
