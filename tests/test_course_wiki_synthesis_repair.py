import json
import subprocess
import sys
from pathlib import Path

import pytest

from test_course_wiki import archive as source_archive, published as source_published
from test_course_wiki_documents import courseware as source_courseware
from test_course_wiki_snapshot_writers import retain_frames
from test_course_wiki_synthesis import long_plan as source_long_plan, model
from wechat_decrypt_tool import course_wiki_synthesis as synthesis
from wechat_decrypt_tool import course_wiki_synthesis_repair as repair
from wechat_decrypt_tool import course_wiki_snapshot as snapshot
from wechat_decrypt_tool import course_wiki_writer as writer
from wechat_decrypt_tool.course_library import canonical
from wechat_decrypt_tool.course_video_visual_audit import ReadOnlyArchive
from wechat_decrypt_tool.course_wiki import Wiki

archive = source_archive
published = source_published
courseware = source_courseware
long_plan = source_long_plan


@pytest.fixture
def failed_parent(long_plan, monkeypatch):
    store, plan = long_plan

    def answer(stage, context):
        if "fixed_question" not in context:
            return None
        ref = next(iter(context["sources"]))
        return json.dumps(
            {
                "quiz": {
                    "answer": "Synthetic answer.",
                    "explanation": "Synthetic condition.",
                    "criteria": [] if stage == "quiz-1" else ["State the condition."],
                    "citations": [ref],
                },
                "pitfalls": [{"text": "Synthetic condition.", "citations": [ref]}],
                "gaps": [],
            }
        )

    calls = model(monkeypatch, answer)
    with pytest.raises(ValueError, match="budget_exhausted_self-check-1"):
        synthesis.generate(ReadOnlyArchive(store.root), plan, fixed_assessments=True)
    parent = json.loads((store.root / "wiki/last-build.json").read_bytes())["id"]
    monkeypatch.setattr(repair, "local_model_digest", lambda _: "frozen-local-weights")
    return store, plan, parent, calls


def good_answers(stage, context):
    if "fixed_question" not in context:
        return None
    ref = next(iter(context["sources"]))
    return json.dumps(
        {
            "quiz": {
                "answer": "Synthetic answer.",
                "explanation": "Synthetic condition.",
                "criteria": ["State the condition."],
                "citations": [ref],
            },
            "pitfalls": [{"text": "Synthetic condition.", "citations": [ref]}],
            "gaps": [],
        }
    )


def test_repairs_only_failed_and_unattempted_questions_preserving_every_parent_byte(
    failed_parent, monkeypatch
):
    store, plan, parent, old_calls = failed_parent
    original = store.root / "wiki/jobs" / parent
    before = {p.relative_to(original): p.read_bytes() for p in original.rglob("*.json")}
    body_id = json.loads((original / "manifest.json").read_bytes())["recipe"]["extra"][
        "unchanged_body_job"
    ]
    body = store.root / "wiki/jobs" / body_id
    body_before = {p.relative_to(body): p.read_bytes() for p in body.rglob("*.json")}
    calls = model(monkeypatch, good_answers)
    prepared = repair.repair(ReadOnlyArchive(store.root), parent, prepare_only=True)
    assert prepared["single_call_stages"] == 3 and prepared["reused_assessments"] == 1
    assert calls == []
    result = repair.repair(ReadOnlyArchive(store.root), parent)
    assert result["id"] == prepared["id"] and result["id"] != parent
    assert [r["stage"] for r in calls] == ["quiz-1", "quiz-2", "quiz-3"]
    assert all(
        c["schema"]["properties"]["quiz"]["properties"]["criteria"]["minItems"] == 1
        for c in calls
    )
    article = Wiki(store.root).article(result["id"])
    assert (
        len(article["article"]["sections"]) == 4
        and len(article["article"]["quiz"]) == 4
    )
    assert all(q["criteria"] for q in article["article"]["quiz"])
    assert {
        p.relative_to(original): p.read_bytes() for p in original.rglob("*.json")
    } == before
    assert {
        p.relative_to(body): p.read_bytes() for p in body.rglob("*.json")
    } == body_before
    assert (
        repair.repair(ReadOnlyArchive(store.root), parent)["state"]
        == "already_published"
    )
    assert len(calls) == 3
    with pytest.raises(ValueError, match="budget_exhausted_self-check-1"):
        synthesis.generate(ReadOnlyArchive(store.root), plan, fixed_assessments=True)
    assert len(calls) == 3


