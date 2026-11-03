import hashlib
import json

import pytest

from wechat_decrypt_tool import course_pdf_batch_backup as module
from test_course_pdf_selected_pages import page_history, retained_fixture
from test_course_pdf_validation import fixture_archive


def test_selected_batch_copies_only_selected_history_and_preserves_native_count(
    tmp_path, monkeypatch
):
    store, renderer, independent, plan, run, _, calls, _ = retained_fixture(
        tmp_path, monkeypatch
    )
    run(plan([1], "prior-plan.json"), "prior")
    prior = page_history(store, 1)
    selected = plan([3, 5])
    run(selected, "selected")
    call_snapshot = list(calls)
    destination = tmp_path / "selected-delta"
    result = module.backup_batch(
        store,
        selected,
        store.root / "selected-ocr-report.json",
        renderer,
        destination,
        prefix="selected-copy",
        minimum_free_gib=0,
        rerender=independent,
    )
    manifest = json.loads((destination / "manifest.json").read_bytes())
    assert result["page_occurrences"] == result["unique_raw_pages"] == 2
    assert result["selected_page_scope"] and not result["standalone_reader"]
    assert set(result["full_native_page_counts"].values()) == {5}
    rows = json.loads((destination / "selected-copy-row-export.json").read_bytes())[
        "rows"
    ]
    assert {x["revision"]["source_key"] for x in rows} == {
        "visual  page 3",
        "visual  page 5",
    }
    assert not any(
        "00001" in x["path"] or x["path"].startswith("prior-")
        for x in manifest["files"]
    )
    assert any(x["path"].startswith("original-pdf/") for x in manifest["files"])
    for entry in manifest["files"]:
        assert (
            hashlib.sha256((destination / entry["path"]).read_bytes()).hexdigest()
            == entry["sha256"]
        )
    assert page_history(store, 1) == prior and calls == call_snapshot
    assert (
        not result["source_deletion_allowed"]
        and not result["semantic_accuracy_verified"]
    )


@pytest.mark.parametrize("tiled", [False, "single", True])
def test_full_batches_include_native_pdf_page_cache_and_all_tile_dependencies(
    tmp_path, tiled
):
    store, renderer, independent, _, _, _ = fixture_archive(tmp_path, tiled=tiled)
    result = module.backup_batch(
        store,
        "plan.json",
        "pdf-ocr-report.json",
        renderer,
        tmp_path / "delta",
        prefix="full-copy",
        minimum_free_gib=0,
        rerender=independent,
    )
    assert result["page_occurrences"] == 2 and result["unique_raw_pages"] == 1
    assert not result["selected_page_scope"]
    files = json.loads((tmp_path / "delta/manifest.json").read_bytes())["files"]
    assert sum(x["path"].startswith("original-pdf/") for x in files) == 1
    assert any("tile-ocr-cache/" in x["path"] for x in files) == bool(tiled)
    assert any("tiled-images/" in x["path"] for x in files) == bool(tiled)


def test_existing_backup_is_rejected_before_any_validation_or_writes(
    tmp_path, monkeypatch
):
    store, renderer, _, _, _, _ = fixture_archive(tmp_path)
    destination = tmp_path / "prior"
    destination.mkdir()
    (destination / "manifest.json").write_bytes(b"preserved")
    monkeypatch.setattr(
        module, "verify_pdf_archive", lambda *a, **k: pytest.fail("must not validate")
    )
    with pytest.raises(FileExistsError, match="destination_already_exists"):
        module.backup_batch(
            store,
            "plan.json",
            "pdf-ocr-report.json",
            renderer,
            destination,
            prefix="copy",
        )
    assert (destination / "manifest.json").read_bytes() == b"preserved"
    assert not (store.root / "copy-page-validation.json").exists()


@pytest.mark.parametrize("tamper", ["missing", "duplicate", "error"])
def test_incomplete_or_conflicting_completed_partition_is_rejected(tmp_path, tamper):
    store, renderer, independent, _, _, _ = fixture_archive(tmp_path)
    report = json.loads((store.root / "pdf-ocr-report.json").read_bytes())
    if tamper == "missing":
        report["completed"].pop()
    elif tamper == "duplicate":
        report["completed"][1] = report["completed"][0]
    else:
        report["errors"] = [report["completed"].pop()]
        report["processed"] -= 1
    store.write_json("pdf-ocr-report.json", report)
    with pytest.raises(ValueError, match="completed_partition_differs"):
        module.backup_batch(
            store,
            "plan.json",
            "pdf-ocr-report.json",
            renderer,
            tmp_path / "delta",
            prefix="copy",
            minimum_free_gib=0,
            rerender=independent,
        )
    assert not (tmp_path / "delta").exists()


def test_post_validation_cache_drift_cannot_publish_a_backup(tmp_path, monkeypatch):
    store, renderer, independent, _, _, cache = fixture_archive(tmp_path)
    real = module.verify_pdf_archive

    def validate_then_drift(*args, **kwargs):
        result = real(*args, **kwargs)
        value = json.loads(cache.read_bytes())
        value["text"] = "post_validation_cache_drift_fixture"
        cache.write_text(json.dumps(value), "utf-8")
        return result

    monkeypatch.setattr(module, "verify_pdf_archive", validate_then_drift)
    with pytest.raises(ValueError, match="cached_text_changed"):
        module.backup_batch(
            store,
            "plan.json",
            "pdf-ocr-report.json",
            renderer,
            tmp_path / "delta",
            prefix="copy",
            minimum_free_gib=0,
            rerender=independent,
        )
    assert not (tmp_path / "delta").exists()
    assert not (store.root / "copy-result-backup-validation.json").exists()


def test_post_copy_row_drift_preserves_partial_without_success_receipt(
    tmp_path, monkeypatch
):
    store, renderer, independent, _, _, _ = fixture_archive(tmp_path)
    real, calls = module.current_rows, []

    def rows_then_drift(*args):
        calls.append(True)
        return real(*args) if len(calls) == 1 else []

    monkeypatch.setattr(module, "current_rows", rows_then_drift)
    with pytest.raises(ValueError, match="rows_changed_during_copy"):
        module.backup_batch(
            store,
            "plan.json",
            "pdf-ocr-report.json",
            renderer,
            tmp_path / "delta",
            prefix="copy",
            minimum_free_gib=0,
            rerender=independent,
        )
    assert (tmp_path / "delta").is_dir()
    assert not (tmp_path / "delta/manifest.json").exists()
    assert not (store.root / "copy-result-backup-validation.json").exists()


def test_page_pixel_drift_is_rejected_by_independent_rerender(tmp_path):
    from PIL import Image

    store, renderer, independent, _, image, _ = fixture_archive(tmp_path)
    Image.new("RGB", (64, 32), (1, 2, 3)).save(image)
    with pytest.raises(RuntimeError):
        module.backup_batch(
            store,
            "plan.json",
            "pdf-ocr-report.json",
            renderer,
            tmp_path / "delta",
            prefix="copy",
            minimum_free_gib=0,
            rerender=independent,
        )
    assert not (tmp_path / "delta").exists()
