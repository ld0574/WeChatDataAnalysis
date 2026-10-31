from fractions import Fraction
from pathlib import Path

import pytest

from wechat_decrypt_tool import course_gap_audio as module
from wechat_decrypt_tool.course_audio_archive import file_digest, packet_summary
from wechat_decrypt_tool.course_knowledge import KnowledgeStore


def frame(start, *, nonfinite=False):
    import av
    import numpy as np

    data = np.full((1, 4800), float("nan") if nonfinite else 0.4, dtype="float32")
    result = av.AudioFrame.from_ndarray(data, format="fltp", layout="mono")
    result.sample_rate = 48000
    result.time_base = Fraction(1, 48000)
    result.pts = int(Fraction(str(start)) * 48000)
    return result


class Packet:
    size = 1
    time_base = Fraction(1, 48000)

    def __init__(self, value=None, *, error=False):
        self.value = value
        self.error = error
        self.pts = value.pts if value is not None else 9600

    def decode(self):
        if self.error:
            import av

            raise av.error.InvalidDataError(1094995529, "synthetic bad AAC")
        return [self.value]


class Codec:
    resets = 0

    def flush_buffers(self):
        self.resets += 1


def write_events(tmp_path, packets):
    stats = {}
    codec = Codec()
    writer = module.ClipWriter(tmp_path / "clips")
    for item in module.accepted_frames(packets, codec, stats):
        writer.accept(item)
    writer.close()
    for clip in writer.completed:
        module.inspect_clip(writer.directory / clip["file"], clip)
    return writer, stats, codec


def test_decode_error_preserves_absolute_intervals_and_never_bridges_missing_audio(
    tmp_path,
):
    packets = [Packet(frame(t)) for t in (0, 0.1)]
    packets += [Packet(error=True)]
    packets += [Packet(frame(t)) for t in (0.3, 0.4, 0.5, 0.6, 0.7)]
    writer, stats, codec = write_events(tmp_path, packets)
    assert stats["decode_errors"] == 1 and stats["guard_frames_dropped"] == 2
    assert codec.resets == 1
    assert [(c["start"], c["end"]) for c in writer.completed] == [(0, 0.2), (0.5, 0.8)]
    assert sum(c["samples"] for c in writer.completed) == 8000
    import numpy as np
    import wave

    for clip in writer.completed:
        with wave.open(str(writer.directory / clip["file"]), "rb") as reader:
            samples = np.frombuffer(reader.readframes(reader.getnframes()), dtype="<i2")
            assert (samples[32:-32] > 3000).all()


def test_unreported_clock_gap_resets_decoder_and_resampler(tmp_path):
    packets = [Packet(frame(t)) for t in (0, 0.1, 1, 1.1, 1.2, 1.3, 1.4)]
    writer, stats, codec = write_events(tmp_path, packets)
    assert stats["clock_discontinuities"] == 1
    assert stats["guard_frames_dropped"] == 3 and codec.resets == 1
    assert [(c["start"], c["end"]) for c in writer.completed] == [(0, 0.2), (1.3, 1.5)]


@pytest.mark.parametrize("bad", ["backward", "nonfinite"])
def test_invalid_frames_are_excluded_with_explicit_boundaries(tmp_path, bad):
    packets = [Packet(frame(t)) for t in (0, 0.1)]
    packets.append(
        Packet(frame(0.05 if bad == "backward" else 0.2, nonfinite=bad == "nonfinite"))
    )
    packets += [Packet(frame(t)) for t in (0.3, 0.4, 0.5, 0.6)]
    writer, stats, _ = write_events(tmp_path, packets)
    assert stats["invalid_frames_dropped"] == 1
    assert len(writer.completed) == 2 and writer.completed[1]["start"] == 0.5


def test_wav_payload_tampering_does_not_pass_duration_only_check(tmp_path):
    writer, _, _ = write_events(tmp_path, [Packet(frame(0))])
    clip = writer.completed[0]
    path = writer.directory / clip["file"]
    data = bytearray(path.read_bytes())
    data[-1] ^= 1
    path.write_bytes(data)
    with pytest.raises(RuntimeError, match="PCM or rational timeline"):
        module.inspect_clip(path, clip)


