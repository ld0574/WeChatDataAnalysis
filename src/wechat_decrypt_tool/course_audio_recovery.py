"""Explicit alternate-recording audio recovery; never relabel video byte aliases."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import time
from pathlib import Path

from .course_audio_archive import (
    _source_state,
    decoded_summary,
    file_digest,
    packet_summary,
    remux_aac,
)
from .course_mp4_edits import preserve_audio_edits

METHOD = "alternate-recording-audio-v1"
NAMES = ("alternate-audio.m4a", "damaged-original-audio.m4a")


def video_key(path):
    return hashlib.sha256(str(Path(path).resolve()).encode()).hexdigest()[:24]


def _json_digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def _clock(path):
    import av

    def fraction(value):
        return [value.numerator, value.denominator] if value is not None else None

    with av.open(str(path), metadata_errors="replace") as media:
        if len(media.streams.audio) != 1:
            raise ValueError("Alternate recovery requires one audio stream")
        stream = media.streams.audio[0]
        clock = {
            "start": fraction(stream.start_time * stream.time_base)
            if stream.start_time is not None
            else None,
            "duration": fraction(stream.duration * stream.time_base)
            if stream.duration is not None
            else None,
        }
        first = next(packet for packet in media.demux(stream) if packet.size)
        packet_clock = {
            name: fraction(getattr(first, name) * first.time_base)
            if getattr(first, name) is not None
            else None
            for name in ("pts", "dts", "duration")
        }
        return clock, packet_clock


def recovery_evidence(store, key, *, rehash=False):
    """Return retained provenance independently of the original drive's presence."""
    if not re.fullmatch(r"[a-f0-9]{24}", key):
        return None
    folder = (store.root / "lessons" / key).resolve()
    if not folder.is_relative_to((store.root / "lessons").resolve()):
        return None
    manifest = folder / "audio-recovery.json"
    if not manifest.is_file():
        return None
    value = json.loads(manifest.read_text("utf-8"))
    if not (
        value.get("method") == METHOD
        and value.get("video") == key
        and value.get("alternate_audio_verified")
        and value.get("original_damaged_audio_packets_retained")
        and value.get("video_byte_alias") is False
        and value.get("original_audio_verified") is False
        and set(value.get("files", {})) == set(NAMES)
        and _json_digest(value.get("proofs")) == value.get("proof_digest")
    ):
        raise RuntimeError("Alternate audio provenance is invalid")
    for name, asset in value["files"].items():
        path = (folder / name).resolve()
        if not path.is_relative_to(folder) or not path.is_file():
            raise RuntimeError("Retained alternate audio asset is missing or unsafe")
        if path.stat().st_size != asset["bytes"] or (
            rehash and file_digest(path, store.checkpoint) != asset["sha256"]
        ):
            raise RuntimeError("Retained alternate audio asset changed")
    return value


