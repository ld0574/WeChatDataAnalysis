"""Long topic drafts with complete, bounded original evidence per chapter.

No summaries replace sources. Plans explicitly bind chapter responsibilities,
literal code ranges and self-check scopes. Whole-topic semantics remain unproven.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import psutil

from .course_knowledge import GpuBusyError, atomic_json, schema_lease
from .course_library import canonical
from .course_pdf_ocr import local_model_digest
from .course_video_visual_audit import ReadOnlyArchive
from .course_wiki import (
    CLAIM,
    QUIZ,
    SECTION,
    STRINGS,
    prepare,
    object_schema,
    publish_draft,
    validate_article,
    Wiki,
)
from .course_wiki_writer import (
    COMMON,
    SECTION_PROMPT,
    DraftJob,
    claim,
    nonempty,
    references,
    validate_section,
)

POLICY = "wiki-chapter-scoped-synthesis-v1"
ALLOCATED_STRUCTURE = "allocated-developed-chapters-and-limitations-v1"
FLEXIBLE_STRUCTURE = "allocated-material-shaped-chapters-and-assessments-v1"


def developed_structure(packet):
    return packet.get("structure_contract") in {ALLOCATED_STRUCTURE, FLEXIBLE_STRUCTURE}


def flexible_assessments(chapters, exercises):
    if not 2 <= len(chapters) <= 5 or not 3 <= len(exercises) <= 5:
        raise ValueError("wiki_flexible_requires_2_to_5_chapters_and_3_to_5_questions")
    if any(
        type(e.get("chapter")) is not int or not 0 <= e["chapter"] < len(chapters)
        for e in exercises
    ):
        raise ValueError("wiki_synthesis_invalid_quiz_chapter")
    if {e["chapter"] for e in exercises} != set(range(len(chapters))):
        raise ValueError("wiki_flexible_requires_assessment_for_every_chapter")
    for e in exercises:
        bounded(e["focus"], 400)
    if len({e["focus"].strip() for e in exercises}) != len(exercises):
        raise ValueError("wiki_flexible_requires_distinct_assessment_goals")


INTRO = object_schema(
    {
        "overview": CLAIM,
        "objectives": STRINGS,
        "prerequisites": {"type": "array", "items": CLAIM},
        "gaps": STRINGS,
    }
)
EXERCISE = object_schema(
    {
        "quiz": QUIZ,
        "pitfalls": {"type": "array", "items": CLAIM},
        "gaps": STRINGS,
    }
)
PINNED_SECTION = json.loads(json.dumps(SECTION))
for field in ("code", "code_citations"):
    PINNED_SECTION["properties"].pop(field)
    PINNED_SECTION["required"].remove(field)
INTRO_PROMPT = (
    COMMON
    + """
本次只编写长主题的概述、2到6个学习目标、前置知识和资料缺口。
章节标题与分工是编辑计划，不是事实证据；概述和前置知识只引用本次给出的原文。
不预先宣称已覆盖整门课程，不把没有看到的章节内容写成结论。"""
)
CHAPTER_PROMPT = (
    SECTION_PROMPT
    + """
这是多章文章中的一章。按 current_section 的分工展开，不重复其他章的任务。
其他章的标题只是导航，不能据此补写事实。只使用本次 sources 中的原文。
若提供 selected_code，程序会在本章显示该原样代码；请解释它的作用与材料说明的条件，
至少一段引用 selected_code.source。不重新转抄代码，不把静态摘录当成已执行验证。"""
)
EXERCISE_PROMPT = (
    COMMON
    + """
本次只编写一道聚焦 focus 的自测，含问题、答案、解释、判断标准和支持答案的出处。
根据本次原文列出有依据的易错点，没依据可为空，缺少的条件写入 gaps。
不要重复 previous_questions；其他章节标题与题目不是事实证据，不使用它们补造答案。"""
)
FIXED_EXERCISE = json.loads(json.dumps(EXERCISE))
FIXED_EXERCISE["properties"]["quiz"]["properties"].pop("question")
FIXED_EXERCISE["properties"]["quiz"]["required"].remove("question")
FIXED_EXERCISE_PROMPT = (
    COMMON
    + """
