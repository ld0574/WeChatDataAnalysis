"""Use original unused roles after a corpus writer padded its paragraph array.

Original failed responses remain untrusted and byte-identical. A supplementary
execution binds real wire receipts, the authenticated prefix and prospective
format; no accepted write cache, extra model role or retry budget is created.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import re
import sys
import time
from types import SimpleNamespace

import jsonschema

from . import course_textbook_corpus_wait_resume as original
from .course_knowledge import atomic_json, schema_lease

fresh, writing, whole = original.fresh, original.writing, original.whole
visible, snapshot, guard = fresh.visible, fresh.snapshot, original.original.private_guard
digest = original.digest
DIRECTORY = "wiki/corpus-structural-resumes"
POLICY = "original-corpus-unused-roles-with-nonempty-variable-paragraphs-v1"
NOTICE = """
本角色允许 2–12 段实际完整正文，不再要求固定十二段；按知识逻辑充分讲解。
不得用空白、重复段落或机械拆句凑数，段数不证明覆盖。每项有效教学仍必须充分解释。
coverage.paragraphs 从零编号，必须指向本次实际输出的数组位置，不是上限十二的占位。
若原文有效教学不能完整写入，不能通过省略教学、空段或虚假映射来满足格式。
"""


def require(ok, code):
    if not ok:
        raise ValueError("requires_corpus_structural_" + code)


def defects(raw):
    paragraphs = [p["text"].strip() for p in raw.get("paragraphs", [])]
    return {"blank_paragraph_indices": [i for i, text in enumerate(paragraphs) if not text],
        "duplicate_paragraph_groups": [[i for i, item in enumerate(paragraphs) if item == text]
            for text in dict.fromkeys(paragraphs) if text and paragraphs.count(text) > 1]}


def section_schema(schema, packet, pending):
    # Retain the original teaching/disposition rules before changing paragraph length.
    result = original.bounded.mapped.ORIGINAL_SCHEMA(schema, packet, pending)

    def visit(node):
        props = node.get("properties", {})
        paragraphs = props.get("paragraphs", {})
        if paragraphs.get("type") == "array" and "coverage" in props:
            paragraphs.update(minItems=2, maxItems=12, uniqueItems=True)
            paragraphs["items"]["properties"]["text"].update(minLength=1, pattern=r"\S")

            def bound(row):
                array = row.get("properties", {}).get("paragraphs")
                if array is not None:
                    array["items"].update(minimum=0, maximum=11)
                for branch in row.get("oneOf", []):
                    bound(branch)

            for row in props["coverage"]["properties"].values():
                bound(row)
        for branch in node.get("oneOf", []):
            visit(branch)

    visit(result)
    return result


def citation_candidate(raw, ids, working, inventory, scope):
    errors = defects(raw)
    require(not errors["blank_paragraph_indices"], "nonempty_actual_paragraphs")
    require(not errors["duplicate_paragraph_groups"], "distinct_actual_paragraphs")
    for row in raw.get("coverage", {}).values():
        require(all(0 <= i < len(raw.get("paragraphs", [])) for i in row["paragraphs"]),
            "mapping_indices_within_actual_array_length_" + str(len(raw.get("paragraphs", []))))
    with visible.packet_scope(scope):
        try:
            visible.ORIGINAL_VALIDATE(raw, ids, working, inventory)
        except ValueError as exc:
            # Only a primary-reference metadata failure permits a citation proposal.
            if str(exc) != fresh.continuation.proposal.ERROR:
                raise
            return fresh.continuation.proposal.propose(raw, ids, working, inventory, scope)
    return raw, {}


@contextmanager
def variable_format():
    old_schema, old_candidate = fresh.section_schema, fresh.citation_candidate
    prompts = {role: writing.composition.PROMPTS[role]
        for role in (writing.author.WRITE_PROMPT, writing.author.REPAIR_PROMPT)}
    fresh.section_schema, fresh.citation_candidate = section_schema, citation_candidate
    try:
        for role, text in prompts.items():
            writing.composition.PROMPTS[role] = text + NOTICE
        yield
    finally:
        fresh.section_schema, fresh.citation_candidate = old_schema, old_candidate
        writing.composition.PROMPTS.update(prompts)


def inputs(root, batch, inventory, ledger, count):
    root, tokenizer, job, contract, extra, parent = original.first_bound(root, batch, inventory, ledger)
    inv, working = writing.load_inputs(root, inventory, ledger, tokenizer)
    replay = original.first_type(contract, extra, parent, read_only=True)(writing.TextbookStore(root), inv, working, tokenizer)
    reader = replay.reader
    require(reader.saved("private-working-inputs") == working, "same_complete_working_inputs")
    original_contract = root / "wiki/textbook-keyed-jobs" / ledger / "whole-source-authoring-execution.json"
    require(whole.read(original_contract) == {"inventory": inventory, "ledger_sha256": digest(working), "job": job.key},
        "original_writer_identity_and_budget")
    reader.paths.update([original_contract, job.root / "manifest.json"])
    plan = writing.organize(replay, working)
    units = writing.writing_units(plan, working, inv, replay.tokenizer)
    require(reader.saved("concept-plan") == plan and reader.saved("writing-allocation") == units,
        "original_plan_and_complete_allocation")
    require(type(count) is int and 0 < count < len(units), "actual_prefix_before_failed_unit")
    sections, _, issues = snapshot.replay_units(reader, units, plan, count)
    unit = units[count]
    context = snapshot.refs.unit_context(unit, count, units, plan, sections, working, inv)
    packet = visible.ORIGINAL_PACKET(unit["point_ids"], working, inv)
    scope = fresh.continuation.scope_for(unit["point_ids"], working, inv, context, packet)
    for path in job.root.glob("response-*.json"):
        if whole.read(path)["stage"] in reader.stages:
            reader.paths.add(path)
    return SimpleNamespace(root=root, tokenizer=tokenizer, job=job, batch=batch, inventory=inv, working=working,
        original=contract, extra=extra, parent=parent, count=count, units=units, plan=plan, issues=issues,
        prefix=unit["id"], context=context, scope=scope, paths=reader.paths,
        folder=root / DIRECTORY / job.key)


def failed_writes(bundle):
    name, ids = bundle.prefix + "-write", list(bundle.context["assigned_points"])
    schema = original.bounded.bounded_schema(writing.section_schema(ids, bundle.working),
        bundle.scope["expanded"], bundle.working["pending_source_cards"])
    with original.bounded.bounded_format():
        request = fresh.continuation.draft.capture_request(bundle.job, name, writing.author.WRITE_PROMPT,
            schema, bundle.context, bundle.scope["expanded"])
    path = bundle.job.root / (name + "-prospective-request.json")
    require(whole.read(path) == {"policy": fresh.POLICY, "execution_sha256": digest(bundle.original), "stage": name,
        "original_schema_sha256": digest(writing.section_schema(ids, bundle.working)),
        "actual_schema_sha256": digest(schema), "original_context_sha256": digest(bundle.context),
        "actual_context_sha256": digest(bundle.context), "original_packet_sha256": digest(bundle.scope["original"]),
        "actual_packet_sha256": digest(bundle.scope["expanded"]), "original_budgets_reset": False,
        "reader_publication_allowed": False}, "real_failed_request_and_decoding_schema")
    paths = sorted((bundle.job.root / "attempts").glob(name + "-[0-9][0-9].json"))
    require([p.name for p in paths] == [name + "-01.json", name + "-02.json"]
        and not (bundle.job.root / (name + ".json")).exists(), "two_exhausted_writes_without_accepted_cache")
    receipts = [(p, whole.read(p)) for p in bundle.job.root.glob("response-*.json")]
    prior, history = "", []
    bundle.paths.add(path)
    for number, path in enumerate(paths, 1):
        row = whole.read(path)
        messages = [{"role": "system", "content": request["prompt"]}, {"role": "user", "content": json.dumps(
            {**bundle.context, "previous_validation_failure": prior}, ensure_ascii=False)}]
        require(row["stage"] == name and row["attempt"] == number and not row.get("accepted_structure")
            and row["input_sha256"] == request["binding"] and row["request_sha256"] == digest(messages)
            and row["model_digest"] == bundle.job.models["qwen3.5:9b"] and row.get("error_type") == "ValueError"
            and row["validation_error"] == "requires_fresh_authoring_only_primary_citation_metadata_proposed",
            "exact_original_failed_attempt")
        whole.editor.e.verify_response(bundle.job.root, name, row)
        matches = [p for p, r in receipts if r["stage"] == name and r["request_sha256"] == row["request_sha256"]
            and r.get("schema_sha256") == digest(schema) and r["response"]["message"]["content"] == row["raw_response"]
            and r["metrics"] == row["model_metrics"]]
        require(len(matches) == 1 and row["model_metrics"]["done_reason"] == "stop", "complete_real_gpu_wire_response")
        raw = json.loads(row["raw_response"])
        jsonschema.validate(raw, schema)
        metadata = defects(raw)
        require(any(metadata.values()), "observed_padding_defect_not_generic_recovery")
        history.append({"attempt": path.name, "raw_sha256": digest(raw), "defects": metadata})
        bundle.paths.update([path, *matches])
        prior = row["validation_error"]
    bundle.candidate, bundle.history, bundle.defects = raw, history, metadata


def require_unused(bundle):
    names = [bundle.prefix + suffix for suffix in ("-review", "-repair", "-repair-review")]
    names += [unit["id"] + "-" + role for unit in bundle.units[bundle.count + 1:] for role in fresh.continuation.ROLES]
    require(all(not list((bundle.job.root / "attempts").glob(name + "-[0-9][0-9].json"))
        and not list(bundle.job.root.glob(name + "*.json")) for name in names)
        and not list(bundle.job.root.glob("reading-*.json")), "remaining_original_roles_unused")


def execution(bundle):
    return {"policy": POLICY, "batch": bundle.batch, "job": bundle.job.key, "inventory": digest(bundle.inventory),
        "ledger": bundle.working["job"], "prefix_units": bundle.count, "total_units": len(bundle.units),
        "section": bundle.prefix, "future_units": [u["id"] for u in bundle.units[bundle.count + 1:]],
        "original_execution_sha256": digest(bundle.original), "first_execution_sha256": digest(bundle.extra),
        "old_status_sha256": digest(bundle.old_status),
        "candidate_sha256": digest(bundle.candidate), "history": bundle.history,
        "context_sha256": digest(bundle.context), "module_sha256": whole.sha(Path(__file__).read_bytes()),
        "parent_code": original.bindings(), "format": "nonempty_distinct_2_to_12_actual_paragraphs",
        "files": {p.relative_to(bundle.root).as_posix(): whole.sha(p.read_bytes()) for p in sorted(bundle.paths)},
        "original_budgets_reset": False, "new_model_roles": 0, "mandatory_original_repair": True,
        "reader_publication_allowed": False, "source_deletion_allowed": False}


def prepare(root, batch, inventory, ledger):
    root = Path(root).resolve()
    _, _, job, _, _, _ = original.first_bound(root, batch, inventory, ledger)
    status = whole.read(job.root / "progress.json")
    require(status["state"] == "stopped" and not writing.identity_live(status), "old_writer_exited")
    bundle = inputs(root, batch, inventory, ledger, status["completed_sections"])
    bundle.old_status = status
    with schema_lease(root / "wiki/textbook-whole-record-evidence" / inventory / "authoring-worker.lock", timeout=0):
        require(status == whole.read(job.root / "progress.json") and status["stage"] == bundle.prefix + "-write"
            and status["reason_code"] == "wiki_stage_attempt_budget_exhausted_" + status["stage"], "actual_exhausted_write")
        failed_writes(bundle)
        require_unused(bundle)
        require(not (bundle.folder / "execution.json").exists(), "prepare_once_no_budget_refresh")
        bundle.paths.update(job.root.glob("failures/*.json"))
        old_guard = root / "wiki/full-course-batches" / batch / original.DIRECTORY / "item-00-authoring-guard/guard-progress.json"
        previous = whole.read(old_guard)
        require(previous["state"] == "worker_exited" and previous.get("returncode") == 1
            and not writing.identity_live(previous), "old_private_guard_failed_and_exited")
        bundle.paths.add(old_guard)
        fresh.retain(bundle.folder / "execution.json", execution(bundle))
        fresh.retain(bundle.folder / "old-status.json", status)
    return {"state": "padded_corpus_writer_prepared_for_unused_original_roles", "job": job.key,
        "verified_prefix_units": bundle.count, "total_units": len(bundle.units), "model_calls": 0}


def load(root, job):
    require(re.fullmatch(r"[a-f0-9]{32}", job) is not None, "job_id")
    root = Path(root).resolve()
    contract = whole.read(root / DIRECTORY / job / "execution.json")
    require(contract["policy"] == POLICY and contract["job"] == job
        and contract["module_sha256"] == whole.sha(Path(__file__).read_bytes())
        and contract["parent_code"] == original.bindings(), "frozen_resume_code")
    require(all(whole.sha((root / name).read_bytes()) == value for name, value in contract["files"].items()),
        "unchanged_original_evidence")
    bundle = inputs(root, contract["batch"], contract["inventory"], contract["ledger"], contract["prefix_units"])
    bundle.old_status = whole.read(bundle.folder / "old-status.json")
    failed_writes(bundle)
    bundle.paths = {root / name for name in contract["files"]}
    require(execution(bundle) == contract, "same_bound_original_history")
    bundle.contract = contract
    return bundle


def make_job(bundle, *, read_only):
    cls = original.first_type(bundle.original, bundle.extra, bundle.parent, read_only=read_only)
    return cls(writing.TextbookStore(bundle.root), bundle.inventory, bundle.working, bundle.tokenizer)


def original_roles(bundle, *, read_only):
    job = make_job(bundle, read_only=read_only)
    job.finished_sections, job.section_count = bundle.count, len(bundle.units)
    # Bypass only the old fixed-count wrapper for these unused roles.
    stage = next(cls.stage for cls in type(job).__mro__ if cls.__name__ == "FreshJob")
    ids = list(bundle.context["assigned_points"])
    common = {**bundle.context, "candidate_is_untrusted": True, "original_repair_is_mandatory": True,
        "observed_structural_defects": bundle.defects}

    def audit(candidate, suffix):
        schema = writing.review_schema(candidate, ids)
        return stage(job, bundle.prefix + suffix, writing.author.REVIEW_PROMPT, schema,
            {**common, "candidate": candidate}, lambda row: writing.author.validate_review(row, schema),
            evidence_packet=bundle.scope["original"])

    initial = audit(bundle.candidate, "-review")
    with variable_format():
        candidate = stage(job, bundle.prefix + "-repair", writing.author.REPAIR_PROMPT,
            writing.section_schema(ids, bundle.working), {**common, "candidate": bundle.candidate, "review": initial},
            lambda row: writing.validate_section(row, ids, bundle.working, bundle.inventory),
            evidence_packet=bundle.scope["original"])
    final = audit(candidate, "-repair-review")
    job.reader.paths.update(p for p in job.root.glob("response-*.json") if whole.read(p)["stage"].startswith(bundle.prefix + "-"))
    result = {"policy": POLICY, "candidate": candidate, "initial_review": initial, "final_review": final,
        "final_checks_passed": all(writing.author.verdicts(final)), "original_budgets_reset": False,
        "files": {p.relative_to(bundle.root).as_posix(): whole.sha(p.read_bytes()) for p in sorted(job.reader.paths)},
        "reader_publication_allowed": False}
    fresh.retain(bundle.folder / "original-role-result.json", result, read_only=read_only)
    return result


def resumed_type(bundle, result, *, read_only):
    base = original.first_type(bundle.original, bundle.extra, bundle.parent, read_only=read_only)
    original_stage = next(cls.stage for cls in base.__mro__ if cls.__name__ == "FreshJob")

    class ResumedJob(base):
        def __init__(self, *args):
            super().__init__(*args)
            self.reader.paths.update(bundle.paths | {bundle.folder / "execution.json", bundle.folder / "original-role-result.json"})
            self.reader.paths.update(bundle.root / p for p in result["files"])

        def stage(self, name, prompt, schema, context, validate, **kwargs):
            match = re.fullmatch(fresh.PATTERN, name)
            if match and match[1] == bundle.prefix:
                mapped = original.bounded.mapped
                value, path = mapped.derived_stage(bundle, result, name, prompt, schema, context, validate, kwargs, read_only=read_only)
                self.reader.paths.add(path)
                return value
            if match and match[1] in bundle.contract["future_units"] and match[2] in {"write", "repair"}:
                with variable_format():
                    return original_stage(self, name, prompt, schema, context, validate, **kwargs)
            return super().stage(name, prompt, schema, context, validate, **kwargs)

    return ResumedJob


def worker(root, key):
    bundle = load(root, key)
    guard.checkpoint(bundle.root)
    with schema_lease(bundle.folder / "worker.lock", timeout=0):
        require(not (bundle.folder / "started.json").exists(), "worker_once_no_automatic_retry")
        fresh.retain(bundle.folder / "started.json", bundle.job.identity)
        try:
            with schema_lease(bundle.root / "wiki/textbook-whole-record-evidence" / digest(bundle.inventory) / "authoring-worker.lock", timeout=0):
                result = original_roles(bundle, read_only=False)
            with runtime_issues(bundle), fresh.installed(resumed_type(bundle, result, read_only=False)):
                output = writing.run(bundle.root, digest(bundle.inventory), bundle.working["job"], bundle.tokenizer)
            fresh.retain(bundle.folder / "result.json", output)
            return output
        except BaseException as exc:
            atomic_json(bundle.folder / ("failure-" + str(time.time_ns()) + ".json"), whole.failure_status(exc))
            raise


def reconstruct(root, key, *, complete=True):
    bundle = load(root, key)
    result = original_roles(bundle, read_only=True)
    previous = fresh.job_type
    fresh.job_type = lambda contract, *, read_only: resumed_type(bundle, result, read_only=read_only)
    try:
        with runtime_issues(bundle):
            rebuilt = fresh.reconstruct(bundle.root, digest(bundle.inventory), bundle.working["job"], bundle.tokenizer, complete=complete)
    finally:
        fresh.job_type = previous
    rebuilt["proof"].update(policy=POLICY, supplemental_execution_sha256=digest(bundle.contract),
        supplemental_module_sha256=whole.sha(Path(__file__).read_bytes()))
    return rebuilt


@contextmanager
def runtime_issues(bundle):
    previous = writing.final_issues

    def issues(inventory, private, existing):
        require(digest(inventory) == digest(bundle.inventory), "same_full_original_inventory")
        return [*previous(inventory, private, existing), {
            "kind": "untrusted_padded_write_requires_complete_original_coverage",
            "section": bundle.prefix, "original_candidate_sha256": digest(bundle.candidate),
            "independent_review_does_not_prove_complete_source_coverage": True}]

    writing.final_issues = issues
    try:
        yield
    finally:
        writing.final_issues = previous


def guarded(root, key):
    bundle = load(root, key)
    result = guard.run(bundle.root, bundle.folder, [sys.executable, "-m", __spec__.name, "--root", str(bundle.root), "--job", key, "worker"])
    if result["state"] == "private_worker_exited":
        rebuilt = reconstruct(root, key)
        return {**rebuilt["summary"], "snapshot": str(snapshot.save(root, rebuilt))}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--batch")
    parser.add_argument("--inventory")
    parser.add_argument("--ledger")
    parser.add_argument("--job")
    parser.add_argument("command", choices=["prepare", "load", "worker", "guard", "prefix", "verify"])
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = prepare(args.root, args.batch, args.inventory, args.ledger)
        elif args.command == "load":
            bundle = load(args.root, args.job)
            result = {"state": "original_corpus_padding_failure_authenticated", "job": bundle.job.key,
                "verified_prefix_units": bundle.count, "model_calls": 0}
        elif args.command in {"prefix", "verify"}:
            rebuilt = reconstruct(args.root, args.job, complete=args.command == "verify")
            result = {**rebuilt["summary"], "snapshot": str(snapshot.save(args.root, rebuilt))}
        else:
            result = (guarded if args.command == "guard" else worker)(args.root, args.job)
        print(json.dumps(result))
    except BaseException as exc:
        print(json.dumps(whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