@pytest.fixture
def source(tmp_path):
    import av
    import numpy as np

    path = tmp_path / "source.mp4"
    with av.open(str(path), "w") as media:
        stream = media.add_stream("aac", rate=48000)
        stream.layout = "mono"
        for n in range(24):
            audio = av.AudioFrame.from_ndarray(
                np.full((1, 1024), 0.1, dtype="float32"), format="fltp", layout="mono"
            )
            audio.sample_rate = 48000
            audio.pts = n * 1024
            for packet in stream.encode(audio):
                media.mux(packet)
        for packet in stream.encode():
            media.mux(packet)
    state = path.stat()
    return {
        "path": str(path),
        "bytes": state.st_size,
        "mtime_ns": state.st_mtime_ns,
        "duration_seconds": 0.512,
        "sha256": file_digest(path),
        "audio_streams": [{"codec": "aac"}],
    }


def test_real_packet_copy_and_wav_recovery_are_readonly_and_resumable(tmp_path, source):
    store = KnowledgeStore(tmp_path / "kb")
    before = Path(source["path"]).read_bytes()
    value = module.recover_gap_audio(store, source)
    assert value["original_packets_retained"] and value["full_packet_scope_attempted"]
    assert not value["complete_original_audio_verified"]
    assert value["decoded_intervals_are_best_effort"]
    folder = store.root / "lessons" / value["video"]
    assert packet_summary(folder / value["original_packets"]["file"]) == packet_summary(
        source["path"]
    )
    for clip in value["clips"]:
        module.inspect_clip(folder / clip["file"], clip)
    assert Path(source["path"]).read_bytes() == before
    assert module.recover_gap_audio(store, source)["cached"]
    clip = folder / value["clips"][0]["file"]
    data = bytearray(clip.read_bytes())
    data[-1] ^= 1
    clip.write_bytes(data)
    with pytest.raises(RuntimeError, match="asset hash differs"):
        module.recover_gap_audio(store, source)


def test_gap_working_audio_budget_stops_before_publishing(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "MAX_PCM_BYTES", 100)
    writer = module.ClipWriter(tmp_path / "clips")
    try:
        with pytest.raises(InterruptedError, match="PCM size budget"):
            writer.accept(frame(0))
        assert not writer.completed
    finally:
        if writer.writer is not None:
            writer.writer.close()


@pytest.mark.parametrize("tamper", ["gaps", "path"])
def test_gap_manifest_rejects_changed_coverage_and_unsafe_clip_paths(
    tmp_path, source, tamper
):
    import json

    store = KnowledgeStore(tmp_path / "kb")
    value = module.recover_gap_audio(store, source)
    path = store.root / "lessons" / value["video"] / "audio-gap-recovery.json"
    if tamper == "gaps":
        value["gaps"] = [{"start_rational": [9, 1], "end_rational": [10, 1]}]
    else:
        value["clips"][0]["file"] = "gap-audio/../../escape.wav"
    path.write_text(json.dumps(value), "utf-8")
    with pytest.raises(RuntimeError, match="missing-interval|unsafe"):
        module.gap_evidence(store, value["video"], rehash=True)


def test_real_corrupted_aac_packet_is_retained_while_decoding_recovers_later_audio(
    tmp_path, source
):
    import av

    path = Path(source["path"])
    with av.open(str(path)) as media:
        packets = [packet for packet in media.demux(audio=0) if packet.size]
        damaged = packets[8]
        position, size = damaged.pos, damaged.size
    data = bytearray(path.read_bytes())
    data[position : position + size] = b"\0" * size
    path.write_bytes(data)
    source.update(mtime_ns=path.stat().st_mtime_ns, sha256=file_digest(path))
    store = KnowledgeStore(tmp_path / "kb")
    value = module.recover_gap_audio(store, source)
    assert value["stats"]["decode_errors"] >= 1
    assert value["stats"]["guard_frames_dropped"] >= 2
    assert len(value["clips"]) >= 2 and value["gaps"]
    folder = store.root / "lessons" / value["video"]
    assert packet_summary(folder / value["original_packets"]["file"]) == packet_summary(
        path
    )
    assert module.gap_evidence(store, value["video"], rehash=True)[
        "original_packets_retained"
    ]


def test_portable_gap_backup_keeps_raw_packets_and_every_verified_clip_without_original(
    tmp_path, source
):
    from wechat_decrypt_tool.course_backup import create_snapshot, restore_snapshot

    store = KnowledgeStore(tmp_path / "kb")
    value = module.recover_gap_audio(store, source)
    snapshot = create_snapshot(store, tmp_path / "backup", reserve_gib=0)
    restored = KnowledgeStore(
        restore_snapshot(snapshot["snapshot"], tmp_path / "restored", reserve_gib=0)
    )
    Path(source["path"]).unlink()
    evidence = module.gap_evidence(restored, value["video"], rehash=True)
    assert (
        evidence["clips"] == value["clips"]
        and not evidence["complete_original_audio_verified"]
    )
