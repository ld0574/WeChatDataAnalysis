import copy
import json

import pytest

from test_course_wiki_evidence import (
    archive as source_archive,
    long_evidence as source_long_evidence,
    published as source_published,
    retain_lesson,
)
from wechat_decrypt_tool import course_wiki_batch as batch
from wechat_decrypt_tool import course_wiki_batch_refresh as refresh
from wechat_decrypt_tool.course_knowledge import atomic_json
from wechat_decrypt_tool.course_library import Library, canonical
from wechat_decrypt_tool.course_lesson_exports import digest
from wechat_decrypt_tool.course_video_visual_audit import ReadOnlyArchive

archive = source_archive
published = source_published
long_evidence = source_long_evidence


@pytest.fixture
def reserved(long_evidence, monkeypatch):
    store, lesson, partition = long_evidence
    monkeypatch.setattr(batch, "local_model_digest", lambda _: "synthetic-model")
    monkeypatch.setattr(refresh, "local_model_digest", lambda _: "synthetic-model")
    reader = ReadOnlyArchive(store.root)
    old = batch.prepare_allocations(reader, [partition["partition"]])
    return reader, lesson, old["batch"]


def change_lesson(reader, lesson, field="rows_sha256"):
    updated = copy.deepcopy(lesson)
    if field in refresh.PROVENANCE:
        updated[field] = "a" * 64 if updated.get(field) != "a" * 64 else "b" * 64
    elif field == "text":
        next(iter(updated["evidence"].values()))["text"] += " changed source"
    elif field == "image":
        next(e for e in updated["evidence"].values() if e["kind"] == "frame")[
            "image_sha256"
        ] = "f" * 64
    else:
        updated[field] = "changed"
    retain_lesson(reader.root, updated)
    return updated


def test_refresh_retains_all_scopes_exact_originals_and_single_successor(reserved):
    reader, lesson, key = reserved
    source_raw = (reader.root / f"wiki/theme-batches/{key}/manifest.json").read_bytes()
    old = batch.manifest(reader.root, key)[1]
    change_lesson(reader, lesson)
    result = refresh.refresh(reader, key)
    assert result["scopes"] == len(old["courses"]) > 1
    assert result["batch"] != key and result["model_calls"] == 0
    assert (
        reader.root / f"wiki/theme-batches/{key}/manifest.json"
    ).read_bytes() == source_raw
    target = batch.manifest(reader.root, result["batch"])[1]
    assert [r["allocation_scope"] for r in target["courses"]] == [
        r["allocation_scope"] for r in old["courses"]
    ]
    pointer = json.loads(
        (reader.root / f"wiki/theme-batch-refreshes/{key}.json").read_bytes()
    )
    refresh.validate_binding(reader.root, old, pointer)
    assert refresh.refresh(reader, key) == result
    assert not list(reader.root.glob("wiki/planning-jobs/*/manifest.json"))
    with pytest.raises(ValueError, match="superseded"):
        batch.run(reader, key)


@pytest.mark.parametrize("field", ["text", "image", "sections", "audio_kind", "title"])
def test_substantive_change_is_never_rebound(reserved, field):
    reader, lesson, key = reserved
    change_lesson(reader, lesson, field)
    with pytest.raises(ValueError, match="substantive_source_change"):
        refresh.refresh(reader, key)
    assert not list(reader.root.glob("wiki/theme-batch-refreshes/*.json"))


