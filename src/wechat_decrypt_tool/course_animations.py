"""Full retained animation-frame evidence; corrupt tails and unknown timing stay explicit."""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
import shutil
import tempfile
import time
from pathlib import Path

POLICY = "pillow-full-composited-rgba-frames-v1/no-truncated-decode/no-frame-sampling"
BUDGET = 4 * 1024**3
RESERVE = 32 * 1024**3
FORMATS = {"GIF": "gif", "WEBP": "webp", "PNG": "png", "TIFF": "tiff"}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def pixel_digest(image):
    return sha(str(image.size).encode("ascii") + image.tobytes())


def prepare_animation(store, raw, expected):
    from PIL import Image, ImageFile, __version__
    from .course_knowledge import atomic_json, schema_lease

    if len(raw) > 32 * 1024**2 or sha(raw) != expected:
        raise ValueError("Animation input byte bound or frozen hash differs")
    if ImageFile.LOAD_TRUNCATED_IMAGES:
        raise RuntimeError("Lenient truncated-image decoding is forbidden")
    directory = store.root / "document-animations"
    directory.mkdir(exist_ok=True)
    manifest = directory / (expected + ".json")
    with schema_lease(store.root / "animation-native-assets.lock", timeout=60):
        if manifest.exists():
            return animation_evidence(store, expected, rehash=True)
        used = sum(p.stat().st_size for p in directory.rglob("*") if p.is_file())

        def retain(relative, data):
            nonlocal used
            path = directory / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                if sha(path.read_bytes()) != sha(data):
                    raise RuntimeError("Immutable animation asset changed")
            else:
                if used + len(data) > BUDGET or shutil.disk_usage(
                    store.root
                ).free < RESERVE + len(data):
                    raise InterruptedError(
                        "Animation asset budget or disk reserve reached"
                    )
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(
                        dir=path.parent, delete=False
                    ) as stream:
                        temporary = Path(stream.name)
                        stream.write(data)
                    temporary.replace(path)
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
                used += len(data)
            return {
                "file": str(path.relative_to(store.root)).replace("\\", "/"),
                "sha256": sha(data),
                "bytes": len(data),
            }

        with Image.open(io.BytesIO(raw)) as image:
            if (
                image.format not in FORMATS
                or image.n_frames < 2
                or image.n_frames > 10000
                or image.width * image.height > 64000000
            ):
                raise ValueError("Unsupported animation format/frame/pixel bounds")
            total = image.n_frames
            original = retain("sources/" + expected + "." + FORMATS[image.format], raw)
            frames, errors, assets = [], [], {}
            default_image = bool(image.info.get("default_image"))
            loop = image.info.get("loop")
            cursor = 0
            for n in range(total):
                store.checkpoint()
                try:
                    image.seek(n)
                    rgba = image.convert("RGBA")
                    digest = pixel_digest(rgba)
                    if digest not in assets:
                        encoded = io.BytesIO()
                        rgba.save(encoded, format="PNG")
                        assets[digest] = retain(
                            "frames/" + digest + ".png", encoded.getvalue()
                        )
                    delay = image.info.get("duration")
                    if delay is not None and (
                        not isinstance(delay, (int, float))
                        or not math.isfinite(delay)
                        or delay < 0
                    ):
                        raise ValueError("Invalid recorded animation frame duration")
                    poster = default_image and n == 0
                    start = None if poster else cursor
                    if not poster:
                        cursor = (
                            cursor + delay
                            if cursor is not None and delay is not None
                            else None
                        )
                    frames.append(
                        {
                            "index": n,
                            "poster": poster,
                            "start_ms": start,
                            "end_ms": None if poster else cursor,
                            "duration_ms": delay,
                            "size": list(rgba.size),
                            "rgba_sha256": digest,
                            "image": assets[digest],
                            "disposal": getattr(
                                image, "disposal_method", image.info.get("disposal")
                            ),
                            "blend": image.info.get("blend"),
                        }
                    )
                except (OSError, EOFError, ValueError) as exc:
                    errors.append(
                        {
                            "frame": n,
                            "error_type": type(exc).__name__,
                            "reason": str(exc),
                        }
                    )
                    break
                if n % 20 == 0:
                    store.write_json(
                        "animation-native-progress.json",
                        {
                            "stage": "decoding_every_source_frame",
                            "source_sha256": expected,
                            "decoded_frames": len(frames),
                            "declared_frames": total,
                            "updated": time.time(),
                        },
                    )
        value = {
            "policy": POLICY,
            "source_sha256": expected,
            "source_format": image.format,
            "original": original,
            "decoder": "Pillow/" + __version__,
            "pixel_digest_encoding": "UTF8 Python (width, height) tuple followed by composited RGBA8 bytes",
            "declared_frames": total,
            "frames": frames,
            "errors": errors,
            "complete_frame_decode": len(frames) == total and not errors,
            "complete_recorded_timing": cursor is not None
            and len(frames) == total
            and not errors,
            "retained_duration_ms": cursor,
            "loop": loop,
            "default_image": default_image,
            "frames_not_sampled_or_interpolated": True,
            "browser_minimum_delay_and_loop_behaviour_not_assumed": True,
            "source_deletion_allowed": False,
        }
        atomic_json(manifest, value)
        return animation_evidence(store, expected, rehash=True)


