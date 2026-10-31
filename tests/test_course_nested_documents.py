import contextlib
import hashlib
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from wechat_decrypt_tool import course_attachments as attachments
from wechat_decrypt_tool import course_pdf_ocr as pdf
from wechat_decrypt_tool import course_slide_render as slides
from wechat_decrypt_tool.course_knowledge import KnowledgeStore, create_app
from wechat_decrypt_tool.course_nested_documents import index_nested_courseware
from wechat_decrypt_tool.course_resource_archive import archive_resources


def zip_bytes(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in entries:
            archive.writestr(name, raw)
    return buffer.getvalue()


@pytest.fixture
def corpus(tmp_path):
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    pdf_data = io.BytesIO()
    writer.write(pdf_data)
    deck = zip_bytes(
        [
            (
                "ppt/slides/slide1.xml",
                b'<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>',
            )
        ]
    )
    inner = zip_bytes([("learn.md", b"nested_distinct_reference_marker")])
    bundle = zip_bytes(
        [
            ("main.py", b"def nested_example(): return 42\n"),
            ("paper.pdf", pdf_data.getvalue()),
            ("deck.pptx", deck),
            ("data.jsonl", b'{"nested_training_example": 1}\n'),
            ("data.xlsx", b"retained unparsed dataset bytes"),
            ("inner.zip", inner),
            (".ipynb_checkpoints/ignored.py", b"never index metadata copy"),
        ]
    )
    outer = zip_bytes([("bundle.zip", bundle)])
    source = tmp_path / "source"
    source.mkdir()
    for name in ("a.zip", "b.zip"):
        (source / name).write_bytes(outer)
    inventory = tmp_path / "inventory.json"
    inventory.write_text("{}")
    store = KnowledgeStore(tmp_path / "kb")
    archive_resources(store, source, inventory, reserve_gib=0)
    manifest = json.loads((store.root / "resources-manifest.json").read_text())
    audit = store.root / "nested-audit.json"
    store.write_json(
        audit.name,
        {
            "nested_archives_not_expanded": [
                {
                    "path": str(source / row["relative_path"]),
                    "container_sha256": row["sha256"],
                    "source_key": "archive bundle.zip",
                }
                for row in manifest["completed"]
            ]
        },
    )
    for name in ("a.zip", "b.zip"):
        (source / name).unlink()
    return store, audit, outer, pdf_data.getvalue(), deck


def test_nested_index_is_resumable_and_raw_downloads_survive_missing_originals(corpus):
    store, audit, outer, _, _ = corpus
    first = index_nested_courseware(store, audit)
    with store.connect() as db:
        before = db.execute("SELECT count(*) FROM documents").fetchone()[0]
        row = db.execute(
            "SELECT id FROM documents WHERE text='nested_distinct_reference_marker' LIMIT 1"
        ).fetchone()
    assert first["indexed_entities"] == 10 and not first["errors"]
    assert len(first["skipped"]) == 2  # unparsed XLSX retained explicitly
    assert len(first["pdf_pages_needing_ocr"]) == 2
    assert len(first["visual_pages_needing_ocr"]) == 2
    second = index_nested_courseware(store, audit)
    assert second["cached_entities"] == 10
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM documents").fetchone()[0] == before
        assert not db.execute(
            "SELECT 1 FROM documents WHERE text LIKE '%never index metadata%'"
        ).fetchone()
    assert any(
        r["id"] == -row[0] - 1000000000
        for r in store.search("nested_distinct_reference_marker")
    )
    with TestClient(create_app(store, "unused")) as client:
        assert (
            client.get(f"/document-source/{row[0]}").content
            == b"nested_distinct_reference_marker"
        )
        assert client.get(f"/document-source/{row[0]}?container=true").content == outer


def test_nested_pdf_and_deck_render_sources_work_from_retained_bytes(corpus, tmp_path):
    store, audit, _, raw_pdf, raw_deck = corpus
    report = index_nested_courseware(store, audit)
    pdf_target = pdf.pdf_targets([report])[0]
    with pdf.pdf_source(
        tmp_path / "originals-gone", pdf_target, tmp_path, store=store
    ) as (path, sha):
        assert path.read_bytes() == raw_pdf
        assert sha == hashlib.sha256(raw_pdf).hexdigest()
    slide_target = slides.slide_targets([report])[0]
    with slides.slide_source(
        tmp_path / "originals-gone", slide_target, tmp_path, store=store
    ) as (path, sha):
        assert path.read_bytes() == raw_deck
        assert sha == hashlib.sha256(raw_deck).hexdigest()


def test_nested_empty_pdf_can_gain_hash_bound_ocr_without_original_directory(
    corpus, monkeypatch
):
    from PIL import Image

    store, audit, _, raw_pdf, _ = corpus
    index_nested_courseware(store, audit)
    monkeypatch.setattr(pdf, "local_model_digest", lambda _: "local-weights")
    monkeypatch.setattr(
        pdf,
        "render_page",
        lambda source, page, path, renderer: Image.new("RGB", (100, 100), "white").save(
            path
        ),
    )
    value = pdf.run_pdf_ocr(
        store,
        [store.root / "nested-courseware-report.json"],
        store.root / "gone",
        "local",
        lambda *args: "nested_ocr_fixture_marker",
        lambda _: contextlib.nullcontext(),
        poppler=__file__,
    )
    assert len(value["completed"]) == 2 and not value["errors"]
    with store.connect() as db:
        row = db.execute(
            "SELECT id FROM documents WHERE text LIKE '%nested_ocr_fixture_marker%' LIMIT 1"
        ).fetchone()
    with TestClient(create_app(store, "unused")) as client:
        assert client.get(f"/document-source/{row[0]}").content == raw_pdf


def test_nested_container_corruption_does_not_activate_any_documents(corpus):
    store, audit, _, _, _ = corpus
    manifest = json.loads((store.root / "resources-manifest.json").read_text())
    path = store.root / manifest["completed"][0]["object"]
    value = bytearray(path.read_bytes())
    value[-1] ^= 1
    path.write_bytes(value)
    report = index_nested_courseware(store, audit)
    assert len(report["errors"]) == 2 and not report["indexed_entities"]
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM documents").fetchone()[0] == 0


def test_chain_validation_rejects_unsafe_ambiguous_and_oversized_containers(
    tmp_path, monkeypatch
):
    with pytest.raises(attachments.AttachmentIntegrityError, match="Unsafe"):
        attachments.archive_members('archive-chain ["bundle.zip","../outside.py"]')
    with pytest.raises(attachments.AttachmentIntegrityError, match="Invalid"):
        attachments.archive_members('archive-chain [1, "code.py"]')
    duplicated = tmp_path / "duplicate.zip"
    with pytest.warns(UserWarning):
        duplicated.write_bytes(zip_bytes([("bundle.zip", b"a"), ("bundle.zip", b"b")]))
    with pytest.raises(attachments.SourceVersionUnavailable, match="ambiguous"):
        with attachments.open_archive_member(duplicated, ["bundle.zip", "code.py"]):
            pytest.fail("Ambiguous ZIP must not be read")
    path = tmp_path / "large.zip"
    path.write_bytes(zip_bytes([("bundle.zip", zip_bytes([("code.py", b"fixture")]))]))
    monkeypatch.setattr(attachments, "MAX_NESTED_ZIP_BYTES", 1)
    with pytest.raises(attachments.SourceVersionUnavailable, match="memory budget"):
        with attachments.open_archive_member(path, ["bundle.zip", "code.py"]):
            pytest.fail("Budget must be checked before materialization")
