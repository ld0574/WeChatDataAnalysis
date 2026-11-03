"""Review a source-scoped failed draft before spending its existing repair role.

Citation proposals remain private hypotheses until every factual check passes.
All calls use the original writer's review/repair/repair-review stage budgets.
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
from . import course_textbook_provisional_continue as provisional
from . import course_textbook_section_repair as repair
from .course_knowledge import atomic_json, schema_lease
from .course_library import canonical
from .course_textbook_evidence import sha
from .course_textbook_ledger import TextbookStore
from .course_wiki_text_inference import validate_metrics

POLICY = "review-before-rewrite-with-original-stage-budgets-v1"
MAPPING_REASON_CHARACTERS = 24


def require(ok, reason):
    if not ok:
        raise ValueError("requires_reviewable_write_" + reason)


def context_for_unit(
    root,
    inventory_key,
    ledger_job,
    job_key,
    number,
    units,
    plan,
    working,
    inventory,
    unaccepted,
):
    previous = None
    folder = Path(root) / "wiki/textbook-composition-jobs" / job_key
    if number:
        prior = units[number - 1]
        if prior["id"] == unaccepted:
            previous = provisional.prepare(
                root, inventory_key, ledger_job, job_key, unaccepted
            )[3]
        else:
            audit = repair.saved(folder / (prior["id"] + "-review.json"))["data"]
            suffix = "-write" if all(author.verdicts(audit)) else "-repair"
            previous = repair.saved(folder / (prior["id"] + suffix + ".json"))["data"]
            composition.validate_section(
                previous, prior["point_ids"], working, inventory
            )
    unit = units[number]
    return {
        **composition.writing_context(unit["point_ids"], working, inventory),
        "chapter": unit["chapter"],
        "concept": unit["concept"],
        "article_outline": [
            {key: plan["nodes"][node][key] for key in ("heading", "purpose")}
            for node in plan["chapters"]
        ],
        "diagram_preferred": number == 0
        or units[number - 1]["chapter_id"] != unit["chapter_id"],
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
            "paragraphs": [p["text"] for p in previous["paragraphs"]],
        }
        if previous
        else None,
    }


def validate_reviewable(value, ids, working, inventory):
    try:
        composition.validate_section(value, ids, working, inventory)
    except ValueError as exc:
        require(str(exc) == repair.ERROR, "only_primary_citation_gate_deferred")
        repair.machine_audit(value, ids, working, inventory)


def propose_citations(value, ids, working, inventory):
    """An audit input proposal, never evidence that a paragraph is correct."""
    validate_reviewable(value, ids, working, inventory)
    result = copy.deepcopy(value)
    changes = {}
    for key, row in value["coverage"].items():
        if key in working["pending_source_cards"] and not row["teaching"]:
            continue
        expected = set(working["points"][key]["citations"])
        present = {
            ref
            for index in row["paragraphs"]
            for ref in value["paragraphs"][index]["citations"]
        }
        if present.intersection(expected):
            continue
        for index in row["paragraphs"]:
            paragraph = result["paragraphs"][index]
            paragraph["citations"] = sorted(set(paragraph["citations"]) | expected)
            changes[str(index)] = {
                "old": value["paragraphs"][index]["citations"],
                "new": paragraph["citations"],
            }
    composition.validate_section(result, ids, working, inventory)
    require(
        result["coverage"] == value["coverage"]
        and [p["text"] for p in result["paragraphs"]]
        == [p["text"] for p in value["paragraphs"]],
        "only_citation_metadata_proposed",
    )
    return result, changes


def repair_guidance(audit):
    """Keep every failed reason; passed explanations are not repair evidence."""
    if "passed" in audit:
        return (
            {**audit, "reason": "Passed; preserve."}
            if audit["passed"]
            else copy.deepcopy(audit)
        )
    return {key: repair_guidance(value) for key, value in audit.items()}


def repair_schema(ids, working):
    schema = copy.deepcopy(composition.section_schema(ids, working))
    for row in schema["properties"]["coverage"]["properties"].values():
        row["properties"]["reason"] = {
            **row["properties"]["reason"],
            "maxLength": MAPPING_REASON_CHARACTERS,
        }
    return schema


class ReviewableJob(composition.AuthoringJob):
    def __init__(self, *args, unaccepted_before=0, **kwargs):
        self.unaccepted_before = unaccepted_before
        super().__init__(*args, **kwargs)

    def progress(self, state, stage, **extra):
        super().progress(
            state,
            stage,
            **{
                "draft_sections_processed": self.finished_sections,
                "sections_retained_unaccepted": self.unaccepted_before,
                "completed_sections": max(
                    0, self.finished_sections - self.unaccepted_before
                ),
                **extra,
            },
        )


def promote(folder, path, inventory, working, ids):
    receipt = json.loads(path.read_bytes())
    require(
        receipt["job"] == folder.name
        and re.fullmatch(r"section-[0-9]{4}-write", receipt["stage"]) is not None
        and receipt["section"] == receipt["stage"].removesuffix("-write"),
        "promotion_identity",
    )
    expected = {receipt["stage"] + ".json", receipt["section"] + "-review.json"}
    require(
        expected
        <= set(receipt["payloads"])
        <= expected | {receipt["section"] + "-repair.json"},
        "promotion_cache_scope",
    )
    require(
        all(
            payload["data_sha256"] == sha(canonical(payload["data"]))
            for payload in receipt["payloads"].values()
        ),
        "unchanged_promotion_payloads",
    )
    final = receipt["payloads"][receipt["stage"] + ".json"]["data"]
    composition.validate_section(final, ids, working, inventory)
    audit = receipt["payloads"][receipt["section"] + "-review.json"]["data"]
    author.validate_review(audit, author.review_schema(final, ids))
    require(all(author.verdicts(audit)), "passing_final_factual_review")
    actual = repair.saved(folder / (receipt["section"] + "-repair-review.json"))
    require(
        actual == receipt["payloads"][receipt["section"] + "-review.json"],
        "actual_final_model_review_preserved",
    )
    require(
        all(
            sha((folder / "attempts" / name).read_bytes()) == digest
            for name, digest in receipt["attempt_files"].items()
        ),
        "all_model_attempts_preserved",
    )
    digest = sha(path.read_bytes())
    for name, payload in receipt["payloads"].items():
        target = folder / name
        promoted = {**payload, "recovery_sha256": digest}
        if target.exists():
            if json.loads(target.read_bytes()) == promoted:
                continue
            archive = receipt["old_cache_archives"].get(name)
            require(
                archive is not None and sha(target.read_bytes()) == archive["sha256"],
                "only_archived_pre_promotion_cache_replaced",
            )
            require(
                sha((folder / archive["path"]).read_bytes()) == archive["sha256"],
                "original_cache_bytes_preserved",
            )
        atomic_json(target, promoted)
    return {
        "state": "reviewed_section_ready_to_resume",
        "job": receipt["job"],
        "stage": receipt["stage"],
        "semantic_repair_used": receipt["semantic_repair_used"],
        "citation_paragraphs_proposed": len(receipt["citation_changes"]),
        "receipt_sha256": digest,
        "reader_publication_allowed": False,
        "original_write_budget_remains_exhausted": True,
    }


def recover(
    root,
    inventory_key,
    ledger_job,
    job_key,
    stage,
    *,
    unaccepted=None,
    wait_seconds=3600,
):
    from .course_pdf_ocr import local_model_digest

    root = Path(root).resolve()
    require(re.fullmatch(r"[a-f0-9]{32}", job_key) is not None, "job_identity")
    match = re.fullmatch(r"(section-([0-9]{4}))-write", stage)
    require(match is not None, "original_write_stage")
    require(
        unaccepted is None or re.fullmatch(r"section-[0-9]{4}", unaccepted) is not None,
        "unaccepted_section_identity",
    )
    require(type(wait_seconds) is int and 0 <= wait_seconds <= 3600, "finite_gpu_wait")
    prefix, number = match[1], int(match[2])
    inventory, working = composition.load_inputs(root, inventory_key, ledger_job)
    folder = root / "wiki/textbook-composition-jobs" / job_key
    progress = json.loads((folder / "progress.json").read_bytes())
    try:
        alive = (
            abs(psutil.Process(progress["pid"]).create_time() - progress["created"])
            < 0.001
        )
    except psutil.NoSuchProcess:
        alive = False
    require(not alive, "previous_worker_exited")
    execution_path = folder / (prefix + "-reviewable-execution.json")
    receipt_path = folder / (prefix + "-reviewable-promotion.json")
    if not execution_path.exists():
        require(
            progress["state"] == "stopped" and progress["stage"] == stage,
            "exact_failed_write",
        )
    store = TextbookStore(root)
    store.checkpoint()
    with schema_lease(
        root / "wiki/textbook-evidence" / inventory_key / "authoring-worker.lock",
        timeout=0,
    ):
        job = ReviewableJob(
            store,
            inventory,
            working,
            local_model_digest("qwen3.5:9b"),
            wait_seconds,
            unaccepted_before=int(
                unaccepted is not None and int(unaccepted[-4:]) < number
            ),
        )
        require(job.key == job_key, "unchanged_original_job_and_budgets")
        original_contract = json.loads(
            (
                root / "wiki/textbook-keyed-jobs" / ledger_job / composition.CONTRACT
            ).read_bytes()
        )
        require(
            original_contract
            == {
                "inventory_sha256": inventory_key,
                "ledger_sha256": sha(canonical(working)),
                "job": job_key,
            },
            "unchanged_authoring_execution",
        )
        units = repair.saved(folder / "writing-allocation.json")["data"]
        plan = repair.saved(folder / "concept-plan.json")["data"]
        require(
            number < len(units) and units[number]["id"] == prefix,
            "original_writing_allocation",
        )
        ids = units[number]["point_ids"]
        context = context_for_unit(
            root,
            inventory_key,
            ledger_job,
            job_key,
            number,
            units,
            plan,
            working,
            inventory,
            unaccepted,
        )
        packet = composition.original_packet(ids, working, inventory)
        prompt = composition.PROMPTS[author.WRITE_PROMPT]
        _, binding = job.stage_input(
            prompt,
            composition.section_schema(ids, working),
            context,
            evidence_packet=packet,
        )
        attempts = sorted((folder / "attempts").glob(stage + "-*.json"))
        require(
            [p.name for p in attempts] == [stage + "-01.json", stage + "-02.json"],
            "exhausted_original_write_budget",
        )
        original, candidates, previous = {}, [], ""
        for position, path in enumerate(attempts):
            raw = path.read_bytes()
            record = json.loads(raw)
            require(
                record["input_sha256"] == binding
                and record["attempt"] == position + 1
                and record["stage"] == stage
                and record["model_digest"] == job.model_digest,
                "exact_write_attempt_identity",
            )
            messages = [
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": json.dumps(
                        {**context, "previous_validation_failure": previous},
                        ensure_ascii=False,
                    ),
                },
            ]
            require(
                record["request_sha256"] == sha(canonical(messages)),
                "exact_original_write_request",
            )
            previous = record["validation_error"]
            original[path.name] = sha(raw)
            if record["error_type"] == "ModelOutputTruncatedError":
                require(
                    record["model_metrics"]["done_reason"] == "length",
                    "retained_truncated_write",
                )
                continue
            require(
                record["error_type"] == "ValueError" and previous == repair.ERROR,
                "only_primary_citation_failure",
            )
            validate_metrics(record["model_metrics"], job.model_digest)
            value = json.loads(record["raw_response"])
            repair.machine_audit(value, ids, working, inventory)
            candidates.append((position + 1, value))
        require(bool(candidates), "complete_source_scoped_candidate")
        selected, candidate = candidates[0]
        execution = {
            "policy": POLICY,
            "job": job_key,
            "stage": stage,
            "write_input_sha256": binding,
            "candidate_sha256": sha(canonical(candidate)),
            "selected_write_attempt": selected,
            "original_write_attempts": original,
            "unaccepted_previous_section": unaccepted,
            "module_sha256": sha(Path(__file__).read_bytes()),
            "semantic_repair_limit": 1,
            "helper_sha256": sha(Path(repair.__file__).read_bytes()),
            "stage_structural_attempt_limit": 2,
            "original_recipe_changed": False,
            "repair_guidance_projection": "all_failed_reasons_retained_passed_reasons_replaced_by_marker",
            "repair_mapping_reason_maximum_characters": MAPPING_REASON_CHARACTERS,
        }
        if execution_path.exists():
            saved = json.loads(execution_path.read_bytes())
            require(
                all(saved[key] == value for key, value in execution.items()),
                "unchanged_review_execution",
            )
        else:
            require(
                not any((folder / "attempts").glob(prefix + "-review-*.json"))
                and not any((folder / "attempts").glob(prefix + "-repair-*.json"))
                and not (folder / (stage + ".json")).exists(),
                "original_review_and_repair_roles_unused",
            )
            saved = {
                **execution,
                "completed_stages_before": progress["completed_stages"],
            }
            atomic_json(execution_path, saved)
        job.completed = saved["completed_stages_before"]
        job.section_count, job.finished_sections = len(units), number
        try:
            if receipt_path.exists():
                result = promote(folder, receipt_path, inventory, working, ids)
            else:

                def review(value, suffix):
                    schema = author.review_schema(value, ids)
                    job.current = prefix + suffix
                    return job.stage(
                        job.current,
                        author.REVIEW_PROMPT,
                        schema,
                        {**context, "candidate": value},
                        lambda row: author.validate_review(row, schema),
                        evidence_packet=packet,
                    )

                audit = review(candidate, "-review")
                repaired = not all(author.verdicts(audit))
                if repaired:
                    job.current = prefix + "-repair"
                    schema = repair_schema(ids, working)

                    def validate_repair(value):
                        jsonschema.validate(value, schema)
                        validate_reviewable(value, ids, working, inventory)

                    candidate = job.stage(
                        job.current,
                        author.REPAIR_PROMPT,
                        schema,
                        {
                            **context,
                            "candidate": candidate,
                            "review": repair_guidance(audit),
                        },
                        validate_repair,
                        evidence_packet=packet,
                    )
                proposal, changes = propose_citations(
                    candidate, ids, working, inventory
                )
                final_audit = review(proposal, "-repair-review")
                require(
                    all(author.verdicts(final_audit)), "passing_final_factual_review"
                )
                _, final_binding = job.stage_input(
                    composition.PROMPTS[author.REVIEW_PROMPT],
                    author.review_schema(proposal, ids),
                    {**context, "candidate": proposal},
                    evidence_packet=packet,
                )
                final_review = repair.saved(folder / (prefix + "-repair-review.json"))
                require(
                    final_review["input_sha256"] == final_binding,
                    "actual_final_review_request",
                )
                payloads = {
                    stage + ".json": {
                        "input_sha256": binding,
                        "data_sha256": sha(canonical(proposal)),
                        "data": proposal,
                    },
                    prefix + "-review.json": final_review,
                }
                if repaired:
                    raw_repair = repair.saved(folder / (prefix + "-repair.json"))
                    payloads[prefix + "-repair.json"] = {
                        **raw_repair,
                        "data_sha256": sha(canonical(proposal)),
                        "data": proposal,
                    }
                archives = {}
                for name in payloads:
                    path = folder / name
                    if not path.exists():
                        continue
                    require(name != stage + ".json", "no_accepted_write_replaced")
                    raw = path.read_bytes()
                    archive = folder / (
                        name.removesuffix(".json") + "-before-reviewable-promotion.json"
                    )
                    if archive.exists():
                        require(
                            archive.read_bytes() == raw,
                            "unchanged_original_cache_archive",
                        )
                    else:
                        archive.write_bytes(raw)
                    archives[name] = {"path": archive.name, "sha256": sha(raw)}
                receipt = {
                    "policy": POLICY,
                    "job": job_key,
                    "stage": stage,
                    "section": prefix,
                    "semantic_repair_used": repaired,
                    "citation_changes": changes,
                    "payloads": payloads,
                    "old_cache_archives": archives,
                    "factual_checks_all_passed": True,
                    "annotation_was_a_private_proposal_until_final_review": True,
                    "attempt_files": {
                        p.name: sha(p.read_bytes())
                        for p in (folder / "attempts").glob(prefix + "-*.json")
                    },
                    "source_deletion_allowed": False,
                    "reader_publication_allowed": False,
                }
                atomic_json(receipt_path, receipt)
                result = promote(folder, receipt_path, inventory, working, ids)
            job.finished_sections = number + 1
            job.progress(result["state"], stage)
            return result
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
    parser.add_argument("--unaccepted-section")
    parser.add_argument("--resume-composition", action="store_true")
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
            if args.unaccepted_section:
                command += ["--unaccepted-section", args.unaccepted_section]
            if args.resume_composition:
                command.append("--resume-composition")
            guarded(
                args.root,
                command,
                maximum_power=200,
                maximum_temperature=70,
                guard_name="wiki-revision-thermal-guard",
                sampler=lambda: checked_sample(args.root),
            )
        else:
            protocol = (
                provisional.protocol_for(
                    args.root,
                    args.inventory,
                    args.ledger_job,
                    args.job,
                    args.unaccepted_section,
                )
                if args.resume_composition and args.unaccepted_section
                else composition
            )
            print(
                json.dumps(
                    recover(
                        args.root,
                        args.inventory,
                        args.ledger_job,
                        args.job,
                        args.stage,
                        unaccepted=args.unaccepted_section,
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
                            protocol=protocol,
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
