"""Read-only, per-lesson checks of committed evidence and graph exports.

This is a structured consistency gate, not an audio/visual coverage or semantic
acceptance test. Each lesson is observed separately so a full run does not pin a
large production WAL snapshot. No model, original media, or schema lease is used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import time
from pathlib import Path

from .course_gap_asr import note_pages, window_plan
from .course_video_visual_audit import ReadOnlyArchive

MODEL = "qwen3.5:9b"
ASR_MODEL = "qwen3-asr-06b-hf/float32"


class ExportMismatch(ValueError):
    """Only fixed error codes are published; never include course content."""


def require(condition, code):
    if not condition:
        raise ExportMismatch(code)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_rows(store, key):
    with store.connect() as db:
        db.execute("BEGIN")
        video = db.execute("SELECT * FROM videos WHERE id=?", (key,)).fetchone()
        require(video is not None, "unregistered_lesson")
        result = {"video": dict(video)}
        for table, order in (
            ("segments", "n"),
            ("frames", "time,id"),
            ("notes", "window"),
        ):
            result[table] = [
                dict(row)
                for row in db.execute(
                    f"SELECT * FROM {table} WHERE video=? ORDER BY {order}", (key,)
                )
            ]
    return result


def validate_lesson(store, key, expected_source=None):
    """Freeze files/rows at both ends; reject a lesson changing during the check."""
    require(bool(re.fullmatch(r"[0-9a-f]{24}", key)), "invalid_lesson_id")
    folder = store.root / "lessons" / key
    require(folder.resolve().is_relative_to(store.root.resolve()), "unsafe_lesson_path")
    frozen = {}

    def read(name, *, optional=False):
        path = folder / name
        require(path.resolve().is_relative_to(folder.resolve()), "unsafe_export_path")
        data = path.read_bytes() if path.exists() else None
        frozen[name] = data
        require(optional or data is not None, "missing_export_file")
        return json.loads(data) if data is not None else None

    rows = read_rows(store, key)
    video, segments, frames, notes = (
        rows[k] for k in ("video", "segments", "frames", "notes")
    )
    require(
        video["status"] in ("transcribed", "transcribed_with_gaps"), "asr_incomplete"
    )
    require(not video["error"], "recorded_lesson_error")
    require(video["model"] == ASR_MODEL, "unexpected_asr_model")
    if expected_source is not None:
        require(
            (
                str(Path(video["path"]).resolve()),
                video["size"],
                video["mtime_ns"],
                video["duration"],
            )
            == (
                str(Path(expected_source["path"]).resolve()),
                expected_source["bytes"],
                expected_source["mtime_ns"],
                expected_source["duration_seconds"],
            ),
            "inventory_identity_mismatch",
        )
    require(bool(segments), "missing_segments")
    require(
        [r["n"] for r in segments] == list(range(len(segments))),
        "missing_segment_windows",
    )
    require(
        all(
            math.isfinite(r["start"])
            and math.isfinite(r["end"])
            and 0 <= r["start"] < r["end"]
            for r in segments
        ),
        "invalid_segment_clock",
    )
    capture = read("frames-completed.json")
    require(
        capture.get("step") == 30 and capture.get("complete_visual_coverage") is False,
        "capture_checkpoint_incomplete",
    )
    require(
        all(r["status"] == "ready" and r["model"] == MODEL for r in frames),
        "frame_ocr_incomplete",
    )
    gap = read("audio-gap-recovery.json", optional=True)
    binding = read("asr-gap-source.json", optional=True)
    require(gap is None or bool(gap), "empty_gap_manifest")
    require(
        (video["status"] == "transcribed_with_gaps") == (gap is not None),
        "gap_status_mismatch",
    )
    if gap:
        require(
            binding is not None
            and binding["video"] == key
            and binding["manifest_sha256"] == digest(frozen["audio-gap-recovery.json"])
            and binding["windows"] == window_plan(gap),
            "gap_binding_mismatch",
        )
    else:
        require(binding is None, "orphan_gap_binding")
    pages = note_pages(segments, gap)
    require(
        [r["window"] for r in notes] == list(range(len(pages))), "missing_note_windows"
    )
    require(
        all(r["status"] == "ready" and r["model"] == MODEL for r in notes),
        "notes_incomplete",
    )
    parsed = []
    nodes, edges = {}, []
    for row, page in zip(notes, pages):
        note = json.loads(row["data"])
        ids = {r["id"] for r in page} | {
            -r["id"] for r in frames if page[0]["start"] <= r["time"] < page[-1]["end"]
        }
        names = {node["name"] for node in note["concepts"]}
        for claim in note["concepts"] + note["relations"]:
            require(
                bool(claim["evidence"])
                and all(type(i) is int and i in ids for i in claim["evidence"]),
                "citation_outside_note_window",
            )
        for edge in note["relations"]:
            require(
                edge["from"] in names and edge["to"] in names,
                "missing_relation_endpoint",
            )
        for node in note["concepts"]:
            merged = nodes.setdefault(
                node["name"], {"name": node["name"], "explanations": [], "evidence": []}
            )
            merged["explanations"].append(node["explanation"])
            merged["evidence"] = sorted(set(merged["evidence"] + node["evidence"]))
        edges.extend(note["relations"])
        parsed.append(note)
    evidence = read("evidence.json")
    graph = read("graph.json")
    # Export runs before the producer clears error/updates its timestamp.
    stable_video = {k: v for k, v in video.items() if k not in ("error", "updated")}
    require(
        {k: v for k, v in evidence["video"].items() if k not in ("error", "updated")}
        == stable_video,
        "export_video_mismatch",
    )
    require(evidence["segments"] == segments, "export_segments_mismatch")
    require(evidence["frames"] == frames, "export_frames_mismatch")
    require(evidence["notes"] == parsed, "export_notes_mismatch")
    require(evidence["deletion_ready"] is False, "unsafe_export_deletion_claim")
    require(
        evidence.get("audio_gap_recovery") == gap
        and evidence.get("asr_gap_source") == binding,
        "export_gap_mismatch",
    )
    require(
        graph == {"video": key, "concepts": list(nodes.values()), "relations": edges},
        "graph_mismatch",
    )
    for name, data in frozen.items():
        path = folder / name
        require(
            (path.read_bytes() if path.exists() else None) == data,
            "export_changed_during_validation",
        )
    require(read_rows(store, key) == rows, "rows_changed_during_validation")
    return {
        "video": key,
        "segments": len(segments),
        "frames": len(frames),
        "note_windows": len(notes),
        "concepts": len(nodes),
        "relations": len(edges),
        "has_audio_gaps": gap is not None,
        "rows_sha256": digest(
            json.dumps(rows, sort_keys=True, ensure_ascii=False).encode()
        ),
        "files_sha256": {
            name: digest(data) if data is not None else None
            for name, data in frozen.items()
        },
    }


def validate_exports(store, video_ids=None):
    """Full scope is determined by the saved canonical audio inventory, not DB rows."""
    inventory_path = store.root / "inventory-unique.json"
    inventory_bytes = None
    sources = {}
    excluded = 0
    if video_ids is None:
        inventory_bytes = inventory_path.read_bytes()
        inventory = json.loads(inventory_bytes)
        for item in inventory["videos"]:
            if (
                item.get("error")
                or not item.get("audio_streams")
                or not item.get("duration_seconds")
            ):
                excluded += 1
                continue
            key = digest(str(Path(item["path"]).resolve()).encode())[:24]
            require(key not in sources, "duplicate_canonical_inventory_source")
            sources[key] = item
        require(bool(sources), "empty_audio_inventory")
        require(
            inventory.get("unique_readable_videos") == len(sources),
            "inventory_count_mismatch",
        )
        keys = list(sources)
    else:
        keys = list(video_ids)
        require(bool(keys) and len(keys) == len(set(keys)), "empty_or_duplicate_subset")
        require(
            all(isinstance(k, str) and re.fullmatch(r"[0-9a-f]{24}", k) for k in keys),
            "invalid_lesson_id",
        )
    completed, errors = [], []
    for key in keys:
        store.checkpoint()
        try:
            completed.append(validate_lesson(store, key, sources.get(key)))
        except ExportMismatch as exc:
            errors.append({"video": key, "code": str(exc)})
        except (OSError, ValueError, TypeError, KeyError, RuntimeError):
            errors.append(
                {"video": key, "code": "unreadable_or_invalid_export_structure"}
            )
    if inventory_bytes is not None:
        require(
            inventory_path.read_bytes() == inventory_bytes,
            "inventory_changed_during_validation",
        )
    return {
        "policy": "lesson-export-consistency-v1",
        "updated": time.time(),
        "scope": "full_canonical_audio_inventory"
        if video_ids is None
        else "explicit_lesson_subset",
        "requested": len(keys),
        "excluded_noneligible_inventory_rows": excluded,
        "inventory_sha256": digest(inventory_bytes)
        if inventory_bytes is not None
        else None,
        "completed": completed,
        "errors": errors,
        "accepted": not errors,
        "full_audio_inventory_exports_consistent": video_ids is None and not errors,
        "observation": "separate_per_lesson_snapshots_with_before_after_file_and_row_checks",
        "semantic_accuracy_verified": False,
        "complete_visual_coverage": False,
        "audio_coverage_verified": False,
        "source_deletion_allowed": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument(
        "--video",
        action="append",
        help="Explicit subset; omit for full audio inventory",
    )
    args = parser.parse_args()
    store = ReadOnlyArchive(args.root)
    report = validate_exports(store, args.video)
    from .course_knowledge import atomic_json

    name = (
        "lesson-export-validation.json"
        if args.video is None
        else "lesson-export-subset-validation.json"
    )
    atomic_json(store.root / name, report)
    print(
        json.dumps(
            {k: report[k] for k in ("scope", "requested", "accepted")}
            | {"completed": len(report["completed"]), "errors": len(report["errors"])}
        )
    )
    return 0 if report["accepted"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
