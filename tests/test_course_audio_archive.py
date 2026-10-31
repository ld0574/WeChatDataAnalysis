import json
import struct

import pytest

from wechat_decrypt_tool.course_knowledge import (
    KnowledgeStore,
    create_app,
    export_review,
)
from wechat_decrypt_tool import course_audio_archive as module
from wechat_decrypt_tool import course_mp4_edits as edits
from wechat_decrypt_tool import course_audio_validation as validation
from wechat_decrypt_tool import course_audio_recovery as recovery


@pytest.fixture
def source(tmp_path):
    av = pytest.importorskip("av")
    np = pytest.importorskip("numpy")
    path = tmp_path / "source.mp4"
    with av.open(str(path), "w") as media:
        stream = media.add_stream("aac", rate=48000)
        stream.layout = "stereo"
        for n in range(4):
            data = np.full((2, 1024), 0.01 * (n + 1), dtype="float32")
            frame = av.AudioFrame.from_ndarray(data, format="fltp", layout="stereo")
            frame.sample_rate = 48000
            frame.pts = n * 1024
            for packet in stream.encode(frame):
                media.mux(packet)
        for packet in stream.encode():
            media.mux(packet)
    s = path.stat()
    return {
        "path": str(path),
        "title": "audio fixture",
        "bytes": s.st_size,
        "mtime_ns": s.st_mtime_ns,
        "duration_seconds": 0.1,
        "audio_streams": [{"codec": "aac"}],
    }


def test_packet_copy_preserves_pcm_timeline_and_source_and_is_resumable(
    tmp_path, source
):
    store = KnowledgeStore(tmp_path / "kb")
    before = module.file_digest(source["path"])
    first = module.archive_one(store, source)
    second = module.archive_one(store, source)
    assert (
        first["verified"]
        and second["cached"]
        and first["file_digest"] == second["file_digest"]
    )
    assert module.file_digest(source["path"]) == before == first["source_digest"]
    output = store.root / "lessons" / first["video"] / "audio.m4a"
    assert module.packet_summary(output) == module.packet_summary(source["path"])
    assert module.decoded_summary(output) == module.decoded_summary(source["path"])


def test_verified_audio_corruption_is_not_overwritten(tmp_path, source):
    store = KnowledgeStore(tmp_path / "kb")
    value = module.archive_one(store, source)
    path = store.root / "lessons" / value["video"] / "audio.m4a"
    path.write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="archive changed"):
        module.archive_one(store, source)
    assert path.read_bytes() == b"tampered"


def test_full_readonly_validation_upgrades_legacy_proof_without_changing_manifest(
    tmp_path, source
):
    store = KnowledgeStore(tmp_path / "kb")
    value = module.archive_one(store, source)
    manifest = store.root / "lessons" / value["video"] / "audio.json"
    del value["decoded"]["timeline_digest"]
    manifest.write_text(json.dumps(value), encoding="utf-8")
    before = manifest.read_bytes()
    result = validation.validate_archived_audio(store, source)
    assert result["all_packets_pcm_and_every_frame_timestamps_identical"]
    assert result["legacy_manifest_lacked_timeline"]
    assert result["decoded"]["timeline_digest"]
    assert manifest.read_bytes() == before


def test_full_validation_rejects_interior_timing_change(tmp_path, source, monkeypatch):
    store = KnowledgeStore(tmp_path / "kb")
    module.archive_one(store, source)
    decode = validation.decoded_summary

    def changed(path, checkpoint):
        result = decode(path, checkpoint)
        if str(path).endswith("audio.m4a"):
            result["timeline_digest"] = "interior_shift_with_same_endpoints_and_pcm"
        return result

    monkeypatch.setattr(validation, "decoded_summary", changed)
    with pytest.raises(RuntimeError, match="every-frame timeline"):
        validation.validate_archived_audio(store, source)


def test_full_validation_preserves_corrupted_audio_for_review(tmp_path, source):
    store = KnowledgeStore(tmp_path / "kb")
    value = module.archive_one(store, source)
    output = store.root / "lessons" / value["video"] / "audio.m4a"
    raw = bytearray(output.read_bytes())
    raw[-1] ^= 1
    output.write_bytes(raw)
    with pytest.raises(RuntimeError, match="audio hash differs"):
        validation.validate_archived_audio(store, source)
    assert output.read_bytes() == raw


