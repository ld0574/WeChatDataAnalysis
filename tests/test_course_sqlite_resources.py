import hashlib
import io
import json
import sqlite3
import zipfile

import pytest
from fastapi.testclient import TestClient

from wechat_decrypt_tool.course_knowledge import KnowledgeStore, create_app
from wechat_decrypt_tool.course_sqlite_resources import (
    index_sqlite_resources,
    sqlite_chunks,
)


def database(tmp_path):
    path = tmp_path / "fixture.db"
    with sqlite3.connect(path) as db:
        db.execute(
            'CREATE TABLE "odd"" table"(id INTEGER PRIMARY KEY, message TEXT, number REAL, data BLOB, absent TEXT, generated TEXT GENERATED ALWAYS AS (upper(message)) VIRTUAL)'
        )
        db.execute(
            'INSERT INTO "odd"" table" (id,message,number,data) VALUES(?,?,?,?)',
            (0, "sqlite_local_marker", float("inf"), b"\x00\xff"),
        )
        db.execute(
            'CREATE VIEW dangerous_view AS SELECT load_extension(message) FROM "odd"" table"'
        )
        db.execute("CREATE TABLE empty_table(id INTEGER)")
        db.execute("CREATE VIRTUAL TABLE search_data USING fts5(content)")
        db.execute(
            "INSERT INTO search_data VALUES('virtual_row_must_not_be_evaluated')"
        )
    return path.read_bytes()


def test_read_only_table_scan_handles_identifiers_types_and_skips_expressions(tmp_path):
    raw = database(tmp_path)
    chunks, metadata = sqlite_chunks(raw)
    rows = [
        json.loads(line)
        for _, body in chunks
        for line in body.splitlines()
        if '"row_ordinal"' in line
    ]
    assert metadata["rows"] == 1 and metadata["tables"] == 2
    assert metadata["binary_cells"] == 1
    assert not metadata["source_sql_executed"]
    assert rows[0]["values"]["id"] == {"type": "integer", "value": 0}
    assert rows[0]["values"]["number"] == {"type": "real", "value": "inf"}
    assert rows[0]["values"]["absent"] == {"type": "null", "value": None}
    assert (
        rows[0]["values"]["data"]["sha256"] == hashlib.sha256(b"\x00\xff").hexdigest()
    )
    assert "generated" not in rows[0]["values"]
    omitted = metadata["omitted_objects"]
    assert any(item["kind"] == "virtual" for item in omitted if "kind" in item)
    assert any(item["kind"] == "shadow" for item in omitted if "kind" in item)
    assert any(item["kind"] == "view" for item in omitted if "kind" in item)
    assert not any("virtual_row_must_not_be_evaluated" in body for _, body in chunks)
    assert (tmp_path / "fixture.db").read_bytes() == raw


def corpus(tmp_path, *, sidecar=False, declared_sidecar=False):
    store = KnowledgeStore(tmp_path / "knowledge")
    raw = database(tmp_path)
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as z:
        z.writestr("fixture.db", raw)
        if sidecar:
            z.writestr("fixture.db-wal", b"unapplied changes")
    outer = io.BytesIO()
    with zipfile.ZipFile(outer, "w") as z:
        z.writestr("inner.zip", inner.getvalue())
    container = outer.getvalue()
    sha = hashlib.sha256(container).hexdigest()
    obj = store.root / "resources" / "objects" / sha
    obj.parent.mkdir(parents=True)
    obj.write_bytes(container)
    source = tmp_path / "unavailable-originals"
    store.write_json(
        "resources-manifest.json",
        {
            "source": str(source),
            "completed": [
                {
                    "relative_path": name,
                    "sha256": sha,
                    "bytes": len(container),
                    "object": str(obj.relative_to(store.root)),
                }
                for name in ("one.zip", "two.zip")
            ],
        },
    )
    store.write_json(
        "audit.json",
        {
            "completed": [
                {
                    "path": str(source / "one.zip"),
                    "chain": ["inner.zip", "fixture.db"],
                    "raw_digest": hashlib.sha256(raw).hexdigest(),
                    "sidecar_members": ["fixture.db-wal"] if declared_sidecar else [],
                }
            ]
        },
    )
    return store, store.root / "audit.json", obj, raw


def test_nested_sqlite_aliases_search_download_and_resume_without_originals(tmp_path):
    store, audit, _, raw = corpus(tmp_path)
    report = index_sqlite_resources(store, audit)
    assert len(report["completed"]) == 2 and not report["errors"]
    assert all(x["cached"] for x in index_sqlite_resources(store, audit)["completed"])
    hits = store.search("sqlite_local_marker")
    assert len(hits) == 2
    with TestClient(create_app(store, "unused")) as client:
        for hit in hits:
            document_id = -hit["id"] - 1000000000
            assert client.get(f"/document-source/{document_id}").content == raw


@pytest.mark.parametrize("sidecar,declared", [(True, False), (False, True)])
def test_sidecars_cannot_be_silently_ignored_even_if_missing_from_audit(
    tmp_path, sidecar, declared
):
    store, audit, _, _ = corpus(tmp_path, sidecar=sidecar, declared_sidecar=declared)
    report = index_sqlite_resources(store, audit)
    assert len(report["errors"]) == 2 and not report["completed"]
    assert not store.search("sqlite_local_marker")


def test_changed_raw_digest_fails_before_any_partial_rows_are_committed(tmp_path):
    store, audit, _, _ = corpus(tmp_path)
    value = json.loads(audit.read_text())
    value["completed"][0]["raw_digest"] = "0" * 64
    store.write_json(audit.name, value)
    report = index_sqlite_resources(store, audit)
    assert len(report["errors"]) == 2 and not report["completed"]
    assert not store.search("sqlite_local_marker")


def test_corrupt_sqlite_and_cancellation_raise_instead_of_partial_success(tmp_path):
    with pytest.raises(ValueError):
        sqlite_chunks(b"not a database")

    def stop():
        raise InterruptedError("local pause")

    with pytest.raises(InterruptedError):
        sqlite_chunks(database(tmp_path), stop)
