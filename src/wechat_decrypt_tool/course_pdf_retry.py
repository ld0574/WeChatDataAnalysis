"""Finer initial grids for failed PDF pages, preserving accepted OCR caches."""

from __future__ import annotations

import hashlib
import io
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .course_pdf_ocr import local_model_digest, pdf_source, render_page
from .course_tiled_ocr import describe_tile, retain_tiles, tile_boxes

RETRY_SIZE = 640
RETRY_OVERLAP = 64


@contextmanager
def wait_for_lease(store, name, progress):
    """Long independent audits may own the lease for hours; keep waiting cancellably."""
    import time
    from .course_knowledge import schema_lease

    while True:
        store.checkpoint()
        manager = schema_lease(store.root / name, timeout=0)
        try:
            manager.__enter__()
            break
        except RuntimeError as exc:
            if str(exc) != "Another course archive operation still holds " + name:
                raise
            progress("waiting_for_archive_lease", lease=name)
            time.sleep(20)
    try:
        yield
    finally:
        manager.__exit__(None, None, None)


class RetryReportStore:
    """Delegate archive writes while preserving all original producer reports."""

    def __init__(self, store, prefix="pdf-retry"):
        self.store = store
        self.prefix = prefix

    def __getattr__(self, name):
        return getattr(self.store, name)

    def write_json(self, name, value):
        replacements = {
            "pdf-ocr-report.json": self.prefix + "-ocr-report.json",
            "pdf-ocr-progress.json": self.prefix + "-ocr-progress.json",
            "pdf-ocr-errors.json": self.prefix + "-ocr-errors.json",
        }
        return self.store.write_json(replacements.get(name, name), value)


def prepare_retry_page(
    store, target, source, model, chat, lease, poppler, *, progress=lambda *a, **k: None
):
    """Prepare a normal v3 accepted page cache using smaller native-pixel crops.

    The inference engine and its leaf policy are unchanged: 640px roots are
    below the existing 1200px maximum. Actual root geometry and retry parameters
    are retained in tile_coverage and independently replayed by the validator.
    No failed/split cache is removed or promoted to accepted evidence.
    """
    from PIL import Image
    from .course_knowledge import schema_lease

    if type(target.get("page")) is not int or target["page"] < 1:
        raise ValueError("Retry requires an explicit positive PDF page")
    if not (store.root / "resources-manifest.json").is_file():
        raise ValueError("Retry requires retained PDF originals")
    poppler = Path(poppler)
    renderer = (
        hashlib.sha256(poppler.read_bytes()).hexdigest()[:16] + "-scale2000-dpi150"
    )
    model_digest = local_model_digest(model)
    extractor = "pdf-vision-v3-tiled-v1/" + renderer + "/" + model + "@" + model_digest
    key = "visual " + target["source_key"] + " page " + str(target["page"])
    with tempfile.TemporaryDirectory(prefix="pdf-retry-", dir=store.root) as temporary:
        with pdf_source(source, target, temporary, store=store) as (pdf, digest):
            if (
                store.document_revision(target["path"], key, digest, extractor)
                is not None
            ):
                return {"cached_committed_page": True}
            folder = store.root / "document-visuals" / renderer / digest
            folder.mkdir(parents=True, exist_ok=True)
            image = folder / f"{target['page']:05}.png"
            cache = image.with_name(
                image.stem
                + "-"
                + hashlib.sha256(extractor.encode()).hexdigest()[:20]
                + ".json"
            )
            if cache.exists():
                # The normal producer validates the accepted cache before commit.
                return {"cached_page_result": True}
            if not image.exists():
                rendered = Path(temporary) / "page.png"
                render_page(pdf, target["page"], rendered, poppler)
                rendered.replace(image)
            raw = image.read_bytes()
            image_digest = hashlib.sha256(raw).hexdigest()
            pieces, regions = [], []
            with Image.open(io.BytesIO(raw)) as original:
                original.load()
                width, height = original.size
                if max(width, height) > 2000:
                    raise ValueError("PDF retry image exceeds retained renderer policy")
                boxes = tile_boxes(
                    width, height, size=RETRY_SIZE, overlap=RETRY_OVERLAP
                )
                for box in boxes:
                    store.checkpoint()
                    buffer = io.BytesIO()
                    original.crop(box).save(buffer, format="PNG")
                    data = buffer.getvalue()
                    # Reuse the bounded, alpha-aware, hash-checked asset writer.
                    inner = retain_tiles(store, data, hashlib.sha256(data).hexdigest())
                    if len(inner) != 1:
                        raise RuntimeError(
                            "Retry root unexpectedly requires multiple bounded crops"
                        )
                    tile = inner[0]
                    path = (
                        store.root
                        / "document-visuals/tiled-images"
                        / image_digest
                        / ("-".join(map(str, box)) + ".png")
                    )
                    with schema_lease(
                        store.root / "tiled-image-assets.lock", timeout=60
                    ):
                        if path.exists():
                            if (
                                hashlib.sha256(path.read_bytes()).hexdigest()
                                != tile["image_digest"]
                            ):
                                raise RuntimeError(
                                    "Retry crop changed; preserve evidence"
                                )
                        else:
                            path.parent.mkdir(parents=True, exist_ok=True)
                            os.link(store.root / tile["image"], path)
                    tile = {
                        **tile,
                        "box": list(box),
                        "image": str(path.relative_to(store.root)),
                        "source_image_digest": image_digest,
                        "source_width": width,
                        "source_height": height,
                    }
                    text, detail = describe_tile(
                        store,
                        tile,
                        model,
                        chat,
                        lease,
                        model_digest=model_digest,
                        progress=progress,
                        wait=True,
                    )
                    pieces.append("页面原图区域 " + str(list(box)) + "\n" + text)
                    regions.append(
                        {
                            "box": list(box),
                            "inference_regions": detail["inference_regions"],
                        }
                    )
            content = (
                "页面按原渲染像素分块转录（重叠可能重复，需对照整页复核）\n"
                + "\n\n".join(pieces)
            )
            coverage = {
                "source_width": width,
                "source_height": height,
                "tiles": regions,
                "all_planned_tiles_completed": True,
                "retry_grid_size": RETRY_SIZE,
                "retry_grid_overlap": RETRY_OVERLAP,
            }
            if local_model_digest(model) != model_digest:
                raise RuntimeError(
                    "Local PDF retry model changed before cache acceptance"
                )
            store.write_json(
                str(cache.relative_to(store.root)),
                {
                    "text": content,
                    "image_digest": image_digest,
                    "model": model,
                    "model_digest": model_digest,
                    "tile_coverage": coverage,
                },
            )
            return {
                "prepared_retry_page": True,
                "tiles": len(regions),
                "image_digest": image_digest,
            }


