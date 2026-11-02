import json
from pathlib import Path

import pytest

from test_course_wiki_synthesis import (
    archive as source_archive,
    courseware as source_courseware,
    long_plan as source_long_plan,
    model,
    published as source_published,
)
from wechat_decrypt_tool import course_wiki_synthesis as synthesis
from wechat_decrypt_tool.course_knowledge import GpuBusyError
from wechat_decrypt_tool.course_video_visual_audit import ReadOnlyArchive

archive = source_archive
published = source_published
courseware = source_courseware
long_plan = source_long_plan


def test_new_chapter_requests_pin_exact_heading_and_resume_same_checkpoints(
    long_plan, monkeypatch
):
    store, plan = long_plan
    paused = [True]

    def intercept(stage, context):
        if stage == "quiz-0" and paused[0]:
            raise GpuBusyError("Synthetic GPU yield")

    calls = model(monkeypatch, intercept)
    reader = ReadOnlyArchive(store.root)
    with pytest.raises(GpuBusyError):
        synthesis.generate(reader, plan)
    chapter_calls = [c for c in calls if c["stage"].startswith("Part ")]
    assert len(chapter_calls) == 4
    assert all(
        c["schema"]["properties"]["heading"]["enum"] == [c["stage"]]
        for c in chapter_calls
    )
    paused[0] = False
    result = synthesis.generate(reader, plan)
    assert result["id"]
    assert len([c for c in calls if c["stage"].startswith("Part ")]) == 4


def test_unmodified_legacy_checkpoints_keep_their_original_generic_schema(
    long_plan, monkeypatch
):
    store, plan = long_plan
    selector = synthesis.chapter_schema
    monkeypatch.setattr(
        synthesis, "chapter_schema", lambda job, name, schema, *a: schema
    )
    paused = [True]

    def intercept(stage, context):
        if stage == "quiz-0" and paused[0]:
            raise GpuBusyError("Synthetic GPU yield")

    calls = model(monkeypatch, intercept)
    reader = ReadOnlyArchive(store.root)
    with pytest.raises(GpuBusyError):
        synthesis.generate(reader, plan)
    before = {
        str(p): p.read_bytes() for p in store.root.glob("wiki/jobs/*/chapter-*.json")
    }
    assert before
    assert all(
        "enum" not in c["schema"]["properties"]["heading"]
        for c in calls
        if c["stage"].startswith("Part ")
    )
    monkeypatch.setattr(synthesis, "chapter_schema", selector)
    paused[0] = False
    synthesis.generate(reader, plan)
    assert all(
        Path(p).read_bytes() == raw for p, raw in before.items()
    )
    assert len([c for c in calls if c["stage"].startswith("Part ")]) == 4


def test_exhausted_legacy_heading_attempts_cannot_gain_fresh_calls(
    long_plan, monkeypatch
):
    store, plan = long_plan
    selector = synthesis.chapter_schema
    monkeypatch.setattr(
        synthesis, "chapter_schema", lambda job, name, schema, *a: schema
    )

    def intercept(stage, context):
        if stage == "Part 3":
            return json.dumps({"heading": "Changed frozen heading"})

    calls = model(monkeypatch, intercept)
    reader = ReadOnlyArchive(store.root)
    with pytest.raises(ValueError, match="attempt_budget_exhausted_chapter-3"):
        synthesis.generate(reader, plan)
    assert sum(c["stage"] == "Part 3" for c in calls) == 3
    old = {
        str(p): p.read_bytes() for p in store.root.glob("wiki/jobs/*/attempts/*.json")
    }
    before = len(calls)
    monkeypatch.setattr(synthesis, "chapter_schema", selector)
    with pytest.raises(ValueError, match="attempt_budget_exhausted_chapter-3"):
        synthesis.generate(reader, plan)
    assert len(calls) == before
    assert all(
        Path(p).read_bytes() == raw for p, raw in old.items()
    )


def test_unrecognized_retained_input_fails_without_new_model_calls(
    long_plan, monkeypatch
):
    store, plan = long_plan

    def intercept(stage, context):
        if stage == "quiz-0":
            raise GpuBusyError("Synthetic GPU yield")

    calls = model(monkeypatch, intercept)
    reader = ReadOnlyArchive(store.root)
    with pytest.raises(GpuBusyError):
        synthesis.generate(reader, plan)
    attempt = next(store.root.glob("wiki/jobs/*/attempts/chapter-0-*.json"))
    value = json.loads(attempt.read_bytes())
    value["input_sha256"] = "0" * 64
    attempt.write_text(json.dumps(value), encoding="utf-8")
    before = len(calls)
    with pytest.raises(ValueError, match="retained_schema_binding_changed"):
        synthesis.generate(reader, plan)
    assert len(calls) == before