@pytest.mark.parametrize("crash", [False, True])
def test_single_call_repair_survives_failed_response_and_process_interruption(
    failed_parent, monkeypatch, crash
):
    store, _, parent, _ = failed_parent
    calls = []

    def chat(*args, **kwargs):
        calls.append(True)
        if crash:
            raise KeyboardInterrupt("synthetic worker interruption")
        return json.dumps(
            {
                "quiz": {
                    "answer": "Synthetic",
                    "explanation": "Synthetic",
                    "criteria": [],
                    "citations": [],
                },
                "pitfalls": [],
                "gaps": [],
            }
        )

    monkeypatch.setattr(writer, "local_chat", chat)
    with pytest.raises(KeyboardInterrupt if crash else ValueError):
        repair.repair(ReadOnlyArchive(store.root), parent)
    with pytest.raises(ValueError, match="budget_exhausted_self-check-1"):
        repair.repair(ReadOnlyArchive(store.root), parent)
    assert calls == [True]


@pytest.mark.parametrize("mutation", ["failure", "parent_file", "body_file", "model"])
def test_changed_lineage_or_model_is_rejected_before_any_new_inference(
    failed_parent, monkeypatch, mutation
):
    store, _, parent, _ = failed_parent
    prepared = repair.repair(ReadOnlyArchive(store.root), parent, prepare_only=True)
    original = store.root / "wiki/jobs" / parent
    if mutation == "model":
        monkeypatch.setattr(repair, "local_model_digest", lambda _: "other-weights")
    elif mutation == "failure":
        path = original / "attempts/self-check-1-01.json"
        v = json.loads(path.read_bytes())
        v["validation_error"] = "different_error"
        path.write_bytes(canonical(v))
    elif mutation == "parent_file":
        path = original / "self-check-0.json"
        v = json.loads(path.read_bytes())
        v["data"]["quiz"]["answer"] = "Changed accepted answer"
        v["data_sha256"] = repair.digest(canonical(v["data"]))
        path.write_bytes(canonical(v))
    else:
        body = json.loads((original / "manifest.json").read_bytes())["recipe"]["extra"][
            "unchanged_body_job"
        ]
        path = store.root / "wiki/jobs" / body / "chapter-0.json"
        v = json.loads(path.read_bytes())
        v["data"]["paragraphs"][0]["text"] = "Changed accepted body"
        v["data_sha256"] = repair.digest(canonical(v["data"]))
        path.write_bytes(canonical(v))
    calls = model(monkeypatch, good_answers)
    with pytest.raises(ValueError):
        repair.repair(ReadOnlyArchive(store.root), parent)
    assert calls == []
    assert not any(
        row["id"] == prepared["id"] for row in Wiki(store.root).catalog()["articles"]
    )


def test_snapshot_keeps_original_failed_assessment_and_all_body_dependencies(
    failed_parent, monkeypatch, tmp_path, published
):
    store, _, parent, _ = failed_parent
    retain_frames(store, published[1])
    model(monkeypatch, good_answers)
    result = repair.repair(ReadOnlyArchive(store.root), parent)
    publication = Wiki(store.root).article(result["id"])
    folders = snapshot.writer_dependencies(store.root, publication)
    assert parent in {p.name for p in folders} and len(folders) == 3
    before = {
        p.relative_to(store.root): p.read_bytes()
        for folder in folders
        for p in folder.rglob("*.json")
    }
    backup = snapshot.create(store.root, tmp_path / "backup", reserve_gib=0)
    restored = snapshot.restore(backup, tmp_path / "restored", reserve_gib=0)
    assert all((restored / name).read_bytes() == raw for name, raw in before.items())
    assert Wiki(restored).article(result["id"]) == publication
    process = subprocess.run(
        [
            sys.executable,
            "-I",
            str(Path(__file__).with_name("wiki_snapshot_isolated_reader.py")),
            str(restored),
            str(store.root),
            str(Path(snapshot.__file__).parent),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    assert json.loads(process.stdout)["original_root_access_attempts"] == 0
    target = restored / "wiki/jobs" / parent / "attempts/self-check-1-01.json"
    target.unlink()
    with pytest.raises(ValueError):
        snapshot.writer_dependencies(restored, publication)


def test_exact_exercise_message_preserves_frozen_source_order():
    packet = {
        "title": "Synthetic topic",
        "exercise_plan": [{"chapter": 0, "focus": "Explain condition"}],
        "chapter_scopes": [["v:10", "v:2"]],
        "sources": {
            "v:2": {"text": "Earlier original."},
            "v:10": {"text": "Later original."},
        },
    }
    extra = {
        "prompts": ["", "", "Synthetic instruction"],
        "schemas": [{}, {}, synthesis.FIXED_EXERCISE],
    }
    _, _, context, _, _, _ = repair.exercise_input(packet, extra, 0)
    assert list(context["sources"]) == ["v:2", "v:10"]


def test_draft_job_default_budget_recipe_stays_unchanged(failed_parent):
    store, _, parent, _ = failed_parent
    _, recipe = repair.manifest(store.root, parent)
    assert "stage_attempt_budget" not in recipe
    assert (
        recipe["extra"]["schemas"][2]["properties"]["quiz"]["properties"][
            "criteria"
        ].get("minItems", 0)
        == 0
    )
