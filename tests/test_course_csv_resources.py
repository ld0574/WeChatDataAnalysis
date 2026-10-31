import csv
import hashlib
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from wechat_decrypt_tool import course_csv_resources as module
from wechat_decrypt_tool.course_knowledge import KnowledgeStore, create_app


def fixture_csv(rows):
    stream = io.StringIO(newline="")
    csv.writer(stream).writerows(rows)
    return stream.getvalue().encode("utf-8")


def read_records(spool):
    body = "".join(
        json.loads(line)[1] for line in spool.read_text("utf-8").splitlines()
    )
    return [json.loads(line) for line in body.splitlines()]


def test_complete_field_strings_multiline_whitespace_and_row_coordinates(tmp_path):
    rows = [
        ["kind", "value", "empty"],
        ["01", '\r\nquoted,"x"\n tail ', ""],
        [],
        ["中文", "=1+2", "0003"],
    ]
    source = tmp_path / "source.csv"
    source.write_bytes(fixture_csv(rows))
    spool = tmp_path / "chunks.jsonl"
    metadata = module.spool_rows(
        source, spool, {"encoding": "utf-8-sig", "delimiter": ","}
    )
    assert read_records(spool) == [
        {"row": n, "values": row} for n, row in enumerate(rows, 1)
    ]
    assert metadata["rows"] == 4 and metadata["cells"] == 9
    assert (
        metadata["field_types_not_coerced"]
        and metadata["source_code_executed"] is False
    )
    assert metadata["first_row_is_not_inferred_header"]


def test_long_quoted_field_crosses_chunks_without_losing_characters(tmp_path):
    rows = [["column"], ["one\nquoted,值" * 1400], ["end"]]
    source = tmp_path / "long.csv"
    source.write_bytes(fixture_csv(rows))
    spool = tmp_path / "chunks.jsonl"
    meta = module.spool_rows(source, spool, {"encoding": "utf-8", "delimiter": ","})
    chunks = [json.loads(line) for line in spool.read_text("utf-8").splitlines()]
    assert len(chunks) > 3 and all(len(x[1]) <= 3000 for x in chunks)
    assert [int(x[0].split()[2]) for x in chunks] == list(
        range(0, meta["indexed_characters"], 3000)
    )
    assert read_records(spool) == [
        {"row": n, "values": row} for n, row in enumerate(rows, 1)
    ]


def test_legacy_encoding_is_explicit_strict_and_profile_counts_are_enforced(tmp_path):
    source = tmp_path / "legacy.csv"
    source.write_bytes("列,值\r\n标题,中文\r\n".encode("gb18030"))
    spool = tmp_path / "chunks.jsonl"
    with pytest.raises(ValueError, match="strict Unicode"):
        module.spool_rows(source, spool, {"encoding": "utf-8", "delimiter": ","})
    meta = module.spool_rows(source, spool, {"encoding": "gb18030", "delimiter": ","})
    assert read_records(spool)[1]["values"] == ["标题", "中文"]
    assert meta["rows"] == 2
    with pytest.raises(ValueError, match="frozen full-source profile"):
        module.spool_rows(
            source, spool, {"encoding": "gb18030", "delimiter": ",", "cells": 999}
        )
    source.write_bytes(b'field\r\n"unterminated')
    with pytest.raises(ValueError, match="strict record parsing"):
        module.spool_rows(source, spool, {"encoding": "utf-8", "delimiter": ","})


def test_sensitive_columns_and_assignments_masked_without_coercing_other_values(
    tmp_path,
):
    source = tmp_path / "credentials.csv"
    source.write_bytes(
        fixture_csv(
            [
                ["api_key", "content", "tokenizer"],
                ["fixture-secret", "password=other-secret", "training_fixture"],
            ]
        )
    )
    spool = tmp_path / "chunks.jsonl"
    meta = module.spool_rows(source, spool, {"encoding": "utf-8", "delimiter": ","})
    indexed = spool.read_text("utf-8")
    assert "fixture-secret" not in indexed and "other-secret" not in indexed
    assert "training_fixture" in indexed and meta["masked_cells"] == 2


def test_interrupted_staging_stays_hidden_and_resume_preserves_ids_and_history(
    tmp_path, monkeypatch
):
    store = KnowledgeStore(tmp_path / "kb")
    source = tmp_path / "rows.csv"
    source.write_bytes(fixture_csv([["new_csv_marker_" * 300]] * 3))
    spool = tmp_path / "chunks.jsonl"
    meta = module.spool_rows(source, spool, {"encoding": "utf-8", "delimiter": ","})
    old_sha = "a" * 64
    store.save_document_versions(
        [
            {
                "path": "rows.csv",
                "source_key": "",
                "raw_digest": old_sha,
                "extractor": "old-csv-fixture",
                "chunks": [(" old", "old_csv_marker")],
                "metadata": {},
            }
        ]
    )
    original = store.checkpoint

    def pause_after_commit():
        with store.connect() as db:
            if db.execute("SELECT count(*) FROM documents WHERE active=0").fetchone()[
                0
            ]:
                raise InterruptedError(
                    "fixture pause after bounded staging transaction"
                )
        original()

    monkeypatch.setattr(store, "checkpoint", pause_after_commit)
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    with pytest.raises(InterruptedError):
        module.publish_spool(store, "rows.csv", "", sha, spool, meta, batch_size=1)
    assert store.search("old_csv_marker") and not store.search("new_csv_marker_")
    with store.connect() as db:
        staged = {
            r["locator"]: r["id"]
            for r in db.execute("SELECT * FROM documents WHERE active=0")
        }
    monkeypatch.setattr(store, "checkpoint", original)
    module.publish_spool(store, "rows.csv", "", sha, spool, meta, batch_size=1)
    assert store.search("new_csv_marker_") and not store.search("old_csv_marker")
    with store.connect() as db:
        active = {
            r["locator"]: r["id"]
            for r in db.execute("SELECT * FROM documents WHERE active=1")
        }
        assert all(
            active[location] == identity for location, identity in staged.items()
        )
        assert (
            db.execute("SELECT count(*) FROM document_version_metadata").fetchone()[0]
            == 2
        )
    module.publish_spool(store, "rows.csv", "", sha, spool, meta, batch_size=2)
    with store.connect() as db:
        assert active == {
            r["locator"]: r["id"]
            for r in db.execute("SELECT * FROM documents WHERE active=1")
        }


