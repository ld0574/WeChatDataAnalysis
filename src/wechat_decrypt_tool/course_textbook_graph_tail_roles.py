"""Finish the original finite tail using only unused original graph repair roles.

An exhausted graph write is never retried or cached as accepted. Its original
review, mandatory repair and final review may run once; other failures stop.
The earlier graph continuation and all original evidence remain immutable.
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

from . import course_textbook_graph_write_resume as prior
from .course_knowledge import atomic_json, schema_lease

fresh, writing, whole = prior.fresh, prior.writing, prior.whole
digest, guard, reads = prior.digest, prior.guard, prior.reads
POLICY = "finite-original-tail-unused-graph-review-repair-roles-v1"
DIRECTORY = "graph-tail-original-roles"


def require(ok, code):
    if not ok:
        raise ValueError("requires_graph_tail_roles_" + code)


def folder_for(root, key):
    require(isinstance(key, str) and re.fullmatch(r"[a-f0-9]{32}", key), "writer_id")
    return Path(root).resolve() / "wiki" / DIRECTORY / key


def bindings():
    return {"module_sha256": whole.sha(Path(__file__).read_bytes()), "prior": prior.bindings()}


def parent_type(bundle, *, read_only):
    return prior.resumed_type(bundle.previous, bundle.previous_result, read_only=read_only)


def inputs(root, key, count):
    previous = prior.load(root, key)
    previous_result = prior.original_roles(previous, read_only=True)
    bundle = copy.copy(previous)
    bundle.previous, bundle.previous_result = previous, previous_result
    job = parent_type(bundle, read_only=True)(writing.TextbookStore(previous.root),
        previous.inventory, previous.working, previous.tokenizer)
    require(job.reader.saved("private-working-inputs") == previous.working, "same_complete_original_inputs")
    plan = writing.organize(job, previous.working)
    units = writing.writing_units(plan, previous.working, previous.inventory, job.tokenizer)
    require(job.reader.saved("concept-plan") == plan and job.reader.saved("writing-allocation") == units,
        "same_original_complete_allocation")
    require(type(count) is int and previous.count < count < len(units), "strictly_later_original_failed_unit")
    sections, _, issues = fresh.snapshot.replay_units(job.reader, units, plan, count)
    bundle.job, bundle.count, bundle.units, bundle.prefix = job, count, units, units[count]["id"]
    bundle.context = fresh.snapshot.refs.unit_context(units[count], count, units, plan,
        sections, previous.working, previous.inventory)
    packet = writing.composition.original_packet(units[count]["point_ids"], previous.working, previous.inventory)
    bundle.scope = fresh.continuation.scope_for(units[count]["point_ids"], previous.working,
        previous.inventory, bundle.context, packet)
    bundle.paths, bundle.issues = set(job.reader.paths), issues
    bundle.folder = folder_for(previous.root, key)
    prior.failed_writes(bundle)
    return bundle


def execution(bundle):
    return {"policy": POLICY, "writer": bundle.job.key, "code": bindings(),
        "previous_execution_sha256": digest(bundle.previous.contract),
        "previous_original_role_result_sha256": digest(bundle.previous_result),
        "original_status": bundle.old_status, "verified_prefix_units": bundle.count,
        "total_units": len(bundle.units), "remaining_units": [u["id"] for u in bundle.units[bundle.count:]],
        "initial_candidate_sha256": digest(bundle.candidate), "initial_history": bundle.history,
        "files": {p.relative_to(bundle.root).as_posix(): whole.sha(p.read_bytes()) for p in sorted(bundle.paths)},
        "original_stage_attempt_budget": 2, "semantic_repairs_per_unit": 1,
        "additional_model_roles": 0, "original_budgets_reset": False,
        "exhausted_write_retry_allowed": False, "mandatory_original_repair": True,
        "future_failure_dispatch": "only_authenticated_graph_write_to_unused_original_roles",
        "automatic_thermal_resume_allowed": False, "reader_publication_allowed": False,
        "source_deletion_allowed": False}


def prepare(root, key):
    root = Path(root).resolve()
    guard.checkpoint(root)
    status_path = root / "wiki/textbook-whole-authoring-jobs" / key / "progress.json"
    status = whole.read(status_path)
    require(status["state"] == "stopped" and not writing.identity_live(status), "actual_failed_writer_exited")
    bundle = inputs(root, key, status["completed_sections"])
    bundle.old_status = status
    with schema_lease(root / "wiki/full-course-batch-worker.lock", timeout=0), schema_lease(
            root / "wiki/textbook-whole-record-evidence" / bundle.base.inventory / "authoring-worker.lock", timeout=0):
        require(whole.read(status_path) == status and status["stage"] == bundle.prefix + "-write"
            and status["reason_code"] == "wiki_stage_attempt_budget_exhausted_" + status["stage"],
            "same_exhausted_original_write")
        old = bundle.previous.folder / "guard/guard-progress.json"
        state = whole.read(old)
        require(state["state"] == "worker_exited" and state["returncode"] == 1
            and not writing.identity_live(state), "actual_previous_graph_guard_failed_and_exited")
        bundle.paths.add(old)
        bundle.paths.update(bundle.job.root.glob("failures/*.json"))
        prior.require_unused(bundle)
        require(not bundle.folder.exists(), "single_explicit_original_tail_registration")
        fresh.retain(bundle.folder / "execution.json", execution(bundle))
    return {"state": "finite_original_graph_tail_prepared", "writer": key,
        "verified_prefix_units": bundle.count, "total_units": len(bundle.units),
        "remaining_units": len(bundle.units) - bundle.count, "additional_model_roles": 0, "model_calls": 0}


def load(root, key):
    root, folder = Path(root).resolve(), folder_for(root, key)
    saved = whole.read(folder / "execution.json")
    require(saved["policy"] == POLICY and saved["writer"] == key and saved["code"] == bindings(), "frozen_code")
    for name, checksum in saved["files"].items():
        path = (root / name).resolve()
        require(path.is_relative_to(root) and whole.sha(path.read_bytes()) == checksum, "immutable_complete_original_evidence")
    bundle = inputs(root, key, saved["verified_prefix_units"])
    bundle.old_status = saved["original_status"]
    bundle.paths = {root / name for name in saved["files"]}
    require(execution(bundle) == saved, "same_entire_original_tail_registration")
    bundle.contract = saved
    return bundle


def unit_bundle(base, job, number, context, kwargs):
    require(base.count <= number < len(base.units) and set(kwargs) == {"evidence_packet"}, "only_declared_original_tail_unit")
    unit = base.units[number]
    require(list(context["assigned_points"]) == unit["point_ids"]
        and kwargs["evidence_packet"] == writing.composition.original_packet(unit["point_ids"], base.working, base.inventory),
        "complete_original_unit_and_packet")
    bundle = copy.copy(base)
    bundle.job, bundle.count, bundle.prefix, bundle.context = job, number, unit["id"], context
    bundle.scope = fresh.continuation.scope_for(unit["point_ids"], base.working, base.inventory,
        context, kwargs["evidence_packet"])
    bundle.folder = base.folder / "units" / unit["id"]
    bundle.paths = set(job.reader.paths)
    prior.failed_writes(bundle)
    return bundle


def unit_execution(base, bundle):
    return {"policy": POLICY, "writer": bundle.job.key, "tail_execution_sha256": digest(base.contract),
        "section": bundle.prefix, "number": bundle.count, "candidate_sha256": digest(bundle.candidate),
        "context_sha256": digest(bundle.context), "history": bundle.history,
        "observed_graph_defects": bundle.defects,
        "files": {p.relative_to(bundle.root).as_posix(): whole.sha(p.read_bytes()) for p in sorted(bundle.paths)},
        "original_budgets_reset": False, "additional_model_roles": 0,
        "mandatory_original_repair": True, "reader_publication_allowed": False}


def original_roles(base, bundle, *, read_only):
    path = bundle.folder / "execution.json"
    if not read_only:
        require(not path.exists(), "single_original_unit_registration_without_retry")
        prior.require_unused(bundle)
    bundle.contract = unit_execution(base, bundle)
    fresh.retain(path, bundle.contract, read_only=read_only)
    bundle.paths.add(path)
    # Build before installing: the parent factory itself calls the original one.
    cls, previous = parent_type(base, read_only=read_only), prior.original.job_type
    prior.original.job_type = lambda *args, **kwargs: cls
    try:
        return prior.original_roles(bundle, read_only=read_only)
    finally:
        prior.original.job_type = previous


def tail_type(base, registry, *, read_only):
    parent = parent_type(base, read_only=read_only)
    positions = {unit["id"]: n for n, unit in enumerate(base.units) if n >= base.count}

    class OriginalTailWriter(parent):
        def __init__(self, *args):
            super().__init__(*args)
            self.reader.paths.add(base.folder / "execution.json")

        def stage(self, name, prompt, schema, context, validate, **kwargs):
            match = re.fullmatch(fresh.PATTERN, name)
            if not match or match[1] not in positions:
                return super().stage(name, prompt, schema, context, validate, **kwargs)
            prefix, role = match[1], match[2]
            if prefix not in registry and role == "write":
                path = base.folder / "units" / prefix / "execution.json"
                if positions[prefix] != base.count and not path.exists():
                    try:
                        return super().stage(name, prompt, schema, context, validate, **kwargs)
                    except ValueError as exc:
                        require(not read_only and str(exc) == "wiki_stage_attempt_budget_exhausted_" + name,
                            "only_original_graph_exhaustion_dispatch_no_other_retry")
                bundle = unit_bundle(base, self, positions[prefix], context, kwargs)
                result = original_roles(base, bundle, read_only=read_only)
                registry[prefix] = (bundle, result)
                self.reader.paths.update(bundle.paths | {bundle.folder / "original-role-result.json"})
                self.reader.paths.update(base.root / name for name in result["files"])
            if prefix in registry:
                bundle, result = registry[prefix]
                value, path = prior.mapped.derived_stage(bundle, result, name, prompt, schema, context,
                    validate, kwargs, read_only=read_only)
                self.reader.paths.add(path)
                return value
            return super().stage(name, prompt, schema, context, validate, **kwargs)

        def progress(self, state, stage, **extra):
            count, total = self.finished_sections, self.section_count
            self.finished_sections, self.section_count = max(count, base.count), max(total, len(base.units))
            try:
                super().progress(state, stage, **extra)
            finally:
                self.finished_sections, self.section_count = count, total

    return OriginalTailWriter


@contextmanager
def runtime_issues(base, registry):
    previous = writing.final_issues

    def combined(inventory, private, existing):
        require(digest(inventory) == digest(base.inventory), "same_original_whole_inventory")
        return [*previous(inventory, private, existing), *[{
            "kind": "original_tail_invalid_graph_requires_complete_source_and_editorial_review",
            "section": prefix, "candidate_sha256": digest(bundle.candidate),
            "observed_graph_defects": bundle.defects, "adapter_graph_changes": 0}
            for prefix, (bundle, _) in sorted(registry.items())]]

    writing.final_issues = combined
    try:
        yield
    finally:
        writing.final_issues = previous


def worker(root, key):
    bundle = load(root, key)
    guard.checkpoint(bundle.root)
    require(whole.read(bundle.job.root / "progress.json") == bundle.old_status, "same_original_stopped_writer")
    with schema_lease(bundle.folder / "worker.lock", timeout=0):
        require(not (bundle.folder / "started.json").exists(), "single_tail_worker_no_automatic_retry")
        restored = whole.read(bundle.folder / "independent-input-restore.json")
        require(restored["state"] == "original_graph_tail_inputs_independently_restored"
            and restored["writer"] == key and restored["original_root_reads"] == restored["model_calls"] == 0,
            "actual_independent_inputs_restored_before_original_roles")
        fresh.retain(bundle.folder / "started.json", {"writer": key, "started": time.time()})
        registry = {}
        with reads.bounded_ledger_reads(writing), prior.runtime_issues(bundle.previous), runtime_issues(bundle, registry), fresh.installed(
                tail_type(bundle, registry, read_only=False)):
            result = writing.run(bundle.root, bundle.base.inventory, bundle.base.ledger, bundle.tokenizer)
        fresh.retain(bundle.folder / "result.json", result)
        return result


def verify(root, key):
    bundle = load(root, key)
    state = whole.read(bundle.folder / "guard/guard-progress.json")
    require(state["state"] == "worker_exited" and state["returncode"] == 0 and not writing.identity_live(state),
        "actual_complete_tail_guard_successful_exit")
    registry, previous = {}, fresh.job_type
    fresh.job_type = lambda value, *, read_only: tail_type(bundle, registry, read_only=read_only)
    try:
        with reads.bounded_ledger_reads(writing), prior.runtime_issues(bundle.previous), runtime_issues(bundle, registry):
            result = fresh.reconstruct(bundle.root, bundle.base.inventory, bundle.base.ledger, bundle.tokenizer)
    finally:
        fresh.job_type = previous
    result["proof"].update(original_graph_tail_sha256=digest(bundle.contract),
        prior_graph_continuation_sha256=digest(bundle.previous.contract), adapter_graph_changes=0)
    return result


def run(root, key):
    root, folder = Path(root).resolve(), folder_for(root, key)
    require(not (folder / "guard").exists(), "single_guard_no_automatic_retry")
    with schema_lease(root / "wiki/full-course-batch-worker.lock", timeout=0):
        (folder / "guard").mkdir()
        return guard.run(root, folder / "guard", [sys.executable, "-m", __spec__.name,
            "--root", str(root), "--writer", key, "worker"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--writer", required=True)
    parser.add_argument("command", choices=["prepare", "load", "run", "worker", "verify"])
    args = parser.parse_args()
    try:
        if args.command == "verify":
            actual = verify(args.root, args.writer)
            result = {**actual["summary"], "snapshot": str(fresh.snapshot.save(args.root, actual))}
        elif args.command == "load":
            bundle = load(args.root, args.writer)
            result = {"state": "original_graph_tail_inputs_authenticated", "writer": args.writer,
                "verified_prefix_units": bundle.count, "total_units": len(bundle.units), "model_calls": 0}
        else:
            result = globals()[args.command](args.root, args.writer)
        print(json.dumps(result))
    except BaseException as exc:
        result = whole.failure_status(exc)
        folder = folder_for(args.root, args.writer)
        if folder.exists():
            atomic_json(folder / ("failure-" + str(time.time_ns()) + ".json"), result)
        print(json.dumps(result))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