本次只回答 fixed_question 中程序固定的完整章节学习任务，不生成或修改题干。
只输出有原文依据的答案、解释和判断标准，并列出来源；不能借用 previous_questions 的事实。
previous_questions 仅说明其他章已安排的任务，不能替代当前章任务或其 sources。
保留关键条件、步骤和限制；原文不能回答的部分明确写入 gaps，不虚构答案。
根据本次原文列出有依据的易错点，没依据可为空。"""
)
LIMITATIONS_NOTICE = """
本题还须给出至少一项由当前原文直接支持的易错点或适用限制，保留对应原文引用。
不要以外部常识填补材料缺失，也不要为了满足结构虚构限制；来源支持仍将另外核对。"""


def developed_schema(schema):
    value = json.loads(json.dumps(schema))
    value["properties"]["paragraphs"]["minItems"] = 2
    return value


def limitations_schema(schema):
    value = json.loads(json.dumps(schema))
    value["properties"]["pitfalls"]["minItems"] = 1
    return value


def chapter_schema(job, name, schema, context, packet, heading):
    """Pin new requests while preserving every prior stage's exact schema/budget."""
    pinned = json.loads(json.dumps(schema))
    pinned["properties"]["heading"]["enum"] = [heading]
    candidates = {
        job.stage_input(CHAPTER_PROMPT, candidate, context, evidence_packet=packet)[
            1
        ]: candidate
        for candidate in (schema, pinned)
    }
    bindings = set()
    path = job.root / (name + ".json")
    if path.exists():
        bindings.add(json.loads(path.read_bytes())["input_sha256"])
    for attempt in (job.root / "attempts").glob(name + "-*.json"):
        bindings.add(json.loads(attempt.read_bytes())["input_sha256"])
    if not bindings:
        return pinned
    if len(bindings) != 1 or not bindings <= candidates.keys():
        raise ValueError("wiki_chapter_retained_schema_binding_changed")
    return candidates[next(iter(bindings))]


def bounded(value, maximum):
    nonempty(value)
    if len(value) > maximum:
        raise ValueError("wiki_synthesis_plan_text_exceeds_limit")


def bounded_context(value):
    if len(canonical(value).decode()) > 15000:
        raise ValueError("wiki_synthesis_stage_context_exceeds_limit")
    return value


def code_excerpt(specification, packet):
    if specification is None:
        return None
    ref, start, end = (specification[k] for k in ("source", "start", "end"))
    if ref not in packet["sources"]:
        raise ValueError("wiki_selected_code_source_outside_chapter")
    text = packet["sources"][ref]["text"]
    if (
        type(start) is not int
        or type(end) is not int
        or not 0 <= start < end <= len(text)
        or not 20 <= end - start <= 1600
    ):
        raise ValueError("wiki_selected_code_range_invalid")
    return {"source": ref, "start": start, "end": end, "text": text[start:end]}


def prepare_plan(root, plan):
    title = plan["title"]
    bounded(title, 160)
    scope_note = plan.get(
        "scope_note",
        "本篇仅整理显式选定的原文，不代表完整课程或工程已经覆盖；代码未执行验证。",
    )
    bounded(scope_note, 800)
    chapters, exercises = plan["chapters"], plan["exercises"]
    if plan.get("structure_contract") == FLEXIBLE_STRUCTURE:
        flexible_assessments(chapters, exercises)
    elif not 4 <= len(chapters) <= 8 or not 3 <= len(exercises) <= 5:
        raise ValueError("wiki_synthesis_requires_4_to_8_chapters_and_3_to_5_questions")
    headings = [c["heading"] for c in chapters]
    if len(set(headings)) != len(headings):
        raise ValueError("wiki_synthesis_duplicate_chapter_heading")
    intro = prepare(root, title, plan["introduction"])
    packets = []
    sources, bindings = dict(intro["sources"]), dict(intro["bindings"])
    for chapter in chapters:
        bounded(chapter["heading"], 120)
        bounded(chapter["focus"], 500)
        current = prepare(root, title, chapter["selections"])
        code_excerpt(chapter.get("code_excerpt"), current)
        for target, incoming in (
            (sources, current["sources"]),
            (bindings, current["bindings"]),
        ):
            for key, value in incoming.items():
                if key in target and target[key] != value:
                    raise ValueError("wiki_synthesis_sources_changed_between_chapters")
                target[key] = value
        packets.append(current)
    for exercise in exercises:
        if type(exercise["chapter"]) is not int or not 0 <= exercise["chapter"] < len(
            chapters
        ):
            raise ValueError("wiki_synthesis_invalid_quiz_chapter")
        bounded(exercise["focus"], 400)
    if (
        len(sources) > 96
        or sum(len(s["text"]) for s in sources.values()) > 26000
        or len(canonical(sources)) > 300000
    ):
        raise ValueError("wiki_synthesis_union_evidence_exceeds_limit")
    packet = {
        "policy": POLICY,
        "title": title,
        "sources": sources,
        "bindings": bindings,
        "scope": "explicit_chapter_scoped_original_evidence",
        "source_freeze_policy": "per_packet_observation",
        "full_topic_coverage_verified": False,
        "scope_note": scope_note,
        "chapter_scopes": [sorted(p["sources"]) for p in packets],
        "overview_scope": sorted(intro["sources"]),
        "quiz_scopes": [sorted(packets[e["chapter"]]["sources"]) for e in exercises],
        "chapter_plan": chapters,
        "exercise_plan": exercises,
    }
    partitions = {}
    for current in [intro, *packets]:
        for key, pages in current.get("evidence_partitions", {}).items():
            partitions.setdefault(key, set()).update(pages)
    if partitions:
        packet["evidence_partitions"] = {
            key: sorted(pages) for key, pages in partitions.items()
        }
    if "structure_contract" in plan:
        if not developed_structure(plan) or not partitions:
            raise ValueError("wiki_synthesis_invalid_allocated_structure_contract")
        packet["structure_contract"] = plan["structure_contract"]
    return packet, intro, packets


