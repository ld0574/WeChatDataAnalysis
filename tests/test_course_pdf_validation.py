import hashlib
import io
import json

import pytest
from PIL import Image
from pypdf import PdfWriter

from wechat_decrypt_tool import course_pdf_validation as module
from wechat_decrypt_tool.course_knowledge import KnowledgeStore


def fixture_archive(tmp_path, *, tiled=False, legacy=False):
    store = KnowledgeStore(tmp_path / "kb")
    writer = PdfWriter()
    writer.add_blank_page(width=64, height=32)
    buffer = io.BytesIO()
    writer.write(buffer)
    raw = buffer.getvalue()
    digest = hashlib.sha256(raw).hexdigest()
    original = tmp_path / "originals-not-present"
    obj = store.root / "resources/objects" / digest
    obj.parent.mkdir(parents=True)
    obj.write_bytes(raw)
    store.write_json(
        "resources-manifest.json",
        {
            "source": str(original),
            "completed": [
                {
                    "relative_path": name,
                    "sha256": digest,
                    "bytes": len(raw),
                    "object": str(obj.relative_to(store.root)),
                }
                for name in ("a.pdf", "b.pdf")
            ],
        },
    )
    poppler = tmp_path / "pdftoppm-fixture.exe"
    poppler.write_bytes(b"independent-renderer-fixture")
    renderer = module.sha(poppler.read_bytes())[:16] + "-scale2000-dpi150"
    model, model_digest = "local-fixture", "a" * 64
    extractor = (
        ("pdf-vision-v2/" if legacy else "pdf-vision-v3-tiled-v1/")
        + renderer
        + "/"
        + model
        + "@"
        + model_digest
    )
    image = store.root / "document-visuals" / renderer / digest / "00001.png"
    image.parent.mkdir(parents=True)
    Image.new("RGB", (64, 32), (250, 251, 252)).save(image)
    image_digest = module.sha(image.read_bytes())
    cache = image.with_name("00001-" + module.sha(extractor.encode())[:20] + ".json")
    content = "pdf_validation_fixture_text"
    tile_coverage = None
    if tiled:
        box = [0, 0, 64, 32]
        crop = (
            store.root
            / "document-visuals/tiled-images"
            / image_digest
            / "0-0-64-32.png"
        )
        crop.parent.mkdir(parents=True)
        crop.write_bytes(image.read_bytes())
        crop_digest = module.sha(crop.read_bytes())
        policy = module.TILED_POLICY + "/" + model + "@" + model_digest
        accepted = (
            store.root
            / "document-visuals/tile-ocr-cache"
            / module.sha((crop_digest + "\0" + policy).encode())
        )
        accepted.mkdir(parents=True)
        regions = (
            [[0, 0, 64, 32]] if tiled == "single" else [[0, 0, 34, 32], [30, 0, 64, 32]]
        )
        texts = []
        for region in regions:
            value = {"policy": policy, "image_digest": crop_digest, "text": content}
            (accepted / ("-".join(map(str, region)) + ".json")).write_text(
                json.dumps(value), "utf-8"
            )
            texts.append(
                "裁切图内部区域 " + str(region) + "（重叠可能重复）\n" + content
            )
        content = (
            "页面按原渲染像素分块转录（重叠可能重复，需对照整页复核）\n页面原图区域 "
            + str(box)
            + "\n"
            + "\n\n".join(texts)
        )
        tile_coverage = {
            "source_width": 64,
            "source_height": 32,
            "all_planned_tiles_completed": True,
            "tiles": [{"box": box, "inference_regions": regions}],
        }
    cache.write_text(
        json.dumps(
            {
                "image_digest": image_digest,
                "model": model,
                "model_digest": model_digest,
                "text": content,
                "tile_coverage": tile_coverage,
            },
            ensure_ascii=False,
        ),
        "utf-8",
    )
    versions, completed, targets = [], [], []
    for name in ("a.pdf", "b.pdf"):
        path = str(original / name)
        meta = {
            "parent_source_key": "",
            "parent_raw_digest": digest,
            "image": str(image.relative_to(store.root)),
            "image_digest": image_digest,
            "page": 1,
            "model": model,
            "model_digest": model_digest,
            "review_required": True,
            "rendered_page_max_dimension": 2000,
        }
        if tiled:
            meta["tile_coverage"] = tile_coverage
        key = "visual  page 1"
        versions.append(
            {
                "path": path,
                "source_key": key,
                "raw_digest": digest,
                "extractor": extractor,
                "chunks": [(key + " chars 0", module.BODY_PREFIX + content)],
                "metadata": meta,
            }
        )
        completed.append({"path": path, "source_key": "", "page": 1})
        targets.append(
            {"path": path, "source_key": "", "parent_raw_digest": digest, "pages": 1}
        )
    store.save_document_versions(versions)
    store.write_json(
        "plan.json",
        {"pdf_sources_all_pages": targets, "summary": {"page_occurrences": 2}},
    )
    store.write_json(
        "pdf-ocr-report.json",
        {
            "completed": completed,
            "errors": [],
            "processed": 2,
            "limit": 0,
            "full_requested_scope_attempted": True,
            "attempted_page_occurrences": 2,
            "deferred_gpu": False,
        },
    )
    calls = []

    def rerender(pdf, page, destination, executable):
        assert hashlib.sha256(pdf.read_bytes()).hexdigest() == digest
        assert page == 1 and executable == poppler
        calls.append((pdf, page))
        Image.new("RGB", (64, 32), (250, 251, 252)).save(destination)
        return {"stderr_bytes": 0, "stderr_sha256": module.sha(b"")}

    return store, poppler, rerender, calls, image, cache


