"""Execute unused original repair roles deferred by cross-section context size.

Every source, assigned point, candidate and failed factual-review reason stays
complete. Broad navigation is reserved for whole-manuscript reading review.
Original calls, deferrals and private articles remain immutable. This module
does not publish, refresh a consumed role, or allocate another semantic round.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from pathlib import Path

import jsonschema
import psutil

from . import course_textbook_authoring as author
from . import course_textbook_composition as composition
from . import course_textbook_post_repair_review as post
from . import course_textbook_reviewable_write as reviewable
from .course_knowledge import atomic_json, schema_lease
from .course_library import canonical
from .course_textbook_evidence import sha
from .course_textbook_ledger import TextbookStore
from .course_wiki_text_inference import validate_metrics

POLICY = "original-unused-size-deferred-section-repair-v1"
CONTRACT = "deferred-original-repair-execution.json"
OUTPUT = "private-deferred-repair-article.json"


def require(value, code):
    if not value:
        raise ValueError("requires_deferred_original_repair_" + code)


def readonly_job(folder, inventory):
    manifest = json.loads((folder / "manifest.json").read_bytes())
    recipe = manifest["recipe"]
    require(
        manifest["id"] == folder.name == sha(canonical(recipe))[:32]
        and manifest["recipe_sha256"] == sha(canonical(recipe))
        and recipe["stage_attempt_budget"] == 2
        and recipe["packet"] == {"sources": inventory["sources"]},
        "same_original_manifest",
    )
    job = object.__new__(DeferredJob)
    job.root, job.key = folder, folder.name
    job.packet, job.model_digest = recipe["packet"], recipe["model_digest"]
    job.range_notice = recipe.get("original_range_notice", "")
    return job


def compact_context(original, candidate, audit):
    """Discard no evidence or negative review; only omit broad navigation."""
    return {
        **{
            key: original[key]
            for key in (
                "assigned_points",
                "sources",
                "pending_source_cards",
                "pending_cards_are_verified_facts",
                "chapter",
                "concept",
            )
        },
        "candidate": candidate,
        "review": reviewable.repair_guidance(audit),
        "cross_section_readability_review_pending": True,
    }


def request_size(job, prompt, schema, context, packet):
    schema, binding = job.stage_input(
        composition.PROMPTS[prompt], schema, context, evidence_packet=packet
    )
    messages = [
        {"role": "system", "content": composition.PROMPTS[prompt] + job.range_notice},
        {
            "role": "user",
            "content": json.dumps(
                {**context, "previous_validation_failure": ""}, ensure_ascii=False
            ),
        },
    ]
    return len(
        canonical({"messages": messages, "schema": schema}).decode("utf-8")
    ), binding


def unused_roles(folder, section):
    return all(
        not (folder / (section + role + ".json")).exists()
        and not any((folder / "attempts").glob(section + role + "-[0-9][0-9].json"))
        for role in ("-repair", "-repair-review")
    )


def section_request(
    job, section, number, units, plan, original_sections, working, inventory
):
    path = job.root / "private-manuscript-continuation" / (section + ".json")
    receipt = json.loads(path.read_bytes())
    require(receipt["section"] == section, "same_deferred_section")
    require(
        receipt["candidate_sha256"]
        == sha(canonical(original_sections[section]))
        == sha(canonical(receipt["candidate"])),
        "exact_original_deferred_body",
    )
    ids = units[number]["point_ids"]
    context = post.context_for(
        number, units, plan, original_sections, working, inventory
    )
    require(receipt["context"] == context, "same_original_write_context")
    request = post.review_request(
        job, context, original_sections[section], ids, working, inventory
    )
    audit = post.verified_review(job, section + "-review", request)
    require(not all(author.verdicts(audit)), "actual_negative_original_review")
    deferred = receipt["deferred_stages"][section + "-repair"]
    require(
        deferred["reason"] == "complete_original_request_exceeds_character_limit"
        and deferred["model_call_performed"] is False
        and deferred["actual_factual_review"] is False,
        "original_size_deferral_without_call",
    )
    packet = composition.original_packet(ids, working, inventory)
    _, original_binding = job.stage_input(
        composition.PROMPTS[author.REPAIR_PROMPT],
        post.original_schema(job, section, ids, working),
        {**context, "candidate": original_sections[section], "review": audit},
        evidence_packet=packet,
    )
    require(original_binding == deferred["input_sha256"], "original_deferral_binding")
    compact = compact_context(context, original_sections[section], audit)
    schema = reviewable.repair_schema(ids, working)
    size, binding = request_size(job, author.REPAIR_PROMPT, schema, compact, packet)
    require(size <= post.CONTEXT_LIMIT, "complete_compact_repair_fits")
    return (
        compact,
        schema,
        packet,
        {
            "number": number,
            "original_candidate_sha256": sha(canonical(original_sections[section])),
            "original_review_sha256": sha(canonical(audit)),
            "original_deferral_input_sha256": original_binding,
            "repair_input_sha256": binding,
            "repair_request_characters": size,
            "receipt_sha256": sha(path.read_bytes()),
        },
    )


def prepare(root, job_key):
    folder, inventory, working, plan, units, original, _ = post.inputs(root, job_key)
    parent = post.verified_output(root, job_key)
    job = readonly_job(folder, inventory)
    path = folder / CONTRACT
    previous = json.loads(path.read_bytes()) if path.exists() else None
    numbers = {unit["id"]: number for number, unit in enumerate(units)}
    if previous is None:
        eligible, skipped = {}, {}
        for section in sorted(parent.get("unaccepted_sections", {})):
            receipt = folder / "private-manuscript-continuation" / (section + ".json")
            if not receipt.exists():
                skipped[section] = "no_original_size_deferral"
                continue
            saved = json.loads(receipt.read_bytes())
            deferred = saved["deferred_stages"].get(section + "-repair")
            if (
                not deferred
                or deferred["reason"]
                != "complete_original_request_exceeds_character_limit"
            ):
                skipped[section] = "not_an_unused_size_deferred_repair"
                continue
            if not unused_roles(folder, section):
                skipped[section] = "original_repair_or_final_review_role_consumed"
                continue
            if not (folder / (section + "-review.json")).exists():
                skipped[section] = "no_actual_primary_factual_review"
                continue
            try:
                _, _, _, eligible[section] = section_request(
                    job,
                    section,
                    numbers[section],
                    units,
                    plan,
                    original,
                    working,
                    inventory,
                )
            except ValueError as exc:
                if (
                    str(exc)
                    != "requires_deferred_original_repair_complete_compact_repair_fits"
                ):
                    raise
                skipped[section] = "complete_compact_request_still_exceeds_limit"
    else:
        eligible, skipped = previous["eligible"], previous["skipped"]
        for section, row in eligible.items():
            _, _, _, reconstructed = section_request(
                job,
                section,
                numbers[section],
                units,
                plan,
                original,
                working,
                inventory,
            )
            require(row == reconstructed, "unchanged_deferred_section_request")
    contract = {
        "policy": POLICY,
        "job": job_key,
        "inventory_sha256": sha(canonical(inventory)),
        "parent_article_sha256": sha(canonical(parent)),
        "working_sha256": sha(canonical(working)),
        "model_digest": job.model_digest,
        "eligible": eligible,
        "skipped": skipped,
        "original_stage_attempt_budget": 2,
        "original_semantic_repairs_per_section": 1,
        "additional_model_stage_roles": 0,
        "original_budget_refreshed": False,
        "source_or_point_truncation_allowed": False,
        "reader_publication_allowed": False,
        "module_sha256": {
            module.__name__: sha(Path(module.__file__).read_bytes())
            for module in (sys.modules[__name__], post, reviewable, composition, author)
        },
    }
    if previous is None:
        atomic_json(path, contract)
    else:
        require(previous == contract, "unchanged_execution_contract")
    return job, inventory, working, plan, units, original, parent, contract


def authenticated_stage(job, name, prompt, schema, context, packet, validate):
    cached = post.saved(job.root / (name + ".json"))
    _, binding = job.stage_input(
        composition.PROMPTS[prompt], schema, context, evidence_packet=packet
    )
    require(cached["input_sha256"] == binding, "same_actual_stage_cache")
    records = post.verify_requests(
        job, name, composition.PROMPTS[prompt], schema, context, packet
    )
    accepted = [row for _, row in records if row.get("accepted_structure")]
    require(
        len(accepted) == 1 and accepted[0] is records[-1][1], "one_terminal_actual_call"
    )
    validate_metrics(accepted[0]["model_metrics"], job.model_digest)
    require(
        json.loads(accepted[0]["raw_response"]) == cached["data"],
        "exact_raw_model_response",
    )
    validate(cached["data"])
    return cached["data"]


def repair_validator(ids, working, inventory, schema):
    def validate(value):
        jsonschema.validate(value, schema)
        reviewable.validate_reviewable(value, ids, working, inventory)

    return validate


def derive(parent, adopted, contract):
    value = copy.deepcopy(parent)
    for chapter in value["chapters"]:
        for number, section in enumerate(chapter["sections"]):
            if section["id"] in adopted:
                chapter["sections"][number] = {
                    "id": section["id"],
                    **adopted[section["id"]],
                }
    for section, candidate in adopted.items():
        require(
            section in value["unaccepted_sections"], "adopt_only_unaccepted_section"
        )
        expected = {
            key
            for key, row in value["private_coverage"].items()
            if row["section"] == section
        }
        require(
            set(candidate["coverage"]) == expected,
            "all_original_assigned_points_retained",
        )
        del value["unaccepted_sections"][section]
        for key, row in candidate["coverage"].items():
            require(
                value["private_coverage"][key]["section"] == section,
                "same_point_allocation",
            )
            value["private_coverage"][key] = {"section": section, **row}
    firsts = [chapter["sections"][0] for chapter in value["chapters"]]
    value["introduction"] = [firsts[0]["overview"]]
    value["objectives"] = [section["objective"] for section in firsts]
    value["takeaways"] = [section["takeaway"] for section in firsts]
    value["quiz"] = [
        section["quiz"]
        for chapter in value["chapters"]
        for section in chapter["sections"]
    ]
    value["deferred_original_repair_lineage"] = {
        "policy": POLICY,
        "parent_article_sha256": contract["parent_article_sha256"],
        "execution_sha256": sha(canonical(contract)),
        "adopted_sections": {key: sha(canonical(row)) for key, row in adopted.items()},
        "whole_lesson_coverage_verified": False,
        "reader_publication_allowed": False,
    }
    return value


class DeferredJob(composition.AuthoringJob):
    def progress(self, state, stage, **extra):
        self.current = stage
        atomic_json(
            self.root / "deferred-original-repair-progress.json",
            {
                **self.identity,
                "job": self.key,
                "state": state,
                "stage": stage,
                "updated": time.time(),
                "completed_stages": self.completed,
                "reader_publication_allowed": False,
                "source_deletion_allowed": False,
                **extra,
            },
        )


def execute_section(
    job, section, row, units, plan, original, working, inventory, *, execute=True
):
    context, schema, packet, reconstructed = section_request(
        job, section, row["number"], units, plan, original, working, inventory
    )
    require(row == reconstructed, "same_prepared_section")
    ids = units[row["number"]]["point_ids"]
    validate = repair_validator(ids, working, inventory, schema)
    if execute:
        job.stage(
            section + "-repair",
            author.REPAIR_PROMPT,
            schema,
            context,
            validate,
            evidence_packet=packet,
        )
    repaired = authenticated_stage(
        job,
        section + "-repair",
        author.REPAIR_PROMPT,
        schema,
        context,
        packet,
        validate,
    )
    candidate, changes = reviewable.propose_citations(repaired, ids, working, inventory)
    final_context = {
        key: value
        for key, value in context.items()
        if key not in {"candidate", "review"}
    }
    request = post.review_request(
        job, final_context, candidate, ids, working, inventory
    )
    require(request["characters"] <= post.CONTEXT_LIMIT, "complete_final_review_fits")
    if execute:
        job.stage(
            section + "-repair-review",
            author.REVIEW_PROMPT,
            request["schema"],
            request["context"],
            lambda value: author.validate_review(value, request["schema"]),
            evidence_packet=packet,
        )
    audit = post.verified_review(job, section + "-repair-review", request)
    proof = {
        "candidate_sha256": sha(canonical(candidate)),
        "audit_sha256": sha(canonical(audit)),
        "all_final_factual_checks_passed": all(author.verdicts(audit)),
        "citation_metadata_proposals": changes,
        "original_calls_only": True,
    }
    return candidate, proof


def result_for(job_key, parent, adopted, results, contract):
    private = derive(parent, adopted, contract)
    result = {
        "policy": POLICY,
        "job": job_key,
        "state": "private_deferred_original_repairs_complete",
        "execution_sha256": sha(canonical(contract)),
        "parent_article_sha256": contract["parent_article_sha256"],
        "private_article_sha256": sha(canonical(private)),
        "eligible": len(contract["eligible"]),
        "adopted_sections": list(adopted),
        "sections": results,
        "unaccepted_sections_remaining": len(private["unaccepted_sections"]),
        "original_budget_refreshed": False,
        "reader_publication_allowed": False,
        "whole_lesson_coverage_verified": False,
        "source_deletion_allowed": False,
    }
    return private, result


def verify_exhaustion(job, section, row, units, plan, original, working, inventory):
    context, schema, packet, _ = section_request(
        job, section, row["number"], units, plan, original, working, inventory
    )
    ids = units[row["number"]]["point_ids"]
    stage, prompt = section + "-repair", author.REPAIR_PROMPT
    if (job.root / (stage + ".json")).exists():
        repaired = authenticated_stage(
            job,
            stage,
            prompt,
            schema,
            context,
            packet,
            repair_validator(ids, working, inventory, schema),
        )
        candidate, _ = reviewable.propose_citations(repaired, ids, working, inventory)
        base = {
            key: value
            for key, value in context.items()
            if key not in {"candidate", "review"}
        }
        request = post.review_request(job, base, candidate, ids, working, inventory)
        context, schema = request["context"], request["schema"]
        stage, prompt = section + "-repair-review", author.REVIEW_PROMPT
    require(
        not (job.root / (stage + ".json")).exists(), "no_successful_exhausted_stage"
    )
    records = post.verify_requests(
        job, stage, composition.PROMPTS[prompt], schema, context, packet
    )
    require(
        len(records) == 2
        and not any(row.get("accepted_structure") for _, row in records),
        "two_actual_exhausted_original_calls",
    )


def verified_output(root, job_key):
    """Reconstruct adoption from real requests and reviews without model calls."""
    job, inventory, working, plan, units, original, parent, contract = prepare(
        root, job_key
    )
    state = json.loads(
        (job.root / "deferred-original-repair-progress.json").read_bytes()
    )
    require(
        state["stage"] == "complete"
        and state["state"] == "private_deferred_original_repairs_complete",
        "completed_original_repairs",
    )
    try:
        alive = (
            abs(psutil.Process(state["pid"]).create_time() - state["created"]) < 0.001
        )
    except psutil.NoSuchProcess:
        alive = False
    require(not alive, "repair_worker_exited")
    recorded = post.saved(job.root / "deferred-original-repair-result.json")["data"]
    adopted, results = {}, {}
    for section, row in contract["eligible"].items():
        if (
            recorded["sections"][section].get("state")
            == "original_stage_budget_exhausted"
        ):
            verify_exhaustion(
                job, section, row, units, plan, original, working, inventory
            )
            results[section] = {
                "state": "original_stage_budget_exhausted",
                "accepted": False,
            }
        else:
            candidate, proof = execute_section(
                job,
                section,
                row,
                units,
                plan,
                original,
                working,
                inventory,
                execute=False,
            )
            results[section] = proof
            if proof["all_final_factual_checks_passed"]:
                adopted[section] = candidate
    private, result = result_for(job_key, parent, adopted, results, contract)
    require(recorded == result, "exact_result_from_real_calls")
    require(
        post.saved(job.root / OUTPUT)["data"] == private,
        "exact_reconstructed_private_article",
    )
    return private


def run(root, job_key, *, wait_seconds=3600):
    from .course_pdf_ocr import local_model_digest

    require(type(wait_seconds) is int and 0 <= wait_seconds <= 3600, "finite_gpu_wait")
    root = Path(root).resolve()
    readonly, inventory, working, plan, units, original, parent, contract = prepare(
        root, job_key
    )
    store = TextbookStore(root)
    store.checkpoint()
    with schema_lease(
        root
        / "wiki/textbook-evidence"
        / contract["inventory_sha256"]
        / "authoring-worker.lock",
        timeout=0,
    ):
        job = DeferredJob(
            store, inventory, working, local_model_digest("qwen3.5:9b"), wait_seconds
        )
        require(job.key == readonly.key, "original_job_and_budget")
        adopted, results = {}, {}
        try:
            for section, row in contract["eligible"].items():
                try:
                    candidate, proof = execute_section(
                        job, section, row, units, plan, original, working, inventory
                    )
                except ValueError as exc:
                    # Exhaustion is terminal for this role, not a new plan.
                    if not str(exc).startswith("wiki_stage_attempt_budget_exhausted_"):
                        raise
                    results[section] = {
                        "state": "original_stage_budget_exhausted",
                        "accepted": False,
                    }
                    continue
                results[section] = proof
                if proof["all_final_factual_checks_passed"]:
                    adopted[section] = candidate
                job.progress(
                    "repairing_original_size_deferrals",
                    section,
                    adopted_sections=len(adopted),
                )
            private, result = result_for(job_key, parent, adopted, results, contract)
            author.save(job.root, OUTPUT.removesuffix(".json"), private)
            author.save(job.root, "deferred-original-repair-result", result)
            job.progress(
                result["state"],
                "complete",
                adopted_sections=len(adopted),
                unaccepted_sections_remaining=result["unaccepted_sections_remaining"],
            )
            return {key: value for key, value in result.items() if key != "sections"}
        except BaseException as exc:
            job.progress(
                "paused" if isinstance(exc, InterruptedError) else "stopped",
                job.current,
                error_type=type(exc).__name__,
                error_sha256=sha(str(exc).encode()),
            )
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--job", required=True)
    parser.add_argument("command", choices=["run", "guard"])
    args = parser.parse_args(argv)
    try:
        if args.command == "guard":
            from .course_archive_gpu import checked_sample
            from .course_gpu_guard import run as guarded

            guarded(
                args.root,
                [
                    sys.executable,
                    "-m",
                    __spec__.name,
                    "--root",
                    str(args.root.resolve()),
                    "--job",
                    args.job,
                    "run",
                ],
                maximum_power=200,
                maximum_temperature=70,
                guard_name="wiki-deferred-repair-thermal-guard",
                sampler=lambda: checked_sample(args.root),
            )
        else:
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