def reconcile_pdf_reports(primary, retry):
    """Replace only explicit primary errors with a complete retry partition."""

    def identity(row):
        if type(row.get("page")) is not int or row["page"] < 1:
            raise ValueError("PDF report has no explicit positive page")
        return row["path"], row["source_key"], row["page"]

    def partition(report):
        complete = {identity(row): row for row in report["completed"]}
        failed = {identity(row): row for row in report["errors"]}
        if (
            len(complete) != len(report["completed"])
            or len(failed) != len(report["errors"])
            or complete.keys() & failed.keys()
            or report.get("limit") != 0
            or report.get("deferred_gpu")
            or not report.get("full_requested_scope_attempted")
            or report.get("processed") != len(complete)
            or report.get("attempted_page_occurrences") != len(complete) + len(failed)
        ):
            raise ValueError(
                "PDF report is not a complete disjoint attempted partition"
            )
        return complete, failed

    complete, failed = partition(primary)
    recovered, remaining = partition(retry)
    if recovered.keys() | remaining.keys() != failed.keys():
        raise ValueError("PDF retry scope differs from primary failed pages")
    merged = {
        **primary,
        "completed": list(complete.values()) + list(recovered.values()),
        "errors": list(remaining.values()),
        "processed": len(complete) + len(recovered),
        "full_requested_scope_processed": not remaining,
        "recovered_primary_errors": len(recovered),
        "primary_report_preserved": True,
    }
    partition(merged)
    return merged


def run_failed_pages(
    store,
    primary,
    source,
    model,
    chat,
    lease,
    poppler,
    *,
    progress=lambda *a, **k: None,
):
    """Retry the exact failed partition once; accepted caches make interruption resumable."""
    from .course_pdf_ocr import run_pdf_ocr

    failures = primary["errors"]
    empty = {
        "completed": [],
        "errors": failures,
        "processed": 0,
        "limit": 0,
        "deferred_gpu": False,
        "full_requested_scope_attempted": True,
        "attempted_page_occurrences": len(failures),
    }
    # Validate the complete primary partition before doing any inference or writes.
    reconcile_pdf_reports(primary, empty)
    result = {**empty, "errors": [], "full_requested_scope_processed": False}
    proxy = RetryReportStore(store, "pdf-retry-page")
    for index, target in enumerate(failures):
        store.checkpoint()
        progress(
            "retrying_failed_page",
            attempted=index,
            requested=len(failures),
            recovered=len(result["completed"]),
        )
        try:
            prepare_retry_page(
                proxy,
                target,
                source,
                model,
                chat,
                lease,
                poppler,
                progress=lambda stage, **extra: progress(
                    stage, attempted=index, requested=len(failures), **extra
                ),
            )
            store.write_json(
                "pdf-retry-page-input.json",
                {
                    "pdf_pages_needing_ocr": [
                        {
                            "path": target["path"],
                            "location": target["source_key"]
                            + " page "
                            + str(target["page"]),
                            **(
                                {"parent_raw_digest": target["expected_digest"]}
                                if "expected_digest" in target
                                else {}
                            ),
                        }
                    ]
                },
            )
            page = run_pdf_ocr(
                proxy,
                [store.root / "pdf-retry-page-input.json"],
                source,
                model,
                chat,
                lease,
                poppler=poppler,
                wait=True,
            )
            if page["processed"] + len(page["errors"]) != 1 or page["deferred_gpu"]:
                raise RuntimeError("PDF retry page did not reach a terminal partition")
            result["completed"].extend(page["completed"])
            result["errors"].extend(page["errors"])
        except InterruptedError:
            raise
        except Exception as exc:
            result["errors"].append(
                {**target, "error": type(exc).__name__ + ": " + str(exc)}
            )
    result["processed"] = len(result["completed"])
    result["full_requested_scope_processed"] = not result["errors"]
    merged = reconcile_pdf_reports(primary, result)
    store.write_json("pdf-retry-ocr-report.json", result)
    store.write_json("pdf-reconciled-report.json", merged)
    return merged