def animation_evidence(store, digest, *, rehash=False):
    from PIL import Image

    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        return None
    directory = store.root / "document-animations"
    path = directory / (digest + ".json")
    if not path.exists():
        return None
    value = json.loads(path.read_text("utf-8"))
    if (
        value.get("policy") != POLICY
        or value.get("source_sha256") != digest
        or value.get("source_format") not in FORMATS
        or value.get("frames_not_sampled_or_interpolated") is not True
    ):
        raise RuntimeError("Animation provenance policy differs")
    frames = value["frames"]
    if (
        [f["index"] for f in frames] != list(range(len(frames)))
        or len(frames) > value["declared_frames"]
        or value["complete_frame_decode"]
        != (len(frames) == value["declared_frames"] and not value["errors"])
    ):
        raise RuntimeError("Animation frame coverage inventory differs")
    cursor = 0
    seen = set()
    for asset in [value["original"], *[f["image"] for f in frames]]:
        relative = asset["file"]
        path = (store.root / relative).resolve()
        if (
            not path.is_relative_to(directory.resolve())
            or not path.is_file()
            or path.stat().st_size != asset["bytes"]
        ):
            raise RuntimeError("Animation asset is missing or unsafe")
        if (
            rehash
            and relative not in seen
            and sha(path.read_bytes()) != asset["sha256"]
        ):
            raise RuntimeError("Animation asset hash differs")
        seen.add(relative)
    if value["original"]["sha256"] != digest:
        raise RuntimeError("Animation raw source hash differs")
    if (
        value["original"]["file"]
        != "document-animations/sources/"
        + digest
        + "."
        + FORMATS[value["source_format"]]
    ):
        raise RuntimeError("Animation raw source locator differs")
    if not value["complete_frame_decode"] and (
        not value["errors"] or value["errors"][0]["frame"] != len(frames)
    ):
        raise RuntimeError(
            "Animation missing-frame error is not bound to the decoded prefix"
        )
    pixel_checks = set()
    for frame in frames:
        if (
            frame["image"]["file"]
            != "document-animations/frames/" + frame["rgba_sha256"] + ".png"
        ):
            raise RuntimeError("Animation frame locator differs")
        poster = value["default_image"] and frame["index"] == 0
        delay = frame["duration_ms"]
        if delay is not None and (
            not isinstance(delay, (int, float)) or not math.isfinite(delay) or delay < 0
        ):
            raise RuntimeError("Animation recorded timing is invalid")
        expected_start = None if poster else cursor
        if not poster:
            cursor = (
                cursor + delay if cursor is not None and delay is not None else None
            )
        if (
            frame["poster"] != poster
            or frame["start_ms"] != expected_start
            or frame["end_ms"] != (None if poster else cursor)
        ):
            raise RuntimeError("Animation recorded timeline differs")
        if rehash and frame["rgba_sha256"] not in pixel_checks:
            with Image.open(store.root / frame["image"]["file"]) as image:
                if (
                    image.mode != "RGBA"
                    or list(image.size) != frame["size"]
                    or pixel_digest(image) != frame["rgba_sha256"]
                ):
                    raise RuntimeError("Animation composited frame pixels differ")
            pixel_checks.add(frame["rgba_sha256"])
    if value["retained_duration_ms"] != cursor or value["complete_recorded_timing"] != (
        cursor is not None and value["complete_frame_decode"]
    ):
        raise RuntimeError("Animation total recorded timing differs")
    return value