@pytest.mark.parametrize("kind", ["progress", "pointer", "abandoned_job"])
def test_any_prior_start_or_reserved_attempt_blocks_refresh(reserved, kind):
    reader, lesson, key = reserved
    old = batch.manifest(reader.root, key)[1]
    alloc = old["courses"][0]["allocation"]
    if kind == "progress":
        path = reader.root / f"wiki/theme-batches/{key}/progress.json"
    elif kind == "pointer":
        path = reader.root / "wiki/theme-planning-sources/synthetic.json"
    else:
        path = reader.root / ("wiki/planning-jobs/" + "f" * 32 + "/manifest.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(path, {"allocation": alloc})
    change_lesson(reader, lesson)
    with pytest.raises(ValueError, match="started_batch|attempted_allocation"):
        refresh.refresh(reader, key)


def test_model_change_rejected_and_unchanged_lesson_gets_no_new_batch(
    reserved, monkeypatch
):
    reader, lesson, key = reserved
    assert refresh.refresh(reader, key)["state"] == "no_provenance_change"
    change_lesson(reader, lesson)
    monkeypatch.setattr(refresh, "local_model_digest", lambda _: "changed-model")
    with pytest.raises(ValueError, match="model_changed"):
        refresh.refresh(reader, key)


def test_crash_after_binding_resumes_only_same_target_even_after_target_started(
    reserved, monkeypatch
):
    reader, lesson, key = reserved
    change_lesson(reader, lesson)
    original = refresh.atomic_json

    def interrupted(path, value):
        if path.name == "manifest.json" and path.parent.parent.name == "theme-batches":
            raise OSError("Synthetic target publication interruption")
        original(path, value)

    monkeypatch.setattr(refresh, "atomic_json", interrupted)
    with pytest.raises(OSError):
        refresh.refresh(reader, key)
    pointer = reader.root / f"wiki/theme-batch-refreshes/{key}.json"
    before = pointer.read_bytes()
    monkeypatch.setattr(refresh, "atomic_json", original)
    result = refresh.refresh(reader, key)
    atomic_json(
        reader.root / f"wiki/theme-batches/{result['batch']}/progress.json",
        {"state": "running"},
    )
    # Later provenance changes cannot grant a second refreshed batch/budget.
    change_lesson(reader, Library(reader.root).lesson(lesson["id"]))
    assert refresh.refresh(reader, key) == result
    assert pointer.read_bytes() == before
    with pytest.raises(ValueError, match="started_batch"):
        refresh.refresh(reader, result["batch"])


def test_changed_binding_cannot_drop_scopes_or_change_images(reserved):
    reader, lesson, key = reserved
    change_lesson(reader, lesson)
    refresh.refresh(reader, key)
    path = reader.root / f"wiki/theme-batch-refreshes/{key}.json"
    value = json.loads(path.read_bytes())
    value["target_recipe"]["courses"].pop()
    value["target_batch"] = digest(canonical(value["target_recipe"]))[:32]
    atomic_json(path, value)
    with pytest.raises(ValueError, match="recipe_changed"):
        refresh.refresh(reader, key)


def test_pause_preserves_flags_and_does_not_create_successor(reserved):
    reader, lesson, key = reserved
    change_lesson(reader, lesson)
    pause = reader.root / "pause.flag"
    pause.write_text("user_pause", encoding="utf-8")
    with pytest.raises(InterruptedError):
        refresh.refresh(reader, key)
    assert pause.read_text("utf-8") == "user_pause"
    assert not list(reader.root.glob("wiki/theme-batch-refreshes/*.json"))


def test_refresh_shares_batch_execution_lock(reserved):
    reader, lesson, key = reserved
    change_lesson(reader, lesson)
    from wechat_decrypt_tool.course_knowledge import schema_lease

    with schema_lease(reader.root / "wiki-theme-batch.lock", timeout=0):
        with pytest.raises(RuntimeError, match="still holds"):
            refresh.refresh(reader, key)
    assert not list(reader.root.glob("wiki/theme-batch-refreshes/*.json"))


def test_saved_target_cannot_be_overwritten_on_resume(reserved):
    reader, lesson, key = reserved
    change_lesson(reader, lesson)
    result = refresh.refresh(reader, key)
    path = reader.root / f"wiki/theme-batches/{result['batch']}/manifest.json"
    raw = path.read_bytes()
    value = json.loads(raw)
    value["recipe"]["model"] = "changed-model"
    atomic_json(path, value)
    with pytest.raises(ValueError, match="reserved_target_changed"):
        refresh.refresh(reader, key)
    assert path.read_bytes() != raw


def test_attempted_current_version_cannot_receive_a_refreshed_reservation(reserved):
    reader, lesson, key = reserved
    change_lesson(reader, lesson)
    old = batch.manifest(reader.root, key)[1]
    original, _, _ = refresh.old_allocation(
        reader.root, old["courses"][0]["allocation"]
    )
    partition = refresh.evidence.create(reader, original["video"], original["section"])
    current = refresh.allocation.create(reader, partition["partition"])
    path = reader.root / "wiki/planning-jobs" / ("a" * 32) / "manifest.json"
    path.parent.mkdir(parents=True)
    atomic_json(path, {"allocation": current["allocation"]})
    with pytest.raises(ValueError, match="attempted_allocation"):
        refresh.refresh(reader, key)
    assert not list(reader.root.glob("wiki/theme-batch-refreshes/*.json"))
