import contextlib
import hashlib
import io
import json
import zipfile

import pytest
from PIL import Image

from wechat_decrypt_tool import course_frame_ocr, course_image_ocr
from wechat_decrypt_tool import course_image_validation as module
from wechat_decrypt_tool.course_knowledge import KnowledgeStore
from wechat_decrypt_tool.course_video_visual_audit import ReadOnlyArchive


@pytest.fixture
def archive(tmp_path, monkeypatch):
    store = KnowledgeStore(tmp_path / "kb")
    originals = tmp_path / "absent-originals"
    image = Image.new("RGBA", (40, 240), (0, 0, 0, 0))
    image.putpixel((20, 120), (0, 0, 0, 255))
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    raw = buf.getvalue()
    digest = hashlib.sha256(raw).hexdigest()
    obj = store.root / "resources/objects" / digest
    obj.parent.mkdir(parents=True)
    obj.write_bytes(raw)
    manifest = {
        "source": str(originals),
        "completed": [
            {
                "relative_path": name,
                "sha256": digest,
                "bytes": len(raw),
                "object": str(obj.relative_to(store.root)),
            }
            for name in ["a.png", "b.png"]
        ],
    }
    store.write_json("resources-manifest.json", manifest)
    rows = [
        {
            "path": str(originals / name),
            "source_key": "",
            "sha256": digest,
            "container_sha256": digest,
            "state": "raster_ocr_candidate",
        }
        for name in ["a.png", "b.png"]
    ]
    store.write_json("teaching-image-audit.json", {"image_occurrences": rows})
    monkeypatch.setattr(course_image_ocr, "local_model_digest", lambda _: "a" * 64)
    monkeypatch.setattr(course_frame_ocr, "local_model_digest", lambda _: "a" * 64)

    def run(split=False, text=None):
        calls = []

        def chat(*_):
            calls.append(True)
            if split and len(calls) == 1:
                raise RuntimeError("Text model output was truncated")
            return text or "independently_checked_diagram " + str(len(calls))

        result = course_image_ocr.run_image_ocr(
            store,
            store.root / "teaching-image-audit.json",
            "local",
            chat,
            lambda _: contextlib.nullcontext(),
        )
        assert len(result["completed"]) == 2 and not result["errors"]
        return result

    return store, originals, obj, run


def load(store, name):
    return json.loads((store.root / name).read_text("utf-8"))


def revise_metadata(store, change):
    with store.write() as db:
        rows = db.execute("SELECT * FROM resource_revisions").fetchall()
        for row in rows:
            meta = json.loads(row["metadata"])
            change(meta)
            encoded = json.dumps(meta)
            db.execute(
                "UPDATE resource_revisions SET metadata=? WHERE path=? AND source_key=?",
                (encoded, row["path"], row["source_key"]),
            )
            db.execute(
                "UPDATE document_version_metadata SET metadata=? WHERE path=? AND source_key=? AND revision=?",
                (encoded, row["path"], row["source_key"], row["revision"]),
            )


def cache(store):
    return next((store.root / "document-visuals/image-ocr-cache").iterdir())


@pytest.mark.parametrize("split", [False, True])
def test_all_occurrences_verified_offline_read_only_with_exact_pixels(
    archive, monkeypatch, split
):
    store, originals, _, run = archive
    run(split)
    before = (store.root / "knowledge.sqlite3").read_bytes()
    real_open = type(store.root).open

    def guarded(path, *args, **kwargs):
        assert not path.is_relative_to(originals)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(type(store.root), "open", guarded)
    result = module.validate_primary_images(ReadOnlyArchive(store.root))
    assert result["primary_requested_occurrences"] == 2
    assert result["primary_successful_occurrences_verified"] == 2
    assert result["unique_normalized_images_every_pixel_verified"] == 1
    assert result["accepted_cache_trees_reconstructed"] == 1
    assert result["active_chunks_and_individual_fts_probes_verified"] == 2
    assert result["all_static_occurrences_accepted"]
    assert result["archive_database_read_only"] and not result["models_invoked"]
    assert not result["source_deletion_allowed"]
    assert (store.root / "knowledge.sqlite3").read_bytes() == before


