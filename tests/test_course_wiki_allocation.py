import copy
import json
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

from test_course_wiki_evidence import (
    archive as source_archive,
    long_evidence as source_long_evidence,
    published as source_published,
    retain_lesson,
)
from test_course_wiki_synthesis import model
from wechat_decrypt_tool import course_wiki_allocation as allocation
from wechat_decrypt_tool import course_wiki_planning as planning
from wechat_decrypt_tool import course_wiki_snapshot as snapshot
from wechat_decrypt_tool import course_wiki_synthesis as synthesis
from wechat_decrypt_tool import course_wiki_writer as writer
from wechat_decrypt_tool.course_knowledge import GpuBusyError, atomic_json
from wechat_decrypt_tool.course_library import canonical
from wechat_decrypt_tool.course_lesson_exports import digest
from wechat_decrypt_tool.course_video_visual_audit import ReadOnlyArchive
from wechat_decrypt_tool.course_wiki import Wiki, validate_article

archive = source_archive
published = source_published
long_evidence = source_long_evidence


def proposal(context):
    eligible = context["eligible"]
    return {
        "decision": "propose",
        "title": "Synthetic coherent allocated topic",
        "introduction": eligible[-1],
        "chapters": [
            {
                "heading": f"Synthetic chapter {i}",
                "focus": f"Explain complete synthetic learning task {i}",
                "section": eligible[i % len(eligible)],
            }
            for i in range(4)
        ],
        "reason": "Only the explicit original ranges support this synthetic scope.",
    }


def setup(long_evidence, monkeypatch, respond=None):
    store, lesson, partition = long_evidence
    reader = ReadOnlyArchive(store.root)
    prepared = allocation.create(reader, partition["partition"])
    calls = []

    @contextmanager
    def slot(*args):
        yield

    def chat(name, messages, *, schema):
        assert messages[0]["content"] == planning.ALLOCATION_PROMPT
        context = json.loads(messages[1]["content"])
        calls.append(context)
        return json.dumps(respond(context) if respond else proposal(context))

    monkeypatch.setattr(planning, "slot", slot)
    monkeypatch.setattr(planning, "local_chat", chat)
    monkeypatch.setattr(planning, "local_model_digest", lambda _: "synthetic-weights")
    return reader, lesson, prepared, calls


def test_full_originals_partition_into_bounded_planning_contexts_exactly_once(
    long_evidence,
):
    store, lesson, partition = long_evidence
    reader = ReadOnlyArchive(store.root)
    prepared = allocation.create(reader, partition["partition"])
    manifest, originals, _ = allocation.read(store.root, prepared["allocation"])
    assert prepared["scopes"] > 1
    assert [n for scope in manifest["scopes"] for n in scope] == list(
        range(partition["pages"])
    )
    collected = {}
    for n in range(prepared["scopes"]):
        scoped = allocation.scope(store.root, prepared["allocation"], n, lesson["id"])
        context = scoped["context"]
        assert len(canonical(context).decode()) <= allocation.MAX_CONTEXT
        assert (
            context["original_section"]["summary"] == lesson["sections"][0]["summary"]
        )
        assert not context["semantic_boundary_verified"]
        for page in context["evidence_pages"]:
            selection = scoped["selections"][page["id"]]
            assert selection["evidence_page"] == page["evidence_page"]
            for source in page["originals"]:
                origin = source["text_origin"]
                collected.setdefault(origin["reference"], []).append(source["text"])
                original = originals["originals"][origin["reference"]]
                assert (
                    source["text"] == original["text"][origin["start"] : origin["end"]]
                )
                if source["kind"] == "frame":
                    assert source["image_sha256"] == original["image_sha256"]
    assert {k: "".join(v) for k, v in collected.items()} == {
        k: v["text"] for k, v in originals["originals"].items()
    }
    assert allocation.create(reader, partition["partition"]) == prepared


