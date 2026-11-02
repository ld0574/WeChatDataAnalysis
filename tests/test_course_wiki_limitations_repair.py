import json
import subprocess
import sys
from pathlib import Path

import pytest

from test_course_wiki import archive as source_archive, published as source_published
from test_course_wiki_documents import courseware as source_courseware
from test_course_wiki_snapshot_writers import retain_frames
from test_course_wiki_synthesis import long_plan as source_long_plan, model
from wechat_decrypt_tool import course_wiki_limitations_repair as repair
from wechat_decrypt_tool import course_wiki_snapshot as snapshot
from wechat_decrypt_tool import course_wiki_synthesis as synthesis
from wechat_decrypt_tool import course_wiki_synthesis_repair as criteria
from wechat_decrypt_tool import course_wiki_writer as writer
from wechat_decrypt_tool.course_knowledge import atomic_json
from wechat_decrypt_tool.course_video_visual_audit import ReadOnlyArchive
from wechat_decrypt_tool.course_wiki import Wiki

archive = source_archive
published = source_published
courseware = source_courseware
long_plan = source_long_plan


@pytest.fixture
def candidate(long_plan, monkeypatch):
    store, plan = long_plan

    def answer(stage, context, fail):
        if "fixed_question" not in context:
            return None
        ref = next(iter(context["sources"]))
        return json.dumps(
            {
                "quiz": {
                    "answer": "Synthetic answer.",
                    "explanation": "Synthetic condition.",
                    "criteria": []
                    if fail and stage == "quiz-1"
                    else ["Identify the explicit condition."],
                    "citations": [ref],
                },
                "pitfalls": [],
                "gaps": [],
            }
        )

    model(monkeypatch, lambda s, c: answer(s, c, True))
    with pytest.raises(ValueError, match="budget_exhausted_self-check-1"):
        synthesis.generate(ReadOnlyArchive(store.root), plan, fixed_assessments=True)
    original = json.loads((store.root / "wiki/last-build.json").read_bytes())["id"]
    monkeypatch.setattr(
        criteria, "local_model_digest", lambda _: "frozen-local-weights"
    )
    monkeypatch.setattr(repair, "local_model_digest", lambda _: "frozen-local-weights")
    model(monkeypatch, lambda s, c: answer(s, c, False))
    with pytest.raises(ValueError, match="Expected developed sections and limitations"):
        criteria.repair(ReadOnlyArchive(store.root), original)
    parent = json.loads((store.root / "wiki/last-build.json").read_bytes())["id"]
    article = repair.assemble_parent(store.root, parent)
    atomic_json(
        store.root / "wiki/jobs" / parent / "unpublished-candidate.json",
        {"article": article},
    )
    return store, parent, article


def response(context):
    ref = next(iter(context["sources"]))
    return {
        "action": "add",
        "pitfalls": [
            {
                "text": "Synthetic explicit applicability condition.",
                "citations": [ref],
                "witnesses": [
                    {"source": ref, "quote": context["sources"][ref]["text"][:40]}
                ],
            }
        ],
        "gaps": [],
    }


def fake_model(monkeypatch, edit=None):
    calls = []

    def chat(name, messages, schema):
        context = json.loads(messages[1]["content"])
        calls.append(context)
        value = response(context)
        if edit:
            value = edit(value, context)
        return json.dumps(value)

    monkeypatch.setattr(writer, "local_chat", chat)
    return calls


def test_one_completion_call_preserves_every_original_body_and_question(
    candidate, monkeypatch
):
    store, parent, article = candidate
    paths = list((store.root / "wiki/jobs").rglob("*.json"))
    before = {p: p.read_bytes() for p in paths}
    calls = fake_model(monkeypatch)
    prepared = repair.repair(ReadOnlyArchive(store.root), parent, 0, prepare_only=True)
    assert calls == [] and prepared["new_calls_max"] == 1
    result = repair.repair(ReadOnlyArchive(store.root), parent, 0)
    assert result["id"] == prepared["id"]
    actual = Wiki(store.root).article(result["id"])["article"]
    assert actual["pitfalls"] and len(actual["quiz"]) == 4
    assert {k: v for k, v in actual.items() if k != "pitfalls"} == {
        k: v for k, v in article.items() if k != "pitfalls"
    }
    assert all(p.read_bytes() == raw for p, raw in before.items())
    _, recipe = criteria.manifest(store.root, parent)
    assert calls[0]["sources"] == {
        r: recipe["packet"]["sources"][r] for r in recipe["packet"]["chapter_scopes"][0]
    }
    assert (
        repair.repair(ReadOnlyArchive(store.root), parent, 0)["state"]
        == "already_published"
    )
    assert len(calls) == 1