def test_full_attempted_scope_keeps_primary_failures_explicit(archive):
    store, _, _, run = archive
    run()
    report = load(store, "images-ocr-report.json")
    report["errors"] = [report["completed"].pop()]
    store.write_json("images-ocr-report.json", report)
    result = module.validate_primary_images(ReadOnlyArchive(store.root))
    assert result["all_primary_successful_occurrences_verified"]
    assert (
        result["primary_failed_occurrences_requiring_separate_repair_acceptance"] == 1
    )
    assert not result["all_static_occurrences_accepted"]


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["completed"].append(d["completed"][0]),
        lambda d: d["completed"].pop(),
        lambda d: d["errors"].append(d["completed"][0]),
        lambda d: d.update(limit=1),
        lambda d: d.update(deferred_gpu=True),
        lambda d: d.update(requested=1),
    ],
)
def test_incomplete_duplicate_and_overlapping_reports_rejected(archive, change):
    store, _, _, run = archive
    run()
    report = load(store, "images-ocr-report.json")
    change(report)
    store.write_json("images-ocr-report.json", report)
    with pytest.raises(RuntimeError, match="identity|partition"):
        module.validate_primary_images(ReadOnlyArchive(store.root))


def test_retained_source_tampering_rejected_even_without_originals(archive):
    store, _, obj, run = archive
    run()
    raw = bytearray(obj.read_bytes())
    raw[-1] ^= 1
    obj.write_bytes(raw)
    with pytest.raises(RuntimeError, match="container changed"):
        module.validate_primary_images(ReadOnlyArchive(store.root))


def test_consistent_hash_of_wrong_normalized_pixels_is_not_accepted(archive):
    store, _, _, run = archive
    report = run()
    image = store.root / report["completed"][0]["image"]
    with Image.open(image) as saved:
        changed = saved.copy()
    changed.putpixel((0, 0), (255, 0, 0))
    changed.save(image)
    new_hash = module.sha(image.read_bytes())
    revise_metadata(store, lambda meta: meta.update(image_digest=new_hash))
    with pytest.raises(RuntimeError, match="pixels differ"):
        module.validate_primary_images(ReadOnlyArchive(store.root))


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_digest", "0" * 64),
        ("policy", "another-model"),
        ("full_vertical_extent_covered", False),
        ("width", 41),
        ("regions", [{"top": 1, "bottom": 240}]),
        ("raw_image_retained", "outside/image.png"),
    ],
)
def test_false_cache_manifest_coverage_or_binding_rejected(archive, field, value):
    store, _, _, run = archive
    run()
    target = cache(store) / "manifest.json"
    d = json.loads(target.read_text("utf-8"))
    d[field] = value
    target.write_text(json.dumps(d), "utf-8")
    with pytest.raises(RuntimeError, match="cache"):
        module.validate_primary_images(ReadOnlyArchive(store.root))


def test_missing_split_leaf_is_not_replaced_by_truncated_output(archive):
    store, _, _, run = archive
    run(True)
    (cache(store) / "0-150.json").unlink()
    with pytest.raises(FileNotFoundError):
        module.validate_primary_images(ReadOnlyArchive(store.root))


def test_committed_text_and_cache_must_match_exactly(archive):
    store, _, _, run = archive
    run()
    with store.write() as db:
        db.execute("UPDATE documents SET text='different',search_text='different'")
    with pytest.raises(RuntimeError, match="chunks differ"):
        module.validate_primary_images(ReadOnlyArchive(store.root))


def test_search_text_must_be_rebuilt_from_actual_chunk(archive):
    store, _, _, run = archive
    run()
    with store.write() as db:
        db.execute("UPDATE documents SET search_text='unrelated'")
    with pytest.raises(RuntimeError, match="search/source"):
        module.validate_primary_images(ReadOnlyArchive(store.root))


def test_missing_real_fts_posting_rejected_despite_external_content(archive):
    store, _, _, run = archive
    run()
    with store.write() as db:
        row = db.execute("SELECT * FROM documents LIMIT 1").fetchone()
        db.execute(
            "INSERT INTO document_fts(document_fts,rowid,search_text) VALUES('delete',?,?)",
            (row["id"], row["search_text"]),
        )
    with pytest.raises(RuntimeError, match="FTS probe missing"):
        module.validate_primary_images(ReadOnlyArchive(store.root))


