import json
from pathlib import Path

import pytest
from PIL import Image

from wechat_decrypt_tool import course_lesson_exports as exports
from wechat_decrypt_tool.course_backup import (
    create_snapshot,
    file_hash,
    restore_snapshot,
    verify_snapshot,
)
from wechat_decrypt_tool.course_knowledge import KnowledgeStore, atomic_json
from wechat_decrypt_tool.course_video_visual_audit import ReadOnlyArchive


@pytest.fixture
def completed(tmp_path):
    store = KnowledgeStore(tmp_path / "working")
    source = tmp_path / "original.mp4"
    source.write_bytes(b"synthetic original")
    item = {
        "path": str(source),
        "duration_seconds": 25,
        "bytes": source.stat().st_size,
        "mtime_ns": source.stat().st_mtime_ns,
        "audio_streams": [{"codec": "synthetic"}],
    }
    key = store.register(item)
    store.save_segment(key, 0, 0, 25, "frozen_original_segment")
    folder = store.root / "lessons" / key
    image = folder / "frames" / "0.jpg"
    image.parent.mkdir(parents=True)
    Image.new("RGB", (64, 64), "white").save(image)
    with store.write() as db:
        db.execute(
            "UPDATE videos SET status='transcribed',model=?,decoded_seconds=25 WHERE id=?",
            (exports.ASR_MODEL, key),
        )
        db.execute(
            "INSERT INTO frames(video,time,path,status,model) VALUES(?,?,?,?,?)",
            (key, 0, image.relative_to(store.root).as_posix(), "ready", exports.MODEL),
        )
        evidence = db.execute(
            "SELECT id FROM segments WHERE video=?", (key,)
        ).fetchone()[0]
        note = {
            "summary": "frozen summary",
            "concepts": [
                {
                    "name": "frozen concept",
                    "explanation": "frozen explanation",
                    "evidence": [evidence],
                }
            ],
            "relations": [],
        }
        db.execute(
            "INSERT INTO notes VALUES(?,?,?,?,?)",
            (key, 0, json.dumps(note), exports.MODEL, "ready"),
        )
    atomic_json(
        folder / "frames-completed.json",
        {"step": 30, "complete_visual_coverage": False},
    )
    atomic_json(
        store.root / "inventory-unique.json",
        {"videos": [item], "unique_readable_videos": 1},
    )
    store.export(key)
    return store, key, source


def test_restored_exports_match_frozen_rows_without_original_or_db_rewrite(
    completed, tmp_path
):
    store, key, source = completed
    snapshot = create_snapshot(store, tmp_path / "backup", reserve_gib=0)
    assert snapshot["consistent_lesson_exports"] == 1
    captured = Path(snapshot["snapshot"])
    manifest = json.loads((captured / "manifest.json").read_text("utf-8"))
    scope = manifest["lesson_exports"]
    assert scope["all_registered_audio_exports_included"]
    assert not scope["full_inventory_completion_claimed"] and not scope["deferred"]
    # Later live exports must not leak into an already completed snapshot.
    with store.write() as db:
        db.execute(
            "UPDATE frames SET description='later_live_frame' WHERE video=?", (key,)
        )
    store.export(key)
    source.unlink()
    restored = restore_snapshot(captured, tmp_path / "restored", reserve_gib=0)
    before = file_hash(restored / "knowledge.sqlite3")
    proof = exports.validate_exports(ReadOnlyArchive(restored))
    assert proof["accepted"] and proof["full_audio_inventory_exports_consistent"]
    assert file_hash(restored / "knowledge.sqlite3") == before
    payload = json.loads(
        (restored / "lessons" / key / "evidence.json").read_text("utf-8")
    )
    assert payload["segments"][0]["text"] == "frozen_original_segment"
    assert payload["frames"][0]["description"] == ""


