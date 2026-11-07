"""Resume original failed drafts after explicit, order-preserving citation deduplication.

This adapter retains failed requests, prose and stage budgets. It never removes an
out-of-scope reference, synthesizes a model cache or accepts a semantic judgment.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import re
import sys

import jsonschema

from . import course_textbook_whole_draft_resume as draft
from .course_knowledge import schema_lease
from .course_library import canonical

writing = draft.writing
POLICY = "original-draft-order-preserving-reference-sets-v1"
ORIGINAL_CANDIDATE = draft.candidate_from_history
ORIGINAL_NORMALIZE = draft.normalize


def require(ok, code):
    if not ok:
        raise ValueError("requires_reference_sets_" + code)


def reference_sets(candidate, ids, working, inventory):
    jsonschema.validate(candidate, writing.section_schema(ids, working))
    require(candidate.get("kind") != "non_teaching", "authored_candidate")
    allowed = set(writing.composition.original_packet(ids, working, inventory)["sources"])
    result, changes = copy.deepcopy(candidate), {}
    rows = [("overview", result["overview"]), ("takeaway", result["takeaway"]), ("quiz", result["quiz"])]
    rows += [(f"paragraphs/{n}", row) for n, row in enumerate(result["paragraphs"])]
    rows += [(f"diagrams/{n}", row) for n, row in enumerate(result["diagrams"])]
    for path, row in rows:
        before = row["citations"]
        require(bool(before) and set(before) <= allowed, "nonempty_original_scope_before_deduplication")
        after = list(dict.fromkeys(before))
        if before != after:
            changes[path] = {"old": before, "new": after}
            row["citations"] = after
    return result, changes


def normalize(candidate, ids, working, inventory):
    unique, changes = reference_sets(candidate, ids, working, inventory)
    try:
        return ORIGINAL_NORMALIZE(unique, ids, working, inventory)
    except ValueError as exc:
        if str(exc) != "requires_whole_draft_resume_actual_recoverable_structure_defect" or not changes:
            raise
        writing.validate_section(unique, ids, working, inventory)
        return unique, {}, []


def candidate_from_history(job, name, schema, context, packet, working, inventory):
    path = draft.artifact_path(job, name)
    if path.exists() and "reference_set_normalization" not in writing.whole.read(path)["receipt"]:
        return ORIGINAL_CANDIDATE(job, name, schema, context, packet, working, inventory)
    if not path.exists():
        try:
            return ORIGINAL_CANDIDATE(job, name, schema, context, packet, working, inventory)
        except ValueError as exc:
            if str(exc) != "requires_whole_draft_resume_complete_scope_safe_original_candidate":
                raise
    previous = draft.normalize
    draft.normalize = normalize
    try:
        artifact = ORIGINAL_CANDIDATE(job, name, schema, context, packet, working, inventory)
    finally:
        draft.normalize = previous
    number = artifact["receipt"]["selected_attempt"]
    original = json.loads(writing.whole.read(job.root / "attempts" / f"{name}-{number:02d}.json")["raw_response"])
    _, changes = reference_sets(original, list(context["assigned_points"]), working, inventory)
    require(bool(changes), "actual_duplicate_references_only")
    artifact["receipt"]["reference_set_normalization"] = {
        "policy": POLICY, "changes": changes, "unique_references_and_first_occurrence_order_preserved": True,
        "adapter_sha256": writing.whole.sha(Path(__file__).read_bytes()),
        "prose_coverage_and_stage_budgets_unchanged": True, "model_calls": 0, "semantic_acceptance": False}
    return artifact


def readonly_stage(job, name, prompt, schema, context, validate, **kwargs):
    if draft.plan_resume.artifact_path(job, name).exists():
        require(prompt == writing.author.GROUP_PROMPT and not kwargs, "original_planning_role")
        value = draft.plan_resume.load_recovery(job, job.resume_working, name)
    elif draft.artifact_path(job, name).exists():
        require(prompt == writing.author.WRITE_PROMPT and set(kwargs) == {"evidence_packet"}, "original_write_role")
        value = draft.recover_stage(job, name, schema, context, kwargs["evidence_packet"],
            job.resume_working, job.resume_inventory)["data"]
    else:
        require((job.root / (name + ".json")).is_file(), "prior_stage_must_have_actual_cache")
        packet = kwargs.get("evidence_packet", job.packet)
        expected = draft.capture_request(job, name, prompt, schema, context, packet)["binding"]
        value, binding = writing.whole.editor.e.checked_cache(job.root, name)
        require(binding == expected, "exact_prior_request")
    validate(value)
    return value


def unit_context(unit, number, units, plan, sections, working, inventory):
    previous = next(reversed(sections)) if sections else None
    return {**writing.writing_context(unit["point_ids"], working, inventory), "chapter": unit["chapter"],
        "concept": unit["concept"], "article_outline": [plan["nodes"][key] for key in plan["chapters"]],
        "diagram_preferred": not sections or units[number - 1]["chapter_id"] != unit["chapter_id"],
        "previous_reading_section": sections[previous] if previous else None}


def prepare(root, inventory_key, ledger_job, tokenizer):
    root = Path(root).resolve()
    inventory, working = writing.load_inputs(root, inventory_key, ledger_job, tokenizer)
    with schema_lease(root / "wiki/textbook-whole-record-evidence" / inventory_key / "authoring-worker.lock", timeout=0):
        job = draft.BASE_JOB(writing.TextbookStore(root), inventory, working, tokenizer)
        status = writing.whole.read(job.root / "progress.json")
        stage = status["stage"]
        require(not writing.identity_live(status) and status["state"] == "stopped"
            and re.fullmatch(r"section-[0-9]{4}-write", stage), "original_stopped_write")
        contract = writing.whole.read(root / "wiki/textbook-keyed-jobs" / ledger_job / "whole-source-authoring-execution.json")
        require(contract == {"inventory": inventory_key, "ledger_sha256": writing.whole.sha(canonical(working)),
            "job": job.key}, "unchanged_execution_budget")
        job.resume_inventory, job.resume_working = inventory, working
        job.stage = lambda *args, **kwargs: readonly_stage(job, *args, **kwargs)
        plan = writing.organize(job, working)
        units = writing.writing_units(plan, working, inventory, job.tokenizer)
        require(plan == draft.saved_data(job.root / "concept-plan.json")
            and units == draft.saved_data(job.root / "writing-allocation.json"), "exact_original_plan_and_allocation")
        sections = {}
        for number, unit in enumerate(units):
            ids, prefix = unit["point_ids"], unit["id"]
            context = unit_context(unit, number, units, plan, sections, working, inventory)
            packet = writing.composition.original_packet(ids, working, inventory)
            schema = writing.section_schema(ids, working)
            if prefix + "-write" == stage:
                artifact = draft.recover_stage(job, stage, schema, context, packet, working, inventory)
                changes = artifact["receipt"]["reference_set_normalization"]["changes"]
                return {"state": "explicit_reference_sets_ready_for_original_independent_review", "job": job.key,
                    "stage": stage, "changed_reference_lists": len(changes), "model_calls": 0,
                    "replayed_prior_units": number, "old_budgets_reset": False, "reader_article_published": False}
            candidate = job.stage(prefix + "-write", writing.author.WRITE_PROMPT, schema, context,
                lambda row: writing.validate_section(row, ids, working, inventory), evidence_packet=packet)
            for suffix in ("-review", "-repair-review"):
                audit_schema = writing.review_schema(candidate, ids)
                audit = job.stage(prefix + suffix, writing.author.REVIEW_PROMPT, audit_schema, {**context, "candidate": candidate},
                    lambda row: writing.author.validate_review(row, audit_schema), evidence_packet=packet)
                if all(writing.author.verdicts(audit)) or suffix == "-repair-review":
                    break
                candidate = job.stage(prefix + "-repair", writing.author.REPAIR_PROMPT, schema,
                    {**context, "candidate": candidate, "review": audit},
                    lambda row: writing.validate_section(row, ids, working, inventory), evidence_packet=packet)
            if candidate.get("kind") != "non_teaching":
                sections[prefix] = {key: row for key, row in candidate.items() if key != "kind"}
    raise ValueError("requires_reference_sets_stopped_unit_in_original_allocation")


def worker(root, inventory_key, ledger_job, tokenizer):
    return draft.worker(root, inventory_key, ledger_job, tokenizer)


def guard(root, inventory_key, ledger_job, tokenizer):
    from .course_archive_gpu import checked_sample
    from .course_gpu_guard import run as guarded

    root = Path(root).resolve()
    writing.TextbookStore(root).checkpoint()
    with schema_lease(root / "wiki/whole-source-authoring-dispatch.lock", timeout=0):
        previous = root / "wiki-revision-thermal-guard.json"
        if previous.exists():
            old = writing.whole.read(previous)
            require(not writing.identity_live(old), "prior_guard_exited")
            writing.whole.backup(previous, root / "wiki/reference-sets-guard-history" / (writing.whole.sha(previous.read_bytes()) + ".json"))
        guarded(root, [sys.executable, "-m", __spec__.name, "--root", str(root), "--inventory", inventory_key,
            "--ledger-job", ledger_job, "--tokenizer", str(Path(tokenizer).resolve()), "worker"],
            maximum_temperature=70, maximum_power=200, guard_name="wiki-revision-thermal-guard", sampler=lambda: checked_sample(root))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--ledger-job", required=True)
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("command", choices=["prepare", "worker", "guard"])
    args = parser.parse_args()
    previous = draft.candidate_from_history
    draft.candidate_from_history = candidate_from_history
    try:
        result = {"prepare": prepare, "worker": worker, "guard": guard}[args.command](
            args.root, args.inventory, args.ledger_job, args.tokenizer)
        print(json.dumps(result or {"state": "guard_returned"}))
    except Exception as exc:
        print(json.dumps(writing.whole.failure_status(exc)))
        raise SystemExit(1) from None
    finally:
        draft.candidate_from_history = previous


if __name__ == "__main__":
    main()
