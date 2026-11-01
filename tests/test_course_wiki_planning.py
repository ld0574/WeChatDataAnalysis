import copy
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from wechat_decrypt_tool import course_wiki_planning as planning
from wechat_decrypt_tool.course_knowledge import GpuBusyError


def proposal():
    return {
        "decision": "propose",
        "title": "Synthetic topic",
        "introduction": "section-0",
        "chapters": [
            {
                "heading": f"Chapter {i}",
                "focus": f"Complete learning goal {i}",
                "section": f"section-{i}",
            }
            for i in range(4)
        ],
        "reason": "Only the selected synthetic evidence is in scope.",
    }


def setup(tmp_path, monkeypatch, respond=None):
    lesson = {
        "title": "Synthetic course",
        "sections": [
            {
                "id": f"section-{i}",
                "title": f"Part {i}",
                "summary": "Synthetic planning summary",
            }
            for i in range(5)
        ],
    }
    topics = []
    freezes = []
    calls = []
    monkeypatch.setattr(
        planning, "Library", lambda root: SimpleNamespace(lesson=lambda video: lesson)
    )
    monkeypatch.setattr(
        planning,
        "Wiki",
        lambda root: SimpleNamespace(catalog=lambda: {"articles": topics}),
    )
    monkeypatch.setattr(planning, "prepare", lambda *a, **kw: {})
    monkeypatch.setattr(
        planning, "prepare_plan", lambda root, plan: freezes.append(copy.deepcopy(plan))
    )
    monkeypatch.setattr(
        planning, "local_model_digest", lambda model: "synthetic-weights"
    )

    @contextmanager
    def slot(store, deadline, progress):
        yield

    def model(name, messages, *, schema):
        context = json.loads(messages[1]["content"])
        calls.append(context)
        return respond(context, len(calls)) if respond else json.dumps(proposal())

    monkeypatch.setattr(planning, "slot", slot)
    monkeypatch.setattr(planning, "local_chat", model)
    return (
        SimpleNamespace(root=tmp_path, checkpoint=lambda: None),
        topics,
        freezes,
        calls,
    )


def test_derives_all_chapter_assessments_and_retains_unselected_sources(
    tmp_path, monkeypatch
):
    store, _, freezes, calls = setup(tmp_path, monkeypatch)
    receipt = planning.propose(store, "video")
    assert receipt["state"] == "prepared_for_writing"
    assert receipt["selected_sections"] == [f"section-{i}" for i in range(4)]
    assert receipt["unselected_sections"] == ["section-4"]
    plan = freezes[-1]
    assert plan["exercises"] == [
        {"chapter": i, "focus": chapter["focus"]}
        for i, chapter in enumerate(plan["chapters"])
    ]
    assert [c["selections"] for c in plan["chapters"]] == [
        [{"video": "video", "sections": [f"section-{i}"]}] for i in range(4)
    ]
    assert len(calls) == 1
    assert len(calls[0]["sections"]) == 5
    assert not receipt["full_course_coverage_verified"]


def test_resume_freezes_original_topic_context_without_new_model_calls(
    tmp_path, monkeypatch
):
    store, topics, _, calls = setup(tmp_path, monkeypatch)
    first = planning.propose(store, "video")
    topics.append({"title": proposal()["title"]})
    assert planning.propose(store, "video") == first
    assert len(calls) == 1
    path = store.root / "wiki/planning-jobs" / first["job"] / "plan.json"
    value = json.loads(path.read_bytes())
    value["exercises"][0]["focus"] = "Changed scope"
    path.write_text(json.dumps(value), "utf-8")
    with pytest.raises(ValueError, match="prepared_plan_changed"):
        planning.propose(store, "video")
    assert len(calls) == 1


def test_invalid_sources_exhaust_budget_across_context_changes_and_restarts(
    tmp_path, monkeypatch
):
    def invalid(context, number):
        value = proposal()
        value["chapters"][0]["section"] = "invented"
        return json.dumps(value)

    store, topics, _, calls = setup(tmp_path, monkeypatch, invalid)
    for i in range(2):
        topics.append({"title": f"Other topic {i}"})
        with pytest.raises(ValueError, match="attempt_budget_exhausted"):
            planning.propose(store, "video")
        assert len(calls) == 3
    assert not list(store.root.glob("wiki/planning-jobs/*/plan.json"))


def test_gpu_wait_does_not_spend_attempt_budget(tmp_path, monkeypatch):
    def interrupted(context, number):
        if number == 1:
            raise GpuBusyError("Synthetic busy")
        return json.dumps(proposal())

    store, _, _, calls = setup(tmp_path, monkeypatch, interrupted)
    with pytest.raises(GpuBusyError):
        planning.propose(store, "video")
    assert not list(store.root.glob("wiki/planning-jobs/*/attempt-*.json"))
    receipt = planning.propose(store, "video")
    attempts = list(
        (store.root / "wiki/planning-jobs" / receipt["job"]).glob("attempt-*.json")
    )
    assert len(calls) == 2 and len(attempts) == 1


def test_unsupported_theme_is_explicit_skip_without_article_plan(tmp_path, monkeypatch):
    skip = {
        "decision": "skip",
        "title": "",
        "introduction": "",
        "chapters": [],
        "reason": "Synthetic evidence insufficient",
    }
    store, _, freezes, _ = setup(tmp_path, monkeypatch, lambda *a: json.dumps(skip))
    receipt = planning.propose(store, "video")
    assert receipt["state"] == "no_supported_topic_proposed"
    assert receipt["unselected_sections"] == [f"section-{i}" for i in range(5)]
    assert not freezes


def test_full_original_freeze_failure_prevents_plan_publication(tmp_path, monkeypatch):
    store, _, _, calls = setup(tmp_path, monkeypatch)

    def unsupported(*args):
        raise ValueError("wiki_synthesis_union_evidence_exceeds_limit")

    monkeypatch.setattr(planning, "prepare_plan", unsupported)
    with pytest.raises(ValueError, match="attempt_budget_exhausted"):
        planning.propose(store, "video")
    assert len(calls) == 3
    assert not list(store.root.glob("wiki/planning-jobs/*/plan.json"))


def test_tampered_pointer_cannot_read_other_job_path(tmp_path, monkeypatch):
    store, _, _, calls = setup(tmp_path, monkeypatch)
    planning.propose(store, "video")
    pointer = next(store.root.glob("wiki/theme-planning-sources/*.json"))
    value = json.loads(pointer.read_bytes())
    value["job"] = "../other"
    pointer.write_text(json.dumps(value), "utf-8")
    with pytest.raises(ValueError, match="invalid_job_pointer"):
        planning.propose(store, "video")
    assert len(calls) == 1


def test_oversized_directory_is_rejected_without_truncation_or_model_call(
    tmp_path, monkeypatch
):
    store, _, _, calls = setup(tmp_path, monkeypatch)
    lesson = planning.Library(store.root).lesson("video")
    lesson["sections"][0]["summary"] = "Synthetic complete directory " * 600
    before = copy.deepcopy(lesson)
    with pytest.raises(ValueError, match="complete_directory_exceeds_context_limit"):
        planning.propose(store, "video")
    assert lesson == before
    assert not calls
    assert not list(store.root.glob("wiki/theme-planning-sources/*.json"))