@pytest.mark.parametrize("mutation", ["missing", "repeated", "order", "accepted"])
def test_rehashed_context_manifest_cannot_drop_reorder_repeat_or_accept(
    long_evidence, mutation
):
    store, _, partition = long_evidence
    prepared = allocation.create(ReadOnlyArchive(store.root), partition["partition"])
    value, _, _ = allocation.read(store.root, prepared["allocation"])
    if mutation == "missing":
        value["scopes"][0].pop()
    elif mutation == "repeated":
        value["scopes"][-1].append(value["scopes"][0][0])
    elif mutation == "order":
        value["scopes"].reverse()
    else:
        value["topic_coverage_verified"] = True
    key = digest(canonical(value))[:32]
    path = store.root / "wiki/allocation-contexts" / key
    path.mkdir()
    atomic_json(path / "manifest.json", value)
    with pytest.raises(ValueError, match="contexts_incomplete_or_changed"):
        allocation.read(store.root, key)


@pytest.mark.parametrize("number", [-1, True, 10000])
def test_invalid_scope_never_falls_back_to_a_whole_window(long_evidence, number):
    store, lesson, partition = long_evidence
    prepared = allocation.create(ReadOnlyArchive(store.root), partition["partition"])
    with pytest.raises(ValueError, match="invalid_scope"):
        allocation.scope(store.root, prepared["allocation"], number, lesson["id"])


def test_original_page_too_large_is_explicit_failure_without_truncation(
    long_evidence, monkeypatch
):
    store, _, partition = long_evidence
    monkeypatch.setattr(allocation, "MAX_CONTEXT", 50)
    with pytest.raises(ValueError, match="single_original_page_exceeds_context"):
        allocation.create(ReadOnlyArchive(store.root), partition["partition"])
    assert not list(store.root.glob("wiki/allocation-contexts/*/manifest.json"))


def test_real_originals_go_to_planner_and_selected_ranges_freeze_for_writing(
    long_evidence, monkeypatch
):
    reader, lesson, prepared, calls = setup(long_evidence, monkeypatch)
    result = planning.propose(
        reader, lesson["id"], allocation=prepared["allocation"], allocation_scope=0
    )
    assert result["state"] == "prepared_for_writing"
    assert result["selected_evidence_pages"]
    assert result["unselected_evidence_pages"]
    assert result["complete_sections_selected"] == []
    assert result["partially_selected_sections"] == [prepared["section"]]
    assert len(calls) == 1
    context = calls[0]
    assert context["evidence_pages"][0]["originals"][0]["text"]
    plan = json.loads(
        (reader.root / "wiki/planning-jobs" / result["job"] / "plan.json").read_bytes()
    )
    packet, intro, chapters = synthesis.prepare_plan(reader.root, plan)
    assert packet["evidence_partitions"] == {
        prepared["partition"]: result["selected_evidence_pages"]
    }
    for chapter, row in zip(chapters, plan["chapters"]):
        selected = row["selections"][0]
        assert chapter["evidence_partitions"] == {
            prepared["partition"]: [selected["evidence_page"]]
        }
    assert plan["exercises"] == [
        {"chapter": n, "focus": row["focus"]} for n, row in enumerate(plan["chapters"])
    ]
    assert (
        planning.propose(
            reader, lesson["id"], allocation=prepared["allocation"], allocation_scope=0
        )
        == result
    )
    assert len(calls) == 1
    # The actual existing writer receives the explicit original ranges.
    model(monkeypatch)
    draft = synthesis.generate(reader, plan)
    article = Wiki(reader.root).article(draft["id"])
    assert article["packet"]["evidence_partitions"] == packet["evidence_partitions"]
    assert len(article["article"]["sections"]) == 4
    assert not result["full_course_coverage_verified"]


def test_page_outside_context_exhausts_budget_and_cannot_reset_on_resume(
    long_evidence, monkeypatch
):
    def invalid(context):
        value = proposal(context)
        value["chapters"][0]["section"] = "page-9999"
        return value

    reader, lesson, prepared, calls = setup(long_evidence, monkeypatch, invalid)
    for _ in range(2):
        with pytest.raises(ValueError, match="attempt_budget_exhausted"):
            planning.propose(
                reader,
                lesson["id"],
                allocation=prepared["allocation"],
                allocation_scope=0,
            )
    assert len(calls) == 3
    assert not list(reader.root.glob("wiki/planning-jobs/*/plan.json"))


