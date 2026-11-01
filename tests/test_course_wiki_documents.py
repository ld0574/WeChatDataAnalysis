import copy
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from test_course_wiki import archive as source_archive
from test_course_wiki import article, published as source_published
from wechat_decrypt_tool import course_wiki as wiki
from wechat_decrypt_tool import course_wiki_coverage as coverage
from wechat_decrypt_tool import course_wiki_documents as documents
from wechat_decrypt_tool import course_wiki_writer as writer
from wechat_decrypt_tool.course_knowledge import atomic_json
from wechat_decrypt_tool.course_library import create_app
from wechat_decrypt_tool.course_lesson_exports import digest
from wechat_decrypt_tool.course_video_visual_audit import ReadOnlyArchive

archive = source_archive
published = source_published


@pytest.fixture
def courseware(published):
    store, key = published
    image = io.BytesIO()
    Image.new("RGB", (24, 18), "white").save(image, format="PNG")
    image_bytes = image.getvalue()
    raw = b"retained synthetic courseware"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("lesson.pdf", raw)
    container = buffer.getvalue()
    sha = digest(container)
    obj = store.root / "resources" / "objects" / sha[:2] / sha
    obj.parent.mkdir(parents=True)
    obj.write_bytes(container)
    atomic_json(
        store.root / "resources-manifest.json",
        {
            "source": str(store.root / "original"),
            "completed": [
                {
                    "relative_path": "lesson.zip",
                    "object": obj.relative_to(store.root).as_posix(),
                    "sha256": sha,
                    "bytes": len(container),
                }
            ],
            "pending": [],
        },
    )
    path = str(store.root / "original" / "lesson.zip")
    image_path = store.root / "document-visuals" / "page.png"
    image_path.parent.mkdir()
    image_path.write_bytes(image_bytes)
    text = "Synthetic courseware: use the explicit condition.\nprint('fixture')"
    store.save_document_versions(
        [
            {
                "path": path,
                "source_key": "pdf visual archive lesson.pdf page 2",
                "raw_digest": digest(raw),
                "extractor": "fixture-ocr-v1",
                "chunks": [("page 2 chars 0", text)],
                "metadata": {
                    "parent_source_key": "archive lesson.pdf",
                    "parent_raw_digest": digest(raw),
                    "image": "document-visuals/page.png",
                    "image_digest": digest(image_bytes),
                    "page": 2,
                    "review_required": True,
                },
            }
        ]
    )
    with store.connect() as db:
        row = dict(
            db.execute("SELECT * FROM documents WHERE path=?", (path,)).fetchone()
        )
    selection = {
        "document": row["id"],
        "revision": row["digest"],
        "text_sha256": digest(text.encode()),
    }
    return store, key, selection, obj, image_path


def packet(fixture):
    store, _, selection, _, _ = fixture
    return wiki.prepare(store.root, "Synthetic courseware topic", [selection])


def draft(p):
    # Reuse developed synthetic prose, replacing the video fixture references.
    fake = {"sources": {"s": {"kind": "speech"}, "f": {"kind": "frame"}}}
    value = article(fake)
    ref = next(iter(p["sources"]))
    for claim in [value["overview"], *value["pitfalls"], *value["quiz"]]:
        claim["citations"] = [ref]
    for section in value["sections"]:
        for claim in section["paragraphs"]:
            claim["citations"] = [ref]
        if section["image"]:
            section["image"] = ref
    value["sections"][0].update(code="print('fixture')", code_citations=[ref])
    return value


def test_freezes_entire_chunk_with_zip_ancestry_and_static_image(courseware):
    p = packet(courseware)
    ref, source = next(iter(p["sources"].items()))
    assert source["text"].endswith("print('fixture')")
    assert source["page"] == 2 and source["review_required"]
    assert p["bindings"][ref]["retained_source"]["members"] == ["lesson.pdf"]
    checks = wiki.validate_article(draft(p), p)
    assert checks["citation_ids_verified"] and not checks["semantic_accuracy_verified"]
    schema = writer.evidence_schema(wiki.SECTION, p)
    assert schema["properties"]["image"]["enum"] == ["", ref]
    section = draft(p)["sections"][0]
    section["paragraphs"].append(
        {"text": "Different synthetic explanation. " * 5, "citations": [ref]}
    )
    # The staged writer uses the same document image and literal code constraints.
    writer.validate_section(section, p, {"heading": section["heading"]})


