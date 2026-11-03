"""Unaccepted editorial metadata proposals, checked by original factual roles."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from unittest.mock import patch

from . import course_textbook_final_citations as citations
from . import course_textbook_final_edit as editor
from .course_knowledge import atomic_json
from .course_library import canonical
from .course_textbook_evidence import sha

POLICY = "uniform-one-based-editorial-mapping-hypothesis-before-factual-review-v1"
ERROR = "requires_existing_section_repair_original_mapping_in_bounds"
HEADING_ERROR = "requires_final_editor_unique_section_heading"


def uniform_zero_based(value):
    """Never clamp, drop or guess an individual mapping."""
    count = len(value["paragraphs"])
    indices = [n for row in value["coverage"].values() for n in row["paragraphs"]]
    editor.require(
        bool(indices)
        and all(type(n) is int and 1 <= n <= count for n in indices)
        and count in indices,
        "unambiguous_uniform_one_based_mapping",
    )
    result = copy.deepcopy(value)
    for row in result["coverage"].values():
        row["paragraphs"] = [n - 1 for n in row["paragraphs"]]
    return result


def metadata_proposal(value, name, context):
    """Repair representation only; retain every paragraph for real review."""
    count = len(value["paragraphs"])
    indices = [n for row in value["coverage"].values() for n in row["paragraphs"]]
    valid_zero = all(type(n) is int and 0 <= n < count for n in indices)
    result = copy.deepcopy(value) if valid_zero else uniform_zero_based(value)
    heading_changed = bool(
        context.get("previous_heading")
        and result["heading"].strip() == context["previous_heading"].strip()
    )
    if heading_changed:
        # A navigation disambiguator makes no new teaching or relevance claim.
        ordinal = int(name.split("-", 2)[1]) + 1
        result["heading"] = result["heading"].strip() + "（续" + str(ordinal) + "）"
    return result, 0 if valid_zero else -1, heading_changed


def proposed(job, name, prompt, schema, context, validate, packet, *, write_receipt):
    editor.require(
        citations.editable(name) and not (job.root / (name + ".json")).exists(),
        "only_failed_unaccepted_index_mapping",
    )
    records = editor.post.verify_requests(job, name, prompt, schema, context, packet)
    editor.require(
        len(records) == 2
        and not any(row.get("accepted_structure") for _, row in records),
        "original_index_mapping_budget_preserved",
    )
    ids = list(context["assigned_points"])
    selected = None
    for path, record in reversed(records):
        if (
            record.get("error_type") != "ValueError"
            or record.get("validation_error") != citations.reviewable.repair.ERROR
        ):
            continue
        editor.validate_metrics(record["model_metrics"], job.model_digest)
        original = json.loads(record["raw_response"])
        normalized, offset, heading_changed = metadata_proposal(original, name, context)
        citations.reviewable.validate_reviewable(
            normalized, ids, job.working, job.inventory
        )
        candidate, changes = citations.reviewable.propose_citations(
            normalized, ids, job.working, job.inventory
        )
        validate(candidate)
        selected = path, original, candidate, changes, offset, heading_changed
        break
    editor.require(selected is not None, "complete_unaccepted_index_mapping_hypothesis")
    path, original, candidate, changes, offset, heading_changed = selected
    proof = {
        "policy": POLICY,
        "stage": name,
        "module_sha256": sha(Path(__file__).read_bytes()),
        "input_sha256": job.stage_input(
            prompt, schema, context, evidence_packet=packet
        )[1],
        "original_attempts": {p.name: sha(p.read_bytes()) for p, _ in records},
        "selected_attempt": path.name,
        "raw_candidate_sha256": sha(canonical(original)),
        "proposed_candidate_sha256": sha(canonical(candidate)),
        "candidate": candidate,
        "uniform_index_offset": offset,
        "heading_navigation_disambiguated": heading_changed,
        "body_changed": False,
        "citation_paragraphs_proposed": changes,
        "original_strict_validation_passed": False,
        "mapping_factual_acceptance": False,
        "additional_model_calls": 0,
        "original_budget_refreshed": False,
    }
    target = job.root / "private-index-proposals" / (name + ".json")
    if target.exists():
        editor.require(
            json.loads(target.read_bytes()) == proof,
            "unchanged_index_mapping_hypothesis",
        )
    else:
        editor.require(write_receipt, "existing_index_mapping_hypothesis_required")
        target.parent.mkdir(exist_ok=True)
        atomic_json(target, proof)
    return candidate


class MappingJob(citations.CitationJob):
    def stage(self, name, prompt, schema, context, validate, *, evidence_packet=None):
        try:
            return super().stage(
                name, prompt, schema, context, validate, evidence_packet=evidence_packet
            )
        except ValueError as exc:
            if not citations.editable(name) or str(exc) not in {ERROR, HEADING_ERROR}:
                raise
            packet = self.packet if evidence_packet is None else evidence_packet
            candidate = proposed(
                self,
                name,
                prompt,
                schema,
                context,
                validate,
                packet,
                write_receipt=True,
            )
            self.progress("retaining_unaccepted_index_hypothesis_for_real_review", name)
            return candidate


def authenticated(job, name, prompt, schema, context, validate, packet):
    if (
        citations.editable(name)
        and (job.root / "private-index-proposals" / (name + ".json")).exists()
    ):
        return proposed(
            job, name, prompt, schema, context, validate, packet, write_receipt=False
        )
    return citations.authenticated(job, name, prompt, schema, context, validate, packet)


def run(root, job):
    with (
        patch.object(editor, "EditorJob", MappingJob),
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
