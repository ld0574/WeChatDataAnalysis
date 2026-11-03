"""Use unused original section roles to repair a complete structurally bad draft.

The original two write attempts and all prior stages remain immutable. Complete
sources, assigned teaching points and candidate prose are never clipped. Failed
factual reviews remain unaccepted private sections while the manuscript proceeds.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import jsonschema

from . import course_textbook_authoring as author
from . import course_textbook_composition as composition
from . import course_textbook_diagram_quarantine as quarantine
from . import course_textbook_post_repair_review as post
from . import course_textbook_reviewable_write as reviewable
from .course_knowledge import atomic_json, schema_lease
from .course_library import canonical
from .course_textbook_evidence import sha
from .course_wiki_text_inference import validate_metrics

POLICY = "original-unused-section-roles-structural-repair-v1"
SUFFIX = "-structural-repair-execution.json"


def require(ok, reason):
    if not ok:
        raise ValueError("requires_structural_section_repair_" + reason)


def defects(candidate, packet):
    findings = []
    count = len(candidate["paragraphs"])
    for key, row in candidate["coverage"].items():
        if any(index < 0 or index >= count for index in row["paragraphs"]):
            findings.append({"kind": "paragraph_mapping_out_of_bounds", "point": key})
    for field in ("overview", "takeaway", "quiz"):
        if not set(candidate[field]["citations"]) <= set(packet["sources"]):
            findings.append(
                {"kind": "citation_outside_complete_assigned_sources", "field": field}
            )
    for field in ("paragraphs", "diagrams"):
        for number, row in enumerate(candidate[field]):
            if not set(row["citations"]) <= set(packet["sources"]):
                findings.append(
                    {
                        "kind": "citation_outside_complete_assigned_sources",
                        "field": field,
                        "index": number,
                    }
                )
    _, invalid = quarantine.quarantine_graphs(candidate)
    findings.extend(
        {"kind": "invalid_generated_diagram", "index": row["index"]} for row in invalid
    )
    require(bool(findings), "real_structure_defect")
    return findings


def sized_stage(job, name, prompt, schema, context, validate, packet):
    size = job.request_size(name, prompt, schema, context, {"evidence_packet": packet})
    require(size <= post.CONTEXT_LIMIT, "complete_request_fits_context")
    return composition.AuthoringJob.stage(
        job, name, prompt, schema, context, validate, evidence_packet=packet
    )


def recover(job, section, schema, context, packet, working, inventory):
    ids = list(context["assigned_points"])
    stage = section + "-write"
    records = post.verify_requests(
        job, stage, composition.PROMPTS[author.WRITE_PROMPT], schema, context, packet
    )
    require(
        len(records) == 2
        and not any(row.get("accepted_structure") for _, row in records),
        "exhausted_original_write_attempts",
    )
    selected = None
    for path, row in records:
        if row.get("error_type") != "ValueError":
            continue
        validate_metrics(row["model_metrics"], job.model_digest)
        value = json.loads(row["raw_response"])
        jsonschema.validate(value, schema)
        selected = path, value
        break
    require(selected is not None, "complete_original_candidate")
    attempt, original = selected
    problems = defects(original, packet)
    contract = {
        "policy": POLICY,
        "job": job.key,
        "section": section,
        "model_digest": job.model_digest,
        "module_sha256": sha(Path(__file__).read_bytes()),
        "helpers_sha256": {
            module.__name__: sha(Path(module.__file__).read_bytes())
            for module in (post, quarantine, reviewable)
        },
        "original_write_attempts": {
            path.name: sha(path.read_bytes()) for path, _ in records
        },
        "selected_attempt": attempt.name,
        "selected_candidate_sha256": sha(canonical(original)),
        "original_context_sha256": sha(canonical(context)),
        "complete_original_packet_sha256": sha(canonical(packet)),
        "structural_defects": problems,
        "original_stage_attempt_budget": 2,
        "semantic_repairs": 1,
        "additional_model_stage_roles": 0,
        "source_or_assigned_point_truncation_allowed": False,
        "reader_publication_allowed": False,
    }
    target = job.root / (section + SUFFIX)
    if target.exists():
        require(
            json.loads(target.read_bytes()) == contract, "unchanged_original_execution"
        )
    else:
        require(
            not any((job.root / "attempts").glob(section + "-review-[0-9][0-9].json"))
            and not any(
                (job.root / "attempts").glob(section + "-repair-[0-9][0-9].json")
            )
            and not any(
                (job.root / "attempts").glob(section + "-repair-review-[0-9][0-9].json")
            ),
            "original_review_and_repair_roles_unused",
        )
        atomic_json(target, contract)
    request = post.review_request(job, context, original, ids, working, inventory)
    audit = sized_stage(
        job,
        section + "-review",
        author.REVIEW_PROMPT,
        request["schema"],
        request["context"],
        lambda value: author.validate_review(value, request["schema"]),
        packet,
    )
    post.verified_review(job, section + "-review", request)
    # Earlier prose and broad chapter navigation are editorial context rather
    # than evidence for this section. Keep all assigned points, complete source
    # texts, the whole bad candidate and every negative factual-review reason.
    repair_context = {
        **composition.writing_context(ids, working, inventory),
        "chapter": context["chapter"],
        "concept": context["concept"],
        "candidate": original,
        "review": reviewable.repair_guidance(audit),
        "structural_defects": problems,
        "cross_section_readability_review_pending": True,
    }
    require(
        repair_context["sources"] == context["sources"]
        and repair_context["assigned_points"] == context["assigned_points"],
        "complete_sources_and_assigned_teaching_preserved",
    )
    repair_schema = reviewable.repair_schema(ids, working)

    def validate_repaired(value):
        jsonschema.validate(value, repair_schema)
        reviewable.validate_reviewable(value, ids, working, inventory)

    repaired = sized_stage(
        job,
        section + "-repair",
        author.REPAIR_PROMPT,
        repair_schema,
        repair_context,
        validate_repaired,
        packet,
    )
    post.verify_requests(
        job,
        section + "-repair",
        composition.PROMPTS[author.REPAIR_PROMPT],
        repair_schema,
        repair_context,
        packet,
    )
    candidate, citations = reviewable.propose_citations(
        repaired, ids, working, inventory
    )
    final_request = post.review_request(
        job, context, candidate, ids, working, inventory
    )
    final = sized_stage(
        job,
        section + "-repair-review",
        author.REVIEW_PROMPT,
        final_request["schema"],
        final_request["context"],
        lambda value: author.validate_review(value, final_request["schema"]),
        packet,
    )
    post.verified_review(job, section + "-repair-review", final_request)
    proof = {
        "policy": POLICY,
        "execution_sha256": sha(canonical(contract)),
        "section": section,
        "candidate": candidate,
        "candidate_sha256": sha(canonical(candidate)),
        "actual_final_review_sha256": sha(canonical(final)),
        "factual_checks_passed": all(author.verdicts(final)),
        "citation_proposals": citations,
        "all_attempt_files": {
            path.name: sha(path.read_bytes())
            for path in sorted((job.root / "attempts").glob(section + "-*.json"))
        },
        "semantic_repairs": 1,
        "reader_publication_allowed": False,
    }
    proof_path = job.root / (section + "-structural-repair-private-result.json")
    if proof_path.exists():
        require(
            json.loads(proof_path.read_bytes()) == proof, "unchanged_completed_repair"
        )
    else:
        atomic_json(proof_path, proof)
    require(
        all(
            sha((job.root / "attempts" / name).read_bytes()) == digest
            for name, digest in contract["original_write_attempts"].items()
        ),
        "original_failed_attempts_preserved",
    )
    return candidate, final, sha(canonical(proof))


def protocol_for(root, inventory_key, ledger_job, job_key, legacy_section):
    base = quarantine.protocol_for(
        root, inventory_key, ledger_job, job_key, legacy_section
    )
    inventory, working = composition.load_inputs(root, inventory_key, ledger_job)

    class StructuralRepairJob(base.AuthoringJob):
        def __init__(self, *args, **kwargs):
            self.recovered_sections = {}
            super().__init__(*args, **kwargs)

        def stage(self, name, prompt, schema, context, validate, **kwargs):
            match = re.fullmatch(
                r"(section-[0-9]{4})(-write|-review|-repair|-repair-review)", name
            )
            if match is None:
                return super().stage(name, prompt, schema, context, validate, **kwargs)
            section, suffix = match.groups()
            if suffix == "-write":
                existing = (self.root / (section + SUFFIX)).exists()
                if not existing:
                    try:
                        return super().stage(
                            name, prompt, schema, context, validate, **kwargs
                        )
                    except ValueError as exc:
                        if (
                            str(exc)
                            != "requires_diagram_quarantine_complete_body_with_valid_mapping"
                        ):
                            raise
                candidate, audit, proof = recover(
                    self,
                    section,
                    schema,
                    context,
                    kwargs["evidence_packet"],
                    working,
                    inventory,
                )
                self.recovered_sections[section] = (candidate, audit, proof)
                receipt = self.retain(section, context, candidate)
                receipt["current_body_sha256"] = sha(canonical(candidate))
                receipt["resolved_with_original_roles"] = all(author.verdicts(audit))
                receipt["structural_repair_private_proof_sha256"] = proof
                self.save_receipt(receipt)
                self.progress("retaining_original_role_repaired_private_section", name)
                return candidate
            if section in self.recovered_sections:
                candidate, audit, proof = self.recovered_sections[section]
                require(context["candidate"] == candidate, "same_repaired_candidate")
                value = candidate if suffix == "-repair" else audit
                validate(value)
                self.progress("reusing_actual_original_role_repair_and_review", name)
                return value
            return super().stage(name, prompt, schema, context, validate, **kwargs)

    return SimpleNamespace(**{**vars(base), "AuthoringJob": StructuralRepairJob})


def run(root, inventory_key, ledger_job, job_key, legacy_section):
    root = Path(root).resolve()
    with schema_lease(
        root / "wiki" / (job_key + "-structural-repair-controller.lock"), timeout=0
    ):
        protocol = protocol_for(
            root, inventory_key, ledger_job, job_key, legacy_section
        )
        return author.run(root, inventory_key, ledger_job, protocol=protocol)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    for flag in ("inventory", "ledger-job", "job", "legacy-section"):
        parser.add_argument("--" + flag, required=True)
    parser.add_argument("command", choices=["guard", "run"])
    args = parser.parse_args(argv)
    try:
        if args.command == "guard":
            from .course_archive_gpu import checked_sample
            from .course_gpu_guard import run as guarded

            parameters = ["--root", str(args.root.resolve())]
            for flag in ("inventory", "ledger-job", "job", "legacy-section"):
                parameters += ["--" + flag, getattr(args, flag.replace("-", "_"))]
            guarded(
                args.root,
                [sys.executable, "-m", __spec__.name, *parameters, "run"],
                maximum_power=200,
                maximum_temperature=70,
                guard_name="wiki-revision-thermal-guard",
                sampler=lambda: checked_sample(args.root),
            )
        else:
            print(
                json.dumps(
                    run(
                        args.root,
                        args.inventory,
                        args.ledger_job,
                        args.job,
                        args.legacy_section,
                    )
                )
            )
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