def index_animations(store, audit_path, model, chat, lease, *, limit=0, wait=True):
    from .course_attachments import normalized_original
    from .course_image_ocr import retained_image_bytes
    from .course_tiled_ocr import (
        POLICY as TILE_POLICY,
        describe_tile,
        local_model_digest,
        retain_tiles,
    )

    audit = json.loads(Path(audit_path).read_text("utf-8"))
    targets = [
        x
        for x in audit["image_occurrences"]
        if x["state"] == "animated_raw_retained_motion_audit_pending"
    ]
    resources = json.loads((store.root / "resources-manifest.json").read_text("utf-8"))
    resources = {
        normalized_original(resources["source"] + "/" + x["relative_path"]): x
        for x in resources["completed"]
    }
    model_digest = local_model_digest(model)
    extractor = (
        "course-animation-vision-v1/"
        + POLICY
        + "/"
        + TILE_POLICY
        + "/"
        + model
        + "@"
        + model_digest
    )
    completed, errors, manifests, checked, seen = [], [], {}, set(), set()

    def progress(stage, **extra):
        store.write_json(
            "animation-ocr-progress.json",
            {
                "stage": stage,
                "pid": __import__("os").getpid(),
                "processed": len(completed),
                "errors": len(errors),
                "total": len(targets),
                "updated": time.time(),
                **extra,
            },
        )

    for target in targets:
        if limit and len(completed) + len(errors) >= limit:
            break
        store.checkpoint()
        identity = (target["path"], target["source_key"])
        if identity in seen:
            raise ValueError("Duplicate animation source identity")
        seen.add(identity)
        source_digest = target["sha256"]
        try:
            progress("retaining_every_frame", source_sha256=source_digest)
            if source_digest not in manifests:
                raw = retained_image_bytes(store, target, resources, checked)
                manifests[source_digest] = prepare_animation(store, raw, source_digest)
            manifest = manifests[source_digest]
            manifest_path = (
                store.root / "document-animations" / (source_digest + ".json")
            )
            groups = {}
            for frame in manifest["frames"]:
                groups.setdefault(frame["rgba_sha256"], []).append(frame)
            tiles_done = 0
            for frames in groups.values():
                first = frames[0]
                png = store.root / first["image"]["file"]
                tiles = retain_tiles(store, png.read_bytes(), first["image"]["sha256"])
                for tile in tiles:
                    key = (
                        "animation visual "
                        + target["source_key"]
                        + " pixels "
                        + first["rgba_sha256"]
                        + " tile "
                        + "-".join(map(str, tile["box"]))
                    )
                    if (
                        store.document_revision(
                            target["path"], key, source_digest, extractor
                        )
                        is None
                    ):
                        content, inference = describe_tile(
                            store,
                            tile,
                            model,
                            chat,
                            lease,
                            model_digest=model_digest,
                            progress=progress,
                            wait=wait,
                        )
                        occurrences = [
                            {
                                k: f[k]
                                for k in (
                                    "index",
                                    "poster",
                                    "start_ms",
                                    "end_ms",
                                    "duration_ms",
                                    "disposal",
                                    "blend",
                                )
                            }
                            for f in frames
                        ]
                        metadata = {
                            **tile,
                            **inference,
                            "parent_source_key": target["source_key"],
                            "parent_raw_digest": source_digest,
                            "model": model,
                            "model_digest": model_digest,
                            "review_required": True,
                            "animation_manifest": str(
                                manifest_path.relative_to(store.root)
                            ).replace("\\", "/"),
                            "animation_manifest_digest": sha(
                                manifest_path.read_bytes()
                            ),
                            "animation_source_sha256": source_digest,
                            "animation_frame_occurrences": occurrences,
                            "complete_source_frame_decode": manifest[
                                "complete_frame_decode"
                            ],
                            "animation_format": manifest["source_format"],
                            "media": manifest["original"]["file"],
                            "media_digest": source_digest,
                        }
                        body = (
                            "动画逐帧画面转录与解读（模型草稿，须复核；未推断相邻帧间动作）\n"
                            + json.dumps(occurrences, ensure_ascii=False)
                            + "\n"
                            + content
                        )
                        store.save_document_versions(
                            [
                                {
                                    "path": target["path"],
                                    "source_key": key,
                                    "raw_digest": source_digest,
                                    "extractor": extractor,
                                    "chunks": [
                                        (key + " chars " + str(n), body[n : n + 3000])
                                        for n in range(0, len(body), 3000)
                                    ],
                                    "metadata": metadata,
                                }
                            ]
                        )
                    tiles_done += 1
            completed.append(
                {
                    "path": target["path"],
                    "source_key": target["source_key"],
                    "source_sha256": source_digest,
                    "declared_frames": manifest["declared_frames"],
                    "retained_frames": len(manifest["frames"]),
                    "unique_composited_frames": len(groups),
                    "tile_occurrences": tiles_done,
                    "complete_frame_decode": manifest["complete_frame_decode"],
                    "source_errors": manifest["errors"],
                }
            )
        except InterruptedError:
            progress("paused")
            raise
        except Exception as exc:
            errors.append(
                {
                    "path": target["path"],
                    "source_key": target["source_key"],
                    "source_sha256": source_digest,
                    "error_type": type(exc).__name__,
                    "reason": str(exc),
                }
            )
        store.write_json(
            "animation-ocr-report.json",
            {
                "completed": completed,
                "errors": errors,
                "requested": len(targets),
                "limit": limit,
                "full_requested_scope_attempted": not limit
                and len(completed) + len(errors) == len(targets),
            },
        )
    progress("finished_with_errors" if errors else "batch_finished", limit=limit)
    return {"completed": completed, "errors": errors, "requested": len(targets)}


def main():
    import argparse
    from .course_knowledge import KnowledgeStore, gpu_lease, local_chat, schema_lease

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--model", default="qwen3.5:9b")
    args = parser.parse_args()
    store = KnowledgeStore(args.root)
    with schema_lease(store.root / "animation-ocr.lock", timeout=0):
        result = index_animations(store, args.audit, args.model, local_chat, gpu_lease)
    print(
        json.dumps(
            {"completed": len(result["completed"]), "errors": len(result["errors"])}
        )
    )


if __name__ == "__main__":
    main()
