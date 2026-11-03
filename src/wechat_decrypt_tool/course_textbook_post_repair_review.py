"""Review complete repair candidates using unused original post-repair roles.

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


def verify_original_repair(
    job, section, path, context, original, ids, working, inventory
):
    schema = composition.section_schema(ids, working)
    continuation = job.root / "private-manuscript-continuation-execution.json"
    if continuation.exists():
        bound = json.loads(continuation.read_bytes())
        require(bound["original_stage_attempt_budget"] == 2, "original_repair_limit")
        if section in bound["bounded_mapping_reason_sections"]:
            schema = reviewable.repair_schema(ids, working)
    audit = saved(job.root / (section + "-review.json"))["data"]
    context = {**context, "candidate": original, "review": audit}
    _, binding = job.stage_input(
        composition.PROMPTS[author.REPAIR_PROMPT],
        schema,
        context,
        evidence_packet=composition.original_packet(ids, working, inventory),
    )
    previous = ""
    for prior in sorted((job.root / "attempts").glob(section + "-repair-0*.json")):
        record = json.loads(prior.read_bytes())
        messages = [
            {
                "role": "system",
                "content": composition.PROMPTS[author.REPAIR_PROMPT] + job.range_notice,
            },
            {
                "role": "user",
                "content": json.dumps(
                    {**context, "previous_validation_failure": previous},
                    ensure_ascii=False,
                ),
            },
        ]
        require(
            record["input_sha256"] == binding
            and record["request_sha256"] == sha(canonical(messages)),
            "actual_original_repair_request",
        )
        if prior == path:
            return
        previous = record.get("validation_error", "")
    raise ValueError("requires_post_repair_review_original_repair_record")


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
        return execution
    eligible, skipped = {}, {}
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
        candidate = None
        for attempt in reversed(
            sorted((folder / "attempts").glob(section + "-repair-0*.json"))
        ):
            record = json.loads(attempt.read_bytes())
            if record.get("validation_error") != reviewable.repair.ERROR:
                continue
            candidate, changes = candidate_from_attempt(
                attempt, section + "-repair", job.model_digest, ids, working, inventory
            )
            break
        if candidate is None:
            skipped[section] = "no_complete_primary_citation_only_repair_candidate"
            continue
        context = context_for(number, units, plan, sections, working, inventory)
        verify_original_repair(
            job, section, attempt, context, sections[section], ids, working, inventory
        )
        request = review_request(job, context, candidate, ids, working, inventory)
        if request["characters"] > CONTEXT_LIMIT:
            skipped[section] = "complete_original_review_request_exceeds_limit"
            continue
        eligible[section] = {
            "number": number,
            "stage": stage,
            "attempt": attempt.name,
            "attempt_sha256": sha(attempt.read_bytes()),
            "candidate_sha256": sha(canonical(candidate)),
            "input_sha256": request["binding"],
            "request_characters": request["characters"],
            "citation_paragraphs_proposed": len(changes),
        }
    execution = {**original, "eligible": eligible, "skipped": skipped}
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
    cache = saved(job.root / (row["stage"] + ".json"))
    require(
        cache["input_sha256"] == request["binding"] and cache["data"] == value,
        "actual_frozen_review_cache",
    )
    matching = []
    for path in (job.root / "attempts").glob(row["stage"] + "-*.json"):
        record = json.loads(path.read_bytes())
        if (
            record.get("accepted_structure")
            and record.get("input_sha256") == request["binding"]
        ):
            validate_metrics(record["model_metrics"], job.model_digest)
            require(
                json.loads(record["raw_response"]) == value, "actual_model_review_bytes"
            )
            matching.append(path)
    require(len(matching) == 1, "one_actual_post_repair_model_review")
    return value


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
                ids = units[row["number"]]["point_ids"]
                candidate, _ = candidate_from_attempt(
                    folder / "attempts" / row["attempt"],
                    section + "-repair",
                    job.model_digest,
                    ids,
                    working,
                    inventory,
                )
                require(
                    sha(canonical(candidate)) == row["candidate_sha256"],
                    "same_reviewed_citation_proposal",
                )
                context = context_for(
                    row["number"], units, plan, sections, working, inventory
                )
                verify_original_repair(
                    job,
                    section,
                    folder / "attempts" / row["attempt"],
                    context,
                    sections[section],
                    ids,
                    working,
                    inventory,
                )
                request = review_request(
                    job, context, candidate, ids, working, inventory
                )
                require(
                    request["binding"] == row["input_sha256"]
                    and request["characters"] <= CONTEXT_LIMIT,
                    "same_complete_original_request",
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
                    composition.validate_section(candidate, ids, working, inventory)
                    adopted[section] = candidate
                else:
                    failed[section] = sha(canonical(audit))
            updated_sections = {**sections, **adopted}
            updated = composition.assemble(
                inventory, working, plan, units, updated_sections
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
            author.save(folder, OUTPUT.removesuffix(".json"), updated)
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