def validate_scopes(article, packet):
    """Enforced again at publication/read/revision, not only by model prompts."""
    if "structure_contract" in packet:
        if not developed_structure(packet):
            raise ValueError("wiki_synthesis_invalid_allocated_structure_contract")
        if any(len(s["paragraphs"]) < 2 for s in article["sections"]):
            raise ValueError("section_requires_at_least_two_developed_paragraphs")
        if packet["structure_contract"] == FLEXIBLE_STRUCTURE:
            flexible_assessments(packet["chapter_plan"], packet["exercise_plan"])
            if not packet.get("evidence_partitions") or packet["quiz_scopes"] != [
                packet["chapter_scopes"][e["chapter"]] for e in packet["exercise_plan"]
            ]:
                raise ValueError("wiki_flexible_assessment_scope_changed")

    def check(ids, scope):
        if not set(ids) <= set(scope):
            raise ValueError("wiki_synthesis_citation_outside_assigned_scope")

    if len(article["sections"]) != len(packet["chapter_scopes"]) or len(
        article["quiz"]
    ) != len(packet["quiz_scopes"]):
        raise ValueError("wiki_synthesis_article_scope_count_mismatch")
    for value in [article["overview"], *article["prerequisites"]]:
        check(value["citations"], packet["overview_scope"])
    for section, scope, spec in zip(
        article["sections"],
        packet["chapter_scopes"],
        packet["chapter_plan"],
        strict=True,
    ):
        if section["heading"] != spec["heading"]:
            raise ValueError("wiki_synthesis_chapter_heading_changed")
        for value in section["paragraphs"]:
            check(value["citations"], scope)
        check(section["code_citations"], scope)
        if section["image"]:
            check([section["image"]], scope)
        selected = code_excerpt(
            spec.get("code_excerpt"),
            {"sources": {r: packet["sources"][r] for r in scope}},
        )
        if selected and (
            section["code"] != selected["text"]
            or section["code_citations"] != [selected["source"]]
        ):
            raise ValueError("wiki_synthesis_selected_code_changed")
    for quiz, scope in zip(article["quiz"], packet["quiz_scopes"], strict=True):
        check(quiz["citations"], scope)
    allowed = {ref for scope in packet["quiz_scopes"] for ref in scope}
    for value in article["pitfalls"]:
        check(value["citations"], allowed)


def target_packet(packet, target, original):
    """Keep subsequent targeted repairs within the same original source scope."""
    if "chapter_scopes" not in packet:
        return packet
    if target.startswith("section-"):
        scope = packet["chapter_scopes"][int(target.split("-")[1])]
    elif target.startswith("quiz-"):
        scope = packet["quiz_scopes"][int(target.split("-")[1])]
    elif target == "overview" or target.startswith("prerequisites-"):
        scope = packet["overview_scope"]
    else:
        scope = original["citations"]
    return {**packet, "sources": {r: packet["sources"][r] for r in scope}}


def introduction(value, packet):
    claim(value["overview"], packet)
    if not 2 <= len(value["objectives"]) <= 6:
        raise ValueError("outline_requires_2_to_6_objectives")
    for text in value["objectives"] + value["gaps"]:
        nonempty(text)
    for item in value["prerequisites"]:
        claim(item, packet)


