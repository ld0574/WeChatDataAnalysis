"""ASR plans and review links for explicitly incomplete, independently timed audio."""

from __future__ import annotations

import html
import json
import math
import wave
from fractions import Fraction

from .course_audio_archive import file_digest
from .course_gap_audio import RATE, gap_evidence

NOTICE = (
    "原音轨损坏；仅转写独立恢复的可读区间，缺失区间未填补，不能据此认定课程音频完整。"
)
METHOD = "gap-clips-absolute-source-clock-v1"


def window_plan(value, seconds=25):
    """Use integer sample offsets, restarting at every clip without bridging gaps."""
    count = int(seconds * RATE)
    if count <= 0 or count != seconds * RATE:
        raise ValueError("ASR window size must be a positive whole sample count")
    plan = []
    for index, clip in enumerate(value["clips"]):
        origin = Fraction(*clip["start_rational"])
        for offset in range(0, clip["samples"], count):
            samples = min(count, clip["samples"] - offset)
            plan.append(
                {
                    "n": len(plan),
                    "clip": index,
                    "file": clip["file"],
                    "offset": offset,
                    "samples": samples,
                    "start": float(origin + Fraction(offset, RATE)),
                    "end": float(origin + Fraction(offset + samples, RATE)),
                }
            )
    return plan


def validate_segments(rows, plan, *, complete=False):
    expected = {row["n"]: row for row in plan}
    seen = set()
    for row in rows:
        target = expected.get(row["n"])
        if (
            target is None
            or row["n"] in seen
            or not math.isfinite(row["start"])
            or not math.isfinite(row["end"])
            or abs(row["start"] - target["start"]) > 1e-7
            or abs(row["end"] - target["end"]) > 1e-7
        ):
            raise RuntimeError("Gap ASR windows differ from the immutable clip plan")
        seen.add(row["n"])
    if complete and seen != set(expected):
        raise RuntimeError("Gap ASR has missing planned windows")


def bind_source(store, video, rows, *, seconds=25):
    """Reject unbound old windows and keep every restart on exactly the same plan."""
    key = video["id"]
    folder = store.root / "lessons" / key
    path = folder / "asr-gap-source.json"
    value = gap_evidence(store, key, rehash=True)
    if value is None:
        if path.exists():
            raise RuntimeError("Gap ASR binding exists but recovery is missing")
        return None
    if (folder / "asr-audio-source.json").exists() or (
        folder / "audio-recovery.json"
    ).exists():
        raise RuntimeError("Conflicting alternate and gap ASR evidence")
    if (video["path"], video["size"], video["mtime_ns"]) != (
        value["source"],
        value["source_size"],
        value["source_mtime_ns"],
    ):
        raise RuntimeError("Gap ASR source differs from recovery provenance")
    plan = window_plan(value, seconds)
    binding = {
        "method": METHOD,
        "video": key,
        "manifest_sha256": file_digest(folder / "audio-gap-recovery.json"),
        "source_sha256": value["source_digest"],
        "windows": plan,
        "notice": NOTICE,
        "complete_original_audio_verified": False,
        "recovered_seconds": sum(c["samples"] for c in value["clips"]) / RATE,
    }
    if path.exists():
        if json.loads(path.read_text("utf-8")) != binding:
            raise RuntimeError("Committed gap ASR binding differs")
    elif rows:
        raise RuntimeError("Existing ASR windows have no gap-source binding")
    else:
        store.write_json(str(path.relative_to(store.root)), binding)
    validate_segments(rows, plan)
    return binding


def audio_windows(store, key, binding):
    """Read retained PCM directly; each yielded array belongs to exactly one clip."""
    import numpy as np

    folder = store.root / "lessons" / key
    reader = None
    current = None
    try:
        for row in binding["windows"]:
            store.checkpoint()
            if row["file"] != current:
                if reader is not None:
                    reader.close()
                reader = wave.open(str(folder / row["file"]), "rb")
                current = row["file"]
            reader.setpos(row["offset"])
            data = reader.readframes(row["samples"])
            if len(data) != row["samples"] * 2:
                raise RuntimeError("Gap ASR clip ended before its planned samples")
            yield (
                row["n"],
                row["start"],
                np.frombuffer(data, dtype="<i2").astype("float32") / 32768,
            )
    finally:
        if reader is not None:
            reader.close()


def verify_after(store, video, binding):
    with store.connect() as db:
        rows = list(
            db.execute("SELECT n,start,end FROM segments WHERE video=?", (video["id"],))
        )
    if bind_source(store, video, rows) != binding:
        raise RuntimeError("Gap ASR evidence changed during transcription")
    validate_segments(rows, binding["windows"], complete=True)


def note_pages(rows, value):
    """Keep summary windows within one recovered clip, with stable global note IDs."""
    if not value:
        return [rows[i : i + 6] for i in range(0, len(rows), 6)]
    plan = window_plan(value)
    validate_segments(rows, plan, complete=True)
    clips = {row["n"]: row["clip"] for row in plan}
    pages = []
    for row in rows:
        if (
            not pages
            or len(pages[-1]) == 6
            or clips[pages[-1][-1]["n"]] != clips[row["n"]]
        ):
            pages.append([])
        pages[-1].append(row)
    return pages


def review_html(value, key, *, offline=False):
    """Show original-clock gaps and separate clip players in both review surfaces."""
    from .course_knowledge import timestamp

    if not value:
        return ""
    manifest = "audio-gap-recovery.json" if offline else "/audio-gap-recovery/" + key
    original = "damaged-original-audio.m4a" if offline else "/gap-damaged-audio/" + key
    parts = [
        "<h2>分段恢复音频（存在缺失）</h2><p>" + NOTICE + "</p>",
        '<p><a href="'
        + manifest
        + '">恢复区间与校验证据</a> · <a download href="'
        + original
        + '">保留的原损坏音轨</a></p>',
        "<h3>缺失区间（原视频时间）</h3><ul>",
    ]
    for gap in value["gaps"]:
        start, end = (
            float(Fraction(*gap[k])) for k in ("start_rational", "end_rational")
        )
        parts.append("<li>" + timestamp(start) + "–" + timestamp(end) + "（缺失）</li>")
    if not value["gaps"]:
        parts.append(
            "<li>未记录时间缺口；解码仍属尽力恢复，原音轨完整性未经验证。</li>"
        )
    parts.append("</ul><h3>可读区间（工作音频，16 kHz 单声道 PCM）</h3>")
    for index, clip in enumerate(value["clips"]):
        url = clip["file"] if offline else f"/gap-audio/{key}/{index}"
        parts.append(
            f"<p>{timestamp(clip['start'])}–{timestamp(clip['end'])}（原视频时间）</p>"
            + f'<audio id="gap-{index}" controls preload="none" src="{html.escape(url, quote=True)}"></audio>'
        )
    return "".join(parts)


def playback_button(row, value):
    if not value:
        return ""
    for index, clip in enumerate(value["clips"]):
        if row["start"] >= clip["start"] - 1e-7 and row["end"] <= clip["end"] + 1e-7:
            offset = max(0, row["start"] - clip["start"])
            return f'''<button data-time="{offset:.9f}" onclick="const a=document.getElementById('gap-{index}');a.currentTime=Number(this.dataset.time);a.play()">播放对应恢复片段</button>'''
    raise RuntimeError("ASR citation is outside recovered audio clips")
