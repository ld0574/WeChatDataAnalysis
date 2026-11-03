"""Preserve bounded editorial calls while disambiguating reader navigation."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from . import course_textbook_final_edit as editor
from . import course_textbook_final_mapping as mapping
from .course_knowledge import atomic_json
from .course_library import canonical
from .course_textbook_evidence import sha

POLICY = "editorial-navigation-proposal-without-body-or-budget-changes-v1"


def disambiguate(candidate, name):
    import copy

    value = copy.deepcopy(candidate)
    ordinal = int(name.split("-", 2)[1]) + 1
    value["heading"] = candidate["heading"].strip() + f"（第{ordinal}节）"
    return value


def proposed(job, name, prompt, schema, context, validate, packet, *, write_receipt):
    editor.require(
        mapping.citations.editable(name) and not (job.root / (name + ".json")).exists(),
        "only_failed_unaccepted_navigation",
    )
    records = editor.post.verify_requests(job, name, prompt, schema, context, packet)
    editor.require(
        len(records) == 2
        and not any(row.get("accepted_structure") for _, row in records),
        "navigation_preserves_original_attempts",
    )
    selected = None
    for path, record in reversed(records):
        if record.get("error_type") != "ValueError" or record.get(
            "validation_error"
        ) not in {
            mapping.citations.reviewable.repair.ERROR,
            mapping.HEADING_ERROR,
        }:
            continue
        editor.validate_metrics(record["model_metrics"], job.model_digest)
        original = json.loads(record["raw_response"])
        normalized, offset, _ = mapping.metadata_proposal(original, name, context)
        candidate, citations = mapping.citations.reviewable.propose_citations(
            normalized, list(context["assigned_points"]), job.working, job.inventory
        )
        try:
            validate(candidate)
        except ValueError as exc:
            if str(exc) != mapping.HEADING_ERROR:
                raise
        else:
            editor.require(False, "navigation_change_requires_real_heading_conflict")
        candidate = disambiguate(candidate, name)
        validate(candidate)
        selected = path, original, candidate, citations, offset
        break
    editor.require(selected is not None, "real_navigation_proposal_required")
    path, original, candidate, citations, offset = selected
    proof = {
        "policy": POLICY,
        "stage": name,
        "module_sha256": sha(Path(__file__).read_bytes()),
        "mapping_module_sha256": sha(Path(mapping.__file__).read_bytes()),
        "input_sha256": job.stage_input(
            prompt, schema, context, evidence_packet=packet
        )[1],
        "original_attempts": {p.name: sha(p.read_bytes()) for p, _ in records},
        "selected_attempt": path.name,
        "raw_candidate_sha256": sha(canonical(original)),
        "proposed_candidate_sha256": sha(canonical(candidate)),
        "candidate": candidate,
        "uniform_index_offset": offset,
        "citation_paragraphs_proposed": citations,
        "only_navigation_heading_changed_after_mapping_proposal": True,
        "body_changed": False,
        "factual_acceptance": False,
        "additional_model_calls": 0,
        "original_budget_refreshed": False,
    }
    target = job.root / "private-navigation-proposals" / (name + ".json")
    if target.exists():
        editor.require(
            json.loads(target.read_bytes()) == proof, "unchanged_navigation_proposal"
        )
    else:
        editor.require(write_receipt, "existing_navigation_proposal_required")
        target.parent.mkdir(exist_ok=True)
        atomic_json(target, proof)
    return candidate


class NavigationJob(mapping.MappingJob):
    def stage(self, name, prompt, schema, context, validate, *, evidence_packet=None):
        try:
            return super().stage(
                name, prompt, schema, context, validate, evidence_packet=evidence_packet
            )
        except ValueError as exc:
            if (
                not mapping.citations.editable(name)
                or str(exc) != mapping.HEADING_ERROR
            ):
                raise
            packet = self.packet if evidence_packet is None else evidence_packet
            value = proposed(
                self,
                name,
                prompt,
                schema,
                context,
                validate,
                packet,
                write_receipt=True,
            )
            self.progress("retaining_unaccepted_navigation_for_real_review", name)
            return value


def authenticated(job, name, prompt, schema, context, validate, packet):
    if (
        mapping.citations.editable(name)
        and (job.root / "private-navigation-proposals" / (name + ".json")).exists()
    ):
        return proposed(
            job, name, prompt, schema, context, validate, packet, write_receipt=False
        )
    return mapping.authenticated(job, name, prompt, schema, context, validate, packet)


def run(root, job):
    with (
        patch.object(editor, "EditorJob", NavigationJob),
        patch.object(editor, "authenticated", authenticated),
    ):
        return editor.run(root, job)


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--job", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.root, args.job)))
    except Exception as exc:
        print(
            json.dumps(
                {
                    "state": "stopped",
                    "error_type": type(exc).__name__,
                    "error_sha256": sha(str(exc).encode()),
                }
            )
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
