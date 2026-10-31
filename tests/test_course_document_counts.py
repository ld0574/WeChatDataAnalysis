import sqlite3
from types import SimpleNamespace
from contextlib import contextmanager
from pathlib import Path

import pytest

from wechat_decrypt_tool.course_document_counts import (
    install_document_counts,
    initialize_document_counts,
    verify_document_counts,
)
from wechat_decrypt_tool.course_knowledge import KnowledgeStore


def counts(db):
    return tuple(
        db.execute("SELECT active,historical FROM course_document_counts").fetchone()
    )


def test_seed_and_triggers_follow_legacy_connections_transactions_and_hidden_publication(
    tmp_path,
):
    path = tmp_path / "source.sqlite3"
    legacy = sqlite3.connect(path)
    legacy.executescript(
        "CREATE TABLE documents(id INTEGER PRIMARY KEY,active INTEGER);"
        "INSERT INTO documents VALUES(1,1),(2,0),(3,0);"
    )
    with sqlite3.connect(path) as db:
        install_document_counts(db)

    @contextmanager
    def connect():
        db = sqlite3.connect(path)
        try:
            with db:
                yield db
        finally:
            db.close()

    initialize_document_counts(SimpleNamespace(root=tmp_path, connect=connect))
    with sqlite3.connect(path) as db:
        assert counts(db) == (1, 2)
    # This writer existed before trigger installation, like a running producer.
    with legacy:
        legacy.execute("INSERT INTO documents VALUES(4,0)")
        assert counts(legacy) == (1, 3)
        legacy.execute("UPDATE documents SET active=1 WHERE id IN (3,4)")
        assert counts(legacy) == (3, 1)
    legacy.execute("SAVEPOINT abandoned_batch")
    legacy.execute("DELETE FROM documents WHERE id=1")
    legacy.execute("UPDATE documents SET active=0 WHERE id=3")
    assert counts(legacy) == (1, 2)
    legacy.execute("ROLLBACK TO abandoned_batch")
    legacy.execute("RELEASE abandoned_batch")
    assert counts(legacy) == (3, 1)
    with legacy:
        legacy.execute("UPDATE documents SET active=active")
        legacy.execute("DELETE FROM documents WHERE id=2")
    assert counts(legacy) == (3, 0)
    assert verify_document_counts(legacy)
    legacy.close()


def test_reopen_never_scans_document_rows_again_and_status_reads_only_counters(
    tmp_path, monkeypatch
):
    store = KnowledgeStore(tmp_path / "kb")
    with store.write() as db:
        db.execute(
            "INSERT INTO documents(path,locator,digest,text,search_text) VALUES('a','p1','sha','text','text')"
        )
    original_connect = store.connect

    @contextmanager
    def restricted():
        with original_connect() as db:

            def authorize(action, first, second, database, trigger):
                if action == sqlite3.SQLITE_READ and first == "documents":
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK

            db.set_authorizer(authorize)
            yield db

    monkeypatch.setattr(store, "connect", restricted)
    status = store.status()
    assert status["document_chunks"] == 1 and status["historical_document_chunks"] == 0
    with store.connect() as db:
        install_document_counts(db)
        assert counts(db) == (1, 0)


def test_snapshot_seed_merges_concurrent_writes_without_holding_writer_lease(tmp_path):
    from wechat_decrypt_tool.course_knowledge import schema_lease

    path = tmp_path / "legacy.sqlite3"
    legacy = sqlite3.connect(path)
    legacy.execute("PRAGMA journal_mode=WAL")
    legacy.executescript(
        "CREATE TABLE documents(id INTEGER PRIMARY KEY,active INTEGER);"
        "INSERT INTO documents VALUES(1,1),(2,0),(3,0);"
    )
    with legacy:
        install_document_counts(legacy)
        legacy.execute("DELETE FROM documents WHERE id=1")
    assert counts(legacy) == (-1, 0)
    invoked = []

    @contextmanager
    def connect():
        db = sqlite3.connect(path)

        def concurrent(sql):
            if "sum(active IS 1)" in sql and not invoked:
                invoked.append(True)
                with schema_lease(tmp_path / "writer.lock", timeout=0), legacy:
                    legacy.execute("INSERT INTO documents VALUES(4,1)")
                    legacy.execute("UPDATE documents SET active=1 WHERE id=2")

        db.set_trace_callback(concurrent)
        try:
            with db:
                yield db
        finally:
            db.close()

    initialize_document_counts(SimpleNamespace(root=tmp_path, connect=connect))
    assert invoked and counts(legacy) == (2, 1)
    assert verify_document_counts(legacy)
    legacy.close()


def test_uninitialized_deltas_are_never_accepted_as_snapshot_counts():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE documents(id INTEGER PRIMARY KEY,active INTEGER)")
    install_document_counts(db)
    with pytest.raises(RuntimeError, match="not finished initialization"):
        verify_document_counts(db)
    db.close()


def test_failed_migration_rolls_back_table_and_existing_document_bytes():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE documents(id INTEGER PRIMARY KEY,active INTEGER)")
    db.execute("INSERT INTO documents VALUES(17,1)")
    db.commit()

    def deny_trigger(action, *args):
        return (
            sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_CREATE_TRIGGER
            else sqlite3.SQLITE_OK
        )

    db.set_authorizer(deny_trigger)
    with pytest.raises(sqlite3.DatabaseError):
        install_document_counts(db)
    db.set_authorizer(None)
    assert not db.execute(
        "SELECT name FROM sqlite_master WHERE name='course_document_counts'"
    ).fetchall()
    assert db.execute("SELECT * FROM documents").fetchall() == [(17, 1)]
    db.close()


def test_backup_restore_and_independent_counter_tamper_rejection(tmp_path):
    from wechat_decrypt_tool.course_backup import (
        create_snapshot,
        restore_snapshot,
        verify_snapshot,
        file_hash,
    )

    store = KnowledgeStore(tmp_path / "kb")
    with store.write() as db:
        db.execute(
            "INSERT INTO documents(path,locator,digest,text,search_text) VALUES('a','p1','sha','text','text')"
        )
    result = create_snapshot(store, tmp_path / "backup", reserve_gib=0)
    assert result["document_counts_independently_verified"]
    restored = KnowledgeStore(
        restore_snapshot(result["snapshot"], tmp_path / "restored", reserve_gib=0)
    )
    assert restored.status()["document_chunks"] == 1
    snapshot = Path(result["snapshot"])
    with sqlite3.connect(snapshot / "knowledge.sqlite3") as db:
        db.execute("UPDATE course_document_counts SET active=active+1")
    # Rebind the checksum to isolate an incorrect logical counter from byte tampering.
    import json

    manifest = json.loads((snapshot / "manifest.json").read_text("utf-8"))
    manifest["database_sha256"] = file_hash(snapshot / "knowledge.sqlite3")
    (snapshot / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(RuntimeError, match="counters differ"):
        verify_snapshot(snapshot)


def test_legacy_snapshot_without_counter_table_is_explicitly_supported():
    db = sqlite3.connect(":memory:")
    assert verify_document_counts(db) is False
    db.close()