@pytest.mark.parametrize("field", ["revision", "text_sha256"])
def test_rejects_changed_plan_without_silent_reselection(courseware, field):
    changed = copy.deepcopy(courseware[2])
    changed[field] = "0" * 64
    with pytest.raises(ValueError, match="no longer matches"):
        wiki.prepare(courseware[0].root, "Fixture", [changed])


def test_does_not_truncate_oversized_chunks_or_duplicate_selection(courseware):
    store, _, selection, _, _ = courseware
    with pytest.raises(ValueError, match="Duplicate"):
        wiki.prepare(store.root, "Fixture", [selection, selection])
    with store.write() as db:
        db.execute(
            "UPDATE documents SET text=? WHERE id=?",
            ("a" * 3201, selection["document"]),
        )
    with pytest.raises(ValueError, match="exceeds bounded"):
        wiki.prepare(
            store.root,
            "Fixture",
            [{**selection, "text_sha256": digest(("a" * 3201).encode())}],
        )


def test_rejects_inactive_or_changed_parent_revision(courseware):
    store, _, selection, _, _ = courseware
    store.save_document_versions(
        [
            {
                "path": str(store.root / "original" / "lesson.zip"),
                "source_key": "archive lesson.pdf",
                "raw_digest": "f" * 64,
                "extractor": "fixture-v2",
                "chunks": [],
                "metadata": {},
            }
        ]
    )
    with pytest.raises(ValueError, match="superseded|parent revision"):
        packet(courseware)


def test_member_bytes_are_verified_even_when_container_manifest_matches(courseware):
    store, _, _, obj, _ = courseware
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("lesson.pdf", b"different source")
    obj.write_bytes(buffer.getvalue())
    manifest = json.loads((store.root / "resources-manifest.json").read_text("utf-8"))
    manifest["completed"][0].update(
        sha256=digest(buffer.getvalue()), bytes=len(buffer.getvalue())
    )
    atomic_json(store.root / "resources-manifest.json", manifest)
    with pytest.raises(ValueError, match="member checksum"):
        packet(courseware)


@pytest.mark.parametrize("change", ["escape", "corrupt", "svg", "animation"])
def test_image_provenance_and_real_static_format_required(courseware, change):
    store, _, _, _, image_path = courseware
    if change == "escape":
        image_path = store.root / "private.png"
        image_path.write_bytes(courseware[4].read_bytes())
    elif change == "corrupt":
        image_path.write_bytes(b"changed")
    elif change == "svg":
        image_path.write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg"></svg>')
    else:
        Image.new("RGB", (2, 2), "white").save(
            image_path,
            format="WEBP",
            save_all=True,
            append_images=[Image.new("RGB", (2, 2), "black")],
            duration=10,
            loop=0,
        )
    with store.write() as db:
        row = db.execute(
            "SELECT path,source_key,revision,metadata FROM resource_revisions"
        ).fetchone()
        metadata = json.loads(row["metadata"])
        metadata["image"] = image_path.relative_to(store.root).as_posix()
        if change != "corrupt":
            metadata["image_digest"] = digest(image_path.read_bytes())
        raw = json.dumps(metadata)
        db.execute("UPDATE resource_revisions SET metadata=?", (raw,))
        db.execute("UPDATE document_version_metadata SET metadata=?", (raw,))
    with pytest.raises((ValueError, OSError)):
        packet(courseware)


def test_published_courseware_keeps_old_excerpt_and_image_after_reextract(courseware):
    store, _, selection, _, image_path = courseware
    p = packet(courseware)
    wiki.publish_draft(store, draft(p), p, "e" * 32, "fixture", "fixture-digest")
    image_path.unlink()
    with store.write() as db:
        db.execute("UPDATE documents SET active=0 WHERE id=?", (selection["document"],))
    value = wiki.Wiki(store.root).article("e" * 32)
    assert value["packet"] == p
    client = TestClient(create_app(ReadOnlyArchive(store.root)))
    source = next(iter(p["sources"].values()))
    url = f"/wiki/image/{source['image_sha256']}.png"
    response = client.get(url)
    assert (
        response.status_code == 200 and response.headers["content-type"] == "image/png"
    )
    assert response.headers["x-content-type-options"] == "nosniff"
    assert client.get(url.replace(".png", ".svg")).status_code == 404
    result = coverage.build(ReadOnlyArchive(store.root))
    assert result["summary"]["draft_cited_document_chunks"] == 1
    assert result["summary"]["courses_with_draft_citations"] == 0
    assert result["attachments"][0]["draft_cited_chunks"] == 1
    assert (
        result["issues"][0]["code"] == "frozen_document_differs_from_current_revision"
    )
    assert not result["source_deletion_allowed"]