def test_live_exports_ahead_of_snapshot_are_explicitly_deferred(completed, tmp_path):
    store, key, _ = completed
    original = store.checkpoint
    writes = []

    def write_after_read_transaction_started():
        if not writes:
            writes.append(1)
            with store.write() as db:
                db.execute(
                    "UPDATE frames SET description='later_live_frame' WHERE video=?",
                    (key,),
                )
            store.export(key)
        original()

    store.checkpoint = write_after_read_transaction_started
    snapshot = create_snapshot(store, tmp_path / "backup", reserve_gib=0)
    manifest = json.loads(
        (Path(snapshot["snapshot"]) / "manifest.json").read_text("utf-8")
    )
    scope = manifest["lesson_exports"]
    assert (
        writes
        and not scope["included"]
        and not scope["all_registered_audio_exports_included"]
    )
    assert scope["deferred"] == [{"video": key, "code": "export_frames_mismatch"}]
    assert snapshot["consistent_lesson_exports"] == 0


def test_file_mutation_after_selection_rejects_backup(completed, tmp_path, monkeypatch):
    store, key, _ = completed
    original = exports.snapshot_export_assets

    def raced(*args, **kwargs):
        result = original(*args, **kwargs)
        assert result[0]
        (store.root / "lessons" / key / "graph.json").write_text("{}", "utf-8")
        return result

    monkeypatch.setattr(exports, "snapshot_export_assets", raced)
    with pytest.raises(
        RuntimeError, match="Backup copy does not match committed evidence hash"
    ):
        create_snapshot(store, tmp_path / "backup", reserve_gib=0)


def test_capture_marker_published_after_db_snapshot_is_not_accepted(
    completed, tmp_path
):
    store, key, _ = completed
    marker = store.root / "lessons" / key / "frames-completed.json"
    contents = marker.read_bytes()
    marker.unlink()
    original = store.checkpoint
    writes = []

    def publish_after_transaction_started():
        if not writes:
            writes.append(1)
            marker.write_bytes(contents)
        original()

    store.checkpoint = publish_after_transaction_started
    snapshot = create_snapshot(store, tmp_path / "backup", reserve_gib=0)
    manifest = json.loads(
        (Path(snapshot["snapshot"]) / "manifest.json").read_text("utf-8")
    )
    assert not manifest["lesson_exports"]["included"]
    assert manifest["lesson_exports"]["deferred"] == [
        {"video": key, "code": "capture_checkpoint_newer_than_snapshot"}
    ]


def test_missing_declared_export_rejected_during_verification(completed, tmp_path):
    store, key, _ = completed
    snapshot = create_snapshot(store, tmp_path / "backup", reserve_gib=0)
    path = Path(snapshot["snapshot"]) / "manifest.json"
    manifest = json.loads(path.read_text("utf-8"))
    manifest["files"] = [
        r for r in manifest["files"] if r["path"] != f"lessons/{key}/graph.json"
    ]
    atomic_json(path, manifest)
    with pytest.raises(RuntimeError, match="missing validated lesson exports"):
        verify_snapshot(snapshot["snapshot"])


@pytest.mark.parametrize("case", ["count", "partition", "flag", "full_inventory"])
def test_export_scope_cannot_overstate_captured_rows(completed, tmp_path, case):
    store, _, _ = completed
    snapshot = create_snapshot(store, tmp_path / "backup", reserve_gib=0)
    path = Path(snapshot["snapshot"]) / "manifest.json"
    manifest = json.loads(path.read_text("utf-8"))
    scope = manifest["lesson_exports"]
    if case == "count":
        scope["requested_registered_audio_lessons"] = 340
    elif case == "partition":
        scope["included"].clear()
    elif case == "flag":
        scope["all_registered_audio_exports_included"] = False
    else:
        scope["full_inventory_completion_claimed"] = True
    atomic_json(path, manifest)
    with pytest.raises(RuntimeError, match="export scope differs from frozen database"):
        verify_snapshot(snapshot["snapshot"])