def test_insufficient_originals_skip_and_retain_all_unselected_pages(
    long_evidence, monkeypatch
):
    def skip(context):
        return {
            "decision": "skip",
            "title": "",
            "introduction": "",
            "chapters": [],
            "reason": "Synthetic disconnected evidence cannot support four chapters.",
        }

    reader, lesson, prepared, _ = setup(long_evidence, monkeypatch, skip)
    result = planning.propose(
        reader, lesson["id"], allocation=prepared["allocation"], allocation_scope=0
    )
    assert result["state"] == "no_supported_topic_proposed"
    assert result["selected_evidence_pages"] == []
    assert result["unselected_evidence_pages"] == list(range(prepared["pages"]))
    assert not list(reader.root.glob("wiki/planning-jobs/*/plan.json"))


def test_busy_gpu_does_not_spend_budget_and_changed_source_never_rebinds(
    long_evidence, monkeypatch
):
    reader, lesson, prepared, calls = setup(long_evidence, monkeypatch)

    @contextmanager
    def busy(*args):
        raise GpuBusyError("synthetic_busy")
        yield

    monkeypatch.setattr(planning, "slot", busy)
    with pytest.raises(GpuBusyError):
        planning.propose(
            reader, lesson["id"], allocation=prepared["allocation"], allocation_scope=0
        )
    assert not calls
    assert not list(reader.root.glob("wiki/planning-jobs/*/attempt-*.json"))
    changed = copy.deepcopy(lesson)
    changed["evidence"][str(changed["sections"][0]["context"][0])]["text"] += "Changed"
    retain_lesson(reader.root, changed)
    with pytest.raises(ValueError, match="original_lesson_changed"):
        planning.propose(
            reader, lesson["id"], allocation=prepared["allocation"], allocation_scope=0
        )
    assert not calls


def test_rehashed_planning_recipe_cannot_silently_shorten_the_original_context(
    long_evidence, monkeypatch
):
    reader, lesson, prepared, calls = setup(long_evidence, monkeypatch)
    result = planning.propose(
        reader, lesson["id"], allocation=prepared["allocation"], allocation_scope=0
    )
    folder = reader.root / "wiki/planning-jobs" / result["job"]
    binding = json.loads((folder / "manifest.json").read_bytes())
    binding["context"]["evidence_pages"][0]["originals"][0]["text"] = "Shortened"
    key = digest(canonical(binding))[:32]
    changed = reader.root / "wiki/planning-jobs" / key
    changed.mkdir()
    atomic_json(changed / "manifest.json", binding)
    pointer = next((reader.root / "wiki/theme-planning-sources").glob("*.json"))
    saved = json.loads(pointer.read_bytes())
    saved["job"] = key
    atomic_json(pointer, saved)
    with pytest.raises(ValueError, match="allocation_original_context_changed"):
        planning.propose(
            reader, lesson["id"], allocation=prepared["allocation"], allocation_scope=0
        )
    assert len(calls) == 1


def planned_originals(long_evidence, monkeypatch):
    reader, lesson, prepared, _ = setup(long_evidence, monkeypatch)
    result = planning.propose(
        reader, lesson["id"], allocation=prepared["allocation"], allocation_scope=0
    )
    plan = json.loads(
        (reader.root / "wiki/planning-jobs" / result["job"] / "plan.json").read_bytes()
    )
    assert plan["structure_contract"] == synthesis.ALLOCATED_STRUCTURE
    return reader, plan


