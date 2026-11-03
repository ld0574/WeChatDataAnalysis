"""Retain complete private prose fields when trailing coverage metadata was cut.

This never completes truncated strings or invents missing mappings. All missing
or invalid mapping records remain explicit unaccepted obligations. Reader
publication stays blocked, even if the unused factual-review role passes.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import jsonschema

from . import course_textbook_authoring as author
from . import course_textbook_composition as composition
from . import course_textbook_post_repair_review as post
from . import course_textbook_structural_repair as structural
from .course_knowledge import atomic_json, schema_lease
from .course_library import canonical
from .course_textbook_evidence import sha
from .course_wiki_text_inference import OPTIONS

POLICY = "complete-private-prose-fields-with-explicit-unresolved-metadata-v1"
SUFFIX = "-partial-fields-execution.json"
FIELDS = {
    "heading",
    "objective",
    "overview",
    "paragraphs",
    "diagrams",
    "quiz",
    "takeaway",
}


def require(ok, reason):
    if not ok:
        raise ValueError("requires_private_partial_fields_" + reason)


def complete_fields(raw):
    """Decode only complete values in the original stream; append zero bytes."""
    require(raw.startswith("{"), "original_object")
    decoder, position, values, spans = json.JSONDecoder(), 1, {}, {}
    mappings, mapping_spans = {}, {}

    def whitespace(index, punctuation=""):
        while index < len(raw) and raw[index] in " \r\n\t" + punctuation:
            index += 1
        return index

    while position < len(raw):
        position = whitespace(position, ",")
        start = position
        try:
            key, position = decoder.raw_decode(raw, position)
            position = whitespace(position)
            require(raw[position] == ":", "original_field_separator")
            position = whitespace(position + 1)
            if key == "coverage":
                require(
                    set(values) == FIELDS and raw[position] == "{",
                    "all_semantic_fields_complete_before_metadata",
                )
                position += 1
                while position < len(raw):
                    position = whitespace(position, ",")
                    entry_start = position
                    try:
                        point, position = decoder.raw_decode(raw, position)
                        position = whitespace(position)
                        require(raw[position] == ":", "original_mapping_separator")
                        position = whitespace(position + 1)
                        row, position = decoder.raw_decode(raw, position)
                    except (json.JSONDecodeError, IndexError):
                        break
                    require(point not in mappings, "unique_complete_mapping")
                    mappings[point] = row
                    mapping_spans[point] = {
                        "start": entry_start,
                        "end": position,
                        "sha256": sha(raw[entry_start:position].encode()),
                    }
                return (
                    values,
                    mappings,
                    {"fields": spans, "complete_mappings": mapping_spans},
                )
            require(key in FIELDS and key not in values, "unique_known_semantic_field")
            value, position = decoder.raw_decode(raw, position)
        except (json.JSONDecodeError, IndexError):
            break
        values[key] = value
        spans[key] = {
            "start": start,
            "end": position,
            "sha256": sha(raw[start:position].encode()),
        }
    raise ValueError(
        "requires_private_partial_fields_all_semantic_fields_complete_before_metadata"
    )


def private_candidate(raw, ids, working, inventory):
    fields, mappings, spans = complete_fields(raw)
    require(set(mappings) <= set(ids), "only_assigned_mapping_keys")
    candidate = copy.deepcopy(fields)
    candidate["coverage"] = {}
    unresolved, rejected = [], {}
    for key in ids:
        row = mappings.get(key)
        if (
            row is not None
            and row.get("paragraphs")
            and all(
                type(index) is int and 0 <= index < len(fields["paragraphs"])
                for index in row["paragraphs"]
            )
        ):
            candidate["coverage"][key] = copy.deepcopy(row)
        else:
            unresolved.append(key)
            if row is not None:
                rejected[key] = copy.deepcopy(row)
            candidate["coverage"][key] = {
                "paragraphs": [],
                "reason": "Unverified mapping.",
            }
            if key in working["pending_source_cards"]:
                candidate["coverage"][key]["teaching"] = True
    require(bool(unresolved), "explicit_unresolved_metadata")
    candidate, graphs = structural.quarantine.quarantine_graphs(candidate)
    schema = composition.section_schema(ids, working)
    for key in unresolved:
        schema["properties"]["coverage"]["properties"][key]["properties"]["paragraphs"][
            "minItems"
        ] = 0
    jsonschema.validate(candidate, schema)
    packet = composition.original_packet(ids, working, inventory)
    for row in [
        *candidate["paragraphs"],
        candidate["overview"],
        candidate["takeaway"],
        candidate["quiz"],
        *candidate["diagrams"],
    ]:
        require(
            bool(row["citations"])
            and len(row["citations"]) == len(set(row["citations"]))
            and set(row["citations"]) <= set(packet["sources"]),
            "complete_body_citations_in_scope",
        )
    for row in [*candidate["paragraphs"], candidate["overview"], candidate["takeaway"]]:
        author.nonempty(row["text"])
    for field in ("heading", "objective"):
        author.nonempty(candidate[field])
    for field in ("question", "answer", "explanation"):
        author.nonempty(candidate["quiz"][field])
    for criterion in candidate["quiz"]["criteria"]:
        author.nonempty(criterion)
    require(
        [p["text"] for p in candidate["paragraphs"]]
        == [p["text"] for p in fields["paragraphs"]],
        "all_complete_prose_unchanged",
    )
    return candidate, {
        "unresolved_point_ids": unresolved,
        "rejected_complete_mappings": rejected,
        "quarantined_diagrams": graphs,
        "original_complete_field_spans": spans,
    }


def recover(job, section, context, working, inventory):
    ids = list(context["assigned_points"])
    old = json.loads((job.root / (section + structural.SUFFIX)).read_bytes())
    require(
        old["original_context_sha256"] == sha(canonical(context)),
        "unchanged_original_context",
    )
    source = job.root / "attempts" / old["selected_attempt"]
    original = json.loads(json.loads(source.read_bytes())["raw_response"])
    audit = post.saved(job.root / (section + "-review.json"))["data"]
    repair_context = {
        **composition.writing_context(ids, working, inventory),
        "chapter": context["chapter"],
        "concept": context["concept"],
        "candidate": original,
        "review": structural.reviewable.repair_guidance(audit),
        "structural_defects": old["structural_defects"],
        "cross_section_readability_review_pending": True,
    }
    packet = composition.original_packet(ids, working, inventory)
    records = post.verify_requests(
        job,
        section + "-repair",
        composition.PROMPTS[author.REPAIR_PROMPT],
        structural.reviewable.repair_schema(ids, working),
        repair_context,
        packet,
    )
    require(len(records) == 2, "original_repair_budget_exhausted")
    for _, record in records:
        metrics = record.get("model_metrics", {})
        require(
            record.get("error_type") == "ModelOutputTruncatedError"
            and metrics.get("done_reason") == "length"
            and metrics.get("eval_count") == OPTIONS["num_predict"]
            and type(metrics.get("prompt_eval_count")) is int
            and 0
            < metrics["prompt_eval_count"]
            < OPTIONS["num_ctx"] - OPTIONS["num_predict"],
            "original_measured_truncation_preserved",
        )
    path, record = records[0]
    candidate, issues = private_candidate(
        record["raw_response"], ids, working, inventory
    )
    contract = {
        "policy": POLICY,
        "job": job.key,
        "section": section,
        "module_sha256": sha(Path(__file__).read_bytes()),
        "helpers_sha256": {
            module.__name__: sha(Path(module.__file__).read_bytes())
            for module in (post, structural)
        },
        "original_structural_execution_sha256": sha(canonical(old)),
        "original_context_sha256": sha(canonical(context)),
        "original_repair_attempts": {p.name: sha(p.read_bytes()) for p, _ in records},
        "selected_attempt": path.name,
        "candidate_sha256": sha(canonical(candidate)),
        "issues": issues,
        "truncated_strings_completed": False,
        "missing_mappings_invented": False,
        "semantic_rewrite_calls": 0,
        "reader_publication_allowed": False,
    }
    target = job.root / (section + SUFFIX)
    if target.exists():
        require(
            json.loads(target.read_bytes()) == contract, "unchanged_private_retention"
        )
    else:
        require(
            not any(
                (job.root / "attempts").glob(section + "-repair-review-[0-9][0-9].json")
            ),
            "original_final_review_role_unused",
        )
        atomic_json(target, contract)
    request = post.review_request(job, context, candidate, ids, working, inventory)
    final = structural.sized_stage(
        job,
        section + "-repair-review",
        author.REVIEW_PROMPT,
        request["schema"],
        request["context"],
        lambda value: author.validate_review(value, request["schema"]),
        packet,
    )
    post.verified_review(job, section + "-repair-review", request)
    proof = {
        "policy": POLICY,
        "execution_sha256": sha(canonical(contract)),
        "candidate": candidate,
        "candidate_sha256": contract["candidate_sha256"],
        "issues": issues,
        "actual_factual_review_sha256": sha(canonical(final)),
        "actual_factual_review_passed": all(author.verdicts(final)),
        "accepted": False,
        "reader_publication_allowed": False,
    }
    proof_path = job.root / (section + "-partial-fields-private-result.json")
    if proof_path.exists():
        require(
            json.loads(proof_path.read_bytes()) == proof, "unchanged_private_result"
        )
    else:
        atomic_json(proof_path, proof)
    return candidate, final, proof


def protocol_for(root, inventory_key, ledger_job, job_key, legacy_section):
    base = structural.protocol_for(
        root, inventory_key, ledger_job, job_key, legacy_section
    )
    inventory, working = composition.load_inputs(root, inventory_key, ledger_job)
    active = []

    class PartialFieldsJob(base.AuthoringJob):
        def __init__(self, *args, **kwargs):
            self.partial_sections = {}
            super().__init__(*args, **kwargs)
            active[:] = [self]

        def stage(self, name, prompt, schema, context, validate, **kwargs):
            match = re.fullmatch(
                r"(section-[0-9]{4})(-write|-review|-repair|-repair-review)", name
            )
            if match is not None:
                section, suffix = match.groups()
                if suffix == "-write":
                    if not (self.root / (section + SUFFIX)).exists():
                        try:
                            return super().stage(
                                name, prompt, schema, context, validate, **kwargs
                            )
                        except ValueError as exc:
                            if (
                                str(exc)
                                != "wiki_stage_attempt_budget_exhausted_"
                                + section
                                + "-repair"
                            ):
                                raise
                    result = recover(self, section, context, working, inventory)
                    self.partial_sections[section] = result
                    self.progress(
                        "retaining_complete_private_prose_with_unresolved_metadata",
                        name,
                    )
                    return result[0]
                if section in self.partial_sections:
                    candidate, audit, proof = self.partial_sections[section]
                    require(
                        context["candidate"] == candidate,
                        "same_unaccepted_private_candidate",
                    )
                    return candidate if suffix == "-repair" else audit
            return super().stage(name, prompt, schema, context, validate, **kwargs)

        def progress(self, state, stage, **extra):
            unaccepted = int(self.finished_sections > int(legacy_section[-4:]))
            for path in (self.root / "private-manuscript-continuation").glob(
                "section-*.json"
            ):
                receipt = json.loads(path.read_bytes())
                unaccepted += (
                    self.finished_sections > int(path.stem[-4:])
                    and not receipt["resolved_with_original_roles"]
                )
            unaccepted += sum(
                self.finished_sections > int(section[-4:])
                for section in self.partial_sections
            )
            composition.AuthoringJob.progress(
                self,
                state,
                stage,
                **{
                    "draft_sections_processed": self.finished_sections,
                    "sections_retained_unaccepted": unaccepted,
                    "completed_sections": max(0, self.finished_sections - unaccepted),
                    **extra,
                },
            )

    def assemble(inv, ledger, plan, units, sections):
        private = base.assemble(inv, ledger, plan, units, sections)
        for section, (_, _, proof) in active[0].partial_sections.items():
            private["unaccepted_sections"][section] = {
                "accepted": False,
                "candidate_sha256": proof["candidate_sha256"],
                "unresolved_point_ids": proof["issues"]["unresolved_point_ids"],
                "partial_fields_proof_sha256": sha(canonical(proof)),
            }
        return private

    def final_issues(inv, ledger, private):
        return [
            *base.final_issues(inv, ledger, private),
            *[
                {
                    "kind": "unaccepted_partial_generation_metadata",
                    "section": section,
                    "unresolved_point_ids": proof["issues"]["unresolved_point_ids"],
                    "reader_publication_allowed": False,
                }
                for section, (_, _, proof) in active[0].partial_sections.items()
            ],
        ]

    return SimpleNamespace(
        **{
            **vars(base),
            "AuthoringJob": PartialFieldsJob,
            "assemble": assemble,
            "final_issues": final_issues,
        }
    )


def run(root, inventory_key, ledger_job, job_key, legacy_section):
    root = Path(root).resolve()
    with schema_lease(
        root / "wiki" / (job_key + "-partial-fields-controller.lock"), timeout=0
    ):
        return author.run(
            root,
            inventory_key,
            ledger_job,
            protocol=protocol_for(
                root, inventory_key, ledger_job, job_key, legacy_section
            ),
        )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    for flag in ("inventory", "ledger-job", "job", "legacy-section"):
        parser.add_argument("--" + flag, required=True)
    parser.add_argument("command", choices=["run", "guard"])
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
