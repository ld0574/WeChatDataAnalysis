"""Retain original AAC packets and verify decoded speech before video retirement."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import time
from pathlib import Path
import re


def verified_audio(store, key):
    if not re.fullmatch("[a-f0-9]{24}", key):
        return None
    folder = store.root / "lessons" / key
    manifest = folder / "audio.json"
    path = folder / "audio.m4a"
    if not manifest.is_file() or not path.is_file():
        return None
    value = json.loads(manifest.read_text(encoding="utf-8"))
    if (
        value.get("verified")
        and value.get("video") == key
        and value.get("file") == "audio.m4a"
        and value.get("file_bytes") == path.stat().st_size
    ):
        return value
    return None


def file_digest(path, checkpoint=lambda: None):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(8 * 1024**2), b""):
            checkpoint()
            h.update(block)
    return h.hexdigest()


def packet_summary(path, checkpoint=lambda: None):
    import av

    digest = hashlib.sha256()
    count = 0
    payload_bytes = 0
    with av.open(str(path), metadata_errors="replace") as media:
        if len(media.streams.audio) != 1:
            raise ValueError("Expected exactly one original audio stream")
        stream = media.streams.audio[0]
        ctx = stream.codec_context
        if ctx.name != "aac":
            raise ValueError(
                "Only lossless AAC remux is implemented; retain unsupported original"
            )
        codec = {
            "codec": ctx.name,
            "sample_rate": ctx.sample_rate,
            "channels": ctx.channels,
            "extradata_digest": hashlib.sha256(ctx.extradata or b"").hexdigest(),
        }
        for packet in media.demux(stream):
            if not packet.size:
                continue
            if count % 500 == 0:
                checkpoint()
            value = bytes(packet)
            digest.update(len(value).to_bytes(8, "big"))
            digest.update(value)
            count += 1
            payload_bytes += len(value)
    if not count:
        raise ValueError("No original audio packets found")
    return {
        "packet_digest": digest.hexdigest(),
        "packets": count,
        "payload_bytes": payload_bytes,
        "codec": codec,
    }


def decoded_summary(path, checkpoint=lambda: None):
    import av

    digest = hashlib.sha256()
    count = 0
    samples = 0
    first = None
    end = None
    with av.open(str(path), metadata_errors="replace") as media:
        for frame in media.decode(audio=0):
            if count % 500 == 0:
                checkpoint()
            if frame.time is None:
                raise ValueError("Decoded audio frame has no timestamp")
            if first is None:
                first = frame.time
            digest.update(frame.samples.to_bytes(4, "big"))
            digest.update(frame.sample_rate.to_bytes(4, "big"))
            digest.update(frame.layout.name.encode())
            digest.update(frame.format.name.encode())
            digest.update(frame.to_ndarray().tobytes())
            samples += frame.samples
            count += 1
            end = frame.time + frame.samples / frame.sample_rate
    if not count:
        raise ValueError("No original audio could be decoded")
    return {
        "pcm_digest": digest.hexdigest(),
        "frames": count,
        "samples": samples,
        "first_seconds": first,
        "end_seconds": end,
    }


def remux_aac(source, output, checkpoint=lambda: None):
    import av

    with (
        av.open(str(source), metadata_errors="replace") as media,
        av.open(
            str(output), "w", format="ipod", options={"movflags": "+faststart"}
        ) as target,
    ):
        if (
            len(media.streams.audio) != 1
            or media.streams.audio[0].codec_context.name != "aac"
        ):
            raise ValueError("Unsupported source audio layout/codec")
        stream = media.streams.audio[0]
        copy = target.add_stream_from_template(stream)
        count = 0
        for packet in media.demux(stream):
            if not packet.size:
                continue
            if count % 500 == 0:
                checkpoint()
            if packet.dts is None:
                raise ValueError(
                    "Original payload packet lacks DTS; review before remux"
                )
            packet.stream = copy
            target.mux(packet)
            count += 1


def _source_state(item):
    path = Path(item["path"]).resolve()
    s = path.stat()
    if (s.st_size, s.st_mtime_ns) != (item["bytes"], item["mtime_ns"]):
        raise RuntimeError("Source differs from frozen course inventory")
    return path, s


def archive_one(store, item, *, remaining_budget=None):
    path, before = _source_state(item)
    key = hashlib.sha256(str(path).encode()).hexdigest()[:24]
    folder = store.root / "lessons" / key
    folder.mkdir(parents=True, exist_ok=True)
    output = folder / "audio.m4a"
    manifest = folder / "audio.json"
    if manifest.exists():
        value = json.loads(manifest.read_text(encoding="utf-8"))
        if (
            not value.get("verified")
            or value.get("video") != key
            or value.get("source") != str(path)
            or value["source_size"] != before.st_size
            or value["source_mtime_ns"] != before.st_mtime_ns
        ):
            raise RuntimeError("Audio manifest does not match frozen source")
        if item.get("sha256") and value["source_digest"] != item["sha256"]:
            raise RuntimeError("Audio manifest source hash does not match inventory")
        if remaining_budget is not None and value["file_bytes"] > remaining_budget:
            raise InterruptedError("Audio archive size budget reached")
        if (
            not output.is_file()
            or file_digest(output, store.checkpoint) != value["file_digest"]
        ):
            raise RuntimeError(
                "Verified audio archive changed; preserve evidence and review"
            )
        return {**value, "cached": True}

    def progress(stage):
        store.write_json(
            "audio-archive-progress.json",
            {
                "stage": stage,
                "updated": time.time(),
                "pid": __import__("os").getpid(),
                "video": key,
            },
        )

    progress("reading_source")
    original_packets = packet_summary(path, store.checkpoint)
    if (
        remaining_budget is not None
        and original_packets["payload_bytes"] > remaining_budget
    ):
        raise InterruptedError("Audio archive size budget reached")
    if (
        shutil.disk_usage(store.root).free
        < original_packets["payload_bytes"] * 1.1 + 32 * 1024**3
    ):
        raise InterruptedError(
            "Insufficient disk headroom for audio evidence and remaining archive work"
        )
    with tempfile.TemporaryDirectory(prefix="audio-copy-", dir=folder) as temporary:
        candidate = output if output.exists() else Path(temporary) / "audio.m4a"
        if candidate != output:
            progress("copying_aac")
            remux_aac(path, candidate, store.checkpoint)
        progress("verifying_packets")
        copied_packets = packet_summary(candidate, store.checkpoint)
        if copied_packets != original_packets:
            raise RuntimeError(
                "AAC packet or codec configuration mismatch; original retained"
            )
        progress("verifying_decoded_source")
        original_pcm = decoded_summary(path, store.checkpoint)
        progress("verifying_decoded_copy")
        copied_pcm = decoded_summary(candidate, store.checkpoint)
        if copied_pcm != original_pcm:
            raise RuntimeError("Decoded PCM/timeline mismatch; original retained")
        if remaining_budget is not None and candidate.stat().st_size > remaining_budget:
            raise InterruptedError("Audio archive size budget reached")
        progress("hashing_source")
        source_digest = file_digest(path, store.checkpoint)
        if item.get("sha256") and item["sha256"] != source_digest:
            raise RuntimeError("Original video hash changed")
        _source_state(item)
        value = {
            "schema": 1,
            "video": key,
            "source": str(path),
            "source_size": before.st_size,
            "source_mtime_ns": before.st_mtime_ns,
            "source_digest": source_digest,
            "file": "audio.m4a",
            "file_bytes": candidate.stat().st_size,
            "file_digest": file_digest(candidate, store.checkpoint),
            "packets": original_packets,
            "decoded": original_pcm,
            "method": "aac_packet_copy_no_reencoding",
            "verified": True,
            "verified_at": time.time(),
        }
        if candidate != output:
            candidate.replace(output)
        store.write_json(str(manifest.relative_to(store.root)), value)
    return value


def plan_audio(store, inventory):
    import av

    rows = []
    for n, item in enumerate(inventory["videos"]):
        store.checkpoint()
        path, _ = _source_state(item)
        with av.open(str(path), metadata_errors="replace") as media:
            if (
                len(media.streams.audio) != 1
                or media.streams.audio[0].codec_context.name != "aac"
            ):
                raise ValueError("Audio inventory contains unsupported source")
            stream = media.streams.audio[0]
            rate = stream.bit_rate or 128000
        rows.append(
            {
                "path": str(path),
                "estimated_bytes": int(item["duration_seconds"] * rate / 8 * 1.02)
                + 64 * 1024,
            }
        )
        store.write_json(
            "audio-archive-progress.json",
            {
                "stage": "planning",
                "updated": time.time(),
                "pid": __import__("os").getpid(),
                "probed": n + 1,
                "total": len(inventory["videos"]),
            },
        )
    value = {
        "videos": rows,
        "estimated_bytes": sum(x["estimated_bytes"] for x in rows),
        "codec": "aac",
        "method": "packet_copy",
        "reserve_bytes": 32 * 1024**3,
        "inventory_fingerprint": hashlib.sha256(
            json.dumps(inventory, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest(),
    }
    store.write_json("audio-archive-plan.json", value)
    return value


def archive_audio(store, inventory_path, **options):
    try:
        return _archive_audio(store, inventory_path, **options)
    except BaseException as exc:
        store.write_json(
            "audio-archive-progress.json",
            {
                "stage": "paused"
                if isinstance(exc, (InterruptedError, KeyboardInterrupt))
                else "error",
                "updated": time.time(),
                "pid": __import__("os").getpid(),
                "reason": str(exc),
            },
        )
        raise


def _archive_audio(store, inventory_path, *, limit=0, budget_gib=48):
    inventory = json.loads(Path(inventory_path).read_text(encoding="utf-8"))
    cached_plan = store.root / "audio-archive-plan.json"
    plan = (
        json.loads(cached_plan.read_text(encoding="utf-8"))
        if cached_plan.exists()
        else None
    )
    fingerprint = hashlib.sha256(
        json.dumps(inventory, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    if not plan or plan.get("inventory_fingerprint") != fingerprint:
        plan = plan_audio(store, inventory)
    if plan["estimated_bytes"] > budget_gib * 1024**3:
        raise InterruptedError(
            "Original AAC estimate exceeds audio budget; review storage/compression policy before proceeding"
        )
    completed = []
    errors = []
    used = 0
    for item in inventory["videos"]:
        if limit and len(completed) + len(errors) >= limit:
            break
        try:
            value = archive_one(
                store, item, remaining_budget=budget_gib * 1024**3 - used
            )
            used += value["file_bytes"]
            completed.append(
                {
                    "video": value["video"],
                    "file_bytes": value["file_bytes"],
                    "cached": value.get("cached", False),
                }
            )
        except InterruptedError:
            raise
        except Exception as exc:
            errors.append(
                {"path": item["path"], "error": type(exc).__name__ + ": " + str(exc)}
            )
        store.write_json(
            "audio-archive-report.json",
            {
                "completed": completed,
                "errors": errors,
                "limit": limit,
                "total_videos": len(inventory["videos"]),
                "originals_retained": True,
            },
        )
    result = {
        "completed": completed,
        "errors": errors,
        "limit": limit,
        "total_videos": len(inventory["videos"]),
        "originals_retained": True,
    }
    store.write_json("audio-archive-report.json", result)
    store.write_json(
        "audio-archive-progress.json",
        {
            "stage": "batch_finished" if not errors else "finished_with_errors",
            "updated": time.time(),
            "completed": len(completed),
            "errors": len(errors),
            "limit": limit,
        },
    )
    return result
