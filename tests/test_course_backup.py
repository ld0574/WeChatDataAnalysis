import hashlib
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from wechat_decrypt_tool.course_backup import (
    create_snapshot,
    restore_snapshot,
    verify_snapshot,
)
from wechat_decrypt_tool.course_knowledge import KnowledgeStore, create_app
from wechat_decrypt_tool import course_backup as module


def test_live_wal_snapshot_restores_search_and_visual_evidence_without_source(tmp_path):
    store = KnowledgeStore(tmp_path / "working")
    video = tmp_path / "source.mp4"
    video.write_bytes(b"original media")
    key = store.register(
        {"path": str(video), "duration_seconds": 25, "title": "synthetic"}
    )
    store.save_segment(key, 0, 0, 25, "portable_search_marker")
    image = store.root / "document-visuals" / "test.png"
    image.parent.mkdir()
    image.write_bytes(b"synthetic visible evidence")
    store.save_document_versions(
        [
            {
                "path": str(tmp_path / "source.pdf"),
                "source_key": "visual page 1",
                "raw_digest": "raw",
                "extractor": "test",
                "chunks": [("visual chars 0", "portable_visual_marker")],
                "metadata": {
                    "image": "document-visuals/test.png",
                    "image_digest": hashlib.sha256(image.read_bytes()).hexdigest(),
                },
            }
        ]
    )
    checkpoint = store.checkpoint
    writes = []

    def write_during_backup():
        if not writes:
            writes.append(1)
            store.save_segment(key, 1, 25, 50, "later_change_marker")
        checkpoint()

    store.checkpoint = write_during_backup
    backup = create_snapshot(store, tmp_path / "backup", reserve_gib=0)
    assert writes and store.status()["segments"] == 2
    verify = verify_snapshot(backup["snapshot"])
    assert verify["counts"]["segments"] == 1 and verify["asset_paths"] == 1
    video.unlink()
    destination = restore_snapshot(
        backup["snapshot"], tmp_path / "restored", reserve_gib=0
    )
    restored = KnowledgeStore(destination)
    assert restored.search("portable_search_marker") and not restored.search(
        "later_change_marker"
    )
    evidence = restored.search("portable_visual_marker")[0]
    with TestClient(create_app(restored, "unused")) as client:
        document_id = -evidence["id"] - 1_000_000_000
        assert (
            client.get("/document-visual/" + str(document_id)).content
            == image.read_bytes()
        )


def test_backup_corruption_is_detected_and_restore_does_not_overwrite(tmp_path):
    store = KnowledgeStore(tmp_path / "working")
    result = create_snapshot(store, tmp_path / "backup", reserve_gib=0)
    snapshot = __import__("pathlib").Path(result["snapshot"])
    target = tmp_path / "existing"
    target.mkdir()
    (target / "keep").write_text("keep")
    with pytest.raises(ValueError, match="empty"):
        restore_snapshot(snapshot, target)
    database = snapshot / "knowledge.sqlite3"
    database.write_bytes(database.read_bytes() + b"tampering")
    with pytest.raises(RuntimeError, match="database hash changed"):
        verify_snapshot(snapshot)


def test_backup_rejects_outside_evidence_path_before_copy(tmp_path):
    store = KnowledgeStore(tmp_path / "working")
    video = tmp_path / "source.mp4"
    video.write_bytes(b"synthetic")
    key = store.register({"path": str(video), "duration_seconds": 25})
    with store.connect() as db:
        db.execute(
            "INSERT INTO frames(video,time,path) VALUES(?,?,?)",
            (key, 0, "../outside.png"),
        )
    with pytest.raises(ValueError, match="outside declared root"):
        create_snapshot(store, tmp_path / "backup", reserve_gib=0)


def test_restore_capacity_failure_happens_before_destination_or_copy(
    tmp_path, monkeypatch
):
    store = KnowledgeStore(tmp_path / "working")
    result = create_snapshot(store, tmp_path / "backup", reserve_gib=0)
    target = tmp_path / "missing" / "restored"
    monkeypatch.setattr(
        module.shutil, "disk_usage", lambda _: SimpleNamespace(free=32 * 1024**3)
    )
    monkeypatch.setattr(
        module.shutil,
        "copyfile",
        lambda *args: pytest.fail("No writes before capacity preflight"),
    )
    monkeypatch.setattr(
        module,
        "verify_snapshot",
        lambda *args: pytest.fail(
            "Reject insufficient space before rereading all backup objects"
        ),
    )
    with pytest.raises(InterruptedError, match="restore disk capacity"):
        restore_snapshot(result["snapshot"], target)
    assert not target.exists() and not target.parent.exists()


def test_restore_rechecks_reserve_between_files_without_changing_backup(
    tmp_path, monkeypatch
):
    store = KnowledgeStore(tmp_path / "working")
    store.write_json("goal-audit.json", {"test": True})
    result = create_snapshot(store, tmp_path / "backup", reserve_gib=0)
    target = tmp_path / "restored"
    availability = iter([256 * 1024**2, 256 * 1024**2, 0])
    monkeypatch.setattr(
        module.shutil, "disk_usage", lambda _: SimpleNamespace(free=next(availability))
    )
    with pytest.raises(InterruptedError, match="reserved disk capacity"):
        restore_snapshot(result["snapshot"], target, reserve_gib=0)
    assert (target / "knowledge.sqlite3").exists()
    assert not (target / "goal-audit.json").exists()
    assert verify_snapshot(result["snapshot"])["database_integrity"] == "ok"


def test_restore_checks_capacity_again_after_snapshot_verification(
    tmp_path, monkeypatch
):
    store = KnowledgeStore(tmp_path / "working")
    result = create_snapshot(store, tmp_path / "backup", reserve_gib=0)
    target = tmp_path / "restored"
    availability = iter([256 * 1024**2, 0])
    monkeypatch.setattr(
        module.shutil, "disk_usage", lambda _: SimpleNamespace(free=next(availability))
    )
    with pytest.raises(InterruptedError, match="changed during verification"):
        restore_snapshot(result["snapshot"], target, reserve_gib=0)
    assert not target.exists()


@pytest.mark.parametrize("reserve", [-1, float("nan"), float("inf")])
def test_restore_refuses_invalid_capacity_reserve_before_touching_paths(
    tmp_path, reserve
):
    with pytest.raises(ValueError, match="finite and nonnegative"):
        restore_snapshot(
            tmp_path / "absent-snapshot",
            tmp_path / "absent-target",
            reserve_gib=reserve,
        )
    assert not (tmp_path / "absent-target").exists()


def test_snapshot_validates_every_alias_metadata_even_when_object_hash_is_reused(
    tmp_path,
):
    store = KnowledgeStore(tmp_path / "working")
    store.write_json("goal-audit.json", {"test": True})
    result = create_snapshot(store, tmp_path / "backup", reserve_gib=0)
    manifest_path = __import__("pathlib").Path(result["snapshot"]) / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    original = manifest["files"][0]
    manifest["files"].append(
        {**original, "path": "other-audit.json", "bytes": original["bytes"] + 1}
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(RuntimeError, match="evidence object hash changed"):
        verify_snapshot(result["snapshot"])