def test_all_source_aliases_native_pixels_cache_fts_citations_and_restored_originals(
    tmp_path,
):
    from wechat_decrypt_tool.course_backup import create_snapshot, restore_snapshot

    store, poppler, render, calls, _, _ = fixture_archive(tmp_path)
    result = module.verify_pdf_archive(
        store, store.root / "plan.json", poppler, rerender=render
    )
    assert result["all_requested_pdf_page_occurrences_verified"]
    assert (
        result["verified_completed_page_occurrences"]
        == result["source_download_route_groups"]
        == 2
    )
    assert result["unique_original_pages_rerendered"] == len(calls) == 1
    assert not result[
        "original_small_glyph_layout_and_model_semantics_independently_proven"
    ]
    snapshot = create_snapshot(store, tmp_path / "backup", reserve_gib=0)
    restored = KnowledgeStore(
        restore_snapshot(snapshot["snapshot"], tmp_path / "restored", reserve_gib=0)
    )
    # Restore needs only the retained resources and committed plan, not the original drive.
    restored.write_json(
        "plan.json", json.loads((store.root / "plan.json").read_text("utf-8"))
    )
    restored.write_json(
        "pdf-ocr-report.json",
        json.loads((store.root / "pdf-ocr-report.json").read_text("utf-8")),
    )
    result = module.verify_pdf_archive(
        restored, restored.root / "plan.json", poppler, rerender=render
    )
    assert result["all_requested_pdf_page_occurrences_verified"]


def test_partial_mode_and_explicit_failures_never_claim_full_acceptance(tmp_path):
    store, poppler, render, _, _, _ = fixture_archive(tmp_path)
    pilot = module.verify_pdf_archive(
        store,
        store.root / "plan.json",
        poppler,
        mode="committed",
        limit=1,
        rerender=render,
    )
    assert pilot["verified_completed_page_occurrences"] == 1
    assert not pilot["all_requested_pdf_page_occurrences_verified"]
    report = json.loads((store.root / "pdf-ocr-report.json").read_text("utf-8"))
    report["errors"] = [
        {**report["completed"].pop(), "error": "fixture incomplete source"}
    ]
    report["processed"] = 1
    store.write_json("pdf-ocr-report.json", report)
    result = module.verify_pdf_archive(
        store, store.root / "plan.json", poppler, rerender=render
    )
    assert (
        result["full_requested_scope_attempted_verified"]
        and result["failed_page_occurrences"] == 1
    )
    assert not result["all_requested_pdf_page_occurrences_verified"]
    with pytest.raises(RuntimeError, match="cannot use a page limit"):
        module.verify_pdf_archive(
            store, store.root / "plan.json", poppler, limit=1, rerender=render
        )


def test_tiled_pdf_restores_with_accepted_page_crop_and_inference_caches(tmp_path):
    from wechat_decrypt_tool.course_backup import create_snapshot, restore_snapshot

    store, poppler, render, _, _, _ = fixture_archive(tmp_path, tiled="single")
    snapshot = create_snapshot(store, tmp_path / "backup", reserve_gib=0)
    restored = KnowledgeStore(
        restore_snapshot(snapshot["snapshot"], tmp_path / "restored", reserve_gib=0)
    )
    restored.write_json(
        "plan.json", json.loads((store.root / "plan.json").read_text("utf-8"))
    )
    result = module.verify_pdf_archive(
        restored, restored.root / "plan.json", poppler, rerender=render
    )
    assert result["all_requested_pdf_page_occurrences_verified"]
    assert result["accepted_tiled_inference_regions_verified"] == 2


def test_missing_or_duplicate_report_pages_rejected(tmp_path):
    store, poppler, render, _, _, _ = fixture_archive(tmp_path)
    report = json.loads((store.root / "pdf-ocr-report.json").read_text("utf-8"))
    report["completed"][1] = report["completed"][0]
    store.write_json("pdf-ocr-report.json", report)
    with pytest.raises(RuntimeError, match="partition differs"):
        module.verify_pdf_archive(
            store, store.root / "plan.json", poppler, rerender=render
        )