def test_full_validation_detects_manifest_change_during_decode(
    tmp_path, source, monkeypatch
):
    store = KnowledgeStore(tmp_path / "kb")
    value = module.archive_one(store, source)
    manifest = store.root / "lessons" / value["video"] / "audio.json"
    decode = validation.decoded_summary

    def changed(path, checkpoint):
        result = decode(path, checkpoint)
        if str(path).endswith("audio.m4a"):
            manifest.write_bytes(manifest.read_bytes() + b"\n")
        return result

    monkeypatch.setattr(validation, "decoded_summary", changed)
    with pytest.raises(RuntimeError, match="changed during full validation"):
        validation.validate_archived_audio(store, source)


@pytest.fixture
def alternate_evidence(source, tmp_path):
    from pathlib import Path

    original = dict(source)
    candidate = dict(source)
    candidate_path = tmp_path / "alternate.mp4"
    candidate_path.write_bytes(Path(source["path"]).read_bytes())
    candidate.update(
        path=str(candidate_path), mtime_ns=candidate_path.stat().st_mtime_ns
    )
    # A different original payload remains retained; tests do not decode or
    # promote this untrusted source as healthy original audio.
    import av

    with av.open(source["path"]) as media:
        packet = next(packet for packet in media.demux(audio=0) if packet.size > 20)
        position = packet.pos + 12
    raw = bytearray(Path(source["path"]).read_bytes())
    raw[position] ^= 0xF0
    Path(source["path"]).write_bytes(raw)
    original.update(mtime_ns=Path(source["path"]).stat().st_mtime_ns)
    key, other = (
        recovery.video_key(original["path"]),
        recovery.video_key(candidate["path"]),
    )
    count = module.packet_summary(original["path"])["packets"]
    proof = {
        "video": key,
        "candidate_video": other,
        "source_sha256": module.file_digest(original["path"]),
        "candidate_sha256": module.file_digest(candidate["path"]),
        "codec_configuration_identical": True,
        "tracks": {
            "audio": {
                "source_packets": count,
                "candidate_packets": count,
                "timing_mismatches": 0,
                "payload_mismatches_outside_source_decode_gaps": 0,
            }
        },
    }
    decoded = module.decoded_summary(candidate["path"], rational_endpoints=True)
    health = {
        "video": key,
        "candidate_video": other,
        "candidate_sha256": proof["candidate_sha256"],
        "full_candidate_audio_healthy": True,
        "all_intact_source_audio_packets_identical": True,
        "ready_for_recovery_design_review": True,
        "discontinuities": 0,
        "pcm_digest": decoded["pcm_digest"],
        "timeline_digest": decoded["timeline_digest"],
        "decoded_frames": decoded["frames"],
        "decoded_samples": decoded["samples"],
        "first_seconds": decoded["first_seconds"],
        "end_seconds": decoded["end_seconds"],
    }
    clock, first = recovery._clock(original["path"])
    origins = {
        "source": key,
        "candidate": other,
        "all_absolute_stream_metadata_and_first_packet_clocks_identical": True,
        "source_origins": {
            "streams": {"audio": clock},
            "first_packets": {"audio": first},
        },
        "candidate_origins": {
            "streams": {"audio": clock},
            "first_packets": {"audio": first},
        },
    }
    return original, candidate, proof, health, origins


def test_health_endpoint_uses_one_rational_sum_without_relaxing_pcm_or_clock_checks(
    monkeypatch,
):
    import contextlib
    from fractions import Fraction
    from types import SimpleNamespace

    av = pytest.importorskip("av")
    np = pytest.importorskip("numpy")
    frame = av.AudioFrame.from_ndarray(
        np.zeros((2, 1024), dtype="float32"), format="fltp", layout="stereo"
    )
    frame.sample_rate = 48000
    frame.pts = 492495624
    frame.time_base = Fraction(1, 48000)
    monkeypatch.setattr(
        av,
        "open",
        lambda *a, **k: contextlib.nullcontext(
            SimpleNamespace(decode=lambda **kw: iter([frame]))
        ),
    )
    legacy = module.decoded_summary("unused")
    canonical = module.decoded_summary("unused", rational_endpoints=True)
    expected = float(Fraction(492495624 + 1024, 48000))
    assert legacy["end_seconds"] != expected
    assert canonical["end_seconds"] == expected
    assert {k: v for k, v in legacy.items() if k != "end_seconds"} == {
        k: v for k, v in canonical.items() if k != "end_seconds"
    }