def test_tampered_staged_chunk_cannot_be_published(tmp_path):
    store = KnowledgeStore(tmp_path / "kb")
    spool = tmp_path / "chunks.jsonl"
    spool.write_text(
        json.dumps(["csv chars 000000000000 rows 0000000001-0000000001", "one"]) + "\n",
        "utf-8",
    )
    module.publish_spool(store, "a.csv", "", "b" * 64, spool, {"chunks": 1})
    spool.write_text(
        json.dumps(["csv chars 000000000000 rows 0000000001-0000000001", "changed"])
        + "\n",
        "utf-8",
    )
    with pytest.raises(RuntimeError, match="differs from frozen source"):
        module.publish_spool(store, "a.csv", "", "b" * 64, spool, {"chunks": 1})


def test_legacy_retirement_excludes_hidden_and_adjacent_sources_and_keeps_ids(tmp_path):
    store = KnowledgeStore(tmp_path / "kb")
    path, key = "container.zip", "archive target.csv"
    entries = [
        (key + " chars 0", 1, "legacy-v1", "legacy-with-explicit-key"),
        (key + ".other chars 0", 1, "legacy-v1", ""),
        (key + " chars 3000", 0, module.EXTRACTOR, key),
        (key + " chars 6000", 1, module.EXTRACTOR, key),
    ]
    with store.write() as db:
        for location, active, extractor, source_key in entries:
            db.execute(
                "INSERT INTO documents(path,locator,digest,text,search_text,active,source_key,extractor,raw_digest) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    path,
                    location,
                    "revision",
                    "fixture retained text",
                    "fixture",
                    active,
                    source_key,
                    extractor,
                    "a" * 64,
                ),
            )
        before = {r["id"]: dict(r) for r in db.execute("SELECT * FROM documents")}
        module.retire_legacy_rows(db, path, key)
        after = {r["id"]: dict(r) for r in db.execute("SELECT * FROM documents")}
    assert set(before) == set(after)
    changed = [identity for identity in before if before[identity] != after[identity]]
    assert len(changed) == 1
    assert after[changed[0]] == {**before[changed[0]], "active": 0}


def test_direct_zip_aliases_hash_routes_resume_and_independent_restore(tmp_path):
    from wechat_decrypt_tool.course_backup import create_snapshot, restore_snapshot

    raw = fixture_csv([["title", "value"], ["csv_search_fixture", "0001"]])
    sha = hashlib.sha256(raw).hexdigest()
    store = KnowledgeStore(tmp_path / "kb")
    original = tmp_path / "originals-absent"
    rows, profile = [], []

    def retain(data, names):
        container_sha = hashlib.sha256(data).hexdigest()
        obj = store.root / "resources/objects" / container_sha
        obj.parent.mkdir(parents=True, exist_ok=True)
        obj.write_bytes(data)
        for name in names:
            rows.append(
                {
                    "relative_path": name,
                    "object": str(obj.relative_to(store.root)),
                    "bytes": len(data),
                    "sha256": container_sha,
                }
            )
        return container_sha

    retain(raw, ["a.csv", "b.csv"])
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("folder/data.csv", raw)
    zip_sha = retain(buffer.getvalue(), ["a.zip", "b.zip"])
    for name, container, chain in [
        ("a.csv", sha, []),
        ("a.zip", zip_sha, ["folder/data.csv"]),
    ]:
        profile.append(
            {
                "aliases": [{"path": str(original / name), "chain": chain}],
                "container_sha256": container,
                "raw_digest": sha,
                "bytes": len(raw),
                "encoding": "utf-8",
                "delimiter": ",",
                "rows_including_first": 2,
                "cells": 4,
            }
        )
    store.write_json(
        "resources-manifest.json", {"source": str(original), "completed": rows}
    )
    store.write_json("profile.json", {"completed": profile, "errors": []})
    result = module.index_csv_resources(
        store, store.root / "profile.json", reserve_gib=0
    )
    assert len(result["completed"]) == result["requested"] == 4 and not result["errors"]
    hits = store.search("csv_search_fixture")
    assert len(hits) == 4
    ids = {x["id"] for x in hits}
    assert all(
        x["cached"]
        for x in module.index_csv_resources(
            store, store.root / "profile.json", reserve_gib=0
        )["completed"]
    )
    assert {x["id"] for x in store.search("csv_search_fixture")} == ids
    snapshot = create_snapshot(store, tmp_path / "backup", reserve_gib=0)
    restored = KnowledgeStore(
        restore_snapshot(snapshot["snapshot"], tmp_path / "restored", reserve_gib=0)
    )
    with TestClient(create_app(restored, "unused")) as client:
        for hit in hits:
            doc = -hit["id"] - 1000000000
            response = client.get("/document-source/" + str(doc))
            assert response.status_code == 200 and response.content == raw
    obj = store.root / rows[0]["object"]
    obj.write_bytes(b"tampered")
    failed = module.index_csv_resources(
        store, store.root / "profile.json", reserve_gib=0
    )
    assert len(failed["errors"]) == 2
