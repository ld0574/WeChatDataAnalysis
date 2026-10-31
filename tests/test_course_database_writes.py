import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest

from wechat_decrypt_tool.course_knowledge import KnowledgeStore


def setup(tmp_path):
    store = KnowledgeStore(tmp_path / "knowledge")
    source = tmp_path / "source.mp4"
    source.write_bytes(b"synthetic media")
    video = store.register({"path": str(source), "duration_seconds": 1000})
    return store, video


def test_legacy_writer_contention_retries_before_committing_segment_once(
    tmp_path, monkeypatch
):
    store, video = setup(tmp_path)
    contended = threading.Event()
    attempts = []

    class ObservedConnection(sqlite3.Connection):
        def execute(self, sql, *args):
            if sql == "BEGIN IMMEDIATE":
                attempts.append(1)
            try:
                return super().execute(sql, *args)
            except sqlite3.OperationalError:
                if sql == "BEGIN IMMEDIATE":
                    contended.set()
                raise

    @contextmanager
    def short_wait():
        connection = sqlite3.connect(store.path, timeout=0.01, factory=ObservedConnection)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    monkeypatch.setattr(store, "connect", short_wait)
    legacy = sqlite3.connect(store.path)
    legacy.execute("BEGIN IMMEDIATE")
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(store.save_segment, video, 0, 0, 25, "contention_marker")
        assert contended.wait(2)
        legacy.commit()
        legacy.close()
        result.result(timeout=5)
    assert len(attempts) >= 2
    assert len(store.search("contention_marker")) == 1
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM segments").fetchone()[0] == 1


def test_write_body_errors_roll_back_without_replaying_and_release_lock(tmp_path):
    store, _ = setup(tmp_path)
    calls = []
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        with store.write() as db:
            calls.append(1)
            db.execute("INSERT INTO events VALUES(0,'must_roll_back','{}')")
            db.execute("INSERT INTO missing_table VALUES(1)")
    assert len(calls) == 1
    with store.write() as db:
        assert not db.execute(
            "SELECT 1 FROM events WHERE kind='must_roll_back'"
        ).fetchone()
        db.execute("INSERT INTO events VALUES(0,'after_failure','{}')")
    with store.connect() as db:
        assert (
            db.execute(
                "SELECT count(*) FROM events WHERE kind='after_failure'"
            ).fetchone()[0]
            == 1
        )


def test_bulk_documents_and_frame_speech_writers_keep_every_search_record(tmp_path):
    store, video = setup(tmp_path)
    barrier = threading.Barrier(2)

    def documents():
        barrier.wait()
        for batch in range(10):
            store.save_document_versions(
                [
                    {
                        "path": str(tmp_path / f"document-{batch}-{n}.txt"),
                        "source_key": "",
                        "raw_digest": f"synthetic-{batch}-{n}",
                        "extractor": "test",
                        "chunks": [("text chars 0", "concurrent_document_marker")],
                        "metadata": {},
                    }
                    for n in range(10)
                ]
            )

    def speech_and_frames():
        barrier.wait()
        for n in range(40):
            store.save_segment(
                video, n, n * 25, (n + 1) * 25, "concurrent_speech_marker"
            )
            with store.write() as db:
                db.execute(
                    "INSERT INTO frames(video,time,path,description,search_text,status,model) VALUES(?,?,?,?,?,'ready','test')",
                    (
                        video,
                        n * 25,
                        f"synthetic-{n}.png",
                        "concurrent_frame_marker",
                        "concurrent_frame_marker",
                    ),
                )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(documents), pool.submit(speech_and_frames)]
        for future in futures:
            future.result(timeout=30)
    assert len(store.search("concurrent_document_marker", limit=200)) == 100
    assert len(store.search("concurrent_speech_marker", limit=100)) == 40
    assert len(store.search("concurrent_frame_marker", limit=100)) == 40
    with store.connect() as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
