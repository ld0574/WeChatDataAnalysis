"""Use an existing section's unused semantic repair after a failed write gate.

The original write failures remain exhausted. Only a fully validated repair and
its passing factual review can be promoted into the same writer's caches.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path

import jsonschema
import psutil

from . import course_textbook_authoring as author
from . import course_textbook_composition as composition
from .course_knowledge import atomic_json, schema_lease
from .course_library import canonical
from .course_textbook_evidence import sha
from .course_textbook_ledger import TextbookStore
from .course_wiki_text_inference import validate_metrics

POLICY = "existing-section-semantic-repair-after-primary-support-gate-v1"
ERROR = "requires_private_composition_primary_original_support_in_body"


def require(ok, reason):
    if not ok:
        raise ValueError("requires_existing_section_repair_" + reason)


def saved(path):
    result = json.loads(path.read_bytes())
    require(result["data_sha256"] == sha(canonical(result["data"])), "unchanged_cache")
    return result


def context_for_unit(folder, unit_number, units, plan, working, inventory):
    unit = units[unit_number]
    previous = None
    if unit_number:
        prior = units[unit_number - 1]
        prefix = prior["id"]
        review = saved(folder / (prefix + "-review.json"))["data"]
        suffix = "-write" if all(author.verdicts(review)) else "-repair"
        previous = saved(folder / (prefix + suffix + ".json"))["data"]
        composition.validate_section(previous, prior["point_ids"], working, inventory)
    return {
        **composition.writing_context(unit["point_ids"], working, inventory),
        "chapter": unit["chapter"],
        "concept": unit["concept"],
        "article_outline": [
            {key: plan["nodes"][node][key] for key in ("heading", "purpose")}
            for node in plan["chapters"]
        ],
        "diagram_preferred": unit_number == 0
        or units[unit_number - 1]["chapter_id"] != unit["chapter_id"],
        "chapter_scope": [
            {
                "section": other["id"],
                "point_labels": [
                    working["points"][key]["label"] for key in other["point_ids"]
                ],
            }
            for other in units
            if other["chapter_id"] == unit["chapter_id"]
        ],
        "previous_reading_section": {
            "heading": previous["heading"],
            "paragraphs": [row["text"] for row in previous["paragraphs"]],
        }
        if previous
        else None,
    }


def machine_audit(candidate, ids, working, inventory):
    """Reject the defect; a validation-only copy never becomes article prose."""
    jsonschema.validate(candidate, composition.section_schema(ids, working))
    patched = copy.deepcopy(candidate)
    missing = {}
    for key, row in candidate["coverage"].items():
        if key in working["pending_source_cards"] and not row["teaching"]:
            continue
        require(bool(row["paragraphs"]), "nonempty_original_mapping")
        require(
            all(
                0 <= index < len(candidate["paragraphs"]) for index in row["paragraphs"]
            ),
            "original_mapping_in_bounds",
        )
        refs = {
            ref
            for index in row["paragraphs"]
            for ref in candidate["paragraphs"][index]["citations"]
        }
        expected = set(working["points"][key]["citations"])
        if not refs.intersection(expected):
            missing[key] = sorted(expected)
            first = patched["paragraphs"][row["paragraphs"][0]]
            first["citations"] = sorted(set(first["citations"]) | expected)
    require(bool(missing), "actual_primary_support_defect")
    # The only tolerated incoming error is this citation gate. All other
    # structure, graph and scoped-source requirements must already hold.
    composition.validate_section(patched, ids, working, inventory)
    schema = author.review_schema(candidate, ids)

    def review_node(node):
        if "passed" in node.get("properties", {}):
            return {
                "passed": False,
                "reason": "Machine preflight only; semantic verification remains pending after repair.",
            }
        return {key: review_node(child) for key, child in node["properties"].items()}

    audit = review_node(schema)
    for key, refs in missing.items():
        audit["coverage"][key]["reason"] = (
            "Primary original support missing in the mapped body paragraphs. "
            "Check the actual teaching against the complete originals, repair the explanation and mapping together; "
            "adding a citation alone is insufficient. Required originals: "
            + ", ".join(refs)
        )
    author.validate_review(audit, schema)
    return audit, missing


def annotate_reviewed_candidate(candidate, audit, ids, working, inventory):
    author.validate_review(audit, author.review_schema(candidate, ids))
    require(all(author.verdicts(audit)), "passing_unannotated_factual_review")
    _, missing = machine_audit(candidate, ids, working, inventory)
    result = copy.deepcopy(candidate)
    changes = {}
    for key, refs in missing.items():
        for index in candidate["coverage"][key]["paragraphs"]:
            row = result["paragraphs"][index]
            old = list(row["citations"])
            row["citations"] = sorted(set(old) | set(refs))
            if row["citations"] != old:
                changes[str(index)] = {
                    "old": candidate["paragraphs"][index]["citations"],
                    "new": row["citations"],
                }
    composition.validate_section(result, ids, working, inventory)
    require(
        [p["text"] for p in result["paragraphs"]]
        == [p["text"] for p in candidate["paragraphs"]]
        and result["coverage"] == candidate["coverage"],
        "body_and_point_mappings_unchanged",
    )
    return result, changes


def confirm_failed_repair(
    job, prefix, context, candidate, machine, ids, working, inventory, packet
):
    """Spend only still-unused factual review roles, never another rewrite."""
    repair_context = {**context, "candidate": candidate, "review": machine}
    prompt = composition.PROMPTS[author.REPAIR_PROMPT]
    _, binding = job.stage_input(
        prompt,
        composition.section_schema(ids, working),
        repair_context,
        evidence_packet=packet,
    )
    paths = sorted((job.root / "attempts").glob(prefix + "-repair-0*.json"))
    require(
        [p.name for p in paths]
        == [prefix + "-repair-01.json", prefix + "-repair-02.json"],
        "original_exhausted_semantic_repair",
    )
    require(
        not (job.root / (prefix + "-repair.json")).exists(),
        "no_accepted_repair_replaced",
    )
    failures, candidates, previous = {}, [], ""
    for number, path in enumerate(paths):
        raw = path.read_bytes()
        record = json.loads(raw)
        require(
            record["input_sha256"] == binding
            and record["model_digest"] == job.model_digest
            and record["attempt"] == number + 1,
            "same_original_repair_binding",
        )
        messages = [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": json.dumps(
                    {**repair_context, "previous_validation_failure": previous},
                    ensure_ascii=False,
                ),
            },
        ]
        require(
            record["request_sha256"] == sha(canonical(messages)),
            "same_original_repair_request",
        )
        previous = record["validation_error"]
        failures[path.name] = sha(raw)
        if record["error_type"] == "ModelOutputTruncatedError":
            require(
                record["model_metrics"]["done_reason"] == "length",
                "retained_repair_truncation",
            )
            continue
        require(
            record["error_type"] == "ValueError" and previous == ERROR,
            "repair_has_only_annotation_defect",
        )
        validate_metrics(record["model_metrics"], job.model_digest)
        value = json.loads(record["raw_response"])
        machine_audit(value, ids, working, inventory)
        candidates.append(value)
    require(bool(candidates), "complete_unannotated_repair")
    value = candidates[0]
    execution_path = job.root / (prefix + "-citation-confirmation-execution.json")
    execution = {
        "policy": "review-confirmed-primary-citation-annotation-v1",
        "repair_input_sha256": binding,
        "candidate_sha256": sha(canonical(value)),
        "original_repair_attempts": failures,
        "additional_rewrite_calls": 0,
    }
    if execution_path.exists():
        require(
            json.loads(execution_path.read_bytes()) == execution,
            "same_citation_confirmation_budget",
        )
    else:
        require(
            not any((job.root / "attempts").glob(prefix + "-repair-review-*.json"))
            and not any((job.root / "attempts").glob(prefix + "-review-*.json"))
            and not (job.root / (prefix + "-repair-review.json")).exists()
            and not (job.root / (prefix + "-review.json")).exists(),
            "unused_factual_review_roles",
        )
        atomic_json(execution_path, execution)
    schema = author.review_schema(value, ids)
    job.current = prefix + "-repair-review"
    audit = job.stage(
        job.current,
        author.REVIEW_PROMPT,
        schema,
        {**context, "candidate": value},
        lambda row: author.validate_review(row, schema),
        evidence_packet=packet,
    )
    annotated, changes = annotate_reviewed_candidate(
        value, audit, ids, working, inventory
    )
    final_schema = author.review_schema(annotated, ids)
    job.current = prefix + "-review"
    final = job.stage(
        job.current,
        author.REVIEW_PROMPT,
        final_schema,
        {**context, "candidate": annotated},
        lambda row: author.validate_review(row, final_schema),
        evidence_packet=packet,
    )
    require(all(author.verdicts(final)), "passing_annotated_factual_review")
    require(
        all(
            sha((job.root / "attempts" / name).read_bytes()) == digest
            for name, digest in failures.items()
        ),
        "repair_failures_preserved",
    )
    return (
        annotated,
        final,
        {
            **execution,
            "citation_changes": changes,
            "body_text_and_coverage_mappings_unchanged": True,
        },
    )


def recover(
    root,
    inventory_key,
    ledger_job,
    job_key,
    stage,
    *,
    wait_seconds=3600,
    confirm_citations=False,
):
    from .course_pdf_ocr import local_model_digest

    require(re.fullmatch(r"[a-f0-9]{32}", job_key) is not None, "job_identity")
    match = re.fullmatch(r"section-([0-9]{4})-write", stage)
    require(match is not None, "failed_write_stage")
    require(type(wait_seconds) is int and 0 <= wait_seconds <= 3600, "finite_wait")
    root = Path(root).resolve()
    inventory, working = composition.load_inputs(root, inventory_key, ledger_job)
    folder = root / "wiki/textbook-composition-jobs" / job_key
    previous = json.loads((folder / "progress.json").read_bytes())
    prefix = stage.removesuffix("-write")
    require(
        previous["state"] in {"stopped", "paused"}
        and previous["stage"]
        in {
            stage,
            prefix + "-repair",
            prefix + "-repair-review",
            *([prefix + "-review"] if confirm_citations else []),
        },
        "exact_stopped_write_or_bound_repair",
    )
    try:
        alive = (
            abs(psutil.Process(previous["pid"]).create_time() - previous["created"])
            < 0.001
        )
    except psutil.NoSuchProcess:
        alive = False
    require(not alive, "original_worker_exited")
    store = TextbookStore(root)
    store.checkpoint()
    with schema_lease(
        root / "wiki/textbook-evidence" / inventory_key / "authoring-worker.lock",
        timeout=0,
    ):
        job = composition.AuthoringJob(
            store, inventory, working, local_model_digest("qwen3.5:9b"), wait_seconds
        )
        require(job.key == job_key, "same_bound_job_and_budget")
        contract = json.loads(
            (
                root / "wiki/textbook-keyed-jobs" / ledger_job / composition.CONTRACT
            ).read_bytes()
        )
        require(
            contract
            == {
                "inventory_sha256": inventory_key,
                "ledger_sha256": sha(canonical(working)),
                "job": job_key,
            },
            "same_execution_contract",
        )
        plan = saved(folder / "concept-plan.json")["data"]
        units = saved(folder / "writing-allocation.json")["data"]
        number = int(match[1])
        require(number < len(units), "existing_unit")
        unit = units[number]
        prefix = unit["id"]
        require(stage == prefix + "-write", "same_unit_identity")
        require(not (folder / (stage + ".json")).exists(), "no_accepted_write_replaced")
        require(
            not (folder / (prefix + "-review.json")).exists()
            or (
                confirm_citations
                and (
                    folder / (prefix + "-citation-confirmation-execution.json")
                ).exists()
            ),
            "no_existing_review_replaced",
        )
        context = context_for_unit(folder, number, units, plan, working, inventory)
        ids = unit["point_ids"]
        packet = composition.original_packet(ids, working, inventory)
        prompt = composition.PROMPTS[author.WRITE_PROMPT]
        _, binding = job.stage_input(
            prompt,
            composition.section_schema(ids, working),
            context,
            evidence_packet=packet,
        )
        paths = sorted((folder / "attempts").glob(stage + "-*.json"))
        require(
            [p.name for p in paths] == [stage + "-01.json", stage + "-02.json"],
            "original_exhausted_write_budget",
        )
        originals, candidates, prior_failure = {}, [], ""
        for index, path in enumerate(paths):
            raw = path.read_bytes()
            record = json.loads(raw)
            require(
                record["input_sha256"] == binding
                and record["stage"] == stage
                and record["attempt"] == index + 1
                and record["model_digest"] == job.model_digest,
                "exact_failed_write_binding",
            )
            request = [
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": json.dumps(
                        {**context, "previous_validation_failure": prior_failure},
                        ensure_ascii=False,
                    ),
                },
            ]
            require(
                record["request_sha256"] == sha(canonical(request)),
                "exact_original_write_request",
            )
            prior_failure = record["validation_error"]
            originals[path.name] = sha(raw)
            if record["error_type"] == "ModelOutputTruncatedError":
                require(
                    record["model_metrics"]["done_reason"] == "length",
                    "retained_truncated_attempt",
                )
                continue
            require(
                record["error_type"] == "ValueError"
                and record["validation_error"] == ERROR,
                "only_primary_support_failure",
            )
            validate_metrics(record["model_metrics"], job.model_digest)
            candidate = json.loads(record["raw_response"])
            audit, missing = machine_audit(candidate, ids, working, inventory)
            candidates.append((index + 1, candidate, audit, missing))
        require(bool(candidates), "complete_reviewable_original_candidate")
        selected, candidate, audit, missing = candidates[0]
        proof_path = folder / (prefix + "-existing-repair-recovery.json")
        require(not proof_path.exists(), "one_recovery_only")
        execution_path = folder / (prefix + "-existing-repair-execution.json")
        execution = {
            "policy": POLICY,
            "job": job_key,
            "failed_write_stage": stage,
            "write_input_sha256": binding,
            "original_attempts": originals,
            "candidate_sha256": sha(canonical(candidate)),
            "machine_audit_sha256": sha(canonical(audit)),
        }
        if execution_path.exists():
            retained = json.loads(execution_path.read_bytes())
            require(
                all(retained[key] == value for key, value in execution.items()),
                "unchanged_repair_execution",
            )
        else:
            require(previous["stage"] == stage, "new_execution_starts_at_failed_write")
            require(
                not any((folder / "attempts").glob(prefix + "-repair-*.json"))
                and not (folder / (prefix + "-repair.json")).exists()
                and not (folder / (prefix + "-repair-review.json")).exists(),
                "previously_unused_semantic_repair",
            )
            retained = {
                **execution,
                "completed_stages_before": previous["completed_stages"],
            }
            atomic_json(execution_path, retained)
        # These are precisely the original writer's -repair and -repair-review
        # stage slots, including its persistent two-attempt structural budgets.
        job.completed = retained["completed_stages_before"]
        job.finished_sections, job.section_count = number, len(units)
        try:
            confirmation = None
            if confirm_citations:
                repaired, reviewed, confirmation = confirm_failed_repair(
                    job,
                    prefix,
                    context,
                    candidate,
                    audit,
                    ids,
                    working,
                    inventory,
                    packet,
                )
                review_path = folder / (prefix + "-review.json")
            else:
                job.current = prefix + "-repair"
                repaired = job.stage(
                    prefix + "-repair",
                    author.REPAIR_PROMPT,
                    composition.section_schema(ids, working),
                    {**context, "candidate": candidate, "review": audit},
                    lambda value: composition.validate_section(
                        value, ids, working, inventory
                    ),
                    evidence_packet=packet,
                )
                review_schema = author.review_schema(repaired, ids)
                job.current = prefix + "-repair-review"
                reviewed = job.stage(
                    prefix + "-repair-review",
                    author.REVIEW_PROMPT,
                    review_schema,
                    {**context, "candidate": repaired},
                    lambda value: author.validate_review(value, review_schema),
                    evidence_packet=packet,
                )
                review_path = folder / (prefix + "-repair-review.json")
            review_schema = author.review_schema(repaired, ids)
            require(
                all(author.verdicts(reviewed)),
                "passing_existing_repair_semantic_review",
            )
            _, review_binding = job.stage_input(
                composition.PROMPTS[author.REVIEW_PROMPT],
                review_schema,
                {**context, "candidate": repaired},
                evidence_packet=packet,
            )
            accepted_review = saved(review_path)
            require(
                accepted_review["input_sha256"] == review_binding,
                "identical_reused_review_request",
            )
            require(
                all(
                    sha((folder / "attempts" / name).read_bytes()) == digest
                    for name, digest in originals.items()
                ),
                "original_failures_preserved",
            )
            proof = {
                "policy": POLICY,
                "job": job_key,
                "stage": stage,
                "selected_write_attempt": selected,
                "original_failed_write_attempts": originals,
                "original_primary_support_defects": missing,
                "repair_stage": prefix + "-repair",
                "review_stage": prefix + "-repair-review",
                "promoted_repair_sha256": sha(canonical(repaired)),
                "write_input_sha256": binding,
                "reused_review_input_sha256": review_binding,
                "semantic_repair_budget_refreshed": False,
                "original_write_budget_remains_exhausted": True,
                "reader_publication_allowed": False,
                "semantic_accuracy_verified": False,
                "module_sha256": sha(Path(__file__).read_bytes()),
                "citation_confirmation": confirmation,
            }
            atomic_json(proof_path, proof)
            digest = sha(proof_path.read_bytes())
            if confirmation:
                atomic_json(
                    folder / (prefix + "-repair.json"),
                    {
                        "input_sha256": confirmation["repair_input_sha256"],
                        "data_sha256": sha(canonical(repaired)),
                        "data": repaired,
                        "recovery_sha256": digest,
                    },
                )
            atomic_json(
                folder / (stage + ".json"),
                {
                    "input_sha256": binding,
                    "data_sha256": sha(canonical(repaired)),
                    "data": repaired,
                    "recovery_sha256": digest,
                },
            )
            atomic_json(
                folder / (prefix + "-review.json"),
                {**accepted_review, "recovery_sha256": digest},
            )
            job.finished_sections = number + 1
            job.progress("existing_section_repair_ready_to_resume", stage)
            return {
                "state": "existing_section_repair_ready_to_resume",
                "job": job_key,
                "stage": stage,
                "repaired_primary_support_defects": len(missing),
                "receipt_sha256": digest,
                "semantic_repair_budget_refreshed": False,
                "reader_publication_allowed": False,
            }
        except BaseException as exc:
            job.progress(
                "paused" if isinstance(exc, InterruptedError) else "stopped",
                job.current,
                error_type=type(exc).__name__,
            )
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("inventory", "ledger-job", "job", "stage"):
        parser.add_argument("--" + flag, required=True)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--resume-composition", action="store_true")
    parser.add_argument("--confirm-citations", action="store_true")
    parser.add_argument("command", choices=["run", "guard"])
    args = parser.parse_args(argv)
    try:
        if args.command == "guard":
            from .course_archive_gpu import checked_sample
            from .course_gpu_guard import run as guarded

            command = [
                sys.executable,
                "-m",
                __spec__.name,
                "--root",
                str(args.root.resolve()),
                "--inventory",
                args.inventory,
                "--ledger-job",
                args.ledger_job,
                "--job",
                args.job,
                "--stage",
                args.stage,
                "run",
            ]
            if args.resume_composition:
                command.append("--resume-composition")
            if args.confirm_citations:
                command.append("--confirm-citations")
            guarded(
                args.root,
                command,
                maximum_power=200,
                maximum_temperature=70,
                guard_name="wiki-revision-thermal-guard",
                sampler=lambda: checked_sample(args.root),
            )
        else:
            print(
                json.dumps(
                    recover(
                        args.root,
                        args.inventory,
                        args.ledger_job,
                        args.job,
                        args.stage,
                        confirm_citations=args.confirm_citations,
                    )
                )
            )
            if args.resume_composition:
                print(
                    json.dumps(
                        author.run(
                            args.root,
                            args.inventory,
                            args.ledger_job,
                            protocol=composition,
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