def test_historical_metadata_tampering_rejected(archive):
    store, _, _, run = archive
    run()
    with store.write() as db:
        db.execute("UPDATE document_version_metadata SET metadata='{}'")
    with pytest.raises(RuntimeError, match="immutable metadata"):
        module.validate_primary_images(ReadOnlyArchive(store.root))


def test_read_snapshot_does_not_hold_writer_lease(archive):
    store, _, _, run = archive
    run()
    writes = []
    with store.write() as db:
        db.execute("CREATE TABLE validation_concurrency_fixture(value TEXT)")

    def progress(**_):
        with store.write() as db:
            db.execute(
                "INSERT INTO validation_concurrency_fixture VALUES(?)",
                (str(len(writes)),),
            )
        writes.append(True)

    result = module.validate_primary_images(
        ReadOnlyArchive(store.root), progress=progress
    )
    assert len(writes) == 2 and result["all_static_occurrences_accepted"]


def test_input_change_during_audit_prevents_acceptance(archive):
    store, _, _, run = archive
    run()

    def progress(**_):
        with (store.root / "images-ocr-report.json").open(
            "a", encoding="utf-8"
        ) as stream:
            stream.write(" ")

    with pytest.raises(RuntimeError, match="inputs changed"):
        module.validate_primary_images(ReadOnlyArchive(store.root), progress=progress)


def test_pause_cancels_without_mutating_database(archive):
    store, _, _, run = archive
    run()
    (store.root / "pause.flag").touch()
    with pytest.raises(InterruptedError):
        module.validate_primary_images(ReadOnlyArchive(store.root))


def test_namespace_escape_is_refused(tmp_path):
    with pytest.raises(RuntimeError, match="escaped"):
        module.asset(tmp_path, "../outside.png", "document-visuals")


def test_underscore_separators_do_not_become_empty_fts_probes(archive):
    store, _, _, run = archive
    run(text="_" * 9000 + " valid_label")
    result = module.validate_primary_images(ReadOnlyArchive(store.root))
    assert result["all_static_occurrences_accepted"]
    assert result["active_chunks_and_individual_fts_probes_verified"] > 2


def test_zip_member_sources_verified_without_original_archives(archive):
    store, _, obj, run = archive
    source_raw = obj.read_bytes()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zipped:
        zipped.writestr("inner/figure.png", source_raw)
    value = buffer.getvalue()
    digest = module.sha(value)
    container = obj.with_name(digest)
    container.write_bytes(value)
    manifest = load(store, "resources-manifest.json")
    for row in manifest["completed"]:
        row.update(
            sha256=digest,
            bytes=len(value),
            object=str(container.relative_to(store.root)),
        )
    store.write_json("resources-manifest.json", manifest)
    audit = load(store, "teaching-image-audit.json")
    for row in audit["image_occurrences"]:
        row.update(container_sha256=digest, source_key="archive inner/figure.png")
    store.write_json("teaching-image-audit.json", audit)
    run()
    result = module.validate_primary_images(ReadOnlyArchive(store.root))
    assert result["primary_successful_occurrences_verified"] == 2


def test_exif_orientation_is_compared_in_display_coordinates(archive):
    store, _, obj, run = archive
    image = Image.new("RGB", (16, 24), "white")
    image.putpixel((0, 0), (0, 0, 0))
    exif = Image.Exif()
    exif[274] = 6
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", exif=exif)
    value = buffer.getvalue()
    digest = module.sha(value)
    retained = obj.with_name(digest)
    retained.write_bytes(value)
    manifest = load(store, "resources-manifest.json")
    for row in manifest["completed"]:
        row.update(
            sha256=digest,
            bytes=len(value),
            object=str(retained.relative_to(store.root)),
        )
    store.write_json("resources-manifest.json", manifest)
    audit = load(store, "teaching-image-audit.json")
    for row in audit["image_occurrences"]:
        row.update(sha256=digest, container_sha256=digest)
    store.write_json("teaching-image-audit.json", audit)
    report = run()
    with Image.open(store.root / report["completed"][0]["image"]) as saved:
        assert saved.size == (24, 16)
    assert module.validate_primary_images(ReadOnlyArchive(store.root))[
        "all_static_occurrences_accepted"
    ]
