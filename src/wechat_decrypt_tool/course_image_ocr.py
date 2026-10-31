"""Hash-bound static teaching-image OCR, using retained sources and local inference."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import time
import zipfile
from pathlib import Path

from .course_attachments import digest, normalized_original, safe_member
from .course_frame_ocr import describe_retained_image
from .course_pdf_ocr import local_model_digest, page_gpu_lease

MAX_INPUT = 32 * 1024**2
MAX_PIXELS = 40_000_000
IMAGE_BUDGET = 4 * 1024**3
RESERVE = 32 * 1024**3


def retained_image_bytes(store, row, resources, checked):
    """Resolve lexically against the portable manifest, never the course directory."""
    key = normalized_original(row["path"])
    resource = resources.get(key)
    if resource is None or resource["sha256"] != row["container_sha256"]:
        raise ValueError("Image source is not bound to the retained resource manifest")
    path = (store.root / resource["object"]).resolve()
    if not path.is_relative_to((store.root / "resources" / "objects").resolve()):
        raise ValueError("Image source object outside retained resource root")
    state = path.stat()
    identity = (str(path), state.st_size, state.st_mtime_ns)
    if state.st_size != resource["bytes"] or (
        identity not in checked and digest(path) != resource["sha256"]
    ):
        raise RuntimeError("Retained image container changed; preserve evidence")
    checked.add(identity)
    source_key = row["source_key"]
    if source_key:
        if not source_key.startswith("archive "):
            raise ValueError("Unsupported teaching image source locator")
        member = safe_member(source_key[len("archive ") :])
        with zipfile.ZipFile(path) as archive:
            matches = [
                item
                for item in archive.infolist()
                if item.filename.replace("\\", "/") == member
            ]
            if len(matches) != 1 or matches[0].is_dir():
                raise ValueError("Teaching image archive member missing or ambiguous")
            if matches[0].file_size > MAX_INPUT:
                raise ValueError("Teaching image exceeds bounded input size")
            raw = archive.read(matches[0])
    else:
        if state.st_size > MAX_INPUT:
            raise ValueError("Teaching image exceeds bounded input size")
        raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != row["sha256"]:
        raise RuntimeError("Teaching image differs from audited revision")
    after = path.stat()
    if (state.st_size, state.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError("Teaching image container changed during read")
    return raw


def prepare_image(store, row, raw, used):
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(raw)) as image:
        if getattr(image, "n_frames", 1) != 1:
            raise ValueError("Animated images require motion coverage, not static OCR")
        if image.width * image.height > MAX_PIXELS:
            raise ValueError("Teaching image exceeds bounded pixel size")
        image = ImageOps.exif_transpose(image)
        # Preserve transparent diagram labels on a readable white background.
        image = Image.alpha_composite(
            Image.new("RGBA", image.size, "white"), image.convert("RGBA")
        ).convert("RGB")
        encoded = io.BytesIO()
        image.save(encoded, format="PNG")
    value = encoded.getvalue()
    sha = hashlib.sha256(value).hexdigest()
    path = (
        store.root
        / "document-visuals"
        / "teaching-images"
        / row["sha256"]
        / "image.png"
    )
    if path.exists():
        if digest(path) != sha:
            raise RuntimeError("Retained teaching image changed; preserve evidence")
    else:
        if used + len(value) > IMAGE_BUDGET or shutil.disk_usage(
            store.root
        ).free < RESERVE + len(value):
            raise InterruptedError("Teaching image budget or disk reserve reached")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(value)
        used += len(value)
    return str(path.relative_to(store.root)), sha, used


def run_image_ocr(store, audit_path, model, chat, lease, *, limit=0, wait=False):
    audit = json.loads(Path(audit_path).read_text(encoding="utf-8"))
    rows = [
        r for r in audit["image_occurrences"] if r["state"] == "raster_ocr_candidate"
    ]
    manifest = json.loads(
        (store.root / "resources-manifest.json").read_text(encoding="utf-8")
    )
    resources = {
        normalized_original(
            manifest["source"] + "/" + safe_member(r["relative_path"])
        ): r
        for r in manifest["completed"]
    }
    model_digest = local_model_digest(model)
    extractor = (
        "teaching-image-vision-v1-overlap64-depth3/" + model + "@" + model_digest
    )
    image_root = store.root / "document-visuals" / "teaching-images"
    used = sum(p.stat().st_size for p in image_root.glob("*/image.png"))
    completed, errors = [], []
    seen, checked = set(), set()
    prepared, descriptions = {}, {}
    deferred = False

    def progress(stage, **extra):
        store.write_json(
            "images-ocr-progress.json",
            {
                "stage": stage,
                "pid": __import__("os").getpid(),
                "updated": time.time(),
                "processed": len(completed),
                "errors": len(errors),
                "total": len(rows),
                "unique_images_described_this_pass": len(descriptions),
                **extra,
            },
        )

    for row in rows:
        store.checkpoint()
        identity = (row["path"], row["source_key"])
        if identity in seen:
            continue
        seen.add(identity)
        if limit and len(completed) + len(errors) >= limit:
            break
        progress("checking", source_digest=row["sha256"])
        try:
            raw = retained_image_bytes(store, row, resources, checked)
            if row["sha256"] not in prepared:
                image, image_digest, used = prepare_image(store, row, raw, used)
                prepared[row["sha256"]] = (image, image_digest)
            image, image_digest = prepared[row["sha256"]]
            source_key = "image visual " + row["source_key"]
            if (
                store.document_revision(
                    row["path"], source_key, row["sha256"], extractor
                )
                is not None
            ):
                completed.append(
                    {
                        "path": row["path"],
                        "source_key": row["source_key"],
                        "cached": True,
                    }
                )
                continue
            if row["sha256"] not in descriptions:
                try:
                    with page_gpu_lease(store, lease, progress, wait):
                        progress("gpu_ocr", source_digest=row["sha256"])
                        descriptions[row["sha256"]] = describe_retained_image(
                            store,
                            image,
                            model,
                            chat,
                            expected_model_digest=model_digest,
                        )
                    if wait:
                        time.sleep(0.35)
                except RuntimeError as exc:
                    if type(exc).__name__ == "GpuBusyError":
                        deferred = True
                        break
                    raise
            body = (
                "教学图片转录与图示解读（模型草稿，需对照原图复核）\n"
                + descriptions[row["sha256"]]
            )
            store.save_document_versions(
                [
                    {
                        "path": row["path"],
                        "source_key": source_key,
                        "raw_digest": row["sha256"],
                        "extractor": extractor,
                        "chunks": [
                            (source_key + " chars " + str(n), body[n : n + 3000])
                            for n in range(0, len(body), 3000)
                        ],
                        "metadata": {
                            "parent_source_key": row["source_key"],
                            "parent_raw_digest": row["sha256"],
                            "image": image,
                            "image_digest": image_digest,
                            "model": model,
                            "model_digest": model_digest,
                            "review_required": True,
                            "static_image_only": True,
                            "raw_source_retained": True,
                        },
                    }
                ]
            )
            completed.append(
                {"path": row["path"], "source_key": row["source_key"], "image": image}
            )
        except InterruptedError:
            progress("paused")
            raise
        except Exception as exc:
            errors.append(
                {
                    "path": row["path"],
                    "source_key": row["source_key"],
                    "error": type(exc).__name__ + ": " + str(exc),
                }
            )
        store.write_json(
            "images-ocr-report.json",
            {
                "completed": completed,
                "errors": errors,
                "requested": len(rows),
                "review_required": True,
                "limit": limit,
            },
        )
    result = {
        "completed": completed,
        "errors": errors,
        "requested": len(rows),
        "deferred_gpu": deferred,
        "review_required": True,
        "limit": limit,
    }
    store.write_json("images-ocr-report.json", result)
    progress(
        "deferred_gpu"
        if deferred
        else "finished_with_errors"
        if errors
        else "batch_finished"
    )
    return result