def exercise(value, packet, previous, *, require_limitations=False):
    quiz = value["quiz"]
    for field in ("question", "answer", "explanation"):
        nonempty(quiz[field])
    if quiz["question"].strip() in previous:
        raise ValueError("duplicate_self_check_question")
    if not quiz["criteria"]:
        raise ValueError("missing_self_check_criteria")
    for criterion in quiz["criteria"] + value["gaps"]:
        nonempty(criterion)
    references(quiz["citations"], packet)
    for item in value["pitfalls"]:
        claim(item, packet)
    if require_limitations and not value["pitfalls"]:
        raise ValueError("requires_source_bound_limitation_in_final_self_check")


def fixed_exercise(value, question):
    if "question" in value["quiz"]:
        raise ValueError("wiki_synthesis_fixed_question_must_not_be_generated")
    result = json.loads(json.dumps(value))
    result["quiz"]["question"] = question
    return result


def attach_code(value, selected):
    result = json.loads(json.dumps(value))
    if selected:
        result.update(code=selected["text"], code_citations=[selected["source"]])
        if not any(selected["source"] in p["citations"] for p in result["paragraphs"]):
            raise ValueError("section_selected_code_not_referenced")
    return result


def generate(
    store, plan, model="qwen3.5:9b", *, wait_seconds=0, fixed_assessments=False
):
    if type(wait_seconds) is not int or not 0 <= wait_seconds <= 3600:
        raise ValueError("GPU wait must be bounded to 0..3600 seconds")
    with schema_lease(store.root / "wiki-build.lock", timeout=0):
        try:
            packet, intro_packet, chapters = prepare_plan(store.root, plan)
            assigned = (
                [
                    "请根据本章原文完成以下任务：" + e["focus"].strip()
                    for e in plan["exercises"]
                ]
                if fixed_assessments
                else []
            )
            if fixed_assessments and len(set(assigned)) != len(assigned):
                raise ValueError("wiki_synthesis_fixed_assessments_need_distinct_goals")
            weights = local_model_digest(model)
        except Exception as exc:
            (store.root / "wiki").mkdir(exist_ok=True)
            atomic_json(
                store.root / "wiki" / "last-build.json",
                {
                    "state": "failed",
                    "stage": "preparing",
                    "pipeline": POLICY,
                    "updated": time.time(),
                    "error_type": type(exc).__name__,
                    "pid": os.getpid(),
                    "created": psutil.Process().create_time(),
                },
            )
            raise
        structured = developed_structure(packet)
        section_schema = developed_schema(SECTION) if structured else SECTION
        pinned_schema = (
            developed_schema(PINNED_SECTION) if structured else PINNED_SECTION
        )
        last_schema = (
            limitations_schema(FIXED_EXERCISE if fixed_assessments else EXERCISE)
            if structured
            else None
        )
        last_prompt = (
            FIXED_EXERCISE_PROMPT if fixed_assessments else EXERCISE_PROMPT
        ) + LIMITATIONS_NOTICE
        contract_recipe = (
            {
                "structure_contract": packet["structure_contract"],
                "unpinned_section_schema": section_schema,
                "last_exercise_schema": limitations_schema(EXERCISE),
                "last_exercise_prompt": EXERCISE_PROMPT + LIMITATIONS_NOTICE,
            }
            if structured
            else {}
        )
        legacy_job = DraftJob(
            store,
            packet,
            model,
            weights,
            wait_seconds,
            pipeline=POLICY,
            recipe_extra={
                "prompts": [INTRO_PROMPT, CHAPTER_PROMPT, EXERCISE_PROMPT],
                "schemas": [INTRO, pinned_schema, EXERCISE],
                **contract_recipe,
            },
        )
        job = legacy_job
        if fixed_assessments:
            job = DraftJob(
                store,
                packet,
                model,
                weights,
                wait_seconds,
                pipeline=POLICY,
                recipe_extra={
                    "prompts": [INTRO_PROMPT, CHAPTER_PROMPT, FIXED_EXERCISE_PROMPT],
                    "schemas": [INTRO, pinned_schema, FIXED_EXERCISE],
                    "fixed_assessments": "complete-learning-goal-task-v1",
                    "unchanged_body_job": legacy_job.key,
                    **contract_recipe,
                    **(
                        {
                            "last_exercise_schema": last_schema,
                            "last_exercise_prompt": last_prompt,
                        }
                        if structured
                        else {}
                    ),
                },
            )
        job.total = 1 + len(chapters) + len(plan["exercises"])
        legacy_job.total = job.total
        if any(a["id"] == job.key for a in Wiki(store.root).catalog()["articles"]):
            Wiki(store.root).article(job.key)
            return {"id": job.key, "state": "already_published"}
        navigation = [{"heading": c["heading"]} for c in plan["chapters"]]

        def body_stage(name, *args, **kwargs):
            job.current = name
            value = legacy_job.stage(name, *args, **kwargs)
            if legacy_job is not job:
                job.completed += 1
                job.progress("assembling", name)
            return value

        try:
            intro = body_stage(
                "introduction",
                INTRO_PROMPT,
                INTRO,
                bounded_context(
                    {
                        "topic": packet["title"],
                        "chapter_navigation": navigation,
                        "sources": intro_packet["sources"],
                    }
                ),
                lambda value: introduction(value, intro_packet),
                evidence_packet=intro_packet,
            )
            sections = []
            for number, (spec, scoped) in enumerate(
                zip(plan["chapters"], chapters, strict=True)
            ):
                selected = code_excerpt(spec.get("code_excerpt"), scoped)
                previous = [
                    p["text"].strip() for s in sections for p in s["paragraphs"]
                ]
                context = bounded_context(
                    {
                        "topic": packet["title"],
                        "chapter_navigation": navigation,
                        "current_section": {
                            "heading": spec["heading"],
                            "question": spec["focus"],
                        },
                        "selected_code": selected,
                        "sources": scoped["sources"],
                    }
                )
                value = body_stage(
                    f"chapter-{number}",
                    CHAPTER_PROMPT,
                    chapter_schema(
                        legacy_job,
                        f"chapter-{number}",
                        pinned_schema if selected else section_schema,
                        context,
                        scoped,
                        spec["heading"],
                    ),
                    context,
                    lambda value, s=scoped, p=spec, code=selected: validate_section(
                        attach_code(value, code), s, p, previous
                    ),
                    evidence_packet=scoped,
                )
                sections.append(attach_code(value, selected))
            quizzes, pitfalls, gaps = [], [], [packet["scope_note"], *intro["gaps"]]
            for number, spec in enumerate(plan["exercises"]):
                scoped = chapters[spec["chapter"]]
                previous = [q["question"].strip() for q in quizzes]
                question = assigned[number] if fixed_assessments else None
                context = {
                    "topic": packet["title"],
                    "focus": spec["focus"],
                    "previous_questions": previous,
                    "sources": scoped["sources"],
                }
                if question is not None:
                    context["fixed_question"] = question
                exercise_prompt = (
                    FIXED_EXERCISE_PROMPT if fixed_assessments else EXERCISE_PROMPT
                )
                exercise_schema = FIXED_EXERCISE if fixed_assessments else EXERCISE
                if structured and number == len(plan["exercises"]) - 1:
                    exercise_prompt, exercise_schema = last_prompt, last_schema
                result = job.stage(
                    f"self-check-{number}",
                    exercise_prompt,
                    exercise_schema,
                    bounded_context(context),
                    lambda value, s=scoped, q=question, required=(structured and number == len(plan["exercises"]) - 1): (
                        exercise(
                            fixed_exercise(value, q) if q is not None else value,
                            s,
                            previous,
                            require_limitations=required,
                        )
                    ),
                    evidence_packet=scoped,
                )
                if question is not None:
                    result = fixed_exercise(result, question)
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
            atomic_json(
                job.root / "assembled.json", {"article": article, "checks": checks}
            )
            result = publish_draft(
                store, article, packet, job.key, model, weights, pipeline=POLICY
            )
            job.progress("draft_published", "complete")
            return result
        except Exception as exc:
            job.progress(
                "deferred_gpu"
                if isinstance(exc, GpuBusyError)
                else "paused"
                if isinstance(exc, InterruptedError)
                else "failed",
                job.current,
                error_type=type(exc).__name__,
            )
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--model", default="qwen3.5:9b")
    parser.add_argument("--wait-gpu-seconds", type=int, default=0)
    parser.add_argument("--fixed-assessments", action="store_true")
    args = parser.parse_args()
    result = generate(
        ReadOnlyArchive(args.root),
        json.loads(args.plan.read_text("utf-8")),
        args.model,
        wait_seconds=args.wait_gpu_seconds,
        fixed_assessments=args.fixed_assessments,
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
