"""Resume a failed original draft using only context its request really contained.

The frozen writer, requests and budgets remain unchanged. An explicit recovery
receipt binds the original and extended packets; unused review/repair roles see
the same complete originals. No model cache or semantic verdict is synthesized.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import copy
import json
from pathlib import Path
import re
import sys

import jsonschema

from . import course_textbook_reference_sets as refs
from .course_knowledge import schema_lease
from .course_library import canonical

draft, writing = refs.draft, refs.writing
POLICY = "authenticated-original-write-visible-context-recovery-v1"
FIELD = "visible_context_recovery"
ORIGINAL_PACKET = writing.composition.original_packet
ORIGINAL_VALIDATE = writing.validate_section
ORIGINAL_JOB = draft.BASE_JOB


def require(ok, code):
    if not ok:
        raise ValueError("requires_visible_context_" + code)


def digest(value):
    return writing.whole.sha(canonical(value))


def visible_scope(ids, working, inventory, context, packet):
    require(packet == ORIGINAL_PACKET(ids, working, inventory), "unchanged_original_packet")
    expected = writing.whole.context_for(inventory, list(packet["sources"]))
    require(all(context.get(key) == expected[key] for key in ("sources", "context_sources", "scope")),
        "actual_complete_original_context_only")
    require(context["assigned_points"] == {key: working["points"][key] for key in ids}, "original_assigned_points")
    additional = context["context_sources"]
    require(bool(additional), "actually_supplied_additional_originals")
    expanded = {"sources": {key: inventory["sources"][key] for key in [*packet["sources"], *additional]}}
    return {"ids": tuple(ids), "working": working, "inventory": inventory,
        "context": copy.deepcopy(context), "original": copy.deepcopy(packet), "expanded": copy.deepcopy(expanded)}


@contextmanager
def packet_scope(scope):
    """Extend validation only, never historical request/context reconstruction."""
    previous = writing.composition.original_packet

    def scoped(ids, working, inventory):
        if tuple(ids) == scope["ids"] and working == scope["working"] and inventory == scope["inventory"]:
            return scope["expanded"]
        return previous(ids, working, inventory)

    writing.composition.original_packet = scoped
    try:
        yield
    finally:
        writing.composition.original_packet = previous


def normalize(candidate, ids, working, inventory, scope):
    jsonschema.validate(candidate, writing.section_schema(ids, working))
    require(candidate.get("kind") != "non_teaching", "complete_authored_candidate")
    rows = [candidate["overview"], candidate["takeaway"], candidate["quiz"],
        *candidate["paragraphs"], *candidate["diagrams"]]
    allowed = set(scope["expanded"]["sources"])
    require(all(row["citations"] and len(row["citations"]) == len(set(row["citations"]))
        and set(row["citations"]) <= allowed for row in rows), "nonempty_unique_actually_visible_citations")
    # Composition has no whole-record discriminator. Remove it only in this
    # validation/proposal copy and preserve the original teaching disposition.
    prose = {key: value for key, value in candidate.items() if key != "kind"}
    scoped, invalid = draft.quarantine.quarantine_graphs(prose)
    with packet_scope(scope):
        proposed, changes = draft.reviewable.propose_citations(scoped, ids, working, inventory)
        if "kind" in candidate:
            proposed["kind"] = candidate["kind"]
        ORIGINAL_VALIDATE(proposed, ids, working, inventory)
    require({k: v for k, v in proposed.items() if k not in {"paragraphs", "diagrams"}}
        == {k: v for k, v in candidate.items() if k not in {"paragraphs", "diagrams"}}
        and [{k: v for k, v in p.items() if k != "citations"} for p in proposed["paragraphs"]]
        == [{k: v for k, v in p.items() if k != "citations"} for p in candidate["paragraphs"]],
        "authored_content_coverage_and_disposition_unchanged")
    require(bool(changes or invalid or any(set(row["citations"]) - set(scope["original"]["sources"]) for row in rows)),
        "actual_recoverable_validation_defect")
    return proposed, changes, invalid


class Recovery:
    def __init__(self, targets):
        self.targets = set(targets)
        self.scopes = {}

    def candidate_from_history(self, job, name, schema, context, packet, working, inventory):
        path = draft.artifact_path(job, name)
        if path.exists():
            selected = FIELD in writing.whole.read(path)["receipt"]
        else:
            selected = name in self.targets
        if not selected:
            # Old recovery receipts must reconstruct byte-for-byte, including
            # the separate, frozen reference-deduplication adapter's receipt.
            return refs.candidate_from_history(job, name, schema, context, packet, working, inventory)
        require(re.fullmatch(r"section-[0-9]{4}-write", name) is not None, "original_write_role")
        scope = visible_scope(list(context["assigned_points"]), working, inventory, context, packet)
        previous = draft.normalize
        draft.normalize = lambda value, ids, ledger, inv: normalize(value, ids, ledger, inv, scope)
        try:
            # This authenticates both original requests/responses with the
            # ORIGINAL packet. The extension never rewrites historical input.
            artifact = refs.ORIGINAL_CANDIDATE(job, name, schema, context, packet, working, inventory)
        finally:
            draft.normalize = previous
        artifact["receipt"][FIELD] = {
            "policy": POLICY, "adapter_sha256": writing.whole.sha(Path(__file__).read_bytes()),
            "actual_context_sha256": digest(context), "original_evidence_packet_sha256": digest(packet),
            "expanded_evidence_packet_sha256": digest(scope["expanded"]),
            "additional_original_sha256": {key: digest(inventory["sources"][key]) for key in context["context_sources"]},
            "additional_visible_text_sha256": {key: digest(row) for key, row in context["context_sources"].items()},
            "source_text_present_in_authenticated_original_request": True,
            "prose_coverage_and_teaching_disposition_unchanged": True,
            "composition_validation_omits_preserved_teaching_discriminator": True,
            "model_calls": 0, "original_budgets_reset": False, "semantic_acceptance": False,
            "reader_publication_allowed": False}
        self.scopes[job.key, name.removesuffix("-write")] = scope
        return artifact

    def validate_section(self, value, ids, working, inventory):
        for scope in self.scopes.values():
            if tuple(ids) == scope["ids"] and working == scope["working"] and inventory == scope["inventory"]:
                with packet_scope(scope):
                    return ORIGINAL_VALIDATE(value, ids, working, inventory)
        return ORIGINAL_VALIDATE(value, ids, working, inventory)

    def followup_packet(self, job, name, prompt, context, kwargs):
        match = re.fullmatch(r"(section-[0-9]{4})-(review|repair|repair-review)", name)
        scope = self.scopes.get((job.key, match[1])) if match else None
        if scope is None:
            return kwargs
        expected_prompt = writing.author.REPAIR_PROMPT if match[2] == "repair" else writing.author.REVIEW_PROMPT
        require(prompt == expected_prompt and set(kwargs) == {"evidence_packet"}, "original_remaining_role")
        require({k: v for k, v in context.items() if k not in {"candidate", "review"}} == scope["context"]
            and kwargs["evidence_packet"] == scope["original"], "unchanged_followup_context_and_original_packet")
        return {"evidence_packet": scope["expanded"]}

    @contextmanager
    def hooks(self):
        registry = self

        class ScopedJob(ORIGINAL_JOB):
            def stage(self, name, prompt, schema, context, validate, **kwargs):
                kwargs = registry.followup_packet(self, name, prompt, context, kwargs)
                # Frozen capture_request deliberately invokes this method on
                # the original job instance while authenticating history.
                return ORIGINAL_JOB.stage(self, name, prompt, schema, context, validate, **kwargs)

        previous = draft.candidate_from_history, writing.validate_section, draft.BASE_JOB, writing.AuthoringJob
        draft.candidate_from_history = self.candidate_from_history
        writing.validate_section, draft.BASE_JOB = self.validate_section, ScopedJob
        try:
            yield
        finally:
            draft.candidate_from_history, writing.validate_section, draft.BASE_JOB, writing.AuthoringJob = previous


def replay_unit(job, unit, context, working, inventory):
    ids, prefix = unit["point_ids"], unit["id"]
    packet, schema = ORIGINAL_PACKET(ids, working, inventory), writing.section_schema(ids, working)
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
    return candidate


def backup_recovery(job, stage):
    backup = Path.home() / "Documents/CourseKnowledgeBackups/whole-visible-context" / job.key
    paths = [draft.artifact_path(job, stage), Path(__file__), Path(refs.__file__), Path(draft.__file__)]
    paths += list((job.root / "attempts").glob(stage + "-[0-9][0-9].json"))
    for path in paths:
        relative = path.relative_to(job.root) if path.is_relative_to(job.root) else Path("code") / path.name
        writing.whole.backup(path, backup / relative)


def prepare(root, inventory_key, ledger_job, tokenizer):
    root = Path(root).resolve()
    inventory, working = writing.load_inputs(root, inventory_key, ledger_job, tokenizer)
    with schema_lease(root / "wiki/textbook-whole-record-evidence" / inventory_key / "authoring-worker.lock", timeout=0):
        job = ORIGINAL_JOB(writing.TextbookStore(root), inventory, working, tokenizer)
        status = writing.whole.read(job.root / "progress.json")
        stage = status["stage"]
        require(not writing.identity_live(status) and status["state"] == "stopped"
            and re.fullmatch(r"section-[0-9]{4}-write", stage), "stopped_original_write")
        contract = writing.whole.read(root / "wiki/textbook-keyed-jobs" / ledger_job / "whole-source-authoring-execution.json")
        require(contract == {"inventory": inventory_key, "ledger_sha256": digest(working), "job": job.key},
            "unchanged_original_execution_budget")
        job.resume_inventory, job.resume_working = inventory, working
        job.stage = lambda *args, **kwargs: refs.readonly_stage(job, *args, **kwargs)
        registry = Recovery([stage])
        with registry.hooks():
            plan = writing.organize(job, working)
            units = writing.writing_units(plan, working, inventory, job.tokenizer)
            require(plan == draft.saved_data(job.root / "concept-plan.json")
                and units == draft.saved_data(job.root / "writing-allocation.json"), "original_plan_and_allocation")
            sections = {}
            for number, unit in enumerate(units):
                context = refs.unit_context(unit, number, units, plan, sections, working, inventory)
                if unit["id"] + "-write" == stage:
                    artifact = draft.recover_stage(job, stage, writing.section_schema(unit["point_ids"], working),
                        context, ORIGINAL_PACKET(unit["point_ids"], working, inventory), working, inventory)
                    require(FIELD in artifact["receipt"], "explicit_context_recovery_required")
                    backup_recovery(job, stage)
                    return {"state": "authenticated_visible_context_ready_for_original_independent_review",
                        "job": job.key, "stage": stage, "replayed_prior_units": number,
                        "original_sources": len(ORIGINAL_PACKET(unit["point_ids"], working, inventory)["sources"]),
                        "actual_additional_sources": len(context["context_sources"]),
                        "model_calls": 0, "original_budgets_reset": False, "reader_article_published": False}
                candidate = replay_unit(job, unit, context, working, inventory)
                if candidate.get("kind") != "non_teaching":
                    sections[unit["id"]] = {k: v for k, v in candidate.items() if k != "kind"}
    raise ValueError("requires_visible_context_stopped_unit_in_original_allocation")


def worker(root, inventory_key, ledger_job, tokenizer):
    root = Path(root).resolve()
    inventory, working = writing.load_inputs(root, inventory_key, ledger_job, tokenizer)
    job = ORIGINAL_JOB(writing.TextbookStore(root), inventory, working, tokenizer)
    require(any(FIELD in writing.whole.read(path)["receipt"] for path in (job.root / "draft-resume").glob("*.json")),
        "prepared_context_recovery_required")
    with Recovery([]).hooks():
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
            writing.whole.backup(previous, root / "wiki/visible-context-guard-history" / (writing.whole.sha(previous.read_bytes()) + ".json"))
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
    try:
        result = {"prepare": prepare, "worker": worker, "guard": guard}[args.command](
            args.root, args.inventory, args.ledger_job, args.tokenizer)
        print(json.dumps(result or {"state": "guard_returned"}))
    except Exception as exc:
        print(json.dumps(writing.whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
