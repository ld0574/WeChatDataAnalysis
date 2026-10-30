"""Retain complete silent demonstrations and index sampled screen evidence locally."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .course_audio_archive import file_digest


def retained_demo(store, key):
    if not re.fullmatch("[a-f0-9]{24}", key):
        return None
    manifest = store.root / "resources-manifest.json"
    if not manifest.exists():
        return None
    with store.connect() as db:
        video = db.execute(
            "SELECT path,size,mtime_ns FROM videos WHERE id=?", (key,)
        ).fetchone()
    if not video:
        return None
    resources = json.loads(manifest.read_text(encoding="utf-8"))
    original = Path(resources["source"]).resolve()
    for row in resources["completed"]:
        if (
            not row.get("silent_demo")
            or str((original / row["relative_path"]).resolve()) != video["path"]
        ):
            continue
        if (row["bytes"], row["mtime_ns"]) != (video["size"], video["mtime_ns"]):
            return None
        path = (store.root / row["object"]).resolve()
        if not path.is_relative_to((store.root / "resources" / "objects").resolve()):
            raise ValueError("Silent demo object outside retained evidence root")
        if path.stat().st_size != row["bytes"] or file_digest(path) != row["sha256"]:
            raise RuntimeError(
                "Retained silent demo bytes changed; preserve and investigate"
            )
        return {**row, "video": key, "duration_source": "frozen media inventory"}
    return None


def validate_complete_video(path, declared_duration, checkpoint):
    import av

    count = 0
    first = None
    end = 0
    previous = None
    with av.open(str(path), metadata_errors="replace") as media:
        if media.streams.audio or len(media.streams.video) != 1:
            raise ValueError("Expected one silent video stream")
        stream = media.streams.video[0]
        step = float(1 / stream.average_rate) if stream.average_rate else 0.05
        for frame in media.decode(video=0):
            if count % 50 == 0:
                checkpoint()
            if frame.time is None or (previous is not None and frame.time < previous):
                raise ValueError("Invalid silent demo frame timeline")
            if first is None:
                first = frame.time
            previous = frame.time
            end = frame.time + step
            count += 1
    if not count or first > 0.5 or abs(end - declared_duration) > 1:
        raise ValueError("Silent demo decoded duration differs from inventory")
    return {
        "frames_decoded": count,
        "first_seconds": first,
        "end_seconds": end,
        "complete_raw_motion_retained": True,
        "sampled_ocr_is_not_complete_visual_coverage": True,
    }


def archive_silent_demos(store, inventory_path, model):
    from .course_knowledge import capture_frames, enrich, export_review

    inventory = json.loads(Path(inventory_path).read_text(encoding="utf-8"))
    rows = inventory.get("excluded_without_audio", [])
    completed, errors = [], []
    original_progress = store.progress

    def progress(stage, **extra):
        store.write_json(
            "silent-demo-progress.json",
            {
                "stage": stage,
                "updated": time.time(),
                "pid": __import__("os").getpid(),
                "completed": len(completed),
                "total": len(rows),
                "errors": len(errors),
                **extra,
            },
        )

    store.progress = progress
    try:
        for item in rows:
            store.checkpoint()
            key = None
            try:
                if (
                    item.get("error")
                    or item.get("audio_streams")
                    or not item.get("video_streams")
                ):
                    raise ValueError("Invalid silent demo inventory entry")
                key = store.register(item)
                progress("validating_full_video", video=key)
                retained = retained_demo(store, key)
                if retained is None:
                    raise RuntimeError(
                        "Archive original silent demo with resources-archive first"
                    )
                folder = store.root / "lessons" / key
                folder.mkdir(parents=True, exist_ok=True)
                record = folder / "silent-demo.json"
                if record.exists():
                    validation = json.loads(record.read_text(encoding="utf-8"))
                    if validation["sha256"] != retained["sha256"]:
                        raise RuntimeError("Committed silent demo identity changed")
                else:
                    validation = {
                        **validate_complete_video(
                            store.root / retained["object"],
                            item["duration_seconds"],
                            store.checkpoint,
                        ),
                        "video": key,
                        "sha256": retained["sha256"],
                        "retained_object": retained["object"],
                    }
                    store.write_json(str(record.relative_to(store.root)), validation)
                capture_frames(store, key, step=1)
                store.save_document_versions(
                    [
                        {
                            "path": item["path"],
                            "source_key": "silent demo",
                            "raw_digest": retained["sha256"],
                            "extractor": "silent-demo-v1",
                            "chunks": [
                                (
                                    "silent demo metadata",
                                    "无声操作演示："
                                    + item["title"]
                                    + "。完整原片与运动画面已按 SHA-256 保留；截图文字为抽样识别，需对照原片。",
                                )
                            ],
                            "metadata": {
                                "video": key,
                                "media": retained["object"],
                                "media_digest": retained["sha256"],
                            },
                        }
                    ]
                )
                enrich(store, key, model, cooperative=True)
                with store.connect() as db:
                    db.execute(
                        "UPDATE videos SET status='visual_only',error='',updated=? WHERE id=?",
                        (time.time(), key),
                    )
                export_review(store, key)
                completed.append(
                    {
                        "video": key,
                        "sha256": retained["sha256"],
                        "frames_decoded": validation["frames_decoded"],
                    }
                )
            except InterruptedError:
                progress("paused", video=key)
                raise
            except Exception as exc:
                if key:
                    with store.connect() as db:
                        db.execute(
                            "UPDATE videos SET status='visual_error',error=?,updated=? WHERE id=?",
                            (type(exc).__name__ + ": " + str(exc), time.time(), key),
                        )
                errors.append(
                    {
                        "path": item["path"],
                        "error": type(exc).__name__ + ": " + str(exc),
                    }
                )
            store.write_json(
                "silent-demo-report.json",
                {"completed": completed, "errors": errors, "total": len(rows)},
            )
        progress("finished_with_errors" if errors else "batch_finished")
        return {"completed": completed, "errors": errors, "total": len(rows)}
    finally:
        store.progress = original_progress
