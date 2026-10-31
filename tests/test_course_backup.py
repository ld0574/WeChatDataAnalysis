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


@pytest.mark.parametrize(
    "case", ["valid", "frame", "missing", "other_lesson", "boolean", "edge", "orphan"]
)
def test_snapshot_validates_ready_graph_citations_within_their_lesson(tmp_path, case):
    store = KnowledgeStore(tmp_path / "working")
    keys = []
    for name in ("first", "second"):
        source = tmp_path / (name + ".mp4")
        source.write_bytes(name.encode())
        key = store.register({"path": str(source), "duration_seconds": 25})
        store.save_segment(key, 0, 0, 25, "synthetic evidence")
        keys.append(key)
    with store.connect() as db:
        identifiers = {
            video: identifier
            for identifier, video in db.execute("SELECT id,video FROM segments")
        }
    citation = {
        "valid": identifiers[keys[0]],
        "missing": 999999,
        "other_lesson": identifiers[keys[1]],
        "boolean": True,
        "edge": identifiers[keys[0]],
        "frame": identifiers[keys[0]],
        "orphan": identifiers[keys[0]],
    }[case]
    if case == "frame":
        image = store.root / "lessons" / keys[0] / "frames" / "synthetic.png"
        image.parent.mkdir(parents=True)
        image.write_bytes(b"synthetic frame evidence")
        with store.write() as db:
            citation = -db.execute(
                "INSERT INTO frames(video,time,path) VALUES(?,?,?)",
                (keys[0], 0, image.relative_to(store.root).as_posix()),
            ).lastrowid
    note = {
        "summary": "synthetic",
        "concepts": [
            {"name": "alpha", "explanation": "synthetic", "evidence": [citation]}
        ],
        "relations": [],
        "questions": [],
    }
    if case == "edge":
        note["relations"] = [
            {
                "from": "alpha",
                "to": "absent",
                "relation": "synthetic",
                "evidence": [citation],
            }
        ]
    if case == "orphan":
        note["concepts"] = []
    with store.write() as db:
        db.execute(
            "INSERT INTO notes VALUES(?,?,?,?,?)",
            (
                "missing-video" if case == "orphan" else keys[0],
                0,
                json.dumps(note),
                "synthetic",
                "ready",
            ),
        )
    if case in ("valid", "frame"):
        result = create_snapshot(store, tmp_path / "backup", reserve_gib=0)
        assert result["ready_note_evidence_validated"] == 1
        assert verify_snapshot(result["snapshot"])["ready_note_evidence_validated"] == 1
        with store.write() as db:
            db.execute("DELETE FROM notes")
        if case == "frame":
            image.unlink()
        destination = restore_snapshot(
            result["snapshot"], tmp_path / "restored", reserve_gib=0
        )
        with KnowledgeStore(destination).connect() as db:
            restored = json.loads(db.execute("SELECT data FROM notes").fetchone()[0])
            assert restored == note
            assert module._validate_note_evidence(db) == 1
        if case == "frame":
            assert (
                destination / image.relative_to(store.root)
            ).read_bytes() == b"synthetic frame evidence"
    else:
        with pytest.raises(RuntimeError, match="note evidence"):
            create_snapshot(store, tmp_path / "backup", reserve_gib=0)


@pytest.mark.parametrize("fail_verification", [False, True])
def test_snapshot_connections_close_without_waiting_for_garbage_collection(
    tmp_path, monkeypatch, fail_verification
):
    store = KnowledgeStore(tmp_path / "working")
    connect = module.sqlite3.connect
    connections = []

    def track(*args, **kwargs):
        connection = connect(*args, **kwargs)
        # Keep strong references so a leaked connection cannot pass through GC.
        connections.append(connection)
        return connection

    monkeypatch.setattr(module.sqlite3, "connect", track)
    result = create_snapshot(store, tmp_path / "backup", reserve_gib=0)
    snapshot = __import__("pathlib").Path(result["snapshot"])
    if fail_verification:
        manifest_path = snapshot / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["counts"]["segments"] += 1
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with pytest.raises(RuntimeError, match="row counts differ"):
            verify_snapshot(snapshot)
    else:
        assert verify_snapshot(snapshot)["database_integrity"] == "ok"
    assert connections
    for connection in connections:
        with pytest.raises(module.sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")


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


def test_lesson_export_scope_receipts_survive_independent_restore(tmp_path):
    store = KnowledgeStore(tmp_path / "working")
    receipts = {
        "lesson-export-validation.json": {
            "scope": "full_canonical_audio_inventory",
            "accepted": False,
        },
        "lesson-export-subset-validation.json": {
            "scope": "explicit_lesson_subset",
            "accepted": True,
        },
    }
    for name, value in receipts.items():
        store.write_json(name, value)
    snapshot = create_snapshot(store, tmp_path / "backup", reserve_gib=0)
    restored = restore_snapshot(
        snapshot["snapshot"], tmp_path / "restored", reserve_gib=0
    )
    for name in receipts:
        assert (restored / name).read_bytes() == (store.root / name).read_bytes()
