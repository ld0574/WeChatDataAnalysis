import json
import subprocess
import sys
from pathlib import Path

import pytest

from test_course_wiki_limitations_repair import (
    archive as source_archive,
    candidate as source_candidate,
    courseware as source_courseware,
    fake_model,
    long_plan as source_long_plan,
    published as source_published,
)
from test_course_wiki_snapshot_writers import retain_frames
from wechat_decrypt_tool import course_wiki_limitations_repair as limits
from wechat_decrypt_tool import course_wiki_limitations_subset as subset
from wechat_decrypt_tool import course_wiki_snapshot as snapshot
from wechat_decrypt_tool import course_wiki_writer as writer
from wechat_decrypt_tool.course_knowledge import atomic_json
from wechat_decrypt_tool.course_video_visual_audit import ReadOnlyArchive
from wechat_decrypt_tool.course_wiki import Wiki

archive = source_archive
candidate = source_candidate
courseware = source_courseware
long_plan = source_long_plan
published = source_published


@pytest.fixture
def failed_call(candidate, monkeypatch):
    store, parent, article = candidate

    def edit(value, context):
        bad = json.loads(json.dumps(value["pitfalls"][0]))
        bad["witnesses"][0]["quote"] += " Unsupported suffix."
        value["pitfalls"].insert(0, bad)
        return value

    calls = fake_model(monkeypatch, edit)
    with pytest.raises(ValueError, match="budget_exhausted_repair-limitations"):
        limits.repair(ReadOnlyArchive(store.root), parent, 0)
    failed = json.loads((store.root / "wiki/last-build.json").read_bytes())["id"]
    assert len(calls) == 1
    monkeypatch.setattr(
        writer,
        "local_chat",
        lambda *a, **k: pytest.fail("No additional model call allowed"),
    )
    return store, failed, article


def test_selects_only_whole_valid_claims_preserving_every_parent_byte(failed_call):
    store, failed, article = failed_call
    before = {p: p.read_bytes() for p in (store.root / "wiki/jobs").rglob("*.json")}
    prepared = subset.repair(ReadOnlyArchive(store.root), failed, prepare_only=True)
    assert (
        prepared["additional_model_calls"] == 0
        and prepared["selected_whole_claims"] == 1
    )
    result = subset.repair(ReadOnlyArchive(store.root), failed)
    actual = Wiki(store.root).article(result["id"])
    assert actual["id"] == prepared["id"]
    assert len(actual["article"]["pitfalls"]) == 1
    assert {k: v for k, v in actual["article"].items() if k != "pitfalls"} == {
        k: v for k, v in article.items() if k != "pitfalls"
    }
    assert all(p.read_bytes() == raw for p, raw in before.items())
    spec, _, selected, _ = subset.selection(store.root, failed)
    raw = json.loads(
        json.loads(
            (
                store.root
                / "wiki/jobs"
                / failed
                / "attempts/repair-limitations-01.json"
            ).read_bytes()
        )["raw_response"]
    )
    assert selected["pitfalls"] == [raw["pitfalls"][1]]
    assert spec["selected_whole_claim_indices"] == [1] and spec[
        "rejected_whole_claim_indices"
    ] == [0]
    assert (
        subset.repair(ReadOnlyArchive(store.root), failed)["state"]
        == "already_published"
    )
    _, parent_recipe = limits.criteria.manifest(store.root, failed)
    original_parent = parent_recipe["extra"]["unchanged_assessment_job"]
    with pytest.raises(ValueError, match="budget_exhausted_repair-limitations"):
        limits.repair(ReadOnlyArchive(store.root), original_parent, 0)


@pytest.mark.parametrize(
    "mutation", ["request", "all_invalid", "truncated", "schema", "hidden_duplicate"]
)
def test_refuses_changed_requests_and_other_failure_classes(failed_call, mutation):
    store, failed, _ = failed_call
    path = store.root / "wiki/jobs" / failed / "attempts/repair-limitations-01.json"
    value = json.loads(path.read_bytes())
    if mutation == "request":
        value["request_sha256"] = "0" * 64
    elif mutation == "truncated":
        value["error_type"] = "ModelOutputTruncatedError"
    else:
        raw = json.loads(value["raw_response"])
        if mutation == "all_invalid":
            raw["pitfalls"][1]["witnesses"][0]["quote"] += " Unsupported suffix."
        elif mutation == "schema":
            raw["pitfalls"][0]["citations"] = ["outside:1"]
        else:
            raw["pitfalls"][1]["citations"] *= 2
        value["raw_response"] = json.dumps(raw)
    atomic_json(path, value)
    with pytest.raises(ValueError):
        subset.repair(ReadOnlyArchive(store.root), failed)


def test_snapshot_retains_failed_call_and_all_five_generations(
    failed_call, published, monkeypatch, tmp_path
):
    store, failed, _ = failed_call
    retain_frames(store, published[1])
    result = subset.repair(ReadOnlyArchive(store.root), failed)
    publication = Wiki(store.root).article(result["id"])
    dependencies = snapshot.writer_dependencies(store.root, publication)
    assert len(dependencies) == 5 and failed in {p.name for p in dependencies}
    backup = snapshot.create(store.root, tmp_path / "backup", reserve_gib=0)
    restored = snapshot.restore(backup, tmp_path / "restored", reserve_gib=0)
    assert Wiki(restored).article(result["id"]) == publication
    assert (restored / "wiki/limitations-subset-bindings" / (failed + ".json")).exists()
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
    (restored / "wiki/jobs" / failed / "attempts/repair-limitations-01.json").unlink()
    with pytest.raises(ValueError):
        snapshot.writer_dependencies(restored, publication)


def test_snapshot_rejects_modification_of_preserved_question(failed_call):
    store, failed, _ = failed_call
    result = subset.repair(ReadOnlyArchive(store.root), failed)
    publication = Wiki(store.root).article(result["id"])
    publication["article"]["quiz"][0]["answer"] = "Changed synthetic answer."
    with pytest.raises(ValueError, match="publication_changed"):
        snapshot.writer_dependencies(store.root, publication)
