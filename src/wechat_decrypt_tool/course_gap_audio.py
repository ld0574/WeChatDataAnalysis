"""Preserve damaged AAC and recover timestamp-separated ASR clips without bridging gaps."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import time
import wave
from fractions import Fraction
from pathlib import Path

from .course_audio_archive import _source_state, file_digest, packet_summary
from .course_audio_recovery import video_key
from .course_mp4_edits import preserve_audio_edits

POLICY = "packet-decode-resync-v1/drop2-after-error-drop3-after-clock-gap/mono16k-pcm16"
RATE = 16000
MAX_PCM_BYTES = 2 * 1024**3
MAX_CLIPS = 4096


def rational(value):
    value = Fraction(value)
    return [value.numerator, value.denominator]


def accepted_frames(packets, codec, stats, checkpoint=lambda: None):
    """Yield None at every boundary; require clean frames after decoder resync."""
    import av
    import numpy as np

    stats.update(
        packets=0,
        decoded_frames=0,
        accepted_frames=0,
        decode_errors=0,
        guard_frames_dropped=0,
        invalid_frames_dropped=0,
        clock_discontinuities=0,
        error_examples=[],
    )
    previous_end = last_accepted_end = None
    guard = 0
    for packet in packets:
        stats["packets"] += bool(packet.size)
        if stats["packets"] % 500 == 0:
            checkpoint()
        try:
            frames = packet.decode()
        except av.error.FFmpegError as exc:
            if not packet.size:
                continue
            stats["decode_errors"] += 1
            if len(stats["error_examples"]) < 64:
                stats["error_examples"].append(
                    {
                        "packet": stats["packets"],
                        "errno": exc.errno,
                        "time": rational(packet.pts * packet.time_base)
                        if packet.pts is not None and packet.time_base is not None
                        else None,
                    }
                )
            yield None
            codec.flush_buffers()
            previous_end = None
            guard = 2
            continue
        for frame in frames:
            checkpoint()
            stats["decoded_frames"] += 1
            if (
                frame.pts is None
                or frame.time_base is None
                or frame.sample_rate <= 0
                or frame.samples <= 0
                or not np.isfinite(frame.to_ndarray()).all()
            ):
                stats["invalid_frames_dropped"] += 1
                yield None
                codec.flush_buffers()
                previous_end = None
                guard = 2
                continue
            start = Fraction(frame.pts * frame.time_base)
            end = start + Fraction(frame.samples, frame.sample_rate)
            if start < 0 or (
                last_accepted_end is not None and start < last_accepted_end
            ):
                stats["invalid_frames_dropped"] += 1
                yield None
                codec.flush_buffers()
                previous_end = None
                guard = 2
                continue
            if previous_end is not None and abs(start - previous_end) > Fraction(
                3, 2 * frame.sample_rate
            ):
                stats["clock_discontinuities"] += 1
                yield None
                codec.flush_buffers()
                # Current output preceded the reset. Exclude it plus two fresh
                # frames so AAC overlap state cannot cross the missing interval.
                guard = 3
            previous_end = end
            if guard:
                guard -= 1
                stats["guard_frames_dropped"] += 1
                continue
            stats["accepted_frames"] += 1
            last_accepted_end = end
            yield frame


class ClipWriter:
    """Each contiguous run gets an independent resampler and ordinary PCM WAV."""

    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.completed = []
        self.writer = self.resampler = None
        self.total_pcm_bytes = 0

    def _output(self, outputs):
        import numpy as np

        for frame in outputs:
            if frame.pts is None:
                raise RuntimeError("Recovered resampler output lacks timestamp")
            start = Fraction(frame.pts * frame.time_base)
            if self.output_end is not None and start != self.output_end:
                raise RuntimeError(
                    "Resampled clip has an interior timeline discontinuity"
                )
            if self.output_start is None:
                self.output_start = start
                if abs(start - self.source_start) > Fraction(1, RATE):
                    raise RuntimeError(
                        "Recovered clip origin differs from source frame clock"
                    )
            samples = frame.to_ndarray().reshape(-1)
            if not np.isfinite(samples).all():
                raise RuntimeError("Recovered resampler output is nonfinite")
            pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
            if self.total_pcm_bytes + len(pcm) > MAX_PCM_BYTES:
                raise InterruptedError("Gap recovery PCM size budget reached")
            self.writer.writeframesraw(pcm)
            self.total_pcm_bytes += len(pcm)
            self.pcm_hash.update(pcm)
            self.samples += len(samples)
            self.output_end = start + Fraction(len(samples), RATE)

    def accept(self, frame):
        import av

        if frame is None:
            self.close()
            return
        start = Fraction(frame.pts * frame.time_base)
        end = start + Fraction(frame.samples, frame.sample_rate)
        if self.writer is None:
            if len(self.completed) >= MAX_CLIPS:
                raise InterruptedError("Gap recovery interval count bound reached")
            self.path = self.directory / (str(len(self.completed)).zfill(5) + ".wav")
            self.writer = wave.open(str(self.path), "wb")
            self.writer.setnchannels(1)
            self.writer.setsampwidth(2)
            self.writer.setframerate(RATE)
            self.source_start = start
            self.output_start = self.output_end = None
            self.source_frames = self.samples = 0
            self.pcm_hash = hashlib.sha256()
            self.resampler = av.AudioResampler(format="flt", layout="mono", rate=RATE)
        self.source_end = end
        self.source_frames += 1
        self._output(self.resampler.resample(frame))

    def close(self):
        if self.writer is None:
            return
        try:
            self._output(self.resampler.resample(None))
            if not self.samples or abs(self.output_end - self.source_end) > Fraction(
                1, RATE
            ):
                raise RuntimeError(
                    "Recovered clip does not cover its accepted source frames"
                )
        finally:
            self.writer.close()
            self.writer = self.resampler = None
        self.completed.append(
            {
                "file": self.path.name,
                "start": float(self.source_start),
                "end": float(self.source_start + Fraction(self.samples, RATE)),
                "start_rational": rational(self.source_start),
                "end_rational": rational(
                    self.source_start + Fraction(self.samples, RATE)
                ),
                "resampler_first_output_rational": rational(self.output_start),
                "resampler_origin_rounding_bound_samples": 1,
                "source_frame_start_rational": rational(self.source_start),
                "source_frame_end_rational": rational(self.source_end),
                "source_frames": self.source_frames,
                "samples": self.samples,
                "pcm_digest": self.pcm_hash.hexdigest(),
                "rate": RATE,
                "channels": 1,
                "sample_width": 2,
            }
        )


def inspect_clip(path, expected):
    """Independent WAV payload and rational duration check; no playback or inference."""
    digest = hashlib.sha256()
    with wave.open(str(path), "rb") as reader:
        if (
            reader.getframerate(),
            reader.getnchannels(),
            reader.getsampwidth(),
            reader.getnframes(),
        ) != (RATE, 1, 2, expected["samples"]):
            raise RuntimeError("Recovered WAV format or sample count differs")
        while data := reader.readframes(RATE * 10):
            digest.update(data)
    start = Fraction(*expected["start_rational"])
    end = Fraction(*expected["end_rational"])
    if (
        end - start != Fraction(expected["samples"], RATE)
        or digest.hexdigest() != expected["pcm_digest"]
    ):
        raise RuntimeError("Recovered WAV PCM or rational timeline differs")


def gap_evidence(store, key, *, rehash=False):
    """Validate retained interval evidence without requiring the original video drive."""
    if not re.fullmatch(r"[a-f0-9]{24}", key):
        return None
    folder = (store.root / "lessons" / key).resolve()
    if not folder.is_relative_to((store.root / "lessons").resolve()):
        raise RuntimeError("Gap recovery folder is outside retained lessons")
    manifest = folder / "audio-gap-recovery.json"
    if not manifest.is_file():
        return None
    value = json.loads(manifest.read_text("utf-8"))
    if not (
        value.get("video") == key
        and value.get("policy") == POLICY
        and value.get("full_packet_scope_attempted")
        and value.get("original_packets_retained")
        and value.get("complete_original_audio_verified") is False
        and value.get("missing_intervals_not_filled_or_concatenated")
        and value.get("decoded_intervals_are_best_effort")
        and value.get("clips")
        and value["original_packets"]["file"] == "damaged-original-audio.m4a"
        and value["original_packets"]["packets"]["packets"] == value["stats"]["packets"]
    ):
        raise RuntimeError("Gap recovery provenance is invalid")
    cursor = Fraction(*value["declared_start_rational"])
    declared_end = Fraction(*value["declared_end_rational"])
    expected_gaps = []
    seen = set()
    for clip in value["clips"]:
        if (
            not re.fullmatch(r"gap-audio/[0-9]{5}\.wav", clip["file"])
            or clip["file"] in seen
        ):
            raise RuntimeError("Gap recovery clip locator is unsafe or duplicated")
        seen.add(clip["file"])
        start = Fraction(*clip["source_frame_start_rational"])
        end = Fraction(*clip["source_frame_end_rational"])
        if start < cursor or end <= start:
            raise RuntimeError("Gap recovery source intervals overlap")
        if start > cursor:
            expected_gaps.append(
                {"start_rational": rational(cursor), "end_rational": rational(start)}
            )
        cursor = end
    if cursor < declared_end:
        expected_gaps.append(
            {"start_rational": rational(cursor), "end_rational": rational(declared_end)}
        )
    overrun = max(Fraction(0), cursor - declared_end)
    if overrun != Fraction(
        *value["decoded_tail_overrun_rational"]
    ) or overrun > Fraction(
        2048, value["original_packets"]["packets"]["codec"]["sample_rate"]
    ):
        raise RuntimeError("Gap recovery decoded tail exceeds its declared bound")
    if expected_gaps != value["gaps"]:
        raise RuntimeError("Gap recovery missing-interval inventory differs")
    for asset in [value["original_packets"], *value["clips"]]:
        path = (folder / asset["file"]).resolve()
        if (
            not path.is_relative_to(folder)
            or not path.is_file()
            or path.stat().st_size != asset["bytes"]
        ):
            raise RuntimeError("Gap recovery asset is missing, unsafe or changed")
        if rehash and file_digest(path, store.checkpoint) != asset["sha256"]:
            raise RuntimeError("Gap recovery asset hash differs")
    if rehash:
        for clip in value["clips"]:
            inspect_clip(folder / clip["file"], clip)
        if (
            packet_summary(folder / value["original_packets"]["file"], store.checkpoint)
            != value["original_packets"]["packets"]
        ):
            raise RuntimeError("Gap recovery original AAC packet evidence differs")
    return value


def recover_gap_audio(store, item):
    """Retain every source AAC packet and separately verified, explicitly partial clips."""
    import av
    from .course_knowledge import schema_lease

    source, before = _source_state(item)
    key = video_key(source)
    folder = store.root / "lessons" / key
    folder.mkdir(parents=True, exist_ok=True)
    manifest = folder / "audio-gap-recovery.json"
    with schema_lease(folder / "audio-gap-recovery.lock", timeout=0):
        source_digest = file_digest(source, store.checkpoint)
        if item.get("sha256") and source_digest != item["sha256"]:
            raise RuntimeError("Gap recovery source hash differs from frozen inventory")
        if manifest.exists():
            value = gap_evidence(store, key, rehash=True)
            if value["source_digest"] != source_digest or value["policy"] != POLICY:
                raise RuntimeError("Committed gap recovery differs; preserve evidence")
            return {**value, "cached": True}
        if shutil.disk_usage(store.root).free < 34 * 1024**3:
            raise InterruptedError("Gap recovery disk reserve reached")
        stats = {}
        last_check = last_progress = 0.0

        def checkpoint():
            nonlocal last_check, last_progress
            now = time.monotonic()
            if now - last_check < 0.5:
                return
            last_check = now
            store.checkpoint()
            if shutil.disk_usage(store.root).free < 32 * 1024**3:
                raise InterruptedError("Gap recovery disk reserve reached")
            if now - last_progress >= 2:
                last_progress = now
                store.write_json(
                    "audio-gap-recovery-progress.json",
                    {
                        "stage": "decoding_and_preserving_source_packets",
                        "video": key,
                        "packets": stats.get("packets", 0),
                        "accepted_frames": stats.get("accepted_frames", 0),
                        "decode_errors": stats.get("decode_errors", 0),
                        "updated": time.time(),
                    },
                )

        with tempfile.TemporaryDirectory(
            prefix="gap-recovery-", dir=folder
        ) as temporary:
            temporary = Path(temporary)
            raw_audio = temporary / "damaged-original-audio.m4a.raw.m4a"
            writer = ClipWriter(temporary / "gap-audio")
            packets_hash = hashlib.sha256()
            packet_count = payload_bytes = 0
            try:
                with (
                    av.open(str(source), metadata_errors="replace") as media,
                    av.open(
                        str(raw_audio),
                        "w",
                        format="ipod",
                        options={"movflags": "+faststart"},
                    ) as target,
                ):
                    if (
                        len(media.streams.audio) != 1
                        or media.streams.audio[0].codec_context.name != "aac"
                    ):
                        raise ValueError("Gap recovery requires one AAC stream")
                    stream = media.streams.audio[0]
                    codec = stream.codec_context
                    configuration = {
                        "codec": codec.name,
                        "sample_rate": codec.sample_rate,
                        "channels": codec.channels,
                        "extradata_digest": hashlib.sha256(
                            codec.extradata or b""
                        ).hexdigest(),
                    }
                    declared_start = Fraction(
                        (stream.start_time or 0) * stream.time_base
                    )
                    declared_end = (
                        declared_start + stream.duration * stream.time_base
                        if stream.duration is not None
                        else None
                    )
                    if declared_end is None:
                        raise ValueError(
                            "Gap recovery requires a declared source timeline"
                        )
                    output = target.add_stream_from_template(stream)

                    def packets():
                        nonlocal packet_count, payload_bytes
                        for packet in media.demux(stream):
                            if not packet.size:
                                yield packet
                                continue
                            raw = bytes(packet)
                            packets_hash.update(len(raw).to_bytes(8, "big"))
                            packets_hash.update(raw)
                            packet_count += 1
                            payload_bytes += len(raw)
                            if packet.dts is None:
                                raise ValueError(
                                    "Damaged source payload lacks packet DTS"
                                )
                            yield packet
                            # Decode before assigning the output stream. Preserve
                            # the original payload even when decoding raised.
                            packet.stream = output
                            target.mux(packet)

                    for frame in accepted_frames(packets(), codec, stats, checkpoint):
                        writer.accept(frame)
                    writer.close()
            finally:
                if writer.writer is not None:
                    writer.writer.close()
            original_packets = {
                "packet_digest": packets_hash.hexdigest(),
                "packets": packet_count,
                "payload_bytes": payload_bytes,
                "codec": configuration,
            }
            retained = temporary / "damaged-original-audio.m4a"
            preserve_audio_edits(source, raw_audio, retained, checkpoint)
            if packet_summary(retained, checkpoint) != original_packets:
                raise RuntimeError(
                    "Gap recovery retained AAC packets differ from source"
                )
            if not writer.completed:
                raise RuntimeError("No timestamped usable audio intervals recovered")
            clips = []
            cursor = declared_start
            gaps = []
            for clip in writer.completed:
                start = Fraction(*clip["source_frame_start_rational"])
                end = Fraction(*clip["source_frame_end_rational"])
                if start < cursor or end > declared_end + Fraction(
                    2048, configuration["sample_rate"]
                ):
                    raise RuntimeError(
                        "Recovered intervals overlap or exceed source timeline"
                    )
                if start > cursor:
                    gaps.append(
                        {
                            "start_rational": rational(cursor),
                            "end_rational": rational(start),
                        }
                    )
                cursor = end
                path = writer.directory / clip["file"]
                inspect_clip(path, clip)
                clips.append(
                    {
                        **clip,
                        "file": "gap-audio/" + clip["file"],
                        "sha256": file_digest(path, checkpoint),
                        "bytes": path.stat().st_size,
                    }
                )
            if cursor < declared_end:
                gaps.append(
                    {
                        "start_rational": rational(cursor),
                        "end_rational": rational(declared_end),
                    }
                )
            _source_state(item)
            original = {
                "file": retained.name,
                "sha256": file_digest(retained, checkpoint),
                "bytes": retained.stat().st_size,
                "packets": original_packets,
            }
            value = {
                "video": key,
                "policy": POLICY,
                "source": str(source),
                "source_digest": source_digest,
                "source_size": before.st_size,
                "source_mtime_ns": before.st_mtime_ns,
                "stats": stats,
                "declared_start_rational": rational(declared_start),
                "declared_end_rational": rational(declared_end),
                "decoded_tail_overrun_rational": rational(
                    max(Fraction(0), cursor - declared_end)
                ),
                "original_packets": original,
                "clips": clips,
                "gaps": gaps,
                "full_packet_scope_attempted": True,
                "original_packets_retained": True,
                "complete_original_audio_verified": False,
                "working_audio_format": "16 kHz mono PCM16 for ASR; original AAC retained separately",
                "missing_intervals_not_filled_or_concatenated": True,
                "decoded_intervals_are_best_effort": True,
                "source_deletion_allowed": False,
                "updated": time.time(),
            }
            for asset in [original, *clips]:
                destination = folder / asset["file"]
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists():
                    if file_digest(destination, checkpoint) != asset["sha256"]:
                        raise RuntimeError(
                            "Uncommitted gap asset differs; preserve for review"
                        )
                else:
                    (temporary / asset["file"]).replace(destination)
            store.write_json(str(manifest.relative_to(store.root)), value)
            return value
