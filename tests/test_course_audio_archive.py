import json

import pytest

from wechat_decrypt_tool.course_knowledge import (
    KnowledgeStore,
    create_app,
    export_review,
)
from wechat_decrypt_tool import course_audio_archive as module


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
