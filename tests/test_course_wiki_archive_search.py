"""Retained courseware search reaches unpublished sources without writing SQLite."""

import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from wechat_decrypt_tool.course_library import create_app
from wechat_decrypt_tool.course_wiki_archive_search import (
    ArchiveSearchUnavailable,
    connection,
    prepare_index,
)


def test_failed_seed_can_resume_with_new_writes(tmp_path):
    path, client = fixture(tmp_path)

    def interrupt(stage, **extra):
        if stage == "current_id_snapshot_seeded":
            raise RuntimeError("synthetic interruption")

    with pytest.raises(RuntimeError, match="synthetic interruption"):
        prepare_index(tmp_path, progress=interrupt)
    assert (
        client.get(
            "/wiki/api/courseware/search", params={"q": "SyntheticKey"}
        ).status_code
        == 503
    )
    with sqlite3.connect(path) as db:
        db.execute("UPDATE documents SET active=0 WHERE id=1")
        db.execute("UPDATE documents SET active=1 WHERE id=2")
    prepare_index(tmp_path)
    with sqlite3.connect(path) as db:
        actual = db.execute(
            "SELECT id FROM courseware_current_documents ORDER BY id"
        ).fetchall()
        expected = db.execute(
            "SELECT id FROM documents WHERE active=1 ORDER BY id"
        ).fetchall()
        assert actual == expected


