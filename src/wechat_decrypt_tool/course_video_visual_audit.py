"""Bounded native-frame audit of information omitted by 30-second sampling.

This is a diagnostic, not an additional video extractor or a semantic coverage
claim. It writes timestamps and pixel hashes only; it never sends images to a
model. Source identity is checked by recorded size/mtime, not a full-file hash.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import sqlite3
from contextlib import contextmanager
from fractions import Fraction
from pathlib import Path

POLICY = "native-rgb-window-v1/sample30-mean64x36-threshold1.5"
MAX_SECONDS = 300
MAX_FRAMES = 20_000
MAX_PIXELS = 16_000_000


class ReadOnlyArchive:
    """Read an existing WAL archive without taking a schema or writer lease."""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.path = self.root / "knowledge.sqlite3"
        if not self.path.is_file():
            raise ValueError("An existing course archive is required")
        with self.connect() as db:
            db.execute("SELECT id,path,size,mtime_ns,duration FROM videos LIMIT 0")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            yield db
        finally:
            db.close()

    def checkpoint(self):
        if (self.root / "pause.flag").exists():
            raise InterruptedError("Native-frame diagnostic paused")
        if shutil.disk_usage(self.root).free < 8 * 1024**3:
            raise InterruptedError("Native-frame diagnostic disk reserve reached")


def summarize(records):
    """Compare full RGB identities with anchors retained by the old heuristic."""
    retained = {r["rgb_sha256"] for r in records if r.get("anchor_kept")}
    omitted = [r for r in records if r["rgb_sha256"] not in retained]
    return {
        "decoded_frames": len(records),
        "unique_rgb_images": len({r["rgb_sha256"] for r in records}),
        "sample_anchors_considered": sum(r.get("anchor") is not None for r in records),
        "sample_anchors_kept": sum(bool(r.get("anchor_kept")) for r in records),
        "near_static_skips_with_different_native_pixels": sum(
            bool(r.get("anchor_skipped_with_different_rgb")) for r in records
        ),
        "frames_with_rgb_absent_from_kept_anchors": len(omitted),
        "unique_rgb_images_absent_from_kept_anchors": len(
            {r["rgb_sha256"] for r in omitted}
        ),
        "pixel_differences_are_not_semantic_novelty_proof": True,
        "complete_visual_coverage": False,
        "source_deletion_allowed": False,
    }


def audit_window(store, video, *, start=0, seconds=60):
    """Decode every native frame in a bounded interval and replay the heuristic.

    Seeking, decoder failures, missing or nonmonotonic timestamps and exhausted
    limits fail explicitly. No completed diagnostic is published on failure.
    The 50ms anchor tolerance matches capture_frames; the audit seeks only once
    and does not assert seek-by-seek/JPEG-resume equivalence with that function.
    """
    import av
    from PIL import ImageChops, ImageStat
    from .course_knowledge import atomic_json

    if not re.fullmatch(r"[a-f0-9]{24}", video):
        raise ValueError("Invalid video identity")
    if (
        type(start) is not int
        or type(seconds) is not int
        or start < 0
        or not 0 < seconds <= MAX_SECONDS
        or start % 30
    ):
        raise ValueError("Use an aligned nonnegative start and a bounded window")
    with store.connect() as db:
        row = db.execute("SELECT * FROM videos WHERE id=?", (video,)).fetchone()
    if row is None:
        raise ValueError("Unknown video")
    source = Path(row["path"])
    before = source.stat()
    if (before.st_size, before.st_mtime_ns) != (row["size"], row["mtime_ns"]):
        raise RuntimeError("Video differs from registered source identity")
    if not math.isfinite(row["duration"]) or start >= row["duration"]:
        raise ValueError("Window is outside registered video duration")

    end = min(Fraction(start + seconds), Fraction(str(row["duration"])))
    records = []
    previous_small = previous_sha = None
    target = start
    last_time = None
    boundary_reached = False
    with av.open(str(source), metadata_errors="replace") as container:
        if not container.streams.video:
            raise RuntimeError("Source has no video stream")
        stream = container.streams.video[0]
        stream.thread_count = 1
        if start:
            container.seek(
                int(Fraction(start) / stream.time_base), stream=stream, backward=True
            )
        for frame in container.decode(video=0):
            store.checkpoint()
            if frame.pts is None or frame.time_base is None:
                raise RuntimeError("Native frame has no exact timestamp")
            timestamp = frame.pts * Fraction(frame.time_base)
            if timestamp < start:
                continue
            if timestamp >= end:
                boundary_reached = True
                break
            if last_time is not None and timestamp <= last_time:
                raise RuntimeError(
                    "Native frame timestamps are not strictly increasing"
                )
            if len(records) >= MAX_FRAMES or frame.width * frame.height > MAX_PIXELS:
                raise RuntimeError("Native-frame diagnostic limit reached")
            image = frame.to_image().convert("RGB")
            sha = hashlib.sha256(
                f"RGB:{image.width}x{image.height}\0".encode() + image.tobytes()
            ).hexdigest()
            item = {
                "pts": frame.pts,
                "time_base": [frame.time_base.numerator, frame.time_base.denominator],
                "time": [timestamp.numerator, timestamp.denominator],
                "size": list(image.size),
                "rgb_sha256": sha,
            }
            if target < end and timestamp + Fraction(1, 20) >= target:
                # Matching the capture heuristic deliberately includes its small
                # resize; full RGB hashes are kept separately for comparison.
                small = image.resize((64, 36))
                mean = (
                    sum(
                        ImageStat.Stat(
                            ImageChops.difference(small, previous_small)
                        ).mean
                    )
                    / 3
                    if previous_small is not None
                    else None
                )
                keep = mean is None or mean >= 1.5
                item.update(
                    anchor=target,
                    anchor_kept=keep,
                    anchor_mean_difference=mean,
                    anchor_skipped_with_different_rgb=not keep and sha != previous_sha,
                )
                if keep:
                    previous_small, previous_sha = small, sha
                # Sparse source timelines may cross multiple requested anchors.
                # A single decoded frame is never counted as multiple images.
                target = max(target + 30, 30 * (math.floor(timestamp / 30) + 1))
            records.append(item)
            last_time = timestamp
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError("Video changed during native-frame audit")
    if not records:
        raise RuntimeError("No native frames decoded in requested interval")
    if not boundary_reached and end < Fraction(str(row["duration"])):
        raise RuntimeError("Native video ended before the requested boundary")
    result = {
        "policy": POLICY,
        "decoder": {
            "pyav": av.__version__,
            "libraries": {k: list(v) for k, v in av.library_versions.items()},
            "pixel_format": "Pillow RGB from native frame; no resizing for pixel hash",
        },
        "video": video,
        "source_identity": {"bytes": before.st_size, "mtime_ns": before.st_mtime_ns},
        "full_source_hash_verified": False,
        "requested_start_seconds": start,
        "requested_end_seconds": float(end),
        "boundary_frame_observed": boundary_reached,
        "first_time": records[0]["time"],
        "last_time": records[-1]["time"],
        "decode_at_eof_does_not_prove_source_completeness": True,
        "not_equivalent_to_seek_per_anchor_or_jpeg_resume": True,
        **summarize(records),
        "frames": records,
    }
    folder = store.root / "video-visual-audits"
    folder.mkdir(parents=True, exist_ok=True)
    atomic_json(folder / f"{video}-{start}-{seconds}.json", result)
    return result


def main():
    import argparse
    from .course_knowledge import schema_lease

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--video", required=True)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--seconds", type=int, default=60)
    args = parser.parse_args()
    store = ReadOnlyArchive(args.root)
    with schema_lease(store.root / "video-visual-audit.lock", timeout=0):
        result = audit_window(store, args.video, start=args.start, seconds=args.seconds)
    print(json.dumps({"video": args.video, **summarize(result["frames"])}))


if __name__ == "__main__":
    main()
