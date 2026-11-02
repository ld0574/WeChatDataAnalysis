"""One explicit criteria repair lineage; preserve every original writer byte."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from . import course_wiki_synthesis as synthesis
from .course_knowledge import atomic_json, schema_lease
from .course_library import canonical
from .course_lesson_exports import digest
from .course_pdf_ocr import local_model_digest
from .course_video_visual_audit import ReadOnlyArchive
from .course_wiki import Wiki, publish_draft, validate_article
from .course_wiki_writer import DraftJob, evidence_schema

POLICY = "fixed-assessment-empty-criteria-repair-v1"


def manifest(root, key):
    if not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{32}", key):
        raise ValueError("criteria_repair_invalid_parent")
    folder = Path(root) / "wiki/jobs" / key
    raw = (folder / "manifest.json").read_bytes()
    value = json.loads(raw)
    sha = digest(canonical(value["recipe"]))
    if value["id"] != key or value["recipe_sha256"] != sha or sha[:32] != key:
        raise ValueError("criteria_repair_parent_manifest_changed")
    return folder, value["recipe"]


def inventory(folder):
    return {
        p.relative_to(folder).as_posix(): digest(p.read_bytes())
        for p in sorted(folder.rglob("*.json"))
    }


def exercise_input(packet, extra, number):
    spec = packet["exercise_plan"][number]
    scoped = {
        **packet,
        # Message JSON preserves insertion order; chapter_scopes is sorted.
        "sources": {
            ref: source
            for ref, source in packet["sources"].items()
            if ref in packet["chapter_scopes"][spec["chapter"]]
        },
    }
    questions = [
        "请根据本章原文完成以下任务：" + e["focus"].strip()
        for e in packet["exercise_plan"]
    ]
    if len(set(questions)) != len(questions):
        raise ValueError("criteria_repair_duplicate_fixed_questions")
    required = (
        packet.get("structure_contract") == synthesis.ALLOCATED_STRUCTURE
        and number == len(questions) - 1
    )
    prompt = extra["last_exercise_prompt"] if required else extra["prompts"][2]
    schema = extra["last_exercise_schema"] if required else extra["schemas"][2]
    context = synthesis.bounded_context(
        {
            "topic": packet["title"],
            "focus": spec["focus"],
            "previous_questions": questions[:number],
            "sources": scoped["sources"],
            "fixed_question": questions[number],
        }
    )
    return prompt, schema, context, scoped, questions[number], required


def repair_spec(root, parent):
    folder, recipe = manifest(root, parent)
    extra, packet = recipe["extra"], recipe["packet"]
    if (
        recipe["policy"] != synthesis.POLICY
        or extra.get("fixed_assessments") != "complete-learning-goal-task-v1"
        or "criteria_repair" in extra
    ):
        raise ValueError("criteria_repair_requires_original_fixed_assessments")
    body_key = extra["unchanged_body_job"]
    body, body_recipe = manifest(root, body_key)
    if any(recipe[k] != body_recipe[k] for k in ("packet", "policy", "model_digest")):
        raise ValueError("criteria_repair_body_binding_changed")
    if any(
        not (body / (name + ".json")).is_file()
        for name in [
            "introduction",
            *[f"chapter-{n}" for n in range(len(packet["chapter_plan"]))],
        ]
    ):
        raise ValueError("criteria_repair_requires_complete_original_body")
    reused, failed, unattempted = [], [], []
    for n in range(len(packet["exercise_plan"])):
        stage = f"self-check-{n}"
        attempts = sorted((folder / "attempts").glob(stage + "-*.json"))
        if (folder / (stage + ".json")).exists():
            reused.append(stage)
            continue
        if not attempts:
            unattempted.append(stage)
            continue
        if [p.name for p in attempts] != [stage + f"-{i:02}.json" for i in (1, 2, 3)]:
            raise ValueError("criteria_repair_requires_terminal_empty_criteria_failure")
        prompt, schema, context, scoped, _, _ = exercise_input(packet, extra, n)
        if recipe.get("original_range_notice"):
            prompt += recipe["original_range_notice"]
        binding = digest(
            canonical(
                {
                    "prompt": prompt,
                    "schema": evidence_schema(schema, scoped),
                    "context": context,
                    "model_digest": recipe["model_digest"],
                }
            )
        )
        for number, p in enumerate(attempts, 1):
            record = json.loads(p.read_bytes())
            messages = [
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            **context,
                            "previous_validation_failure": ""
                            if number == 1
                            else "missing_self_check_criteria",
                        },
                        ensure_ascii=False,
                    ),
                },
            ]
            if (
                record.get("input_sha256") != binding
                or record.get("request_sha256") != digest(canonical(messages))
                or record.get("model_digest") != recipe["model_digest"]
                or record.get("stage") != stage
                or record.get("attempt") != number
                or record.get("validation_error") != "missing_self_check_criteria"
                or record.get("accepted_structure")
                or json.loads(record["raw_response"])["quiz"]["criteria"] != []
            ):
                raise ValueError("criteria_repair_not_an_empty_criteria_failure")
        failed.append(stage)
    if not failed:
        raise ValueError("criteria_repair_no_terminal_empty_criteria_failure")
    if any(row["id"] == parent for row in Wiki(root).catalog()["articles"]):
        raise ValueError("criteria_repair_parent_already_published")
    return (
        {
            "policy": POLICY,
            "parent_job": parent,
            "parent_files": inventory(folder),
            "body_job": body_key,
            "body_files": inventory(body),
            "reused_assessment_stages": reused,
            "failed_criteria_stages": failed,
            "previously_unattempted_stages": unattempted,
            "new_calls_per_stage_max": 1,
        },
        recipe,
        body_recipe,
    )


def child_extra(original, spec):
    extra = json.loads(json.dumps(original["extra"]))
    extra["schemas"][2]["properties"]["quiz"]["properties"]["criteria"]["minItems"] = 1
    if "last_exercise_schema" in extra:
        extra["last_exercise_schema"]["properties"]["quiz"]["properties"]["criteria"][
            "minItems"
        ] = 1
    extra["unchanged_assessment_job"] = spec["parent_job"]
    extra["criteria_repair"] = spec
    return extra


def validate_lineage(root, recipe, *, complete=False, job=None):
    extra = recipe["extra"]
    spec, parent, _ = repair_spec(root, extra["unchanged_assessment_job"])
    if recipe != {
        **parent,
        "extra": child_extra(parent, spec),
        "stage_attempt_budget": 1,
    }:
        raise ValueError("criteria_repair_lineage_changed")
    if complete:
        folder = Path(root) / "wiki/jobs" / (job or digest(canonical(recipe))[:32])
        for stage in (
            spec["failed_criteria_stages"] + spec["previously_unattempted_stages"]
        ):
            if not (folder / (stage + ".json")).is_file():
                raise ValueError("criteria_repair_checkpoint_missing")
    return spec


def repair(store, parent, model="qwen3.5:9b", *, wait_seconds=0, prepare_only=False):
    if type(wait_seconds) is not int or not 0 <= wait_seconds <= 3600:
        raise ValueError("criteria_repair_invalid_wait")
    with schema_lease(store.root / "wiki-build.lock", timeout=0):
        store.checkpoint()
        spec, original, body_recipe = repair_spec(store.root, parent)
        weights = local_model_digest(model)
        if weights != original["model_digest"]:
            raise ValueError("criteria_repair_model_changed")
        packet = original["packet"]
        body = DraftJob(
            store,
            packet,
            model,
            weights,
            wait_seconds,
            pipeline=synthesis.POLICY,
            recipe_extra=body_recipe["extra"],
        )
        old = DraftJob(
            store,
            packet,
            model,
            weights,
            wait_seconds,
            pipeline=synthesis.POLICY,
            recipe_extra=original["extra"],
        )
        if body.key != spec["body_job"] or old.key != parent:
            raise ValueError("criteria_repair_original_implementation_changed")
        job = DraftJob(
            store,
            packet,
            model,
            weights,
            wait_seconds,
            pipeline=synthesis.POLICY,
            recipe_extra=child_extra(original, spec),
            max_attempts=1,
        )
        # A fixed original job has one repair recipe, even across process restarts.
        binding = store.root / "wiki/criteria-repair-bindings" / (parent + ".json")
        value = {"policy": POLICY, "parent_job": parent, "child_job": job.key}
        if binding.exists() and json.loads(binding.read_bytes()) != value:
            raise ValueError("criteria_repair_existing_lineage_preserved")
        if not binding.exists():
            binding.parent.mkdir(parents=True, exist_ok=True)
            atomic_json(binding, value)
        validate_lineage(
            store.root, json.loads((job.root / "manifest.json").read_bytes())["recipe"]
        )
        if prepare_only:
            return {
                "id": job.key,
                "state": "prepared_no_model_calls",
                "reused_assessments": len(spec["reused_assessment_stages"]),
                "single_call_stages": len(spec["failed_criteria_stages"])
                + len(spec["previously_unattempted_stages"]),
            }
        if any(row["id"] == job.key for row in Wiki(store.root).catalog()["articles"]):
            Wiki(store.root).article(job.key)
            validate_lineage(
                store.root,
                json.loads((job.root / "manifest.json").read_bytes())["recipe"],
                complete=True,
                job=job.key,
            )
            return {"id": job.key, "state": "already_published"}
        job.total = 1 + len(packet["chapter_plan"]) + len(packet["exercise_plan"])
        navigation = [{"heading": c["heading"]} for c in packet["chapter_plan"]]
        scoped_intro = {
            **packet,
            "sources": {r: packet["sources"][r] for r in packet["overview_scope"]},
        }
        intro = body.stage(
            "introduction",
            body_recipe["extra"]["prompts"][0],
            body_recipe["extra"]["schemas"][0],
            synthesis.bounded_context(
                {
                    "topic": packet["title"],
                    "chapter_navigation": navigation,
                    "sources": scoped_intro["sources"],
                }
            ),
            lambda v: synthesis.introduction(v, scoped_intro),
            evidence_packet=scoped_intro,
        )
        sections = []
        for n, chapter in enumerate(packet["chapter_plan"]):
            scoped = {
                **packet,
                "sources": {
                    r: packet["sources"][r] for r in packet["chapter_scopes"][n]
                },
            }
            selected = synthesis.code_excerpt(chapter.get("code_excerpt"), scoped)
            schema = (
                body_recipe["extra"]["schemas"][1]
                if selected
                else body_recipe["extra"].get(
                    "unpinned_section_schema", synthesis.SECTION
                )
            )
            previous = [p["text"].strip() for s in sections for p in s["paragraphs"]]
            section = body.stage(
                f"chapter-{n}",
                body_recipe["extra"]["prompts"][1],
                schema,
                synthesis.bounded_context(
                    {
                        "topic": packet["title"],
                        "chapter_navigation": navigation,
                        "current_section": {
                            "heading": chapter["heading"],
                            "question": chapter["focus"],
                        },
                        "selected_code": selected,
                        "sources": scoped["sources"],
                    }
                ),
                lambda v: synthesis.validate_section(
                    synthesis.attach_code(v, selected), scoped, chapter, previous
                ),
                evidence_packet=scoped,
            )
            sections.append(synthesis.attach_code(section, selected))
        quizzes, pitfalls, gaps = [], [], [packet["scope_note"], *intro["gaps"]]
        job.completed = 1 + len(sections)
        try:
            for n in range(len(packet["exercise_plan"])):
                stage = f"self-check-{n}"
                reuse = stage in spec["reused_assessment_stages"]
                prompt, schema, context, scoped, question, required = exercise_input(
                    packet,
                    original["extra"] if reuse else child_extra(original, spec),
                    n,
                )
                result = (old if reuse else job).stage(
                    stage,
                    prompt,
                    schema,
                    context,
                    lambda v: synthesis.exercise(
                        synthesis.fixed_exercise(v, question),
                        scoped,
                        [q["question"].strip() for q in quizzes],
                        require_limitations=required,
                    ),
                    evidence_packet=scoped,
                )
                if reuse:
                    job.completed += 1
                result = synthesis.fixed_exercise(result, question)
                quizzes.append(result["quiz"])
                pitfalls.extend(result["pitfalls"])
                gaps.extend(result["gaps"])
            article = {
                "title": packet["title"],
                **{k: intro[k] for k in ("overview", "objectives", "prerequisites")},
                "sections": sections,
                "quiz": quizzes,
                "pitfalls": pitfalls,
                "gaps": list(dict.fromkeys(gaps)),
            }
            checks = validate_article(article, packet)
            validate_lineage(
                store.root,
                json.loads((job.root / "manifest.json").read_bytes())["recipe"],
                complete=True,
                job=job.key,
            )
            atomic_json(
                job.root / "assembled.json", {"article": article, "checks": checks}
            )
            result = publish_draft(
                store,
                article,
                packet,
                job.key,
                model,
                weights,
                pipeline=synthesis.POLICY,
            )
            job.progress("draft_published", "complete")
            return result
        except Exception as exc:
            job.progress("failed", job.current, error_type=type(exc).__name__)
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--parent-job", required=True)
    parser.add_argument("--model", default="qwen3.5:9b")
    parser.add_argument("--wait-gpu-seconds", default=0, type=int)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            repair(
                ReadOnlyArchive(args.root),
                args.parent_job,
                args.model,
                wait_seconds=args.wait_gpu_seconds,
                prepare_only=args.prepare_only,
            )
        )
    )


if __name__ == "__main__":
    main()