def fixture(tmp_path, count=48):
    path = tmp_path / "knowledge.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE documents(id INTEGER PRIMARY KEY,path,locator,digest,text,
                search_text,active,source_key,extractor,raw_digest);
            CREATE VIRTUAL TABLE document_fts USING fts5(search_text,
                content='documents',content_rowid='id');
            CREATE TABLE resource_revisions(path,source_key,raw_digest,extractor,
                revision,metadata,completed);
            CREATE TABLE document_version_metadata(path,source_key,revision,metadata);
            CREATE INDEX document_source ON documents(path,source_key,active);
        """)
        raw = "a" * 64
        revision = hashlib.sha256((raw + "\0synthetic-v1\0").encode()).hexdigest()
        for number in range(1, count + 1):
            text = "SyntheticKey \n保留\n" + (
                "<script>literal</script>" * (800 if number == 1 else 1)
            )
            values = (
                number,
                f"source-{number}.txt",
                f"chunk {number}",
                revision,
                text,
                "synthetickey 保 留",
                0 if number == 2 else 1,
                "",
                "synthetic-v1",
                raw,
            )
            db.execute("INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?)", values)
            db.execute(
                "INSERT INTO document_fts(rowid,search_text) VALUES(?,?)",
                (number, values[5]),
            )
            db.execute(
                "INSERT INTO resource_revisions VALUES(?,?,?,?,?,?,?)",
                (values[1], "", raw, "synthetic-v1", revision, json.dumps({}), 1),
            )
    return path, TestClient(create_app(SimpleNamespace(root=tmp_path)))


def test_readonly_paging_reaches_all_active_unpublished_sources(tmp_path):
    path, client = fixture(tmp_path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    after, ids = 0, []
    while after is not None:
        result = client.get(
            "/wiki/api/courseware/search",
            params={"q": "SyntheticKey 保留", "after": after},
        ).json()
        assert result["whole_source_coverage_verified"] is False
        ids.extend(row["id"] for row in result["items"])
        after = result["next_after"]
    assert ids == [number for number in range(1, 49) if number != 2]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert not (tmp_path / "library").exists()


def test_complete_text_pages_and_pinned_version_detect_mutation(tmp_path):
    path, client = fixture(tmp_path)
    hit = client.get(
        "/wiki/api/courseware/search", params={"q": "SyntheticKey"}
    ).json()["items"][0]
    params = {key: hit[key] for key in ("revision", "text_sha256")}
    offset, parts = 0, []
    while offset is not None:
        page = client.get(
            f"/wiki/api/courseware/document/{hit['id']}",
            params={**params, "offset": offset},
        ).json()
        assert page["revision_chain_verified"]
        assert page["original_binary_rehashed_on_read"] is False
        assert len(page["text"]) <= 8000
        parts.append(page["text"])
        offset = page["next_offset"]
    with sqlite3.connect(path) as db:
        assert (
            "".join(parts)
            == db.execute("SELECT text FROM documents WHERE id=1").fetchone()[0]
        )
        db.execute("UPDATE documents SET text='changed' WHERE id=1")
    assert (
        client.get("/wiki/api/courseware/document/1", params=params).status_code == 409
    )
    assert (
        client.get("/wiki/api/courseware/document/../x", params=params).status_code
        == 404
    )


def test_snapshot_limits_and_bounded_query_errors_are_explicit(tmp_path):
    _, client = fixture(tmp_path)
    for query in ("x" * 201, " ".join(f"word{n}" for n in range(33))):
        assert (
            client.get("/wiki/api/courseware/search", params={"q": query}).status_code
            == 400
        )
    assert (
        client.get(
            "/wiki/api/courseware/search", params={"q": '" OR * NOT --'}
        ).status_code
        == 200
    )
    offline = TestClient(
        create_app(SimpleNamespace(root=tmp_path, snapshot_info={"id": "snapshot"}))
    )
    response = offline.get("/wiki/api/courseware/search")
    assert response.status_code == 503 and "快照" in response.json()["detail"]


def test_oversized_matches_not_silently_dropped(tmp_path):
    path, client = fixture(tmp_path, 1)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE documents SET text=? WHERE id=1", ("SyntheticKey " * 20000,))
    result = client.get(
        "/wiki/api/courseware/search", params={"q": "SyntheticKey"}
    ).json()
    assert len(result["items"]) == 1
    assert result["items"][0]["readable"] is False
    assert result["items"][0]["text_sha256"] is None


def test_sql_deadline_cancels_and_cannot_write(tmp_path):
    path, _ = fixture(tmp_path, 1)
    store = SimpleNamespace(root=tmp_path)
    before = path.read_bytes()
    with pytest.raises(ArchiveSearchUnavailable):
        with connection(store, seconds=0) as db:
            db.execute(
                "WITH RECURSIVE t(x) AS (VALUES(0) UNION ALL SELECT x+1 FROM t WHERE x<1000000) SELECT sum(x) FROM t"
            ).fetchone()
    with pytest.raises(ArchiveSearchUnavailable):
        with connection(store) as db:
            db.execute("DELETE FROM documents")
    assert path.read_bytes() == before


def test_explicit_index_tracks_existing_producer_changes_and_keeps_rows(tmp_path):
    path, client = fixture(tmp_path)
    with sqlite3.connect(path) as db:
        before = db.execute("SELECT * FROM documents ORDER BY id").fetchall()
    prepare_index(tmp_path)
    prepare_index(tmp_path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT * FROM documents ORDER BY id").fetchall() == before
        db.execute("UPDATE documents SET active=0 WHERE id=1")
        assert (
            db.execute("SELECT min(id) FROM courseware_current_documents").fetchone()[0]
            == 3
        )
        db.execute("UPDATE documents SET active=1 WHERE id=2")
    result = client.get(
        "/wiki/api/courseware/search", params={"q": "SyntheticKey", "limit": 1}
    ).json()
    assert result["items"][0]["id"] == 2


def test_writes_during_unlocked_seed_are_reconciled_without_reintroducing_old_ids(
    tmp_path,
):
    path, _ = fixture(tmp_path)

    def concurrent_producer(stage, **extra):
        if stage != "current_id_snapshot_seeded":
            return
        with sqlite3.connect(path) as db:
            db.execute("DELETE FROM documents WHERE id=1")
            db.execute("UPDATE documents SET active=1 WHERE id=2")
            db.execute("UPDATE documents SET active=0 WHERE id=3")
            db.execute(
                "INSERT INTO documents SELECT 99,path,locator,digest,text,search_text,active,source_key,extractor,raw_digest FROM documents WHERE id=4"
            )
            db.execute("UPDATE documents SET id=100 WHERE id=5")

    prepare_index(tmp_path, progress=concurrent_producer)
    with sqlite3.connect(path) as db:
        expected = db.execute(
            "SELECT id FROM documents WHERE active=1 ORDER BY id"
        ).fetchall()
        actual = db.execute(
            "SELECT id FROM courseware_current_documents ORDER BY id"
        ).fetchall()
        assert expected == actual
        assert (
            db.execute("SELECT count(*) FROM courseware_search_changes").fetchone()[0]
            == 0
        )
