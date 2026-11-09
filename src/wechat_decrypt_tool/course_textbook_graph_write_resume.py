"""Spend only unused original review/repair roles after a failed graph write.

The complete failed candidates remain untrusted and immutable. The adapter
never fixes graph meanings, creates an accepted write cache or refreshes an
attempt budget. Earlier accepted units and the recovered original plan replay.
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
from types import SimpleNamespace

import jsonschema

from . import course_textbook_one_based_writer as original
from . import course_textbook_corpus_index_review as indexed
from . import course_textbook_point_mapped_resume as mapped
from .course_knowledge import atomic_json, schema_lease
from .course_textbook_article import validate_graph

fresh, writing, whole = original.fresh, original.writing, original.whole
digest, guard, reads = original.digest, original.guard, original.reads
DIRECTORY = "graph-write-continuations"
POLICY = "original-unused-graph-write-review-mandatory-repair-v1"
GRAPH_ERROR = "textbook_diagram_unknown_or_unconnected_node"


def require(ok, code):
    if not ok:
        raise ValueError("requires_graph_write_resume_" + code)


def folder_for(root, key):
    require(isinstance(key, str) and re.fullmatch(r"[a-f0-9]{32}", key), "writer_id")
    return Path(root).resolve() / "wiki" / DIRECTORY / key


def bindings():
    return {"original": original.bindings(), "modules": {
        Path(module.__file__).stem: whole.sha(Path(module.__file__).read_bytes())
        for module in (sys.modules[__name__], indexed, mapped)}}


def graph_defects(candidate):
    """Measure defects without removing, renaming or connecting any node."""
    result = []
    for index, graph in enumerate(candidate["diagrams"]):
        try:
            validate_graph({key: value for key, value in graph.items() if key != "citations"})
        except ValueError as exc:
            require(str(exc) == GRAPH_ERROR, "only_observed_endpoint_defect")
            ids = [node["id"] for node in graph["nodes"]]
            endpoints = {edge[key] for edge in graph["edges"] for key in ("from", "to")}
            result.append({"diagram_index": index, "reason_code": GRAPH_ERROR,
                "nodes": len(ids), "edges": len(graph["edges"]),
                "unknown_endpoints": len(endpoints - set(ids)),
                "unconnected_nodes": len(set(ids) - endpoints)})
    require(bool(result), "actual_invalid_graph_not_generic_retry")
    return result


def inputs(root, key, count):
    parent = original.load(root, key)
    job = original.job_type(parent, read_only=True)(writing.TextbookStore(parent.root),
        whole.load(parent.root, parent.inventory), parent.working, parent.tokenizer)
    inventory, working, reader = job.inventory, parent.working, job.reader
    require(reader.saved("private-working-inputs") == working, "original_complete_working_inputs")
    plan = writing.organize(job, working)
    units = writing.writing_units(plan, working, inventory, job.tokenizer)
    require(reader.saved("concept-plan") == plan and reader.saved("writing-allocation") == units,
        "original_complete_plan_and_allocation")
    require(type(count) is int and 0 < count < len(units), "actual_successful_prefix")
    sections, exclusions, issues = fresh.snapshot.replay_units(reader, units, plan, count)
    unit = units[count]
    context = fresh.snapshot.refs.unit_context(unit, count, units, plan, sections, working, inventory)
    packet = writing.composition.original_packet(unit["point_ids"], working, inventory)
    scope = fresh.continuation.scope_for(unit["point_ids"], working, inventory, context, packet)
    paths = set(reader.paths)
    paths.update(parent.root / name for name in parent.execution["original_files"])
    paths.update([parent.folder / "execution.json", parent.folder / "recovery.json"])
    for path in job.root.glob("response-*.json"):
        if whole.read(path)["stage"] in reader.stages:
            paths.add(path)
    return SimpleNamespace(root=parent.root, base=parent, job=job, inventory=inventory, working=working,
        tokenizer=parent.tokenizer, count=count, units=units, plan=plan, issues=issues, exclusions=exclusions,
        prefix=unit["id"], context=context, scope=scope, paths=paths, folder=folder_for(parent.root, key))


def failed_writes(bundle):
    name, ids = bundle.prefix + "-write", list(bundle.context["assigned_points"])
    base_schema = writing.section_schema(ids, bundle.working)
    schema = indexed.tagged_schema(base_schema, bundle.scope["expanded"], bundle.working["pending_source_cards"])
    with indexed.future_format():
        request = fresh.continuation.draft.capture_request(bundle.job, name, writing.author.WRITE_PROMPT,
            schema, bundle.context, bundle.scope["expanded"])
    prospective = bundle.job.root / (name + "-prospective-request.json")
    require(whole.read(prospective) == {"policy": fresh.POLICY,
        "execution_sha256": digest(bundle.base.contract), "stage": name,
        "original_schema_sha256": digest(base_schema), "actual_schema_sha256": digest(schema),
        "original_context_sha256": digest(bundle.context), "actual_context_sha256": digest(bundle.context),
        "original_packet_sha256": digest(bundle.scope["original"]),
        "actual_packet_sha256": digest(bundle.scope["expanded"]),
        "original_budgets_reset": False, "reader_publication_allowed": False},
        "exact_original_prospective_write")
    paths = sorted((bundle.job.root / "attempts").glob(name + "-[0-9][0-9].json"))
    require([path.name for path in paths] == [name + "-01.json", name + "-02.json"]
        and not (bundle.job.root / (name + ".json")).exists(), "two_exhausted_writes_without_cache")
    receipts = [(path, whole.read(path)) for path in bundle.job.root.glob("response-*.json")]
    bundle.paths.add(prospective)
    history, previous = [], ""
    for number, path in enumerate(paths, 1):
        row = whole.read(path)
        messages = [{"role": "system", "content": request["prompt"]}, {"role": "user", "content": json.dumps(
            {**bundle.context, "previous_validation_failure": previous}, ensure_ascii=False)}]
        require(row["stage"] == name and row["attempt"] == number and not row.get("accepted_structure")
            and row["input_sha256"] == request["binding"] and row["request_sha256"] == digest(messages)
            and row["model_digest"] == bundle.job.models["qwen3.5:9b"]
            and row.get("error_type") == "ValueError" and row["validation_error"] == "ValueError",
            "original_failed_request_identity")
        whole.editor.e.verify_response(bundle.job.root, name, row)
        matches = [path for path, receipt in receipts if receipt["stage"] == name
            and receipt["request_sha256"] == row["request_sha256"]
            and receipt.get("schema_sha256") == digest(schema)
            and receipt["response"]["message"]["content"] == row["raw_response"]
            and receipt["metrics"] == row["model_metrics"]]
        require(len(matches) == 1 and row["model_metrics"]["done_reason"] == "stop", "complete_actual_wire_receipt")
        raw = json.loads(row["raw_response"])
        jsonschema.validate(raw, request["schema"])
        candidate = copy.deepcopy(raw)
        require(candidate.pop("paragraph_count") == len(candidate["paragraphs"]), "actual_paragraph_count")
        metadata = graph_defects(candidate)
        try:
            indexed.tagged_candidate(raw, ids, bundle.working, bundle.inventory, bundle.scope)
        except ValueError as exc:
            require(str(exc) == GRAPH_ERROR, "reconstructed_same_actual_graph_failure")
        else:
            require(False, "failed_candidate_still_rejected")
        history.append({"attempt": path.name, "raw_sha256": whole.sha(row["raw_response"].encode()),
            "observed_graph_defects": metadata})
        bundle.paths.update([path, *matches])
        previous = row["validation_error"]
    # Select the final original attempt as untrusted, never an accepted write.
    bundle.candidate, bundle.history, bundle.defects = candidate, history, metadata


def require_unused(bundle):
    names = [bundle.prefix + suffix for suffix in ("-review", "-repair", "-repair-review")]
    names += [unit["id"] + "-" + role for unit in bundle.units[bundle.count + 1:] for role in fresh.continuation.ROLES]
    require(all(not list((bundle.job.root / "attempts").glob(name + "-[0-9][0-9].json"))
        and not list(bundle.job.root.glob(name + "*.json")) for name in names)
        and not list(bundle.job.root.glob("reading-*.json")), "all_remaining_original_roles_unused")


def execution(bundle):
    return {"policy": POLICY, "writer": bundle.job.key, "code": bindings(),
        "inventory": bundle.base.inventory, "ledger": bundle.base.ledger,
        "original_continuation_sha256": digest(bundle.base.execution),
        "original_status": bundle.old_status, "verified_prefix_units": bundle.count,
        "total_units": len(bundle.units), "section": bundle.prefix,
        "future_units": [unit["id"] for unit in bundle.units[bundle.count + 1:]],
        "candidate_sha256": digest(bundle.candidate), "context_sha256": digest(bundle.context),
        "selected_attempt": bundle.history[-1]["attempt"], "history": bundle.history,
        "files": {path.relative_to(bundle.root).as_posix(): whole.sha(path.read_bytes()) for path in sorted(bundle.paths)},
        "original_budgets_reset": False, "new_model_roles": 0, "mandatory_original_repair": True,
        "candidate_is_untrusted": True, "adapter_graph_changes": 0,
        "reader_publication_allowed": False, "source_deletion_allowed": False}


def prepare(root, key):
    root = Path(root).resolve()
    guard.checkpoint(root)
    status_path = root / "wiki/textbook-whole-authoring-jobs" / key / "progress.json"
    status = whole.read(status_path)
    require(status["state"] == "stopped" and not writing.identity_live(status), "old_writer_stopped_and_exited")
    bundle = inputs(root, key, status["completed_sections"])
    bundle.old_status = status
    with schema_lease(root / "wiki/full-course-batch-worker.lock", timeout=0), schema_lease(
            root / "wiki/textbook-whole-record-evidence" / bundle.base.inventory / "authoring-worker.lock", timeout=0):
        require(status == whole.read(status_path) and status["stage"] == bundle.prefix + "-write"
            and status["reason_code"] == "wiki_stage_attempt_budget_exhausted_" + status["stage"], "same_exhausted_write")
        old_guard_path = bundle.base.folder / "guard/guard-progress.json"
        old_guard = whole.read(old_guard_path)
        require(old_guard["state"] == "worker_exited" and old_guard["returncode"] == 1
            and not writing.identity_live(old_guard), "actual_old_guard_failed_and_exited")
        bundle.paths.add(old_guard_path)
        bundle.paths.update(bundle.job.root.glob("failures/*.json"))
        failed_writes(bundle)
        require_unused(bundle)
        require(not bundle.folder.exists(), "single_explicit_registration_no_retry")
        fresh.retain(bundle.folder / "execution.json", execution(bundle))
    return {"state": "original_graph_write_prepared_for_unused_roles", "writer": key,
        "verified_prefix_units": bundle.count, "total_units": len(bundle.units),
        "model_calls": 0, "adapter_graph_changes": 0, "reader_publication_allowed": False}


def contract(root, key):
    root, folder = Path(root).resolve(), folder_for(root, key)
    saved = whole.read(folder / "execution.json")
    require(saved["policy"] == POLICY and saved["writer"] == key and saved["code"] == bindings(), "frozen_execution_code")
    for name, expected in saved["files"].items():
        path = (root / name).resolve()
        require(path.is_relative_to(root) and whole.sha(path.read_bytes()) == expected, "immutable_original_evidence")
    return root, folder, saved


def load(root, key):
    root, _, saved = contract(root, key)
    bundle = inputs(root, key, saved["verified_prefix_units"])
    bundle.old_status = saved["original_status"]
    failed_writes(bundle)
    bundle.paths = {root / name for name in saved["files"]}
    require(execution(bundle) == saved, "same_complete_original_history")
    bundle.contract = saved
    return bundle


def original_roles(bundle, *, read_only):
    job = original.job_type(bundle.base, read_only=read_only)(writing.TextbookStore(bundle.root),
        bundle.inventory, bundle.working, bundle.tokenizer)
    job.finished_sections, job.section_count = bundle.count, len(bundle.units)
    stage = next(cls.stage for cls in type(job).__mro__ if cls.__name__ == "FreshJob")
    ids = list(bundle.context["assigned_points"])
    common = {**bundle.context, "candidate_is_untrusted": True, "original_repair_is_mandatory": True,
        "observed_structural_defects": bundle.defects, "adapter_graph_changes": 0}

    def audit(candidate, suffix):
        schema = writing.review_schema(candidate, ids)
        return stage(job, bundle.prefix + suffix, writing.author.REVIEW_PROMPT, schema,
            {**common, "candidate": candidate}, lambda value: writing.author.validate_review(value, schema),
            evidence_packet=bundle.scope["original"])

    initial = audit(bundle.candidate, "-review")
    with indexed.future_format():
        candidate = stage(job, bundle.prefix + "-repair", writing.author.REPAIR_PROMPT,
            writing.section_schema(ids, bundle.working), {**common, "candidate": bundle.candidate, "review": initial},
            lambda value: writing.validate_section(value, ids, bundle.working, bundle.inventory),
            evidence_packet=bundle.scope["original"])
    final = audit(candidate, "-repair-review")
    job.reader.paths.update(bundle.paths | {bundle.folder / "execution.json"})
    job.reader.paths.update(path for path in job.root.glob("response-*.json")
        if whole.read(path)["stage"].startswith(bundle.prefix + "-"))
    result = {"policy": POLICY, "candidate": candidate, "initial_review": initial, "final_review": final,
        "final_checks_passed": all(writing.author.verdicts(final)), "original_budgets_reset": False,
        "files": {path.relative_to(bundle.root).as_posix(): whole.sha(path.read_bytes()) for path in sorted(job.reader.paths)},
        "reader_publication_allowed": False}
    fresh.retain(bundle.folder / "original-role-result.json", result, read_only=read_only)
    return result


def resumed_type(bundle, result, *, read_only):
    base = original.job_type(bundle.base, read_only=read_only)

    class GraphResumedWriter(base):
        def __init__(self, *args):
            super().__init__(*args)
            require(self.key == bundle.job.key, "same_original_writer_identity")
            self.reader.paths.update(bundle.paths | {bundle.folder / "execution.json", bundle.folder / "original-role-result.json"})
            self.reader.paths.update(bundle.root / name for name in result["files"])

        def stage(self, name, prompt, schema, context, validate, **kwargs):
            match = re.fullmatch(fresh.PATTERN, name)
            if match and match[1] == bundle.prefix:
                value, path = mapped.derived_stage(bundle, result, name, prompt, schema, context, validate,
                    kwargs, read_only=read_only)
                self.reader.paths.add(path)
                return value
            return super().stage(name, prompt, schema, context, validate, **kwargs)

        def progress(self, state, stage, **extra):
            old_count, old_total = self.finished_sections, self.section_count
            self.finished_sections = max(old_count, bundle.count)
            self.section_count = max(old_total, len(bundle.units))
            try:
                super().progress(state, stage, **extra)
            finally:
                self.finished_sections, self.section_count = old_count, old_total

    return GraphResumedWriter


@contextmanager
def runtime_issues(bundle):
    previous = writing.final_issues

    def combined(inventory, private, existing):
        require(digest(inventory) == digest(bundle.inventory), "same_original_full_inventory")
        return [*previous(inventory, private, existing), {
            "kind": "original_invalid_graph_write_requires_complete_source_and_editorial_review",
            "section": bundle.prefix, "candidate_sha256": digest(bundle.candidate),
            "observed_graph_defects": bundle.defects, "adapter_graph_changes": 0}]

    writing.final_issues = combined
    try:
        yield
    finally:
        writing.final_issues = previous


def worker(root, key):
    bundle = load(root, key)
    guard.checkpoint(bundle.root)
    require(whole.read(bundle.job.root / "progress.json") == bundle.old_status, "same_original_stopped_worker")
    with schema_lease(bundle.folder / "worker.lock", timeout=0):
        require(not (bundle.folder / "started.json").exists(), "single_worker_no_automatic_retry")
        fresh.retain(bundle.folder / "started.json", {"writer": key, "started": time.time()})
        with schema_lease(bundle.root / "wiki/textbook-whole-record-evidence" / bundle.base.inventory
                / "authoring-worker.lock", timeout=0):
            result = original_roles(bundle, read_only=False)
        with reads.bounded_ledger_reads(writing), runtime_issues(bundle), fresh.installed(resumed_type(bundle, result, read_only=False)):
            output = writing.run(bundle.root, bundle.base.inventory, bundle.base.ledger, bundle.tokenizer)
        fresh.retain(bundle.folder / "result.json", output)
        return output


def verify(root, key):
    bundle = load(root, key)
    state = whole.read(bundle.folder / "guard/guard-progress.json")
    require(state["state"] == "worker_exited" and state["returncode"] == 0 and not writing.identity_live(state),
        "actual_successful_guard_exit")
    result = original_roles(bundle, read_only=True)
    previous = fresh.job_type
    fresh.job_type = lambda value, *, read_only: resumed_type(bundle, result, read_only=read_only)
    try:
        with reads.bounded_ledger_reads(writing), runtime_issues(bundle):
            rebuilt = fresh.reconstruct(bundle.root, bundle.base.inventory, bundle.base.ledger, bundle.tokenizer)
    finally:
        fresh.job_type = previous
    rebuilt["proof"].update(graph_continuation_sha256=digest(bundle.contract), adapter_graph_changes=0)
    return rebuilt


def run(root, key):
    root, folder, _ = contract(root, key)
    require(not (folder / "guard/guard-progress.json").exists(), "single_guard_no_automatic_retry")
    with schema_lease(root / "wiki/full-course-batch-worker.lock", timeout=0):
        return guard.run(root, folder / "guard", [sys.executable, "-m", __spec__.name,
            "--root", str(root), "--writer", key, "worker"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--writer", required=True)
    parser.add_argument("command", choices=["prepare", "load", "worker", "run", "verify"])
    args = parser.parse_args()
    try:
        if args.command == "load":
            bundle = load(args.root, args.writer)
            result = {"state": "original_graph_write_continuation_authenticated", "writer": args.writer,
                "verified_prefix_units": bundle.count, "model_calls": 0}
        elif args.command == "verify":
            value = verify(args.root, args.writer)
            result = {**value["summary"], "snapshot": str(fresh.snapshot.save(args.root, value))}
        else:
            result = globals()[args.command](args.root, args.writer)
        print(json.dumps(result))
    except BaseException as exc:
        failure = whole.failure_status(exc)
        folder = folder_for(args.root, args.writer)
        if folder.exists():
            atomic_json(folder / ("failure-" + str(time.time_ns()) + ".json"), failure)
        print(json.dumps(failure))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
