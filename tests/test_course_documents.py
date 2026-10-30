import hashlib
import zipfile
from pathlib import Path

import pytest

from wechat_decrypt_tool.course_knowledge import KnowledgeStore, create_app, tokens
from wechat_decrypt_tool import course_documents


def version(path, text, extractor="v1", key="", chunks=None):
    return {
        "path": str(path),
        "source_key": key,
        "raw_digest": hashlib.sha256(b"raw input bytes").hexdigest(),
        "extractor": extractor,
        "chunks": chunks if chunks is not None else [(key + " text chars 0", text)],
        "metadata": {},
    }


def test_corrected_extraction_retires_old_search_text_but_keeps_old_citations(tmp_path):
    from fastapi.testclient import TestClient

    store = KnowledgeStore(tmp_path / "kb")
    path = tmp_path / "lecture.pdf"
    with store.connect() as c:
        old_id = c.execute(
            "INSERT INTO documents(path,locator,digest,text,search_text) VALUES(?,?,?,?,?)",
            (
                str(path),
                " page 1 chars 0",
                "old-raw",
                "obsolete_glyph",
                " ".join(tokens("obsolete_glyph")),
            ),
        ).lastrowid
    first = version(path, "corrected_glyph", extractor="parser-fixed")
    store.save_document_versions([first])
    assert store.search("corrected_glyph") and not store.search("obsolete_glyph")
    with TestClient(create_app(store, "unused")) as client:
        old = client.get("/evidence/" + str(-1_000_000_000 - old_id))
        assert old.status_code == 200 and "obsolete_glyph" in old.text
    store.save_document_versions([first])
    assert (
        store.status()["document_chunks"] == 1
        and store.status()["historical_document_chunks"] == 1
    )
    with store.connect() as c:
        assert (
            c.execute("SELECT text FROM documents WHERE id=?", (old_id,)).fetchone()[0]
            == "obsolete_glyph"
        )


def test_shorter_revision_removes_stale_tail_from_search_without_deleting_history(
    tmp_path,
):
    store = KnowledgeStore(tmp_path / "kb")
    path = tmp_path / "a.pdf"
    first = version(
        path,
        "",
        chunks=[
            (" page 1 chars 0", "current_head"),
            (" page 1 chars 3000", "stale_tail"),
        ],
    )
    store.save_document_versions([first])
    assert store.search("stale_tail")
    store.save_document_versions([version(path, "updated_head", extractor="v2")])
    assert not store.search("stale_tail") and store.search("updated_head")
    assert store.status()["historical_document_chunks"] == 2


def test_archive_members_with_identical_content_have_distinct_provenance(tmp_path):
    store = KnowledgeStore(tmp_path / "kb")
    path = tmp_path / "a.zip"
    store.save_document_versions(
        [
            version(path, "same", key="archive a.py"),
            version(path, "same", key="archive b.py"),
        ]
    )
    assert store.status()["document_chunks"] == 2
    assert (
        store.document_revision(
            path, "archive a.py", version(path, "")["raw_digest"], "v1"
        )
        == {}
    )


def test_invalid_rewrite_rolls_back_activation_for_entire_batch(tmp_path):
    store = KnowledgeStore(tmp_path / "kb")
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    store.save_document_versions([version(a, "old_a"), version(b, "old_b")])
    with pytest.raises(RuntimeError, match="increment extractor"):
        store.save_document_versions(
            [version(a, "new_a", "v2"), version(b, "illegally_changed", "v1")]
        )
    assert store.search("old_a") and not store.search("new_a")
    assert store.search("old_b") and not store.search("illegally_changed")


def test_completed_resource_is_not_reparsed_on_resume(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.py").write_text("resume_marker", encoding="utf-8")
    store = KnowledgeStore(tmp_path / "kb")
    course_documents.index_courseware(store, source)

    def unexpected_parse(*args, **kwargs):
        raise AssertionError("unchanged completed entity was reparsed")

    monkeypatch.setattr(course_documents, "_pages", unexpected_parse)
    report = course_documents.index_courseware(store, source)
    assert (
        report["errors"] == []
        and report["cached_entities"] == 1
        and store.status()["document_chunks"] == 1
    )


def test_large_pptx_streams_xml_without_loading_embedded_images(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    path = source / "slides.pptx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(
            "ppt/slides/slide1.xml",
            '<root xmlns:a="urn:test"><a:t>large_slide_marker</a:t></root>',
        )
        z.writestr("ppt/media/image.bin", b"\0" * (17 * 1024**2))
    old = Path.read_bytes

    def read(p):
        if p == path:
            raise AssertionError("Office container must not be materialized into bytes")
        return old(p)

    monkeypatch.setattr(Path, "read_bytes", read)
    store = KnowledgeStore(tmp_path / "kb")
    report = course_documents.index_courseware(store, source)
    assert (
        report["errors"] == []
        and report["skipped"] == []
        and store.search("large_slide_marker")
    )


def test_bounded_zip_skips_are_explicit_and_dont_execute_or_extract_members(
    tmp_path, monkeypatch
):
    source = tmp_path / "source"
    source.mkdir()
    path = source / "a.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("good.py", "good_marker")
        z.writestr("../escape.py", "escape_marker")
        z.writestr("large.txt", "x" * 100)
    monkeypatch.setattr(course_documents, "TEXT_LIMIT", 50)
    store = KnowledgeStore(tmp_path / "kb")
    report = course_documents.index_courseware(store, source)
    assert report["errors"] == [] and report["skipped"][0]["member"] == "large.txt"
    assert (
        report["ignored_archive_members"] == 1
        and store.search("good_marker")
        and not store.search("escape_marker")
    )
    assert not (source / "good.py").exists() and not (tmp_path / "escape.py").exists()


def test_legacy_schema_migration_preserves_evidence_ids(tmp_path):
    import sqlite3

    root = tmp_path / "kb"
    root.mkdir()
    c = sqlite3.connect(root / "knowledge.sqlite3")
    c.executescript("""CREATE TABLE documents(id INTEGER PRIMARY KEY,path TEXT,locator TEXT,digest TEXT,text TEXT,search_text TEXT,UNIQUE(path,locator,digest));
                      INSERT INTO documents VALUES(123,'a.pdf',' page 1 chars 0','raw','legacy_word','legacy_word');""")
    c.close()
    store = KnowledgeStore(root)
    assert store.search("legacy_word")[0]["id"] == -1_000_000_123
    # A newly created external-content FTS index must include pre-existing rows.
    with store.connect() as c:
        row = c.execute("SELECT * FROM documents WHERE id=123").fetchone()
        assert row["text"] == "legacy_word" and row["active"] == 1


def test_concurrent_schema_initialization_waits_and_preserves_data(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    root = tmp_path / "kb"
    with ThreadPoolExecutor(max_workers=4) as workers:
        stores = list(workers.map(lambda _: KnowledgeStore(root), range(4)))
    stores[0].save_document_versions(
        [version(tmp_path / "a.py", "concurrent_schema_marker")]
    )
    assert stores[3].search("concurrent_schema_marker")