def main():
    """Queue behind one exact producer, then retry, independently verify, and back up."""
    import argparse
    import json
    import time

    import psutil

    from .course_knowledge import KnowledgeStore, gpu_lease, local_chat, schema_lease
    from .course_pdf_validation import verify_pdf_archive

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--poppler", type=Path, required=True)
    parser.add_argument("--model", default="qwen3.5:9b")
    parser.add_argument("--after-pid", type=int)
    parser.add_argument("--after-created", type=float)
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    if (args.after_pid is None) != (args.after_created is None):
        parser.error("--after-pid and --after-created must be supplied together")
    store = KnowledgeStore(args.root)

    def progress(stage, **extra):
        store.write_json(
            "pdf-retry-driver-progress.json",
            {
                "stage": stage,
                "pid": os.getpid(),
                "updated": time.time(),
                "source_deletion_allowed": False,
                **extra,
            },
        )

    try:
        with schema_lease(store.root / "pdf-retry-driver.lock", timeout=0):
            while args.after_pid is not None:
                store.checkpoint()
                try:
                    dependency = psutil.Process(args.after_pid)
                    if dependency.create_time() != args.after_created:
                        break
                    if not any(
                        arg.endswith("run_all_pdf_pages.py")
                        for arg in dependency.cmdline()
                    ):
                        raise RuntimeError("PDF retry dependency identity changed")
                except psutil.NoSuchProcess:
                    break
                progress(
                    "waiting_for_full_pdf_producer",
                    dependency_pid=args.after_pid,
                    dependency_created=args.after_created,
                )
                time.sleep(20)
            with wait_for_lease(store, "pdf-ocr.lock", progress):
                primary_path = store.root / "pdf-ocr-report.json"
                original = primary_path.read_bytes()
                primary = json.loads(original)
                source = Path(
                    json.loads(
                        (store.root / "resources-manifest.json").read_text("utf-8")
                    )["source"]
                )
                frozen = store.root / "pdf-retry-primary-report.json"
                if frozen.exists() and frozen.read_bytes() != original:
                    raise RuntimeError(
                        "PDF primary report changed since retry; preserve both runs for review"
                    )
                if not frozen.exists():
                    frozen.write_bytes(original)
                merged = run_failed_pages(
                    store,
                    primary,
                    source,
                    args.model,
                    local_chat,
                    gpu_lease,
                    args.poppler,
                    progress=progress,
                )
                if primary_path.read_bytes() != original:
                    raise RuntimeError("PDF primary report changed during retry")
            progress(
                "independent_full_page_validation",
                recovered=merged["recovered_primary_errors"],
                remaining_errors=len(merged["errors"]),
            )
            with wait_for_lease(store, "pdf-independent-validation.lock", progress):
                result = verify_pdf_archive(
                    store,
                    args.plan,
                    args.poppler,
                    report_path=store.root / "pdf-reconciled-report.json",
                    result_name="pdf-reconciled-validation.json",
                )
            snapshot = None
            if args.backup:
                from .course_backup import create_snapshot

                progress(
                    "creating_verified_snapshot", remaining_errors=len(merged["errors"])
                )
                snapshot = create_snapshot(store, args.backup, reserve_gib=64)[
                    "snapshot"
                ]
            progress(
                "batch_finished"
                if result["all_requested_pdf_page_occurrences_verified"]
                else "finished_with_errors",
                recovered=merged["recovered_primary_errors"],
                remaining_errors=len(merged["errors"]),
                verified_pages=result["verified_completed_page_occurrences"],
                snapshot=snapshot,
            )
    except BaseException as exc:
        progress(
            "paused" if isinstance(exc, InterruptedError) else "stopped_with_error",
            error_type=type(exc).__name__,
            error_sha256=hashlib.sha256(str(exc).encode()).hexdigest(),
        )
        raise


if __name__ == "__main__":
    main()
