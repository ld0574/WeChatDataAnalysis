"""Validate and export one exact PDF page scope without overwriting older batches."""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path

from PIL import Image

from .course_attachments import resolve_reference, retained_reference
from .course_knowledge import atomic_json, schema_lease
from .course_pdf_plan import frozen_pdf_scope
from .course_pdf_validation import TILED_POLICY, cache_body, verify_pdf_archive


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def inside(root, path):
    path = Path(path)
    path = path if path.is_absolute() else root / path
    resolved = path.resolve()
    if not resolved.is_relative_to(root) or path.is_symlink() or not resolved.is_file():
        raise ValueError("pdf_batch_backup_source_outside_archive_or_missing")
    return resolved


def current_rows(db, completed):
    rows = []
    for item in completed:
        key = "visual " + item["source_key"] + " page " + str(item["page"])
        identity = (item["path"], key)
        revision = db.execute(
            "SELECT * FROM resource_revisions WHERE path=? AND source_key=?", identity
        ).fetchone()
        documents = db.execute(
            "SELECT * FROM documents WHERE path=? AND source_key=? AND active=1 "
            "ORDER BY locator",
            identity,
        ).fetchall()
        if revision is None or not documents:
            raise ValueError("pdf_batch_backup_committed_rows_missing")
        rows.append(
            {
                "revision": dict(revision),
                "active_documents": [dict(x) for x in documents],
            }
        )
    return rows