def prepare_alternate_audio(store, original, candidate, proof, health, origins):
    """Retain both soundtracks and publish hash-bound, honestly labelled provenance."""
    from .course_knowledge import schema_lease

    source, before = _source_state(original)
    alternate, alternate_before = _source_state(candidate)
    key, other = video_key(source), video_key(alternate)
    if (
        key == other
        or proof.get("video") != key
        or proof.get("candidate_video") != other
        or proof.get("source_sha256") == proof.get("candidate_sha256")
    ):
        raise ValueError("Alternate packet proof identities differ")
    track = proof["tracks"]["audio"]
    if not (
        proof.get("codec_configuration_identical")
        and track["source_packets"] == track["candidate_packets"] > 0
        and track["timing_mismatches"] == 0
        and track["payload_mismatches_outside_source_decode_gaps"] == 0
        and health.get("video") == key
        and health.get("candidate_video") == other
        and health.get("candidate_sha256") == proof["candidate_sha256"]
        and health.get("full_candidate_audio_healthy")
        and health.get("all_intact_source_audio_packets_identical")
        and health.get("ready_for_recovery_design_review")
        and health.get("discontinuities") == 0
        and origins.get("source") == key
        and origins.get("candidate") == other
        and origins.get(
            "all_absolute_stream_metadata_and_first_packet_clocks_identical"
        )
        and origins.get("source_origins") == origins.get("candidate_origins")
    ):
        raise ValueError("Alternate audio proofs do not establish a usable recovery")
    folder = store.root / "lessons" / key
    folder.mkdir(parents=True, exist_ok=True)
    provenance = {
        "packet_comparison": proof,
        "full_candidate_health": health,
        "absolute_origins": origins,
    }
    proof_digest = _json_digest(provenance)
    with schema_lease(folder / "audio-recovery.lock", timeout=0):
        existing = recovery_evidence(store, key, rehash=True)
        if existing:
            if existing["proof_digest"] != proof_digest:
                raise RuntimeError(
                    "Committed recovery differs; preserve existing evidence"
                )
        store.checkpoint()
        for path, frozen, expected in (
            (source, original, proof["source_sha256"]),
            (alternate, candidate, proof["candidate_sha256"]),
        ):
            if (
                frozen.get("sha256", expected) != expected
                or file_digest(path, store.checkpoint) != expected
            ):
                raise RuntimeError(
                    "Recovery source hash differs from full packet proof"
                )
        if existing:
            return {**existing, "cached": True}
        origin_clock = _clock(source)
        alternate_clock = _clock(alternate)
        expected_clock = (
            origins["source_origins"]["streams"]["audio"],
            origins["source_origins"]["first_packets"]["audio"],
        )
        if origin_clock != alternate_clock or origin_clock != expected_clock:
            raise RuntimeError("Recovery absolute audio clocks differ from proof")
        original_packets = packet_summary(source, store.checkpoint)
        alternate_packets = packet_summary(alternate, store.checkpoint)
        if original_packets["packets"] != track["source_packets"] or (
            alternate_packets["packets"] != track["candidate_packets"]
        ):
            raise RuntimeError("Recovery packet counts differ from full comparison")
        estimate = sum(
            x["payload_bytes"] for x in (original_packets, alternate_packets)
        )
        used = sum(
            path.stat().st_size
            for name in NAMES
            for path in (store.root / "lessons").glob("*/" + name)
        )
        if used + estimate * 1.1 > 4 * 1024**3 or shutil.disk_usage(store.root).free < (
            32 * 1024**3 + estimate * 3
        ):
            raise InterruptedError("Alternate audio budget or disk reserve reached")
        with tempfile.TemporaryDirectory(prefix="recovery-", dir=folder) as temporary:
            temporary = Path(temporary)
            assets = {}
            edits = {}
            for path, name, packets in (
                (source, NAMES[1], original_packets),
                (alternate, NAMES[0], alternate_packets),
            ):
                store.checkpoint()
                copied = temporary / (name + ".raw.m4a")
                retained = temporary / name
                remux_aac(path, copied, store.checkpoint)
                edits[name] = preserve_audio_edits(
                    path, copied, retained, store.checkpoint
                )
                if packet_summary(retained, store.checkpoint) != packets:
                    raise RuntimeError("Retained recovery soundtrack packets differ")
                assets[name] = {
                    "bytes": retained.stat().st_size,
                    "sha256": file_digest(retained, store.checkpoint),
                    "packets": packets,
                }
            actual = decoded_summary(temporary / NAMES[0], store.checkpoint)
            comparisons = {
                "pcm_digest": "pcm_digest",
                "timeline_digest": "timeline_digest",
                "frames": "decoded_frames",
                "samples": "decoded_samples",
                "first_seconds": "first_seconds",
                "end_seconds": "end_seconds",
            }
            if any(
                actual[name] != health[other_name]
                for name, other_name in comparisons.items()
            ):
                raise RuntimeError(
                    "Retained alternate PCM or timeline differs from healthy source"
                )
            _source_state(original)
            _source_state(candidate)
            for name in NAMES:
                destination = folder / name
                if destination.exists():
                    if (
                        file_digest(destination, store.checkpoint)
                        != assets[name]["sha256"]
                    ):
                        raise RuntimeError(
                            "Uncommitted recovery asset differs; preserve for review"
                        )
                else:
                    (temporary / name).replace(destination)
            result = {
                "method": METHOD,
                "video": key,
                "candidate_video": other,
                "source": str(source),
                "source_size": before.st_size,
                "source_mtime_ns": before.st_mtime_ns,
                "source_sha256": proof["source_sha256"],
                "candidate_source": str(alternate),
                "candidate_size": alternate_before.st_size,
                "candidate_mtime_ns": alternate_before.st_mtime_ns,
                "candidate_sha256": proof["candidate_sha256"],
                "files": assets,
                "decoded": actual,
                "edits": edits,
                "proofs": provenance,
                "proof_digest": proof_digest,
                "alternate_audio_verified": True,
                "original_damaged_audio_packets_retained": True,
                "original_audio_verified": False,
                "video_byte_alias": False,
                "provenance_notice": "原视频音轨损坏；这里提供已核验的另一录制版本音轨。画面仍来自原视频，两份视频不是字节相同副本。转写来源单独记录。",
                "source_deletion_allowed": False,
                "verified_at": time.time(),
            }
            store.write_json(
                str((folder / "audio-recovery.json").relative_to(store.root)), result
            )
            return result


def transcription_audio(store, video, *, has_segments):
    """Bind ASR to explicit alternate evidence; refuse to mix unproven old windows."""
    key = video["id"]
    folder = store.root / "lessons" / key
    binding_path = folder / "asr-audio-source.json"
    recovered = recovery_evidence(store, key, rehash=True)
    if recovered is None:
        if binding_path.exists():
            raise RuntimeError(
                "ASR alternate-source binding exists but recovery is missing"
            )
        return Path(video["path"]), None
    if (video["path"], video["size"], video["mtime_ns"]) != (
        recovered["source"],
        recovered["source_size"],
        recovered["source_mtime_ns"],
    ):
        raise RuntimeError("ASR source differs from retained recovery provenance")
    binding = {
        "method": METHOD,
        "video": key,
        "recovery_manifest_digest": file_digest(folder / "audio-recovery.json"),
        "alternate_file_digest": recovered["files"][NAMES[0]]["sha256"],
        "candidate_video": recovered["candidate_video"],
        "candidate_sha256": recovered["candidate_sha256"],
        "source_sha256": recovered["source_sha256"],
        "notice": recovered["provenance_notice"],
    }
    if binding_path.exists():
        if json.loads(binding_path.read_text("utf-8")) != binding:
            raise RuntimeError("Committed ASR recovery binding differs")
    elif has_segments:
        raise RuntimeError(
            "Existing ASR windows have no alternate-source binding; explicit migration required"
        )
    else:
        store.write_json(str(binding_path.relative_to(store.root)), binding)
    return folder / NAMES[0], binding
