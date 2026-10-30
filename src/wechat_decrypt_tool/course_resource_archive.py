"""Retain portable, byte-verified course attachments without executing them."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path

BLOCK = 8 * 1024**2


def sha256(path, checkpoint):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(BLOCK), b""):
            checkpoint()
            result.update(block)
    return result.hexdigest()


def archive_resources(store, source, inventory_path, *, budget_gib=16, reserve_gib=32):
    source = Path(source).resolve()
    if store.root.resolve().is_relative_to(source):
        raise ValueError("Archive root must be outside course sources")
    inventory = json.loads(Path(inventory_path).read_text(encoding="utf-8"))
    silent = {
        str(Path(r["path"]).resolve())
        for r in inventory.get("excluded_without_audio", [])
    }
    plan_path = store.root / "resources-plan.json"
    if plan_path.exists():
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        if plan["source"] != str(source):
            raise RuntimeError("Resource plan belongs to another source root")
    else:
        files, pending = [], []
        for path in sorted(source.rglob("*")):
            store.checkpoint()
            if path.is_symlink():
                raise ValueError("Course resource symlinks require separate review")
            if not path.is_file():
                continue
            path = path.resolve()
            stat = path.stat()
            row = {
                "relative_path": str(path.relative_to(source)),
                "bytes": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
            if path.suffix.lower() == ".downloading":
                pending.append(
                    {
                        **row,
                        "reason": "partial download requires media recovery assessment",
                    }
                )
            elif path.suffix.lower() != ".mp4" or str(path) in silent:
                files.append({**row, "silent_demo": str(path) in silent})
        plan = {
            "source": str(source),
            "files": files,
            "pending": pending,
            "estimated_bytes": sum(r["bytes"] for r in files),
        }
        store.write_json("resources-plan.json", plan)
    if plan["estimated_bytes"] > budget_gib * 1024**3:
        raise InterruptedError("Portable resource estimate exceeds configured budget")
    old_path = store.root / "resources-manifest.json"
    old = json.loads(old_path.read_text(encoding="utf-8")) if old_path.exists() else {}
    cached = {r["relative_path"]: r for r in old.get("completed", [])}
    completed, errors, checked = [], [], set()
    objects = store.root / "resources" / "objects"
    temporary = store.root / "resources" / "pending"
    temporary.mkdir(parents=True, exist_ok=True)

    def progress(stage, **extra):
        store.write_json(
            "resources-progress.json",
            {
                "stage": stage,
                "pid": os.getpid(),
                "updated": time.time(),
                "completed": len(completed),
                "total": len(plan["files"]),
                "errors": len(errors),
                **extra,
            },
        )

    def save():
        value = {
            "source": str(source),
            "completed": completed,
            "errors": errors,
            "pending": plan["pending"],
            "originals_retained": True,
            "attachment_bytes": sum(r["bytes"] for r in completed),
        }
        store.write_json("resources-manifest.json", value)
        return value

    try:
        for row in plan["files"]:
            store.checkpoint()
            try:
                path = (source / row["relative_path"]).resolve()
                if not path.is_relative_to(source) or path.is_symlink():
                    raise ValueError("Resource source outside frozen course root")
                state = path.stat()
                if (state.st_size, state.st_mtime_ns) != (
                    row["bytes"],
                    row["mtime_ns"],
                ):
                    raise RuntimeError("Resource source changed since frozen plan")
                progress("copying", current=row["relative_path"])
                prior = cached.get(row["relative_path"])
                if prior:
                    if any(
                        prior[k] != row[k] for k in ("bytes", "mtime_ns", "silent_demo")
                    ):
                        raise RuntimeError(
                            "Frozen resource identity differs from saved archive"
                        )
                    obj = (store.root / prior["object"]).resolve()
                    if not obj.is_relative_to(objects.resolve()):
                        raise ValueError("Resource object outside archive objects root")
                    if obj not in checked:
                        if (
                            obj.stat().st_size != row["bytes"]
                            or sha256(obj, store.checkpoint) != prior["sha256"]
                        ):
                            raise RuntimeError(
                                "Archived resource object changed; preserve for review"
                            )
                        checked.add(obj)
                    completed.append(prior)
                    continue
                part = temporary / (
                    hashlib.sha256(row["relative_path"].encode()).hexdigest() + ".part"
                )
                digest = hashlib.sha256()
                copied = 0
                with path.open("rb") as src, part.open("wb") as dst:
                    for block in iter(lambda: src.read(BLOCK), b""):
                        store.checkpoint()
                        if shutil.disk_usage(
                            store.root
                        ).free < reserve_gib * 1024**3 + len(block):
                            raise InterruptedError(
                                "Portable resource archive reached reserved disk space"
                            )
                        copied += len(block)
                        if copied > row["bytes"]:
                            raise RuntimeError("Resource grew during copy")
                        digest.update(block)
                        dst.write(block)
                    dst.flush()
                    os.fsync(dst.fileno())
                after = path.stat()
                if copied != row["bytes"] or (after.st_size, after.st_mtime_ns) != (
                    state.st_size,
                    state.st_mtime_ns,
                ):
                    raise RuntimeError("Resource changed during copy")
                raw = digest.hexdigest()
                if sha256(part, store.checkpoint) != raw:
                    raise RuntimeError(
                        "Copied resource SHA-256 differs from source stream"
                    )
                obj = objects / raw[:2] / raw
                obj.parent.mkdir(parents=True, exist_ok=True)
                if obj.exists():
                    if (
                        obj.stat().st_size != copied
                        or sha256(obj, store.checkpoint) != raw
                    ):
                        raise RuntimeError(
                            "Existing content-addressed resource is corrupt"
                        )
                    part.unlink()
                else:
                    part.replace(obj)
                checked.add(obj)
                completed.append(
                    {
                        **row,
                        "sha256": raw,
                        "object": obj.relative_to(store.root).as_posix(),
                    }
                )
            except InterruptedError:
                raise
            except Exception as exc:
                errors.append(
                    {
                        "relative_path": row["relative_path"],
                        "error": type(exc).__name__ + ": " + str(exc),
                    }
                )
            save()
        value = save()
        progress("finished_with_errors" if errors else "batch_finished")
        return value
    except (InterruptedError, KeyboardInterrupt):
        save()
        progress("paused")
        raise
