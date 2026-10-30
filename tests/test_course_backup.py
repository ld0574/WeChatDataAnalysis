import hashlib

import pytest
from fastapi.testclient import TestClient

from wechat_decrypt_tool.course_backup import (
    create_snapshot,
    restore_snapshot,
    verify_snapshot,
)
from wechat_decrypt_tool.course_knowledge import KnowledgeStore, create_app


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
    backup = create_snapshot(store, tmp_path / "backup", reserve_gib=0)
    store.save_segment(key, 1, 25, 50, "later_change_marker")
    verify = verify_snapshot(backup["snapshot"])
    assert verify["counts"]["segments"] == 1 and verify["asset_paths"] == 1
    video.unlink()
    destination = restore_snapshot(backup["snapshot"], tmp_path / "restored")
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
