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
