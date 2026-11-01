import copy
import json
from types import SimpleNamespace

import pytest

from test_course_wiki_planning import setup
from wechat_decrypt_tool import course_wiki_directory as directory
from wechat_decrypt_tool import course_wiki_planning as planning
from wechat_decrypt_tool.course_knowledge import atomic_json
from wechat_decrypt_tool.course_library import canonical
from wechat_decrypt_tool.course_lesson_exports import digest


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    def response(context, number):
        selected = context["eligible"]
        return json.dumps(
            {
                "decision": "propose",
                "title": "Synthetic topic " + selected[0],
                "introduction": selected[0],
                "chapters": [
                    {
                        "heading": f"Synthetic heading {i}",
                        "focus": f"Complete learning goal {i}",
                        "section": selected[i % len(selected)],
                    }
                    for i in range(4)
                ],
                "reason": "Only this explicit complete directory page is in scope.",
            }
        )

    store, topics, freezes, calls = setup(tmp_path, monkeypatch, response)
    lesson = {
        "title": "Synthetic long course",
        "raw_revision": "original",
        "sections": [
            {
                "id": f"section-{i}",
                "title": f"Complete section title {i}",
                "summary": f"Full synthetic summary {i}: " + "S" * 250,
            }
            for i in range(54)
        ],
    }

    def library(root):
        return SimpleNamespace(lesson=lambda video: lesson)

    monkeypatch.setattr(directory, "Library", library)
    monkeypatch.setattr(planning, "Library", library)
    monkeypatch.setattr(directory, "freeze", lambda *a, **kw: {})
    return SimpleNamespace(
        store=store, lesson=lesson, topics=topics, freezes=freezes, calls=calls
    )


@pytest.mark.parametrize("count", [1, 2, 3, 16, 17, 18, 33, 54, 57, 62])
def test_all_complete_rows_partition_once_in_original_order_with_ragged_tail(count):
    rows = [
        {"id": f"s{i}", "title": "Synthetic title", "summary": "Whole summary"}
        for i in range(count)
    ]
    before = copy.deepcopy(rows)
    pages = directory.partition(rows)
    assert [v for p in pages for v in p] == [r["id"] for r in rows]
    assert rows == before
    assert all(len(p) <= directory.MAX_ROWS for p in pages)
    if count >= 3:
        assert all(len(p) >= 3 for p in pages)


def test_metadata_partition_does_not_call_model_or_drop_unwritable_sections(
    pipeline, monkeypatch
):
    p = pipeline

    def freeze(root, title, selections):
        if selections[0]["sections"] == ["section-3"]:
            raise ValueError("Synthetic original cannot be frozen for a chapter")

    monkeypatch.setattr(directory, "freeze", freeze)
    receipt = directory.create(p.store, "video")
    value = directory.read(p.store.root, receipt["directory"])
    assert value["ineligible"] == ["section-3"]
    assert sum(map(len, value["pages"])) == 54
    assert value["sections"] == [
        {k: s[k] for k in ("id", "title", "summary")} for s in p.lesson["sections"]
    ]
    assert not p.calls
    assert directory.create(p.store, "video") == receipt


def test_long_default_failure_is_preserved_and_each_page_uses_its_own_full_sources(
    pipeline,
):
    p = pipeline
    with pytest.raises(ValueError, match="complete_directory_exceeds_context_limit"):
        planning.propose(p.store, "video")
    assert not p.calls
    receipt = directory.create(p.store, "video")
    manifest = directory.read(p.store.root, receipt["directory"])
    all_seen, jobs = [], []
    for number, selected in enumerate(manifest["pages"]):
        planned = planning.propose(
            p.store, "video", directory=receipt["directory"], page_number=number
        )
        context = p.calls[-1]
        assert [s["id"] for s in context["sections"]] == selected
        assert all(s in selected for s in planned["selected_sections"])
        assert len(planned["unselected_sections"]) >= 50
        assert context["directory_page"]["total"] == len(manifest["pages"])
        assert context["directory_page"]["whole_directory_sections"] == 54
        assert len(canonical(context).decode()) <= 12000
        all_seen.extend(context["sections"])
        jobs.append(planned["job"])
        assert (
            planning.propose(
                p.store, "video", directory=receipt["directory"], page_number=number
            )
            == planned
        )
    assert all_seen == directory.descriptors(p.lesson)
    assert len(set(jobs)) == len(manifest["pages"]) == len(p.calls)


def test_page_model_attempt_budget_survives_restart(pipeline, monkeypatch):
    p = pipeline
    receipt = directory.create(p.store, "video")
    calls = []

    def invalid(*args, **kwargs):
        calls.append(True)
        return "{}"

    monkeypatch.setattr(planning, "local_chat", invalid)
    for _ in range(2):
        with pytest.raises(ValueError, match="attempt_budget_exhausted"):
            planning.propose(
                p.store, "video", directory=receipt["directory"], page_number=0
            )
    assert len(calls) == 3
    assert len(list(p.store.root.glob("wiki/planning-jobs/*/attempt-*.json"))) == 3


def test_page_cannot_cite_an_original_section_from_another_page(pipeline, monkeypatch):
    p = pipeline
    receipt = directory.create(p.store, "video")
    original = planning.local_chat

    def wrong_page(*args, **kwargs):
        value = json.loads(original(*args, **kwargs))
        value["chapters"][0]["section"] = "section-0"
        return json.dumps(value)

    monkeypatch.setattr(planning, "local_chat", wrong_page)
    with pytest.raises(ValueError, match="attempt_budget_exhausted"):
        planning.propose(
            p.store, "video", directory=receipt["directory"], page_number=1
        )
    assert len(p.calls) == 3
    assert not p.freezes


def test_raw_source_change_rejects_old_pages_even_when_directory_text_unchanged(
    pipeline,
):
    p = pipeline
    receipt = directory.create(p.store, "video")
    p.lesson["raw_revision"] = "changed"
    with pytest.raises(ValueError, match="lesson_changed"):
        planning.propose(
            p.store, "video", directory=receipt["directory"], page_number=0
        )
    assert not p.calls


def test_consistent_new_manifest_cannot_hide_a_dropped_directory_row(pipeline):
    p = pipeline
    receipt = directory.create(p.store, "video")
    value = directory.read(p.store.root, receipt["directory"])
    value["pages"][0].pop()
    key = digest(canonical(value))[:32]
    folder = p.store.root / "wiki/theme-directories" / key
    folder.mkdir(parents=True)
    atomic_json(folder / "manifest.json", value)
    with pytest.raises(ValueError, match="incomplete_or_repeated_partition"):
        directory.read(p.store.root, key)
    assert not p.calls


def test_single_oversized_summary_fails_instead_of_being_truncated():
    with pytest.raises(ValueError, match="single_complete_row_exceeds_limit"):
        directory.partition([{"id": "s0", "title": "Synthetic", "summary": "A" * 9000}])


@pytest.mark.parametrize("number", [-1, True, 999])
def test_invalid_page_never_loads_planning_model(pipeline, number):
    p = pipeline
    receipt = directory.create(p.store, "video")
    with pytest.raises(ValueError, match="invalid_page_scope"):
        planning.propose(
            p.store, "video", directory=receipt["directory"], page_number=number
        )
    assert not p.calls