def test_recovery_does_not_tolerate_a_different_canonical_endpoint(
    tmp_path, alternate_evidence
):
    import math

    original, candidate, proof, health, origins = alternate_evidence
    changed = {**health, "end_seconds": math.nextafter(health["end_seconds"], math.inf)}
    with pytest.raises(RuntimeError, match="PCM or timeline differs"):
        recovery.prepare_alternate_audio(
            KnowledgeStore(tmp_path / "kb"),
            original,
            candidate,
            proof,
            changed,
            origins,
        )


def test_alternate_recovery_retains_both_tracks_and_refuses_implicit_old_asr_mix(
    tmp_path, alternate_evidence
):
    original, candidate, proof, health, origins = alternate_evidence
    store = KnowledgeStore(tmp_path / "kb")
    key = store.register(original)
    result = recovery.prepare_alternate_audio(store, *alternate_evidence)
    assert result["alternate_audio_verified"] and not result["original_audio_verified"]
    assert not result["video_byte_alias"]
    folder = store.root / "lessons" / key
    assert module.packet_summary(
        folder / "damaged-original-audio.m4a"
    ) == module.packet_summary(original["path"])
    assert module.decoded_summary(
        folder / "alternate-audio.m4a"
    ) == module.decoded_summary(candidate["path"])
    with store.connect() as db:
        video = dict(db.execute("SELECT * FROM videos WHERE id=?", (key,)).fetchone())
    with pytest.raises(RuntimeError, match="explicit migration"):
        recovery.transcription_audio(store, video, has_segments=True)
    path, binding = recovery.transcription_audio(store, video, has_segments=False)
    assert (
        path == folder / "alternate-audio.m4a"
        and binding["candidate_video"] == proof["candidate_video"]
    )
    assert recovery.transcription_audio(store, video, has_segments=True) == (
        path,
        binding,
    )
    assert recovery.prepare_alternate_audio(store, *alternate_evidence)["cached"]


@pytest.mark.parametrize(
    "failure", ["source_hash", "unmatched_intact_packet", "unhealthy", "clock"]
)
def test_alternate_recovery_rejects_inadequate_proofs(
    tmp_path, alternate_evidence, failure
):
    original, candidate, proof, health, origins = alternate_evidence
    if failure == "source_hash":
        proof["source_sha256"] = "0" * 64
    elif failure == "unmatched_intact_packet":
        proof["tracks"]["audio"]["payload_mismatches_outside_source_decode_gaps"] = 1
    elif failure == "unhealthy":
        health["full_candidate_audio_healthy"] = False
    else:
        origins["source_origins"]["streams"]["audio"]["start"] = [999, 1]
    store = KnowledgeStore(tmp_path / "kb")
    with pytest.raises((ValueError, RuntimeError)):
        recovery.prepare_alternate_audio(store, *alternate_evidence)
    assert not list(store.root.glob("lessons/*/audio-recovery.json"))


def test_recovered_routes_export_and_backup_preserve_honest_provenance(
    tmp_path, alternate_evidence
):
    from fastapi.testclient import TestClient
    from wechat_decrypt_tool.course_backup import create_snapshot, restore_snapshot

    store = KnowledgeStore(tmp_path / "kb")
    key = store.register(alternate_evidence[0])
    recovery.prepare_alternate_audio(store, *alternate_evidence)
    store.save_segment(key, 0, 0, 0.05, "alternate_fixture_marker")
    page = export_review(store, key).read_text("utf-8")
    assert "替代录制版本音轨" in page and "alternate-audio.m4a" in page
    assert "两份视频不是字节相同副本" in page
    snapshot = create_snapshot(store, tmp_path / "backup", reserve_gib=0)
    restored = KnowledgeStore(
        restore_snapshot(snapshot["snapshot"], tmp_path / "restored", reserve_gib=0)
    )
    for item in alternate_evidence[:2]:
        __import__("pathlib").Path(item["path"]).unlink()
    with TestClient(create_app(restored, "unused")) as client:
        assert client.get("/audio/" + key).status_code == 404
        assert client.get("/recovery-audio/" + key).status_code == 200
        assert client.get("/damaged-audio/" + key).status_code == 200
        assert (
            client.get("/audio-recovery/" + key).json()["original_audio_verified"]
            is False
        )
        assert "播放对应替代音轨" in client.get("/lesson/" + key).text
        assert client.get("/recovery-audio/not-a-key").status_code == 404
    output = restored.root / "lessons" / key / "alternate-audio.m4a"
    raw = bytearray(output.read_bytes())
    raw[-1] ^= 1
    output.write_bytes(raw)
    with TestClient(create_app(restored, "unused")) as client:
        assert client.get("/recovery-audio/" + key).status_code == 404


