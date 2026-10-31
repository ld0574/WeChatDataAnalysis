"""Consistent SQLite snapshots with independently verified portable evidence."""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import sqlite3
import time
import uuid
from pathlib import Path

TABLES = ("videos", "segments", "frames", "notes", "documents")


def file_hash(path, checkpoint=lambda: None):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024**2), b""):
            checkpoint()
            digest.update(block)
    return digest.hexdigest()


def inside(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or path == root.resolve():
        raise ValueError("Backup asset outside declared root")
    return path


def create_snapshot(store, destination, *, reserve_gib=64):
    destination = Path(destination).resolve()
    source = store.root.resolve()
    if destination.is_relative_to(source) or source.is_relative_to(destination):
        raise ValueError("Backup must be outside the working archive")
    destination.mkdir(parents=True, exist_ok=True)
    reserve = reserve_gib * 1024**3
    if shutil.disk_usage(destination).free < reserve + store.path.stat().st_size:
        raise InterruptedError(
            "Insufficient backup disk space beyond reserved capacity"
        )
    key = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    snapshot = destination / "snapshots" / key
    snapshot.mkdir(parents=True)
    database = snapshot / "knowledge.sqlite3"
    started = time.time()

    def progress(stage, **extra):
        store.write_json(
            "backup-progress.json",
            {
                "stage": stage,
                "updated": time.time(),
                "snapshot": str(snapshot),
                **extra,
            },
        )

    progress("sqlite_backup")
    with store.connect() as src, sqlite3.connect(database) as dst:
        # Hold a WAL read transaction so concurrent ASR commits cannot restart
        # every incremental backup step and indefinitely starve a large snapshot.
        src.execute("BEGIN")
        src.execute("SELECT count(*) FROM sqlite_master").fetchone()
        last_update = 0.0

        def copied(status, remaining, total):
            nonlocal last_update
            store.checkpoint()
            now = time.monotonic()
            if now - last_update >= 1 or remaining == 0:
                progress("sqlite_backup", remaining_pages=remaining, total_pages=total)
                last_update = now

        try:
            src.backup(dst, pages=512, progress=copied, sleep=0.01)
        finally:
            src.rollback()
    assets = {}

    def add(relative, expected=None):
        inside(source, relative)
        if relative in assets and expected and assets[relative] not in (None, expected):
            raise RuntimeError("Conflicting immutable asset hashes in snapshot")
        assets[relative] = expected or assets.get(relative)

    with sqlite3.connect(database) as db:
        for (path,) in db.execute("SELECT path FROM frames"):
            add(path)
        for (raw,) in db.execute("SELECT metadata FROM document_version_metadata"):
            metadata = json.loads(raw)
            if metadata.get("image"):
                add(metadata["image"], metadata["image_digest"])
            if metadata.get("media"):
                add(metadata["media"], metadata["media_digest"])
        counts = {
            t: db.execute("SELECT count(*) FROM " + t).fetchone()[0] for t in TABLES
        }
    resources = source / "resources-manifest.json"
    if resources.exists():
        for row in json.loads(resources.read_text(encoding="utf-8"))["completed"]:
            add(row["object"], row["sha256"])
        add("resources-manifest.json")
    from .course_audio_archive import verified_audio

    audio_count = 0
    for manifest in sorted((source / "lessons").glob("*/audio.json")):
        key = manifest.parent.name
        value = verified_audio(store, key)
        if value:
            add(f"lessons/{key}/audio.m4a", value["file_digest"])
            add(f"lessons/{key}/audio.json")
            audio_count += 1
    for name in (
        "inventory.json",
        "inventory-unique.json",
        "video-hashes.json",
        "recovered-media.json",
        "partial-download-full-validation.json",
        "model-manifest.json",
        "resources-plan.json",
        "goal-audit.json",
        "teaching-image-audit.json",
        "images-ocr-report.json",
        "nested-courseware-report.json",
        "nested-full-scope-validation.json",
        "notebook-visual-audit.json",
        "notebook-source-audit.json",
        "notebook-outputs-report.json",
        "notebook-outputs-validation.json",
        "notebook-image-audit.json",
        "notebook-images-report.json",
        "image-repair-source-errors.json",
        "image-repairs-report.json",
        "tiled-image-retention-validation.json",
        "tiled-visual-production-validation.json",
        "tiled-visual-full-validation.json",
        "spreadsheet-resource-audit.json",
        "spreadsheet-index-report.json",
        "spreadsheet-index-validation.json",
        "spreadsheet-cell-value-validation.json",
        "sqlite-course-resource-audit.json",
        "sqlite-index-report.json",
        "sqlite-index-validation.json",
        "supplemental-text-audit.json",
        "supplemental-text-index-report.json",
        "supplemental-text-validation.json",
        "nested-pilot-validation.json",
        "teaching-image-production-validation.json",
        "KNOWN-EXCLUSIONS.md",
        "pdf-full-scope-validation.json",
        "slides-ocr-full-validation.json",
        "silent-demo-validation.json",
        "attachment-source-validation.json",
        "legacy-search-repair-report.json",
        "asr-batch-benchmark.json",
        "source-audio-error-audit.json",
        "source-audio-ffmpeg-crosscheck.json",
        "alternate-packet-difference-report.json",
        "alternate-absolute-origin-validation.json",
        "candidate-audio-health.json",
        "damaged-packet-pattern.json",
        "pdf-all-pages-plan.json",
        "pdf-source-reconciliation.json",
        "pdf-all-pages-retained-plan.json",
        "pdf-retained-production-validation.json",
        "pdf-ocr-report.json",
    ):
        if (source / name).exists():
            add(name)
    completed = []
    objects = destination / "objects"
    for relative, expected in sorted(assets.items()):
        store.checkpoint()
        progress("copying_evidence", completed=len(completed), total=len(assets))
        path = inside(source, relative)
        state = path.stat()
        digest = expected or file_hash(path, store.checkpoint)
        if not re.fullmatch("[a-f0-9]{64}", digest):
            raise ValueError("Invalid backup content digest")
        obj = objects / digest[:2] / digest
        obj.parent.mkdir(parents=True, exist_ok=True)
        if obj.exists():
            if (
                obj.stat().st_size != state.st_size
                or file_hash(obj, store.checkpoint) != digest
            ):
                raise RuntimeError(
                    "Existing backup object changed; preserve and investigate"
                )
        else:
            if shutil.disk_usage(destination).free < reserve + state.st_size:
                raise InterruptedError("Backup evidence reached reserved disk capacity")
            part = obj.with_name(digest + "." + uuid.uuid4().hex + ".part")
            with path.open("rb") as src, part.open("wb") as dst:
                for block in iter(lambda: src.read(8 * 1024**2), b""):
                    store.checkpoint()
                    dst.write(block)
            if file_hash(part, store.checkpoint) != digest:
                raise RuntimeError("Backup copy does not match committed evidence hash")
            after = path.stat()
            if (state.st_size, state.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise RuntimeError(
                    "Archive artifact changed during snapshot; retry after checkpoint"
                )
            part.replace(obj)
        completed.append(
            {
                "path": relative,
                "sha256": digest,
                "bytes": state.st_size,
                "object": obj.relative_to(destination).as_posix(),
            }
        )
    manifest = {
        "format": 1,
        "started": started,
        "finished": time.time(),
        "counts": counts,
        "database_sha256": file_hash(database, store.checkpoint),
        "files": completed,
        "verified_audio_files": audio_count,
        "source_videos_deleted": False,
        "complete_course_archive": False,
    }
    (snapshot / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report = verify_snapshot(snapshot, checkpoint=store.checkpoint)
    (snapshot / "verification.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    progress(
        "verified_snapshot",
        assets=len(completed),
        database_integrity=report["database_integrity"],
        complete_course_archive=False,
    )
    return {"snapshot": str(snapshot), **report}


def verify_snapshot(snapshot, *, checkpoint=lambda: None):
    snapshot = Path(snapshot).resolve()
    root = snapshot.parent.parent
    manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    database = snapshot / "knowledge.sqlite3"
    if file_hash(database, checkpoint) != manifest["database_sha256"]:
        raise RuntimeError("Snapshot database hash changed")
    checked = {}
    for row in manifest["files"]:
        checkpoint()
        inside(Path("restore-target").resolve(), row["path"])
        obj = inside(root, row["object"])
        if not obj.is_relative_to((root / "objects").resolve()):
            raise ValueError("Snapshot object outside backup pool")
        if obj not in checked:
            checked[obj] = (obj.stat().st_size, file_hash(obj, checkpoint))
        if checked[obj] != (row["bytes"], row["sha256"]):
            raise RuntimeError("Snapshot evidence object hash changed")
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok" or db.execute("PRAGMA foreign_key_check").fetchone():
            raise RuntimeError("Snapshot SQLite integrity validation failed")
        counts = {
            t: db.execute("SELECT count(*) FROM " + t).fetchone()[0] for t in TABLES
        }
        if counts != manifest["counts"]:
            raise RuntimeError("Snapshot row counts differ from captured manifest")
        available = {row["path"] for row in manifest["files"]}
        if any(
            row[0] not in available for row in db.execute("SELECT path FROM frames")
        ):
            raise RuntimeError("Snapshot is missing referenced frame evidence")
        for (raw,) in db.execute("SELECT metadata FROM document_version_metadata"):
            metadata = json.loads(raw)
            if metadata.get("image") and metadata["image"] not in available:
                raise RuntimeError("Snapshot is missing referenced document image")
            if metadata.get("media") and metadata["media"] not in available:
                raise RuntimeError("Snapshot is missing referenced document media")
    return {
        "database_integrity": integrity,
        "counts": counts,
        "asset_paths": len(manifest["files"]),
        "unique_objects": len(checked),
        "verified_audio_files": manifest["verified_audio_files"],
    }


def restore_snapshot(snapshot, destination, *, reserve_gib=32):
    if not math.isfinite(reserve_gib) or reserve_gib < 0:
        raise ValueError("Restore reserve must be finite and nonnegative")
    snapshot = Path(snapshot).resolve()
    destination = Path(destination).resolve()
    if destination.is_relative_to(snapshot.parent.parent):
        raise ValueError("Restore destination must be outside backup storage")
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Restore destination must be empty")
    manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    reserve = int(reserve_gib * 1024**3)
    ancestor = destination
    while not ancestor.exists():
        if ancestor == ancestor.parent:
            raise ValueError("Restore target volume is unavailable")
        ancestor = ancestor.parent

    def allocated(size):
        return (size + 4095) // 4096 * 4096

    database_bytes = (snapshot / "knowledge.sqlite3").stat().st_size
    required = (
        allocated(database_bytes)
        + sum(
            allocated(inside(snapshot.parent.parent, row["object"]).stat().st_size)
            for row in manifest["files"]
        )
        + 64 * 1024**2
    )
    available = shutil.disk_usage(ancestor).free
    if available < required + reserve:
        raise InterruptedError(
            f"Insufficient restore disk capacity: need {required} bytes plus {reserve} reserved; {available} available"
        )
    verify_snapshot(snapshot)
    if shutil.disk_usage(ancestor).free < required + reserve:
        raise InterruptedError(
            "Restore disk capacity changed during verification; no destination written"
        )
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(snapshot / "knowledge.sqlite3", destination / "knowledge.sqlite3")
    for row in manifest["files"]:
        source = inside(snapshot.parent.parent, row["object"])
        if (
            shutil.disk_usage(destination).free
            < allocated(source.stat().st_size) + reserve
        ):
            raise InterruptedError(
                "Restore reached reserved disk capacity; partial restore retained"
            )
        target = inside(destination, row["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        if file_hash(target) != row["sha256"]:
            raise RuntimeError("Restored evidence hash mismatch")
    if file_hash(destination / "knowledge.sqlite3") != manifest["database_sha256"]:
        raise RuntimeError("Restored database hash mismatch")
    return destination