def backup_batch(
    store,
    plan_path,
    report_path,
    renderer,
    destination,
    *,
    prefix,
    extra_files=(),
    minimum_free_gib=32,
    rerender=None,
):
    """Rerender/verify the full requested scope, then copy its exact dependencies.

    A selected-page batch preserves each original's native page count. This delta
    exports committed rows and artifacts; it is not a standalone reader archive.
    """
    root, destination = store.root.resolve(), Path(destination).resolve()
    if destination.exists():
        raise FileExistsError("pdf_batch_backup_destination_already_exists")
    if destination.is_relative_to(root) or root.is_relative_to(destination):
        raise ValueError("pdf_batch_backup_destination_overlaps_archive")
    if Path(prefix).name != prefix or not prefix or prefix in {".", ".."}:
        raise ValueError("pdf_batch_backup_invalid_prefix")
    if minimum_free_gib < 0:
        raise ValueError("pdf_batch_backup_invalid_reserve")
    plan_path, report_path = inside(root, plan_path), inside(root, report_path)
    plan_raw, report_raw = plan_path.read_bytes(), report_path.read_bytes()
    plan, report = json.loads(plan_raw), json.loads(report_raw)
    expected = frozen_pdf_scope(plan)
    completed = report.get("completed", [])
    ids = [(x["path"], x["source_key"], x["page"]) for x in completed]
    if not (
        report.get("full_requested_scope_attempted")
        and not report.get("errors")
        and type(report.get("processed")) is int
        and report["processed"] == len(ids) == len(set(ids)) == len(expected)
        and set(ids) == set(expected)
    ):
        raise ValueError("pdf_batch_backup_completed_partition_differs")
    result_path = root / (prefix + "-page-validation.json")
    row_path = root / (prefix + "-row-export.json")
    receipt_path = root / (prefix + "-result-backup-validation.json")
    if any(p.exists() for p in [result_path, row_path, receipt_path]):
        raise FileExistsError("pdf_batch_backup_output_already_exists")
    with schema_lease(root / "pdf-independent-validation.lock", timeout=0):
        kwargs = {} if rerender is None else {"rerender": rerender}
        validation = verify_pdf_archive(
            store,
            plan_path,
            renderer,
            mode="final",
            report_path=report_path,
            result_name=result_path.name,
            **kwargs,
        )
        if not validation["all_requested_pdf_page_occurrences_verified"]:
            raise ValueError("pdf_batch_backup_scope_not_independently_verified")
        files = {}

        def add(name, source):
            if Path(name).is_absolute() or ".." in Path(name).parts:
                raise ValueError("pdf_batch_backup_invalid_file_name")
            source = inside(root, source)
            entry = {
                "source": source,
                "sha256": sha(source.read_bytes()),
                "bytes": source.stat().st_size,
            }
            if name in files and files[name] != entry:
                raise ValueError("pdf_batch_backup_conflicting_file_name")
            files[name] = entry

        with store.connect() as db:
            db.execute("BEGIN")
            rows = current_rows(db, completed)
            for item, exported in zip(completed, rows, strict=True):
                store.checkpoint()
                revision, docs = exported["revision"], exported["active_documents"]
                key = "visual " + item["source_key"] + " page " + str(item["page"])
                target = expected[item["path"], item["source_key"], item["page"]]
                if revision["raw_digest"] != target["parent_raw_digest"]:
                    raise ValueError("pdf_batch_backup_source_revision_changed")
                meta = json.loads(revision["metadata"])
                image = inside(root, meta["image"])
                if sha(image.read_bytes()) != meta["image_digest"]:
                    raise ValueError("pdf_batch_backup_page_pixels_changed")
                with Image.open(image) as pixels:
                    body, _ = cache_body(store, meta, revision["extractor"], pixels)
                if not all(x["digest"] == revision["revision"] for x in docs) or {
                    x["locator"]: x["text"] for x in docs
                } != {
                    key + " chars " + str(n): body[n : n + 3000]
                    for n in range(0, len(body), 3000)
                }:
                    raise ValueError("pdf_batch_backup_cached_text_changed")
                add(image.relative_to(root).as_posix(), image)
                cache = image.with_name(
                    image.stem
                    + "-"
                    + sha(revision["extractor"].encode())[:20]
                    + ".json"
                )
                add(cache.relative_to(root).as_posix(), cache)
                for tile in (meta.get("tile_coverage") or {}).get("tiles", []):
                    crop = inside(
                        root,
                        "document-visuals/tiled-images/"
                        + meta["image_digest"]
                        + "/"
                        + "-".join(map(str, tile["box"]))
                        + ".png",
                    )
                    add(crop.relative_to(root).as_posix(), crop)
                    policy = (
                        TILED_POLICY + "/" + meta["model"] + "@" + meta["model_digest"]
                    )
                    folder = (
                        root
                        / "document-visuals/tile-ocr-cache"
                        / sha((sha(crop.read_bytes()) + "\0" + policy).encode())
                    )
                    for cache in folder.rglob("*"):
                        if cache.is_file():
                            add(cache.relative_to(root).as_posix(), cache)
                retained = resolve_reference(
                    store,
                    retained_reference(
                        store,
                        target["path"],
                        target["source_key"],
                        target["parent_raw_digest"],
                    ),
                )["path"]
                retained = inside(root, retained)
                if sha(retained.read_bytes()) != target["parent_raw_digest"]:
                    raise ValueError("pdf_batch_backup_native_source_changed")
                add("original-pdf/" + target["parent_raw_digest"] + ".pdf", retained)
            db.rollback()
        atomic_json(
            row_path,
            {
                "policy": "exact-finite-pdf-row-export-v1",
                "rows": rows,
                "source_deletion_allowed": False,
            },
        )
        for source in [plan_path, report_path, result_path, row_path, *extra_files]:
            source = inside(root, source)
            add(source.relative_to(root).as_posix(), source)
        needed = sum(x["bytes"] for x in files.values())
        ancestor = destination.parent
        while not ancestor.exists():
            ancestor = ancestor.parent
        if (
            min(shutil.disk_usage(root).free, shutil.disk_usage(ancestor).free)
            < needed + minimum_free_gib * 1024**3
        ):
            raise InterruptedError("pdf_batch_backup_disk_reserve_reached")
        store.checkpoint()
        destination.mkdir(parents=True)
        copied = []
        for name, entry in sorted(files.items()):
            store.checkpoint()
            raw = entry["source"].read_bytes()
            if sha(raw) != entry["sha256"]:
                raise ValueError("pdf_batch_backup_input_changed_during_copy")
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(raw)
            if sha(target.read_bytes()) != entry["sha256"]:
                raise ValueError("pdf_batch_backup_copy_hash_mismatch")
            copied.append({"path": name, "bytes": len(raw), "sha256": entry["sha256"]})
        with store.connect() as db:
            if current_rows(db, completed) != rows:
                raise ValueError("pdf_batch_backup_rows_changed_during_copy")
        if plan_path.read_bytes() != plan_raw or report_path.read_bytes() != report_raw:
            raise ValueError("pdf_batch_backup_scope_changed_during_copy")
        for entry in files.values():
            if sha(entry["source"].read_bytes()) != entry["sha256"]:
                raise ValueError("pdf_batch_backup_input_changed_during_copy")
        manifest = {
            "policy": "validated-finite-pdf-page-delta-v2",
            "files": copied,
            "page_occurrences": len(expected),
            "unique_raw_pages": len(
                {(v["parent_raw_digest"], k[2]) for k, v in expected.items()}
            ),
            "full_native_page_counts": {
                v["parent_raw_digest"]: v["pages"] for v in expected.values()
            },
            "selected_page_scope": "pdf_sources_selected_pages" in plan,
            "active_document_chunks": sum(len(x["active_documents"]) for x in rows),
            "plan_sha256": sha(plan_raw),
            "report_sha256": sha(report_raw),
            "independent_validation_sha256": sha(result_path.read_bytes()),
            "standalone_reader": False,
            "semantic_accuracy_verified": False,
            "source_deletion_allowed": False,
            "updated": time.time(),
        }
        atomic_json(destination / "manifest.json", manifest)
        receipt = {k: v for k, v in manifest.items() if k != "files"}
        receipt.update(
            backup=str(destination),
            files=len(copied),
            bytes=needed,
            manifest_sha256=sha((destination / "manifest.json").read_bytes()),
        )
        atomic_json(receipt_path, receipt)
        return receipt