def test_transcribe_consumes_retained_alternate_with_binding_and_original_video_id(
    tmp_path, alternate_evidence, monkeypatch
):
    from wechat_decrypt_tool import course_knowledge as knowledge

    store = KnowledgeStore(tmp_path / "kb")
    key = store.register(alternate_evidence[0])
    recovery.prepare_alternate_audio(store, *alternate_evidence)
    windows = knowledge.audio_windows
    used = []

    def checked(path):
        used.append(path)
        assert path.name == "alternate-audio.m4a"
        yield from windows(path)

    class Backend:
        is_loaded = False
        precision = "float32"

        def __init__(self, *args, **kwargs):
            pass

        def transcribe_audio_batch(self, paths, language, **kwargs):
            return [{"text": "retained_alternate_asr_fixture"} for _ in paths]

        def close(self):
            pass

    monkeypatch.setattr(knowledge, "audio_windows", checked)
    monkeypatch.setattr("wechat_decrypt_tool.asr_worker.ProcessBackend", Backend)
    knowledge.transcribe(store, key, tmp_path / "model")
    assert len(used) == 1
    exported = store.export(key)
    assert (
        exported["asr_audio_source"]["candidate_video"]
        == alternate_evidence[2]["candidate_video"]
    )
    with store.connect() as db:
        assert (
            db.execute("SELECT status FROM videos WHERE id=?", (key,)).fetchone()[0]
            == "transcribed"
        )
        assert (
            db.execute(
                "SELECT count(*) FROM segments WHERE video=?", (key,)
            ).fetchone()[0]
            == 1
        )


def test_audio_endpoint_and_offline_review_link_to_verified_sound(tmp_path, source):
    from fastapi.testclient import TestClient

    store = KnowledgeStore(tmp_path / "kb")
    key = store.register(source)
    module.archive_one(store, source)
    store.save_segment(key, 0, 0, 0.1, "source words")
    page = export_review(store, key).read_text(encoding="utf-8")
    assert 'src="audio.m4a"' in page
    with TestClient(create_app(store, "unused")) as client:
        response = client.get("/audio/" + key)
        assert (
            response.status_code == 200
            and response.headers["content-type"] == "audio/mp4"
        )
        assert "播放对应原声" in client.get("/lesson/" + key).text
        assert client.get("/audio/not-a-course-id").status_code == 404


def test_audio_budget_pause_does_not_touch_original(tmp_path, source):
    store = KnowledgeStore(tmp_path / "kb")
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps({"videos": [source]}), encoding="utf-8")
    before = module.file_digest(source["path"])
    with pytest.raises(InterruptedError, match="budget"):
        module.archive_audio(store, inventory, budget_gib=0)
    assert module.file_digest(source["path"]) == before
    assert (
        json.loads(
            (store.root / "audio-archive-progress.json").read_text(encoding="utf-8")
        )["stage"]
        == "paused"
    )


@pytest.fixture
def edited_source(source):
    """An empty lead-in and AAC priming trim at the original movie clock."""
    from pathlib import Path

    path = Path(source["path"])
    start, size, moov = edits.movie(path)
    track = edits.audio_track(moov)
    scale, duration = edits.clock(edits.child(edits.child(track, b"mdia"), b"mdhd"))
    assert scale == 48000
    entries = [(1806, -1, 1, 0), (duration - 2112, 2112, 1, 0)]
    payload = b"\0" * 4 + struct.pack(">I", len(entries))
    payload += b"".join(struct.pack(">Iihh", *row) for row in entries)
    elst = struct.pack(">I4s", len(payload) + 8, b"elst") + payload
    raw = struct.pack(">I4s", len(elst) + 8, b"edts") + elst
    track = edits.replace_child(track, b"edts", next(edits.atoms(raw)))
    total = sum(row[0] for row in entries)
    track = edits.replace_child(
        track, b"tkhd", edits.set_track_duration(edits.child(track, b"tkhd"), total)
    )
    rebuilt = edits.replace_child(moov, b"trak", track)
    rebuilt = edits.replace_child(
        rebuilt, b"mvhd", edits.set_clock(edits.child(rebuilt, b"mvhd"), scale, total)
    )
    rebuilt = edits.shift_offsets(rebuilt, start, start + size, len(rebuilt.raw) - size)
    old = path.read_bytes()
    path.write_bytes(old[:start] + rebuilt.raw + old[start + size :])
    source.update(bytes=path.stat().st_size, mtime_ns=path.stat().st_mtime_ns)
    return source


