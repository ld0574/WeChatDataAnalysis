"""Retain complete editorial drafts with unproved citation proposals.

This preserves the original calls, requests, budgets and failed validations.
Only a subsequent original factual review can accept the proposed citations.
No accepted original write cache is fabricated for an exhausted stage.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import patch

from . import course_textbook_final_edit as editor
from . import course_textbook_reviewable_write as reviewable
from .course_knowledge import atomic_json
from .course_library import canonical
from .course_textbook_evidence import sha

POLICY = "complete-editorial-citation-proposals-with-original-factual-review-v1"
CONTRACT = "editorial-citation-proposals-execution.json"
BASE_JOB = editor.EditorJob
BASE_AUTHENTICATED = editor.authenticated


def editable(name):
    return name.endswith("-whole-edit") or name.endswith("-whole-edit-repair")


def bind(folder):
    contract = {
        "policy": POLICY,
        "job": folder.name,
        "module_sha256": sha(Path(__file__).read_bytes()),
        "editor_module_sha256": sha(Path(editor.__file__).read_bytes()),
        "original_stage_attempt_budget": 2,
        "additional_model_roles": 0,
        "original_budget_refreshed": False,
        "body_or_point_mapping_changes_allowed": False,
        "citation_proposals_are_factually_accepted": False,
        "reader_publication_allowed_without_real_factual_review": False,
    }
    path = folder / CONTRACT
    if path.exists():
        editor.require(
            json.loads(path.read_bytes()) == contract,
            "unchanged_editorial_citation_contract",
        )
    else:
        atomic_json(path, contract)
    return contract


def proposed(job, name, prompt, schema, context, validate, packet, *, write_receipt):
    """Reconstruct a proposal from complete genuine failed original calls."""
    editor.require(
        editable(name) and not (job.root / (name + ".json")).exists(),
        "only_unaccepted_complete_editorial_write",
    )
    contract = bind(job.root)
    ids = list(context["assigned_points"])
    inventory, working = job.inventory, job.working
    records = editor.post.verify_requests(job, name, prompt, schema, context, packet)
    editor.require(
        len(records) == 2
        and not any(row.get("accepted_structure") for _, row in records),
        "real_original_exhausted_editorial_write",
    )
    selected = None
    for path, record in reversed(records):
        if (
            record.get("error_type") != "ValueError"
            or record.get("validation_error") != reviewable.repair.ERROR
        ):
            continue
        editor.validate_metrics(record["model_metrics"], job.model_digest)
        original = json.loads(record["raw_response"])
        reviewable.validate_reviewable(original, ids, working, inventory)
        candidate, changes = reviewable.propose_citations(
            original, ids, working, inventory
        )
        validate(candidate)
        selected = path, original, candidate, changes
        break
    editor.require(selected is not None, "complete_citation_only_editorial_hypothesis")
    path, original, candidate, changes = selected
    proof = {
        "policy": POLICY,
        "stage": name,
        "contract_sha256": sha(canonical(contract)),
        "input_sha256": job.stage_input(
            prompt, schema, context, evidence_packet=packet
        )[1],
        "original_attempts": {p.name: sha(p.read_bytes()) for p, _ in records},
        "selected_attempt": path.name,
        "raw_candidate_sha256": sha(canonical(original)),
        "proposed_candidate_sha256": sha(canonical(candidate)),
        "candidate": candidate,
        "citation_paragraphs_proposed": changes,
        "body_changed": False,
        "point_mapping_changed": False,
        "original_strict_validation_passed": False,
        "factual_acceptance": False,
        "additional_model_calls": 0,
    }
    target = job.root / "private-citation-proposals" / (name + ".json")
    if target.exists():
        editor.require(
            json.loads(target.read_bytes()) == proof,
            "unchanged_editorial_citation_hypothesis",
        )
    else:
        editor.require(write_receipt, "existing_editorial_citation_proof_required")
        target.parent.mkdir(exist_ok=True)
        atomic_json(target, proof)
    return candidate


class CitationJob(BASE_JOB):
    def __init__(self, root, composition_job, inventory, working, parent, digest):
        super().__init__(root, composition_job, inventory, working, parent, digest)
        self.inventory, self.working = inventory, working
        bind(self.root)

    def stage(self, name, prompt, schema, context, validate, *, evidence_packet=None):
        try:
            return super().stage(
                name, prompt, schema, context, validate, evidence_packet=evidence_packet
            )
        except ValueError as exc:
            if (
                not editable(name)
                or str(exc) != "wiki_stage_attempt_budget_exhausted_" + name
            ):
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
            self.progress(
                "retaining_unaccepted_citation_hypothesis_for_real_review", name
            )
            return candidate


def authenticated(job, name, prompt, schema, context, validate, packet):
    if editable(name) and not (job.root / (name + ".json")).exists():
        return proposed(
            job, name, prompt, schema, context, validate, packet, write_receipt=False
        )
    return BASE_AUTHENTICATED(job, name, prompt, schema, context, validate, packet)


def run(root, job):
    with (
        patch.object(editor, "EditorJob", CitationJob),
        patch.object(editor, "authenticated", authenticated),
    ):
        return editor.run(root, job)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--job", required=True)
    args = parser.parse_args(argv)
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
