"""Read-only full packet, PCM and every-frame timeline validation of archived audio."""

from __future__ import annotations

import hashlib
import json
import time

from .course_audio_archive import (
    _source_state,
    decoded_summary,
    file_digest,
    packet_summary,
)


def validate_archived_audio(store, item):
    """Return hash-bound proof without repairing or replacing any existing evidence."""
    source, source_stat = _source_state(item)
    key = hashlib.sha256(str(source).encode()).hexdigest()[:24]
    folder = store.root / "lessons" / key
    manifest = folder / "audio.json"
    output = folder / "audio.m4a"
    manifest_raw = manifest.read_bytes()
    value = json.loads(manifest_raw)
    if not (
        value.get("verified")
        and value.get("video") == key
        and value.get("source") == str(source)
        and value.get("file") == "audio.m4a"
        and value.get("source_size") == source_stat.st_size
        and value.get("source_mtime_ns") == source_stat.st_mtime_ns
    ):
        raise RuntimeError("Audio manifest does not match frozen source")
    before = output.stat()
    if before.st_size != value["file_bytes"]:
        raise RuntimeError("Archived audio size differs from manifest")

    def progress(stage):
        store.write_json(
            "audio-timeline-validation-progress.json",
            {"stage": stage, "video": key, "updated": time.time()},
        )

    progress("hashing_source")
    source_digest = file_digest(source, store.checkpoint)
    if source_digest != value["source_digest"] or (
        item.get("sha256") and item["sha256"] != source_digest
    ):
        raise RuntimeError("Original audio source hash differs from frozen evidence")
    progress("hashing_retained_audio")
    if file_digest(output, store.checkpoint) != value["file_digest"]:
        raise RuntimeError("Archived audio hash differs from manifest")
    progress("verifying_original_packets")
    original_packets = packet_summary(source, store.checkpoint)
    progress("verifying_retained_packets")
    if (
        original_packets != value["packets"]
        or packet_summary(output, store.checkpoint) != original_packets
    ):
        raise RuntimeError("Original and retained AAC packets or codec differ")
    progress("decoding_original_timeline")
    original = decoded_summary(source, store.checkpoint)
    progress("decoding_retained_timeline")
    retained = decoded_summary(output, store.checkpoint)
    if not original.get("timeline_digest") or original != retained:
        raise RuntimeError("Original and retained PCM or every-frame timeline differ")
    # Old manifests may omit the rational timeline. Other recorded values remain
    # evidence and must also agree with this fresh, complete decode.
    if not value.get("decoded") or any(
        name not in original or original[name] != recorded
        for name, recorded in value["decoded"].items()
    ):
        raise RuntimeError("Fresh audio decode differs from recorded manifest")
    _source_state(item)
    after = output.stat()
    if (before.st_size, before.st_mtime_ns) != (
        after.st_size,
        after.st_mtime_ns,
    ) or manifest.read_bytes() != manifest_raw:
        raise RuntimeError("Audio evidence changed during full validation")
    return {
        "video": key,
        "manifest_digest": hashlib.sha256(manifest_raw).hexdigest(),
        "source_digest": source_digest,
        "source_size": source_stat.st_size,
        "source_mtime_ns": source_stat.st_mtime_ns,
        "file_digest": value["file_digest"],
        "file_bytes": before.st_size,
        "file_mtime_ns": before.st_mtime_ns,
        "packet_digest": original_packets["packet_digest"],
        "decoded": original,
        "legacy_manifest_lacked_timeline": "timeline_digest" not in value["decoded"],
        "all_packets_pcm_and_every_frame_timestamps_identical": True,
        "original_and_retained_files_unmodified": True,
        "verified_at": time.time(),
    }