def test_original_edits_repair_real_decoder_and_faststart_offsets(
    tmp_path, edited_source
):
    plain = tmp_path / "plain.m4a"
    repaired = tmp_path / "repaired.m4a"
    original = edited_source["path"]
    before = module.file_digest(original)
    module.remux_aac(original, plain)
    assert module.packet_summary(plain) == module.packet_summary(original)
    assert module.decoded_summary(plain) != module.decoded_summary(original)
    result = edits.preserve_audio_edits(original, plain, repaired)
    assert result["compressed_samples_reencoded"] is False
    assert result["metadata_size_delta"] != 0
    assert edits.movie(plain)[0] < plain.stat().st_size // 2  # faststart moov
    assert module.packet_summary(repaired) == module.packet_summary(original)
    assert module.decoded_summary(repaired) == module.decoded_summary(original)
    assert module.decoded_summary(original)["first_seconds"] == 1806 / 48000
    assert module.file_digest(original) == before
    store = KnowledgeStore(tmp_path / "kb")
    value = module.archive_one(store, edited_source)
    assert value["verified"] and value["schema"] == 2
    assert value["edit_repair"]["movie_timescale"] == 48000
    assert value["decoded"]["timeline_digest"]
    assert module.archive_one(store, edited_source)["cached"]


def test_full_validation_preserves_nonzero_original_movie_clock(
    tmp_path, edited_source
):
    store = KnowledgeStore(tmp_path / "kb")
    archived = module.archive_one(store, edited_source)
    result = validation.validate_archived_audio(store, edited_source)
    assert result["decoded"]["first_seconds"] == 1806 / 48000
    assert result["decoded"] == archived["decoded"]


def test_target_decode_error_can_repair_without_ignoring_source_errors(
    tmp_path, edited_source, monkeypatch
):
    import av

    decode = module.decoded_summary
    attempted = []

    def broken_copy(path, checkpoint=lambda: None):
        attempted.append(str(path))
        if str(path).endswith("audio.m4a"):
            raise av.error.InvalidDataError(1094995529, "test exposed priming packet")
        return decode(path, checkpoint)

    monkeypatch.setattr(module, "decoded_summary", broken_copy)
    assert module.archive_one(KnowledgeStore(tmp_path / "kb"), edited_source)[
        "verified"
    ]
    assert len(attempted) == 3


def test_source_decode_error_never_repaired(tmp_path, source, monkeypatch):
    import av

    before = module.file_digest(source["path"])

    def broken_source(*args):
        raise av.error.InvalidDataError(1094995529, "test source decode failure")

    def no_repair(*args):
        pytest.fail("Source decode failure must not be hidden by container repair")

    monkeypatch.setattr(module, "decoded_summary", broken_source)
    monkeypatch.setattr(edits, "preserve_audio_edits", no_repair)
    store = KnowledgeStore(tmp_path / "kb")
    with pytest.raises(av.error.InvalidDataError):
        module.archive_one(store, source)
    assert not list(store.root.glob("lessons/*/audio.json"))
    assert module.file_digest(source["path"]) == before


@pytest.mark.parametrize("kind,width", [(b"stco", 4), (b"co64", 8)])
def test_chunk_offsets_reject_overflow_and_metadata_reference(kind, width):
    def atom(offset):
        payload = b"\0" * 4 + struct.pack(">I", 1) + offset.to_bytes(width, "big")
        return next(edits.atoms(struct.pack(">I4s", len(payload) + 8, kind) + payload))

    with pytest.raises(ValueError, match="overflow"):
        edits.shift_offsets(atom((1 << (8 * width)) - 1), 20, 100, 1)
    with pytest.raises(ValueError, match="inside metadata"):
        edits.shift_offsets(atom(30), 20, 100, 1)


def test_large_movie_and_track_duration_upgrade_preserves_other_fields(
    tmp_path, source
):
    _, _, movie = edits.movie(source["path"])
    original = edits.child(movie, b"mvhd")
    track = edits.child(edits.audio_track(movie), b"tkhd")
    large = 2**32 + 100
    updated = edits.set_clock(original, 48000, large)
    updated_track = edits.set_track_duration(track, large)
    assert edits.clock(updated) == (48000, large)
    assert edits.track_duration(updated_track) == large
    assert updated.payload[32:] == original.payload[20:]
    assert updated_track.payload[36:] == track.payload[24:]