def test_historical_renderer_provenance_preserved_when_new_engine_replays_exact_pixels(
    tmp_path,
):
    store, poppler, render, _, _, _ = fixture_archive(tmp_path)
    original_hash = module.sha(poppler.read_bytes())
    poppler.write_bytes(b"newer-independent-renderer-fixture")
    result = module.verify_pdf_archive(
        store, store.root / "plan.json", poppler, rerender=render
    )
    assert result["all_requested_pdf_page_occurrences_verified"]
    assert result["original_renderer_policies"] == [
        original_hash[:16] + "-scale2000-dpi150"
    ]
    assert result["validation_renderer_executable_sha256"] == module.sha(
        poppler.read_bytes()
    )
    assert result["cross_renderer_page_occurrences_with_exact_rgba_replay"] == 2


def test_backup_selects_cache_for_captured_historical_extractor(tmp_path):
    from wechat_decrypt_tool.course_backup import create_snapshot, restore_snapshot

    store, poppler, render, _, image, cache = fixture_archive(tmp_path, legacy=True)
    with store.connect() as db:
        extractor = db.execute(
            "SELECT extractor FROM resource_revisions LIMIT 1"
        ).fetchone()[0]
    uncommitted_extractor = extractor.replace(
        "pdf-vision-v2/", "pdf-vision-v3-tiled-v1/", 1
    )
    uncommitted = image.with_name(
        "00001-" + module.sha(uncommitted_extractor.encode())[:20] + ".json"
    )
    value = json.loads(cache.read_text("utf-8"))
    value["text"] = "uncommitted_newer_cache_fixture"
    uncommitted.write_text(json.dumps(value), "utf-8")
    snapshot = create_snapshot(store, tmp_path / "backup", reserve_gib=0)
    restored = KnowledgeStore(
        restore_snapshot(snapshot["snapshot"], tmp_path / "restored", reserve_gib=0)
    )
    assert not (restored.root / uncommitted.relative_to(store.root)).exists()
    restored.write_json(
        "plan.json", json.loads((store.root / "plan.json").read_text("utf-8"))
    )
    result = module.verify_pdf_archive(
        restored, restored.root / "plan.json", poppler, rerender=render
    )
    assert result["all_requested_pdf_page_occurrences_verified"]


def test_changed_pixels_rejected_even_after_page_and_cache_hashes_are_updated(tmp_path):
    store, poppler, render, _, image, cache = fixture_archive(tmp_path)
    Image.new("RGB", (64, 32), (250, 251, 251)).save(image)
    digest = module.sha(image.read_bytes())
    value = json.loads(cache.read_text("utf-8"))
    value["image_digest"] = digest
    cache.write_text(json.dumps(value), "utf-8")
    with store.write() as db:
        for row in list(db.execute("SELECT * FROM resource_revisions")):
            metadata = json.loads(row["metadata"])
            metadata["image_digest"] = digest
            encoded = json.dumps(metadata)
            db.execute(
                "UPDATE resource_revisions SET metadata=? WHERE path=? AND source_key=?",
                (encoded, row["path"], row["source_key"]),
            )
            db.execute(
                "UPDATE document_version_metadata SET metadata=? WHERE path=? AND source_key=?",
                (encoded, row["path"], row["source_key"]),
            )
    with pytest.raises(RuntimeError, match="independent original rerender"):
        module.verify_pdf_archive(
            store, store.root / "plan.json", poppler, rerender=render
        )


def test_accepted_cache_text_cannot_disagree_with_committed_chunks(tmp_path):
    store, poppler, render, _, _, cache = fixture_archive(tmp_path)
    value = json.loads(cache.read_text("utf-8"))
    value["text"] = "changed_cache_fixture"
    cache.write_text(json.dumps(value), "utf-8")
    with pytest.raises(RuntimeError, match="cached text/chunk coverage"):
        module.verify_pdf_archive(
            store, store.root / "plan.json", poppler, rerender=render
        )


def test_tiled_every_pixel_and_every_accepted_region_are_verified(tmp_path):
    store, poppler, render, _, _, cache = fixture_archive(tmp_path, tiled=True)
    result = module.verify_pdf_archive(
        store, store.root / "plan.json", poppler, rerender=render
    )
    assert result["accepted_tiled_inference_regions_verified"] == 4
    value = json.loads(cache.read_text("utf-8"))
    value["tile_coverage"]["tiles"][0]["inference_regions"][1][0] = 35
    cache.write_text(json.dumps(value), "utf-8")
    with store.write() as db:
        for row in list(db.execute("SELECT * FROM resource_revisions")):
            meta = json.loads(row["metadata"])
            meta["tile_coverage"] = value["tile_coverage"]
            db.execute(
                "UPDATE resource_revisions SET metadata=? WHERE path=? AND source_key=?",
                (json.dumps(meta), row["path"], row["source_key"]),
            )
    with pytest.raises(RuntimeError, match="pixel coverage has a gap"):
        module.verify_pdf_archive(
            store, store.root / "plan.json", poppler, rerender=render
        )