@pytest.mark.parametrize("failure", ["unavailable", "nonliteral", "crash"])
def test_terminal_result_never_repeats_call_or_switches_scope(
    candidate, monkeypatch, failure
):
    store, parent, _ = candidate

    def edit(value, context):
        if failure == "crash":
            raise KeyboardInterrupt("synthetic interruption")
        if failure == "unavailable":
            return {
                "action": "unavailable",
                "pitfalls": [],
                "gaps": ["No explicit source condition."],
            }
        value["pitfalls"][0]["witnesses"][0]["quote"] = (
            "Invented exact original quotation."
        )
        return value

    calls = fake_model(monkeypatch, edit)
    if failure == "unavailable":
        for _ in range(2):
            assert (
                repair.repair(ReadOnlyArchive(store.root), parent, 0)["state"]
                == "source_limitations_unavailable"
            )
    else:
        with pytest.raises(KeyboardInterrupt if failure == "crash" else ValueError):
            repair.repair(ReadOnlyArchive(store.root), parent, 0)
        with pytest.raises(ValueError, match="budget_exhausted_repair-limitations"):
            repair.repair(ReadOnlyArchive(store.root), parent, 0)
    with pytest.raises(ValueError, match="existing_lineage_preserved"):
        repair.repair(ReadOnlyArchive(store.root), parent, 1)
    assert len(calls) == 1
    assert not any(
        "limitations_repair"
        in criteria.manifest(store.root, row["id"])[1].get("extra", {})
        for row in Wiki(store.root).catalog()["articles"]
        if (store.root / "wiki/jobs" / row["id"] / "manifest.json").exists()
    )


@pytest.mark.parametrize("mutation", ["candidate", "body", "model"])
def test_changed_inputs_reject_before_any_inference(candidate, monkeypatch, mutation):
    store, parent, _ = candidate
    repair.repair(ReadOnlyArchive(store.root), parent, 0, prepare_only=True)
    if mutation == "model":
        monkeypatch.setattr(repair, "local_model_digest", lambda _: "changed-weights")
    elif mutation == "candidate":
        path = store.root / "wiki/jobs" / parent / "unpublished-candidate.json"
        value = json.loads(path.read_bytes())
        value["article"]["quiz"][0]["answer"] = "Changed synthetic answer."
        atomic_json(path, value)
    else:
        _, recipe = criteria.manifest(store.root, parent)
        body = recipe["extra"]["unchanged_body_job"]
        path = store.root / "wiki/jobs" / body / "chapter-0.json"
        value = json.loads(path.read_bytes())
        value["data"]["paragraphs"][0]["text"] = "Changed synthetic body."
        value["data_sha256"] = repair.digest(repair.canonical(value["data"]))
        atomic_json(path, value)
    calls = fake_model(monkeypatch)
    with pytest.raises(ValueError):
        repair.repair(ReadOnlyArchive(store.root), parent, 0)
    assert calls == []


def test_snapshot_keeps_all_generations_and_runs_without_original_sources(
    candidate, monkeypatch, tmp_path, published
):
    store, parent, _ = candidate
    retain_frames(store, published[1])
    fake_model(monkeypatch)
    result = repair.repair(ReadOnlyArchive(store.root), parent, 0)
    publication = Wiki(store.root).article(result["id"])
    dependencies = snapshot.writer_dependencies(store.root, publication)
    assert len(dependencies) == 4 and parent in {p.name for p in dependencies}
    backup = snapshot.create(store.root, tmp_path / "backup", reserve_gib=0)
    restored = snapshot.restore(backup, tmp_path / "restored", reserve_gib=0)
    assert Wiki(restored).article(result["id"]) == publication
    assert (
        restored / "wiki/limitations-repair-bindings" / (parent + ".json")
    ).is_file()
    assert "jsonschema==" in (backup / "reader-requirements.txt").read_text()
    run = subprocess.run(
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
    assert json.loads(run.stdout)["original_root_access_attempts"] == 0
    path = restored / "wiki/jobs" / parent / "unpublished-candidate.json"
    value = json.loads(path.read_bytes())
    value["article"]["gaps"].append("Changed original gap.")
    atomic_json(path, value)
    with pytest.raises(ValueError):
        snapshot.writer_dependencies(restored, publication)


def test_publication_cannot_replace_literal_preserved_questions(candidate, monkeypatch):
    store, parent, _ = candidate
    fake_model(monkeypatch)
    result = repair.repair(ReadOnlyArchive(store.root), parent, 0)
    publication = Wiki(store.root).article(result["id"])
    publication["article"]["quiz"][0]["answer"] = "Changed synthetic answer."
    with pytest.raises(ValueError, match="publication_changed"):
        snapshot.writer_dependencies(store.root, publication)


@pytest.mark.parametrize(
    "mutation",
    ["missing_witness", "outside_scope", "empty_add", "unavailable_with_claim"],
)
def test_completion_validation_rejects_unsupported_structure(mutation):
    packet = {"sources": {"a:1": {"text": "Synthetic original source condition."}}}
    value = response(packet)
    if mutation == "missing_witness":
        value["pitfalls"][0]["witnesses"] = []
    elif mutation == "outside_scope":
        value["pitfalls"][0]["citations"] = ["a:2"]
    elif mutation == "empty_add":
        value["pitfalls"] = []
    else:
        value["action"] = "unavailable"
    with pytest.raises(ValueError):
        repair.validate_result(value, packet, repair.SCHEMA)
