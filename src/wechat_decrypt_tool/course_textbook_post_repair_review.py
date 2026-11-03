"""Review citation proposals using unused original post-repair roles.

Original writer artifacts remain immutable. A derived private manuscript may
adopt a repaired section only after a real factual review passes in full.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import time
from pathlib import Path

import psutil

from . import course_textbook_authoring as author
from . import course_textbook_composition as composition
from . import course_textbook_reviewable_write as reviewable
from .course_knowledge import atomic_json, schema_lease
from .course_library import canonical
from .course_textbook_evidence import sha
from .course_textbook_ledger import TextbookStore
from .course_wiki_text_inference import validate_metrics

POLICY = "immutable-private-manuscript-original-post-repair-reviews-v1"
CONTRACT = "post-repair-review-execution.json"
OUTPUT = "private-post-repair-article.json"
CONTEXT_LIMIT = 35000


def require(ok, reason):
    if not ok:
        raise ValueError("requires_post_repair_review_" + reason)


def saved(path):
    return reviewable.repair.saved(path)


def inputs(root, job_key):
    require(re.fullmatch(r"[a-f0-9]{32}", job_key) is not None, "job_identity")
    root = Path(root).resolve()
    folder = root / "wiki/textbook-composition-jobs" / job_key
    state = json.loads((folder / "progress.json").read_bytes())
    require(state["stage"] == "private_article_assembled", "completed_private_parent")
    try:
        alive = (
            abs(psutil.Process(state["pid"]).create_time() - state["created"]) < 0.001
        )
    except psutil.NoSuchProcess:
        alive = False
    require(not alive, "writer_exited")
    private = saved(folder / "private-article.json")["data"]
    inventory, working = composition.load_inputs(
        root, private["inventory_sha256"], private["ledger_job"]
    )
    plan = saved(folder / "concept-plan.json")["data"]
    units = saved(folder / "writing-allocation.json")["data"]
    sections = {
        section["id"]: {key: value for key, value in section.items() if key != "id"}
        for chapter in private["chapters"]
        for section in chapter["sections"]
    }
    require(set(sections) == {unit["id"] for unit in units}, "all_original_sections")
    baseline = composition.assemble(inventory, working, plan, units, sections)
    require(
        {key: value for key, value in private.items() if key != "unaccepted_sections"}
        == baseline,
        "exact_original_private_assembly",
    )
    return folder, inventory, working, plan, units, sections, private


def context_for(number, units, plan, sections, working, inventory):
    unit = units[number]
    previous = sections[units[number - 1]["id"]] if number else None
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
            "paragraphs": [row["text"] for row in previous["paragraphs"]],
        }
        if previous
        else None,
    }


def candidate_from_attempt(path, stage, model_digest, ids, working, inventory):
    record = json.loads(path.read_bytes())
    require(record["stage"] == stage, "original_repair_stage")
    require(record["model_digest"] == model_digest, "original_repair_model")
    require(
        record.get("error_type") == "ValueError"
        and record.get("validation_error") == reviewable.repair.ERROR,
        "only_primary_citation_structure_defect",
    )
    require(isinstance(record.get("model_metrics"), dict), "actual_model_metrics")
    validate_metrics(record["model_metrics"], model_digest)
    candidate = json.loads(record["raw_response"])
    proposed, changes = reviewable.propose_citations(candidate, ids, working, inventory)
    return proposed, changes


def original_schema(job, section, ids, working):
    schema = composition.section_schema(ids, working)
    continuation = job.root / "private-manuscript-continuation-execution.json"
    if continuation.exists():
        bound = json.loads(continuation.read_bytes())
        require(bound["original_stage_attempt_budget"] == 2, "original_repair_limit")
        if section in bound["bounded_mapping_reason_sections"]:
            schema = reviewable.repair_schema(ids, working)
    return schema


def verify_requests(job, stage, prompt, schema, context, packet):
    """Authenticate retained calls without allocating or running a model role."""
    _, binding = job.stage_input(prompt, schema, context, evidence_packet=packet)
    paths = sorted((job.root / "attempts").glob(stage + "-[0-9][0-9].json"))
    require(
        0 < len(paths) <= 2
        and [path.name for path in paths]
        == [stage + f"-{number:02d}.json" for number in range(1, len(paths) + 1)],
        "original_attempt_sequence",
    )
    previous, records = "", []
    for number, path in enumerate(paths, 1):
        record = json.loads(path.read_bytes())
        messages = [
            {"role": "system", "content": prompt + job.range_notice},
            {
                "role": "user",
                "content": json.dumps(
                    {**context, "previous_validation_failure": previous},
                    ensure_ascii=False,
                ),
            },
        ]
        require(
            record["stage"] == stage
            and record["attempt"] == number
            and record["model_digest"] == job.model_digest
            and record["input_sha256"] == binding
            and record["request_sha256"] == sha(canonical(messages)),
            "actual_original_request",
        )
        records.append((path, record))
        previous = record.get("validation_error", "")
    return records


def verify_original_repair(
    job, section, path, context, original, ids, working, inventory
):
    schema = original_schema(job, section, ids, working)
    audit = saved(job.root / (section + "-review.json"))["data"]
    context = {**context, "candidate": original, "review": audit}
    records = verify_requests(
        job,
        section + "-repair",
        composition.PROMPTS[author.REPAIR_PROMPT],
        schema,
        context,
        composition.original_packet(ids, working, inventory),
    )
    require(any(prior == path for prior, _ in records), "original_repair_record")


def review_request(job, context, candidate, ids, working, inventory):
    schema = author.review_schema(candidate, ids)
    context = {**context, "candidate": candidate}
    packet = composition.original_packet(ids, working, inventory)
    actual_schema, binding = job.stage_input(
        composition.PROMPTS[author.REVIEW_PROMPT],
        schema,
        context,
        evidence_packet=packet,
    )
    messages = [
        {
            "role": "system",
            "content": composition.PROMPTS[author.REVIEW_PROMPT] + job.range_notice,
        },
        {
            "role": "user",
            "content": json.dumps(
                {**context, "previous_validation_failure": ""}, ensure_ascii=False
            ),
        },
    ]
    return {
        "schema": schema,
        "context": context,
        "packet": packet,
        "binding": binding,
        "characters": len(
            canonical({"messages": messages, "schema": actual_schema}).decode("utf-8")
        ),
    }


def verified_review(job, stage, request):
    cache = saved(job.root / (stage + ".json"))
    require(cache["input_sha256"] == request["binding"], "actual_frozen_review_cache")
    value = cache["data"]
    author.validate_review(value, request["schema"])
    records = verify_requests(
        job,
        stage,
        composition.PROMPTS[author.REVIEW_PROMPT],
        request["schema"],
        request["context"],
        request["packet"],
    )
    matching = []
    for path, record in records:
        if record.get("accepted_structure"):
            validate_metrics(record["model_metrics"], job.model_digest)
            require(
                json.loads(record["raw_response"]) == value, "actual_model_review_bytes"
            )
            matching.append(path)
    require(len(matching) == 1, "one_actual_post_repair_model_review")
    return value


def reviewed_write_candidate(job, section, context, original, ids, working, inventory):
    """Only a genuinely passing pre-review permits a citation-only write proposal."""
    receipt_path = job.root / "private-manuscript-continuation" / (section + ".json")
    if not receipt_path.exists():
        return None
    receipt = json.loads(receipt_path.read_bytes())
    require(
        receipt["section"] == section
        and receipt["contract_sha256"]
        == sha(
            canonical(
                json.loads(
                    (
                        job.root / "private-manuscript-continuation-execution.json"
                    ).read_bytes()
                )
            )
        )
        and receipt["context"] == context
        and receipt["context_sha256"] == sha(canonical(context))
        and receipt["candidate"] == original
        and receipt["candidate_sha256"] == sha(canonical(original))
        and receipt["current_body_sha256"] == sha(canonical(original))
        and all(
            sha((job.root / "attempts" / name).read_bytes()) == digest
            for name, digest in receipt["original_attempts"].items()
        ),
        "unchanged_retained_write_and_context",
    )
    if any((job.root / "attempts").glob(section + "-repair-0*.json")):
        return None
    if not (job.root / (section + "-review.json")).exists():
        return None
    request = review_request(job, context, original, ids, working, inventory)
    audit = verified_review(job, section + "-review", request)
    if not all(author.verdicts(audit)):
        return None
    records = verify_requests(
        job,
        section + "-write",
        composition.PROMPTS[author.WRITE_PROMPT],
        original_schema(job, section, ids, working),
        context,
        composition.original_packet(ids, working, inventory),
    )
    for path, record in records:
        if (
            record.get("validation_error") == reviewable.repair.ERROR
            and json.loads(record["raw_response"]) == original
        ):
            candidate, changes = candidate_from_attempt(
                path, section + "-write", job.model_digest, ids, working, inventory
            )
            return path, candidate, changes, receipt_path
    raise ValueError("requires_post_repair_review_original_retained_write_record")


def prepare(job, private, units, plan, sections, working, inventory):
    folder = job.root
    original = {
        "policy": POLICY,
        "job": folder.name,
        "parent_private_sha256": sha(canonical(private)),
        "parent_manifest_sha256": sha((folder / "manifest.json").read_bytes()),
        "working_sha256": sha(canonical(working)),
        "module_sha256": sha(Path(__file__).read_bytes()),
        "helper_sha256": sha(Path(reviewable.__file__).read_bytes()),
        "model_digest": job.model_digest,
        "additional_model_stage_roles": 0,
        "original_stage_attempt_budget": 2,
        "semantic_rewrite_calls": 0,
        "context_limit_characters": CONTEXT_LIMIT,
        "reader_publication_allowed": False,
    }
    path = folder / CONTRACT
    if path.exists():
        execution = json.loads(path.read_bytes())
        require(
            all(execution[key] == value for key, value in original.items()),
            "unchanged_execution",
        )
        for row in execution["eligible"].values():
            require(
                sha((folder / "attempts" / row["attempt"]).read_bytes())
                == row["attempt_sha256"],
                "unchanged_repair_attempt",
            )
            require(
                all(
                    sha((folder / name).read_bytes()) == digest
                    for name, digest in row["support_files"].items()
                ),
                "unchanged_candidate_evidence",
            )
        return execution
    eligible, skipped, invalid_attempts = {}, {}, {}
    numbers = {unit["id"]: number for number, unit in enumerate(units)}
    for section in sorted(private.get("unaccepted_sections", {})):
        require(section in numbers, "known_unaccepted_section")
        stage = section + "-repair-review"
        if (folder / (stage + ".json")).exists() or any(
            (folder / "attempts").glob(stage + "-*.json")
        ):
            skipped[section] = "original_post_repair_role_already_used"
            continue
        number = numbers[section]
        ids = units[number]["point_ids"]
        context = context_for(number, units, plan, sections, working, inventory)
        kind, support = "repair", []
        candidate = None
        for attempt in reversed(
            sorted((folder / "attempts").glob(section + "-repair-0*.json"))
        ):
            record = json.loads(attempt.read_bytes())
            if record.get("validation_error") != reviewable.repair.ERROR:
                continue
            try:
                candidate, changes = candidate_from_attempt(
                    attempt,
                    section + "-repair",
                    job.model_digest,
                    ids,
                    working,
                    inventory,
                )
            except ValueError as exc:
                invalid_attempts[attempt.name] = sha(str(exc).encode())
                continue
            break
        if candidate is None:
            recovered = reviewed_write_candidate(
                job, section, context, sections[section], ids, working, inventory
            )
            if recovered is None:
                skipped[section] = "no_complete_primary_citation_only_repair_candidate"
                continue
            attempt, candidate, changes, receipt_path = recovered
            kind = "write"
            support.append(receipt_path)
            support.extend((folder / "attempts").glob(section + "-review-*.json"))
        else:
            verify_original_repair(
                job,
                section,
                attempt,
                context,
                sections[section],
                ids,
                working,
                inventory,
            )
        support.append(folder / (section + "-review.json"))
        support.extend(
            (folder / "attempts").glob(section + "-" + kind + "-[0-9][0-9].json")
        )
        continuation_path = folder / "private-manuscript-continuation-execution.json"
        if continuation_path.exists():
            support.append(continuation_path)
        request = review_request(job, context, candidate, ids, working, inventory)
        if request["characters"] > CONTEXT_LIMIT:
            skipped[section] = "complete_original_review_request_exceeds_limit"
            continue
        eligible[section] = {
            "number": number,
            "stage": stage,
            "candidate_kind": kind,
            "attempt": attempt.name,
            "attempt_sha256": sha(attempt.read_bytes()),
            "candidate_sha256": sha(canonical(candidate)),
            "input_sha256": request["binding"],
            "request_characters": request["characters"],
            "citation_paragraphs_proposed": len(changes),
            "support_files": {
                path.relative_to(folder).as_posix(): sha(path.read_bytes())
                for path in sorted(support)
            },
        }
    execution = {
        **original,
        "eligible": eligible,
        "skipped": skipped,
        "invalid_candidate_attempts": invalid_attempts,
    }
    atomic_json(path, execution)
    return execution


class PostReviewJob(composition.AuthoringJob):
    def progress(self, state, stage, **extra):
        self.current = stage
        value = {
            **self.identity,
            "job": self.key,
            "state": state,
            "stage": stage,
            "updated": time.time(),
            "completed_stages": self.completed,
            "reader_publication_allowed": False,
            "source_deletion_allowed": False,
            **extra,
        }
        atomic_json(self.root / "post-repair-review-progress.json", value)
        atomic_json(self.store.root / "wiki/post-repair-review-last-build.json", value)


def actual_review(job, row, request):
    value = job.stage(
        row["stage"],
        author.REVIEW_PROMPT,
        request["schema"],
        request["context"],
        lambda audit: author.validate_review(audit, request["schema"]),
        evidence_packet=request["packet"],
    )
    require(
        verified_review(job, row["stage"], request) == value,
        "actual_frozen_review_cache",
    )
    return value


def candidate_request(job, section, row, units, plan, sections, working, inventory):
    number = row["number"]
    require(units[number]["id"] == section, "original_section_number")
    ids = units[number]["point_ids"]
    require(row["candidate_kind"] in {"write", "repair"}, "original_candidate_role")
    require(row["stage"] == section + "-repair-review", "original_post_review_role")
    path = job.root / "attempts" / row["attempt"]
    require(
        path.parent == job.root / "attempts"
        and re.fullmatch(
            section + "-" + row["candidate_kind"] + "-0[12].json", path.name
        )
        is not None
        and sha(path.read_bytes()) == row["attempt_sha256"],
        "unchanged_original_candidate",
    )
    candidate, changes = candidate_from_attempt(
        path,
        section + "-" + row["candidate_kind"],
        job.model_digest,
        ids,
        working,
        inventory,
    )
    require(
        sha(canonical(candidate)) == row["candidate_sha256"]
        and len(changes) == row["citation_paragraphs_proposed"],
        "same_reviewed_citation_proposal",
    )
    context = context_for(number, units, plan, sections, working, inventory)
    if row["candidate_kind"] == "repair":
        verify_original_repair(
            job, section, path, context, sections[section], ids, working, inventory
        )
    else:
        retained = reviewed_write_candidate(
            job, section, context, sections[section], ids, working, inventory
        )
        require(
            retained is not None and retained[0] == path and retained[1] == candidate,
            "same_passing_pre_reviewed_write",
        )
    request = review_request(job, context, candidate, ids, working, inventory)
    require(
        request["binding"] == row["input_sha256"]
        and request["characters"] == row["request_characters"]
        and request["characters"] <= CONTEXT_LIMIT,
        "same_complete_original_request",
    )
    return candidate, request


def derived_result(
    job_key,
    execution,
    inventory,
    working,
    plan,
    units,
    sections,
    private,
    adopted,
    failed,
    exhausted,
):
    updated = composition.assemble(
        inventory, working, plan, units, {**sections, **adopted}
    )
    updated["unaccepted_sections"] = {
        key: copy.deepcopy(value)
        for key, value in private.get("unaccepted_sections", {}).items()
        if key not in adopted
    }
    updated["post_repair_lineage"] = {
        "policy": POLICY,
        "parent_private_sha256": execution["parent_private_sha256"],
        "execution_sha256": sha(canonical(execution)),
        "adopted_sections": {
            key: sha(canonical(value)) for key, value in adopted.items()
        },
        "semantic_rewrites_performed": 0,
        "whole_lesson_coverage_verified": False,
        "reader_publication_allowed": False,
    }
    result = {
        "state": "private_post_repair_reviews_complete",
        "policy": POLICY,
        "job": job_key,
        "eligible": len(execution["eligible"]),
        "adopted_sections": list(adopted),
        "failed_sections": failed,
        "exhausted_stages": exhausted,
        "skipped_sections": execution["skipped"],
        "unaccepted_sections_remaining": len(updated["unaccepted_sections"]),
        "private_article_sha256": sha(canonical(updated)),
        "parent_private_sha256": execution["parent_private_sha256"],
        "execution_sha256": sha(canonical(execution)),
        "reader_publication_allowed": False,
        "semantic_accuracy_verified": False,
        "whole_lesson_coverage_verified": False,
        "source_deletion_allowed": False,
    }
    return updated, result


def verified_output(root, job_key):
    """Read-only reconstruction from the original calls, not a self-reported hash."""
    folder, inventory, working, plan, units, sections, private = inputs(root, job_key)
    state = json.loads((folder / "post-repair-review-progress.json").read_bytes())
    require(
        state["stage"] == "complete"
        and state["state"] == "private_post_repair_reviews_complete",
        "completed_post_review",
    )
    try:
        alive = (
            abs(psutil.Process(state["pid"]).create_time() - state["created"]) < 0.001
        )
    except psutil.NoSuchProcess:
        alive = False
    require(not alive, "post_review_worker_exited")
    manifest = json.loads((folder / "manifest.json").read_bytes())
    recipe = manifest["recipe"]
    require(
        manifest["id"] == job_key == sha(canonical(recipe))[:32]
        and manifest["recipe_sha256"] == sha(canonical(recipe))
        and recipe["policy"] == composition.POLICY
        and recipe["stage_attempt_budget"] == 2
        and recipe["packet"] == {"sources": inventory["sources"]}
        and recipe["extra"]["working_inputs_sha256"] == sha(canonical(working)),
        "unchanged_original_job_manifest",
    )
    # Constructor-free: verification must not create jobs or allocate attempts.
    job = object.__new__(PostReviewJob)
    job.root, job.key = folder, job_key
    job.packet, job.model_digest = recipe["packet"], recipe["model_digest"]
    job.range_notice = recipe.get("original_range_notice", "")
    require((folder / CONTRACT).exists(), "existing_post_review_execution")
    execution = prepare(job, private, units, plan, sections, working, inventory)
    adopted, failed, exhausted = {}, {}, []
    for section, row in execution["eligible"].items():
        require(section in private["unaccepted_sections"], "only_unaccepted_sections")
        candidate, request = candidate_request(
            job, section, row, units, plan, sections, working, inventory
        )
        if not (folder / (row["stage"] + ".json")).exists():
            records = verify_requests(
                job,
                row["stage"],
                composition.PROMPTS[author.REVIEW_PROMPT],
                request["schema"],
                request["context"],
                request["packet"],
            )
            require(
                len(records) == 2
                and not any(record.get("accepted_structure") for _, record in records),
                "real_exhausted_post_review_budget",
            )
            exhausted.append(section)
            continue
        audit = verified_review(job, row["stage"], request)
        if all(author.verdicts(audit)):
            adopted[section] = candidate
        else:
            failed[section] = sha(canonical(audit))
    updated, result = derived_result(
        job_key,
        execution,
        inventory,
        working,
        plan,
        units,
        sections,
        private,
        adopted,
        failed,
        exhausted,
    )
    require(
        saved(folder / OUTPUT)["data"] == updated, "exact_authenticated_derived_article"
    )
    require(
        saved(folder / "post-repair-review-result.json")["data"] == result,
        "exact_authenticated_post_review_result",
    )
    return updated


def run(root, job_key, *, wait_seconds=3600):
    from .course_pdf_ocr import local_model_digest

    require(type(wait_seconds) is int and 0 <= wait_seconds <= 3600, "finite_gpu_wait")
    root = Path(root).resolve()
    folder, inventory, working, plan, units, sections, private = inputs(root, job_key)
    store = TextbookStore(root)
    store.checkpoint()
    with schema_lease(
        root
        / "wiki/textbook-evidence"
        / private["inventory_sha256"]
        / "authoring-worker.lock",
        timeout=0,
    ):
        job = PostReviewJob(
            store, inventory, working, local_model_digest("qwen3.5:9b"), wait_seconds
        )
        require(job.key == job_key, "same_original_job_and_budgets")
        execution = prepare(job, private, units, plan, sections, working, inventory)
        adopted, failed, exhausted = {}, {}, []
        try:
            for section, row in execution["eligible"].items():
                candidate, request = candidate_request(
                    job, section, row, units, plan, sections, working, inventory
                )
                try:
                    audit = actual_review(job, row, request)
                except ValueError as exc:
                    if (
                        str(exc)
                        != "wiki_stage_attempt_budget_exhausted_" + row["stage"]
                    ):
                        raise
                    exhausted.append(section)
                    continue
                if all(author.verdicts(audit)):
                    composition.validate_section(
                        candidate, units[row["number"]]["point_ids"], working, inventory
                    )
                    adopted[section] = candidate
                else:
                    failed[section] = sha(canonical(audit))
            updated, result = derived_result(
                job_key,
                execution,
                inventory,
                working,
                plan,
                units,
                sections,
                private,
                adopted,
                failed,
                exhausted,
            )
            author.save(folder, OUTPUT.removesuffix(".json"), updated)
            author.save(folder, "post-repair-review-result", result)
            job.progress(
                result["state"],
                "complete",
                adopted_sections=len(adopted),
                unaccepted_sections_remaining=result["unaccepted_sections_remaining"],
            )
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
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("--wait-gpu-seconds", type=int, default=3600)
    parser.add_argument("command", choices=["run", "guard"])
    args = parser.parse_args(argv)
    try:
        if args.command == "guard":
            from .course_archive_gpu import checked_sample
            from .course_gpu_guard import run as guarded

            inputs(args.root, args.job)
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
                    "--wait-gpu-seconds",
                    str(args.wait_gpu_seconds),
                    "run",
                ],
                maximum_power=200,
                maximum_temperature=70,
                guard_name="wiki-revision-thermal-guard",
                sampler=lambda: checked_sample(args.root),
            )
        else:
            print(
                json.dumps(run(args.root, args.job, wait_seconds=args.wait_gpu_seconds))
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