def test_new_allocated_contract_guides_structure_without_changing_default_schemas(
    long_evidence, monkeypatch
):
    reader, plan = planned_originals(long_evidence, monkeypatch)
    calls = model(monkeypatch)
    draft = synthesis.generate(reader, plan)
    chapters = [c for c in calls if c["stage"].startswith("Synthetic chapter")]
    assert len(chapters) == 4
    assert all(
        c["schema"]["properties"]["paragraphs"]["minItems"] == 2 for c in chapters
    )
    exercises = [c for c in calls if c["stage"].startswith("quiz-")]
    assert exercises[-1]["schema"]["properties"]["pitfalls"]["minItems"] == 1
    assert all(
        "minItems" not in c["schema"]["properties"]["pitfalls"] for c in exercises[:-1]
    )
    assert "minItems" not in synthesis.SECTION["properties"]["paragraphs"]
    assert "minItems" not in synthesis.EXERCISE["properties"]["pitfalls"]
    publication = Wiki(reader.root).article(draft["id"])
    assert publication["packet"]["structure_contract"] == synthesis.ALLOCATED_STRUCTURE
    changed = copy.deepcopy(publication["article"])
    changed["sections"][0]["paragraphs"].pop()
    with pytest.raises(
        ValueError, match="section_requires_at_least_two_developed_paragraphs"
    ):
        validate_article(changed, publication["packet"])


def test_missing_supported_limitation_is_rejected_at_its_stage_with_same_budget(
    long_evidence, monkeypatch
):
    reader, plan = planned_originals(long_evidence, monkeypatch)
    calls = model(monkeypatch)
    original = writer.local_chat

    def omit(name, messages, schema):
        value = json.loads(original(name, messages, schema))
        context = json.loads(messages[1]["content"])
        if len(context.get("previous_questions", [])) == 3:
            value["pitfalls"] = []
        return json.dumps(value)

    monkeypatch.setattr(writer, "local_chat", omit)
    before = None
    for _ in range(2):
        with pytest.raises(ValueError, match="attempt_budget_exhausted_self-check-3"):
            synthesis.generate(reader, plan)
        attempts = list(reader.root.glob("wiki/jobs/*/attempts/self-check-3-*.json"))
        current = {
            p.relative_to(reader.root).as_posix(): p.read_bytes() for p in attempts
        }
        if before is not None:
            assert current == before
        before = current
    assert sum(c["stage"] == "quiz-3" for c in calls) == 3
    assert not Wiki(reader.root).catalog()["articles"]
    assert len(attempts) == 3
    assert all(
        json.loads(p.read_bytes())["validation_error"]
        == "requires_source_bound_limitation_in_final_self_check"
        for p in attempts
    )
    assert len(calls) == 11


def test_fixed_question_strategy_reuses_the_same_allocated_body_budget(
    long_evidence, monkeypatch
):
    reader, plan = planned_originals(long_evidence, monkeypatch)
    calls = model(monkeypatch)
    original = writer.local_chat

    def fixed(name, messages, schema):
        value = json.loads(original(name, messages, schema))
        if "fixed_question" in json.loads(messages[1]["content"]):
            value["quiz"].pop("question")
        return json.dumps(value)

    monkeypatch.setattr(writer, "local_chat", fixed)
    body = synthesis.generate(reader, plan)
    assessment = synthesis.generate(reader, plan, fixed_assessments=True)
    manifest = json.loads(
        (reader.root / "wiki/jobs" / assessment["id"] / "manifest.json").read_bytes()
    )
    assert manifest["recipe"]["extra"]["unchanged_body_job"] == body["id"]
    assert sum(c["stage"] == "intro" for c in calls) == 1
    assert sum(c["stage"].startswith("Synthetic chapter") for c in calls) == 4
    assert len(calls) == 13
    backup = snapshot.create(
        reader.root, reader.root.parent / "contract-backup", reserve_gib=0
    )
    restored = snapshot.restore(
        backup, reader.root.parent / "contract-restored", reserve_gib=0
    )
    assert Wiki(restored).article(assessment["id"]) == Wiki(reader.root).article(
        assessment["id"]
    )
    checked = subprocess.run(
        [
            sys.executable,
            "-I",
            str(Path(__file__).with_name("wiki_snapshot_isolated_reader.py")),
            str(restored),
            str(reader.root),
            str(Path(snapshot.__file__).parent),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    assert json.loads(checked.stdout)["original_root_access_attempts"] == 0


def test_structure_contract_requires_explicit_allocated_sources(
    long_evidence, monkeypatch
):
    reader, plan = planned_originals(long_evidence, monkeypatch)
    plan["structure_contract"] = "unrecognized"
    with pytest.raises(ValueError, match="invalid_allocated_structure_contract"):
        synthesis.prepare_plan(reader.root, plan)