def test_mixed_video_and_courseware_packet_and_tamper_check(courseware):
    store, key, selection, _, _ = courseware
    p = wiki.prepare(
        store.root,
        "Mixed fixture",
        [selection, {"video": key, "sections": ["section-0"]}],
    )
    assert {s["kind"] for s in p["sources"].values()} == {"document", "speech", "frame"}
    ref = next(k for k, s in p["sources"].items() if s["kind"] == "document")
    p["sources"][ref]["text"] += " invented"
    with pytest.raises(ValueError, match="identity mismatch"):
        wiki.validate_article(draft(p), p)


def native_selection(fixture):
    store = fixture[0]
    with store.connect() as db:
        row = dict(db.execute("SELECT * FROM documents LIMIT 1").fetchone())
    store.save_document_versions(
        [
            {
                "path": row["path"],
                "source_key": "archive lesson.pdf",
                "raw_digest": row["raw_digest"],
                "extractor": "fixture-native-v1",
                "chunks": [("archive lesson.pdf page 2 chars 0", row["text"])],
                "metadata": {},
            }
        ]
    )
    with store.connect() as db:
        native = db.execute(
            "SELECT * FROM documents WHERE source_key='archive lesson.pdf'"
        ).fetchone()
    return {
        "document": native["id"],
        "revision": native["digest"],
        "text_sha256": digest(native["text"].encode()),
        "render_page": 2,
    }


def test_native_pdf_renderer_is_page_bound_and_reuses_verified_raster(
    courseware, monkeypatch
):
    from wechat_decrypt_tool import course_pdf_ocr

    store = courseware[0]
    selection = native_selection(courseware)
    binary = store.root / "renderer.exe"
    binary.write_bytes(b"fixture-renderer")
    monkeypatch.setattr(documents.shutil, "which", lambda _: str(binary))
    calls = []

    def render(pdf, page, destination, poppler):
        calls.append(page)
        destination.write_bytes(courseware[4].read_bytes())

    monkeypatch.setattr(course_pdf_ocr, "render_page", render)
    first = wiki.prepare(store.root, "Fixture PDF", [selection])
    second = wiki.prepare(store.root, "Fixture PDF", [selection])
    assert first == second and calls == [2]
    ref, source = next(iter(first["sources"].items()))
    assert source["page"] == 2 and source["image_mime"] == "image/png"
    wiki.validate_article(draft(first), first)
    assert first["bindings"][ref]["image_evidence"]["provenance"]["page"] == 2
    with pytest.raises(ValueError, match="does not match chunk"):
        wiki.prepare(store.root, "Fixture PDF", [{**selection, "render_page": 3}])
    path = store.root / "wiki" / "media" / (source["image_sha256"] + ".png")
    path.write_bytes(b"corrupt cached page")
    with pytest.raises((OSError, ValueError)):
        wiki.prepare(store.root, "Fixture PDF", [selection])
    assert calls == [2]  # Corrupt cache is preserved for investigation.


def test_legacy_native_chunk_can_freeze_current_metadata_but_cannot_claim_historical_receipt(
    courseware,
):
    store = courseware[0]
    selection = native_selection(courseware)
    selection.pop("render_page")
    with store.write() as db:
        db.execute(
            "DELETE FROM document_version_metadata WHERE source_key='archive lesson.pdf'"
        )
    p = wiki.prepare(store.root, "Legacy native fixture", [selection])
    binding = next(iter(p["bindings"].values()))
    assert binding["revision_chain"][0]["metadata_basis"] == "current_revision_snapshot"
    assert not binding["semantic_accuracy_verified"]


def test_current_document_citations_do_not_inflate_video_coverage(courseware):
    store = courseware[0]
    p = packet(courseware)
    wiki.publish_draft(store, draft(p), p, "e" * 32, "fixture", "fixture-digest")
    result = coverage.build(ReadOnlyArchive(store.root))
    assert result["issues"] == []
    assert result["summary"]["resource_paths_with_draft_citations"] == 1
    assert result["attachments"][0]["wiki_document_citation_supported"]
    assert result["summary"]["courses_without_draft_citations"] == 1
