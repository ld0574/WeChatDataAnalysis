"""One source-scoped limitations call without replaying accepted assessments."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import course_wiki_synthesis as synthesis
from . import course_wiki_synthesis_repair as criteria
from .course_knowledge import atomic_json, schema_lease
from .course_library import canonical
from .course_lesson_exports import digest
from .course_pdf_ocr import local_model_digest
from .course_video_visual_audit import ReadOnlyArchive
from .course_wiki import Wiki, object_schema, publish_draft, validate_article
from .course_wiki_writer import COMMON, DraftJob, evidence_schema

POLICY = "source-scoped-missing-limitations-completion-v1"
STAGE = "repair-limitations"
PROMPT = (
    COMMON
    + """
本次只补充已完成文章缺少的适用限制或易错点，不能修改正文、自测或已有资料缺口。
sources 是一个指定章节的完整冻结原文，chapter 只是该章已写正文，不能充当事实证据。
仅在原文直接说明条件、边界、注意事项时输出 action=add，给出1到3项 pitfalls。
每项包含 text、citations、witnesses；witnesses 中每项包含 source 和 quote。
quote 必须逐字连续摘自对应 sources[source].text，8到600字符；每个引用都须有原文摘录。
不能把一般概念、任务目标、外部常识或自行推导的风险改写为原文明确说明的限制。
若原文没有足够依据，输出 action=unavailable、pitfalls=[]，在 gaps 明确说明缺口。
不得为了通过结构检查虚构限制。只输出 JSON，不生成自测题或正文。"""
)
WITNESS = object_schema(
    {
        "source": {"type": "string"},
        "quote": {"type": "string", "minLength": 8, "maxLength": 600},
    }
)
SCHEMA = object_schema(
    {
        "action": {"type": "string", "enum": ["add", "unavailable"]},
        "pitfalls": {
            "type": "array",
            "maxItems": 3,
            "items": object_schema(
                {
                    "text": {"type": "string", "minLength": 8, "maxLength": 650},
                    "citations": {"type": "array", "items": {"type": "string"}},
                    "witnesses": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 3,
                        "items": WITNESS,
                    },
                }
            ),
        },
        "gaps": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
    }
)


def checkpoint(
    folder, stage, recipe, prompt, schema, context, scoped, validate, *, heading=None
):
    value = json.loads((folder / (stage + ".json")).read_bytes())
    schemas = [schema]
    if heading is not None:
        pinned = json.loads(json.dumps(schema))
        pinned["properties"]["heading"]["enum"] = [heading]
        schemas.append(pinned)
    bindings = {
        digest(
            canonical(
                {
                    "prompt": prompt + recipe.get("original_range_notice", ""),
                    "schema": evidence_schema(candidate, scoped),
                    "context": context,
                    "model_digest": recipe["model_digest"],
                }
            )
        )
        for candidate in schemas
    }
    if value["input_sha256"] not in bindings or value["data_sha256"] != digest(
        canonical(value["data"])
    ):
        raise ValueError("limitations_repair_original_checkpoint_changed")
    validate(value["data"])
    return value["data"]


def assemble_parent(root, parent):
    """Reconstruct every accepted byte from original, input-bound checkpoints."""
    folder, recipe = criteria.manifest(root, parent)
    spec = criteria.validate_lineage(root, recipe, complete=True, job=parent)
    body, body_recipe = criteria.manifest(root, spec["body_job"])
    original, original_recipe = criteria.manifest(root, spec["parent_job"])
    packet = recipe["packet"]
    navigation = [{"heading": c["heading"]} for c in packet["chapter_plan"]]
    scoped = {
        **packet,
        "sources": {r: packet["sources"][r] for r in packet["overview_scope"]},
    }
    intro = checkpoint(
        body,
        "introduction",
        body_recipe,
        body_recipe["extra"]["prompts"][0],
        body_recipe["extra"]["schemas"][0],
        synthesis.bounded_context(
            {
                "topic": packet["title"],
                "chapter_navigation": navigation,
                "sources": scoped["sources"],
            }
        ),
        scoped,
        lambda v: synthesis.introduction(v, scoped),
    )
    sections = []
    for n, chapter in enumerate(packet["chapter_plan"]):
        scoped = {
            **packet,
            "sources": {r: packet["sources"][r] for r in packet["chapter_scopes"][n]},
        }
        selected = synthesis.code_excerpt(chapter.get("code_excerpt"), scoped)
        schema = (
            body_recipe["extra"]["schemas"][1]
            if selected
            else body_recipe["extra"].get("unpinned_section_schema", synthesis.SECTION)
        )
        previous = [p["text"].strip() for s in sections for p in s["paragraphs"]]
        section = checkpoint(
            body,
            f"chapter-{n}",
            body_recipe,
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
            scoped,
            lambda v: synthesis.validate_section(
                synthesis.attach_code(v, selected), scoped, chapter, previous
            ),
            heading=chapter["heading"],
        )
        sections.append(synthesis.attach_code(section, selected))
    quizzes, pitfalls, gaps = [], [], [packet["scope_note"], *intro["gaps"]]
    for n in range(len(packet["exercise_plan"])):
        stage = f"self-check-{n}"
        reused = stage in spec["reused_assessment_stages"]
        selected_recipe = original_recipe if reused else recipe
        prompt, schema, context, scoped, question, required = criteria.exercise_input(
            packet, selected_recipe["extra"], n
        )
        result = checkpoint(
            original if reused else folder,
            stage,
            selected_recipe,
            prompt,
            schema,
            context,
            scoped,
            lambda v: synthesis.exercise(
                synthesis.fixed_exercise(v, question),
                scoped,
                [q["question"].strip() for q in quizzes],
                require_limitations=required,
            ),
        )
        result = synthesis.fixed_exercise(result, question)
        quizzes.append(result["quiz"])
        pitfalls.extend(result["pitfalls"])
        gaps.extend(result["gaps"])
    return {
        "title": packet["title"],
        **{k: intro[k] for k in ("overview", "objectives", "prerequisites")},
        "sections": sections,
        "quiz": quizzes,
        "pitfalls": pitfalls,
        "gaps": list(dict.fromkeys(gaps)),
    }


def repair_spec(root, parent, chapter):
    folder, recipe = criteria.manifest(root, parent)
    packet = recipe["packet"]
    if type(chapter) is not int or not 0 <= chapter < len(packet["chapter_scopes"]):
        raise ValueError("limitations_repair_invalid_chapter")
    candidate_path = folder / "unpublished-candidate.json"
    candidate = json.loads(candidate_path.read_bytes())["article"]
    if candidate != assemble_parent(root, parent) or candidate["pitfalls"]:
        raise ValueError(
            "limitations_repair_requires_exact_missing_limitations_candidate"
        )
    if any(row["id"] == parent for row in Wiki(root).catalog()["articles"]):
        raise ValueError("limitations_repair_parent_already_published")
    scope = packet["chapter_scopes"][chapter]
    allowed = {ref for scope in packet["quiz_scopes"] for ref in scope}
    if not set(scope) <= allowed:
        raise ValueError("limitations_repair_scope_not_allocated_to_assessments")
    return (
        {
            "policy": POLICY,
            "parent_job": parent,
            "parent_files": criteria.inventory(folder),
            "candidate_file_sha256": digest(candidate_path.read_bytes()),
            "candidate_article_sha256": digest(canonical(candidate)),
            "chapter": chapter,
            "source_refs": scope,
            "new_calls_max": 1,
        },
        recipe,
        candidate,
    )


def extra(parent, spec):
    return {
        "unchanged_body_job": parent["extra"]["unchanged_body_job"],
        "unchanged_assessment_job": spec["parent_job"],
        "limitations_repair": spec,
        "limitations_prompt": PROMPT,
        "limitations_schema": SCHEMA,
    }


def stage_input(packet, candidate, spec):
    scoped = {
        **packet,
        "sources": {
            r: s for r, s in packet["sources"].items() if r in spec["source_refs"]
        },
    }
    schema = json.loads(json.dumps(SCHEMA))
    schema["properties"]["pitfalls"]["items"]["properties"]["witnesses"]["items"][
        "properties"
    ]["source"]["enum"] = sorted(scoped["sources"])
    context = synthesis.bounded_context(
        {
            "topic": packet["title"],
            "chapter": candidate["sections"][spec["chapter"]],
            "sources": scoped["sources"],
        }
    )
    return scoped, schema, context


def validate_result(value, scoped, schema):
    from jsonschema import Draft202012Validator

    if not Draft202012Validator(evidence_schema(schema, scoped)).is_valid(value):
        raise ValueError("limitations_repair_invalid_result_schema")
    for gap in value["gaps"]:
        synthesis.nonempty(gap)
    if value["action"] == "unavailable":
        if value["pitfalls"] or not value["gaps"]:
            raise ValueError("limitations_repair_unavailable_requires_explicit_gap")
        return
    if not value["pitfalls"]:
        raise ValueError("limitations_repair_add_requires_limitations")
    for item in value["pitfalls"]:
        synthesis.claim(item, scoped)
        if len(set(item["citations"])) != len(item["citations"]):
            raise ValueError("limitations_repair_duplicate_citations")
        if {w["source"] for w in item["witnesses"]} != set(item["citations"]):
            raise ValueError("limitations_repair_witness_citations_mismatch")
        for witness in item["witnesses"]:
            if witness["quote"] not in scoped["sources"][witness["source"]]["text"]:
                raise ValueError("limitations_repair_witness_not_literal")


def completed_article(candidate, value):
    return {
        **candidate,
        "pitfalls": [
            {k: p[k] for k in ("text", "citations")} for p in value["pitfalls"]
        ],
        "gaps": list(dict.fromkeys([*candidate["gaps"], *value["gaps"]])),
    }


def validate_lineage(root, recipe, *, complete=False, job=None, publication=None):
    saved = recipe["extra"]["limitations_repair"]
    spec, parent, candidate = repair_spec(root, saved["parent_job"], saved["chapter"])
    if recipe != {**parent, "extra": extra(parent, spec), "stage_attempt_budget": 1}:
        raise ValueError("limitations_repair_lineage_changed")
    if complete:
        key = job or digest(canonical(recipe))[:32]
        folder = Path(root) / "wiki/jobs" / key
        binding = (
            Path(root)
            / "wiki/limitations-repair-bindings"
            / (spec["parent_job"] + ".json")
        )
        if json.loads(binding.read_bytes()) != {
            "policy": POLICY,
            "parent_job": spec["parent_job"],
            "child_job": key,
        }:
            raise ValueError("limitations_repair_existing_lineage_preserved")
        scoped, schema, context = stage_input(recipe["packet"], candidate, spec)
        result = checkpoint(
            folder,
            STAGE,
            recipe,
            PROMPT,
            schema,
            context,
            scoped,
            lambda v: validate_result(v, scoped, schema),
        )
        if result["action"] != "add":
            raise ValueError("limitations_repair_unavailable_not_publishable")
        article = completed_article(candidate, result)
        validate_article(article, recipe["packet"])
        assembled = json.loads((folder / "assembled.json").read_bytes())
        if assembled["article"] != article:
            raise ValueError("limitations_repair_assembled_article_changed")
        if publication is not None and publication != article:
            raise ValueError("limitations_repair_publication_changed")
    return spec


def repair(
    store, parent, chapter, model="qwen3.5:9b", *, wait_seconds=0, prepare_only=False
):
    if type(wait_seconds) is not int or not 0 <= wait_seconds <= 3600:
        raise ValueError("limitations_repair_invalid_wait")
    with schema_lease(store.root / "wiki-build.lock", timeout=0):
        store.checkpoint()
        spec, original, candidate = repair_spec(store.root, parent, chapter)
        weights = local_model_digest(model)
        if weights != original["model_digest"]:
            raise ValueError("limitations_repair_model_changed")
        job = DraftJob(
            store,
            original["packet"],
            model,
            weights,
            wait_seconds,
            pipeline=synthesis.POLICY,
            recipe_extra=extra(original, spec),
            max_attempts=1,
        )
        recipe = json.loads((job.root / "manifest.json").read_bytes())["recipe"]
        validate_lineage(store.root, recipe)
        binding = store.root / "wiki/limitations-repair-bindings" / (parent + ".json")
        value = {"policy": POLICY, "parent_job": parent, "child_job": job.key}
        if binding.exists() and json.loads(binding.read_bytes()) != value:
            raise ValueError("limitations_repair_existing_lineage_preserved")
        if not binding.exists():
            binding.parent.mkdir(parents=True, exist_ok=True)
            atomic_json(binding, value)
        if prepare_only:
            return {
                "id": job.key,
                "state": "prepared_no_model_calls",
                "chapter": chapter,
                "new_calls_max": 1,
            }
        if any(row["id"] == job.key for row in Wiki(store.root).catalog()["articles"]):
            validate_lineage(store.root, recipe, complete=True, job=job.key)
            publication = Wiki(store.root).article(job.key)
            if (
                publication["article"]
                != json.loads((job.root / "assembled.json").read_bytes())["article"]
            ):
                raise ValueError("limitations_repair_publication_changed")
            return {"id": job.key, "state": "already_published"}
        job.total = 1
        scoped, schema, context = stage_input(job.packet, candidate, spec)
        try:
            result = job.stage(
                STAGE,
                PROMPT,
                schema,
                context,
                lambda v: validate_result(v, scoped, schema),
                evidence_packet=scoped,
            )
            if result["action"] == "unavailable":
                job.progress("needs_review", "source_limitations_unavailable")
                return {
                    "id": job.key,
                    "state": "source_limitations_unavailable",
                    "published": False,
                }
            article = completed_article(candidate, result)
            checks = validate_article(article, job.packet)
            atomic_json(
                job.root / "assembled.json", {"article": article, "checks": checks}
            )
            validate_lineage(store.root, recipe, complete=True, job=job.key)
            result = publish_draft(
                store,
                article,
                job.packet,
                job.key,
                model,
                weights,
                pipeline=synthesis.POLICY,
            )
            job.progress("draft_published", "complete")
            return result
        except Exception as exc:
            job.progress("failed", STAGE, error_type=type(exc).__name__)
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--parent-job", required=True)
    parser.add_argument("--chapter", required=True, type=int)
    parser.add_argument("--model", default="qwen3.5:9b")
    parser.add_argument("--wait-gpu-seconds", default=0, type=int)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            repair(
                ReadOnlyArchive(args.root),
                args.parent_job,
                args.chapter,
                args.model,
                wait_seconds=args.wait_gpu_seconds,
                prepare_only=args.prepare_only,
            )
        )
    )


if __name__ == "__main__":
    main()
