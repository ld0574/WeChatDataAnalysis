"""Inventory retained teaching images locally, without sending their contents."""

import hashlib
import io
import json
import os
import time
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath

from PIL import Image

from wechat_decrypt_tool.course_documents import IGNORED_PARTS

EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".svg"}


def audit_images(store):
    root = store.root
    manifest = json.loads(
        (root / "resources-manifest.json").read_text(encoding="utf-8")
    )
    results = []
    objects = {}
    nested_objects = {}
    nested_archives = []

    def image_info(data, suffix):
        value = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        if suffix == ".svg":
            return {**value, "state": "vector_raw_retained_render_audit_pending"}
        try:
            with Image.open(io.BytesIO(data)) as image:
                width, height = image.size
                frames = getattr(image, "n_frames", 1)
                image.verify()
            state = (
                "raster_ocr_candidate"
                if width * height >= 65536 and max(width, height) >= 256
                else "small_asset_raw_retained"
            )
            if frames > 1:
                state = "animated_raw_retained_motion_audit_pending"
            return {
                **value,
                "width": width,
                "height": height,
                "frames": frames,
                "state": state,
            }
        except Exception as exc:
            return {
                **value,
                "state": "image_read_error_raw_retained",
                "error_type": type(exc).__name__,
            }

    for n, row in enumerate(manifest["completed"]):
        store.checkpoint()
        relative = row["relative_path"].replace("\\", "/")
        suffix = PurePosixPath(relative).suffix.lower()
        path = (root / row["object"]).resolve()
        if not path.is_relative_to((root / "resources" / "objects").resolve()):
            raise ValueError("Image audit source outside retained resource root")
        if not path.is_file() or path.stat().st_size != row["bytes"]:
            raise RuntimeError(
                "Retained resource missing or changed during image audit"
            )
        occurrences = []
        nested = []
        if row["sha256"] in objects:
            occurrences = [{**x} for x in objects[row["sha256"]]]
            nested = nested_objects[row["sha256"]]
        elif suffix in EXTENSIONS:
            occurrences = (
                [{"source_key": "", **image_info(path.read_bytes(), suffix)}]
                if row["bytes"] <= 32 * 1024**2
                else [
                    {
                        "source_key": "",
                        "state": "image_exceeds_probe_limit_raw_retained",
                    }
                ]
            )
        elif suffix == ".zip":
            try:
                with zipfile.ZipFile(path) as archive:
                    for info in archive.infolist():
                        name = info.filename.replace("\\", "/")
                        member = PurePosixPath(name)
                        if member.suffix.lower() == ".zip" and not info.is_dir():
                            if not (
                                set(member.parts) & IGNORED_PARTS
                                or member.name.startswith("._")
                            ):
                                nested.append(
                                    {
                                        "source_key": "archive " + info.filename,
                                        "bytes": info.file_size,
                                        "state": "nested_archive_not_expanded_raw_retained",
                                    }
                                )
                            continue
                        if member.suffix.lower() not in EXTENSIONS or info.is_dir():
                            continue
                        if member.is_absolute() or ".." in member.parts:
                            occurrences.append(
                                {
                                    "source_key": "archive " + info.filename,
                                    "state": "unsafe_member_raw_container_retained",
                                }
                            )
                        elif set(
                            member.parts
                        ) & IGNORED_PARTS or member.name.startswith("._"):
                            continue
                        elif info.file_size > 32 * 1024**2:
                            occurrences.append(
                                {
                                    "source_key": "archive " + info.filename,
                                    "state": "image_exceeds_probe_limit_raw_retained",
                                }
                            )
                        else:
                            try:
                                value = image_info(
                                    archive.read(info), member.suffix.lower()
                                )
                            except Exception as exc:
                                value = {
                                    "state": "member_read_error_raw_container_retained",
                                    "error_type": type(exc).__name__,
                                }
                            occurrences.append(
                                {"source_key": "archive " + info.filename, **value}
                            )
            except Exception as exc:
                occurrences = [
                    {
                        "source_key": "",
                        "state": "container_read_error_raw_retained",
                        "error_type": type(exc).__name__,
                    }
                ]
        objects.setdefault(row["sha256"], occurrences)
        nested_objects.setdefault(row["sha256"], nested)
        source_path = str(Path(manifest["source"]) / Path(row["relative_path"]))
        nested_archives.extend(
            {"path": source_path, "container_sha256": row["sha256"], **item}
            for item in nested
        )
        for item in occurrences:
            results.append(
                {"path": source_path, "container_sha256": row["sha256"], **item}
            )
        if n % 50 == 0:
            store.write_json(
                "teaching-image-audit-progress.json",
                {
                    "stage": "scanning_retained_resources",
                    "pid": os.getpid(),
                    "resources": n + 1,
                    "total_resources": len(manifest["completed"]),
                    "image_occurrences": len(results),
                    "updated": time.time(),
                },
            )
    candidates = {x["sha256"] for x in results if x["state"] == "raster_ocr_candidate"}
    counts = dict(Counter(x["state"] for x in results))
    store.write_json(
        "teaching-image-audit.json",
        {
            "image_occurrences": results,
            "counts": counts,
            "unique_raster_ocr_candidates": len(candidates),
            "all_raw_sources_retained": True,
            "ocr_completed_by_this_audit": False,
            "nested_archives_not_expanded": nested_archives,
            "supported_archive_depth": 1,
            "updated": time.time(),
        },
    )
    store.write_json(
        "teaching-image-audit-progress.json",
        {
            "stage": "batch_finished",
            "pid": os.getpid(),
            "resources": len(manifest["completed"]),
            "image_occurrences": len(results),
            "unique_raster_ocr_candidates": len(candidates),
            "updated": time.time(),
        },
    )
    return {
        "counts": counts,
        "unique_raster_ocr_candidates": len(candidates),
        "raw_sources_retained": True,
        "nested_archive_occurrences_not_expanded": len(nested_archives),
    }
