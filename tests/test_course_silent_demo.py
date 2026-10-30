import json

import pytest
from fastapi.testclient import TestClient

from wechat_decrypt_tool import course_knowledge as knowledge
from wechat_decrypt_tool.course_backup import create_snapshot, restore_snapshot
from wechat_decrypt_tool.course_resource_archive import archive_resources
from wechat_decrypt_tool.course_silent_demo import archive_silent_demos, retained_demo


def test_silent_motion_is_searchable_playable_resumable_and_portable(
    tmp_path, monkeypatch
):
    av = pytest.importorskip("av")
    np = pytest.importorskip("numpy")
    source = tmp_path / "source"
    source.mkdir()
    video = source / "synthetic.mp4"
    with av.open(str(video), "w") as media:
        stream = media.add_stream("mpeg4", rate=1)
        stream.width = 32
        stream.height = 32
        stream.pix_fmt = "yuv420p"
        for n in range(3):
            frame = av.VideoFrame.from_ndarray(
                np.full((32, 32, 3), n * 100, dtype="uint8"), format="rgb24"
            )
            for packet in stream.encode(frame):
                media.mux(packet)
        for packet in stream.encode():
            media.mux(packet)
    original = video.read_bytes()
    item = knowledge.inspect_video(video, source)
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps({"excluded_without_audio": [item]}))
    store = knowledge.KnowledgeStore(tmp_path / "working")
    monkeypatch.setattr(
        "wechat_decrypt_tool.course_frame_ocr.local_model_digest",
        lambda _: "synthetic-weights",
    )
    archive_resources(store, source, inventory, reserve_gib=0)
    calls = []

    def chat(*args, **kwargs):
        with pytest.raises(knowledge.GpuBusyError):
            with knowledge.gpu_lease(store):
                pass
        calls.append(1)
        return "silent_visual_marker"

    monkeypatch.setattr(knowledge, "local_chat", chat)
    for _ in range(2):
        result = archive_silent_demos(store, inventory, "local")
    assert not result["errors"] and len(result["completed"]) == 1 and len(calls) == 3
    key = result["completed"][0]["video"]
    assert result["completed"][0]["frames_decoded"] == 3 and store.search(
        "silent_visual_marker"
    )
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM frames").fetchone()[0] == 3
        assert db.execute("SELECT status FROM videos").fetchone()[0] == "visual_only"
    video.unlink()
    assert retained_demo(store, key)
    backup = create_snapshot(store, tmp_path / "backup", reserve_gib=0)
    restored = knowledge.KnowledgeStore(
        restore_snapshot(backup["snapshot"], tmp_path / "restored")
    )
    with TestClient(knowledge.create_app(restored, "unused")) as client:
        response = client.get("/silent-demo/" + key, headers={"Range": "bytes=0-31"})
        assert response.status_code == 206 and response.content == original[:32]
        assert "<video controls" in client.get("/lesson/" + key).text
        assert client.get("/silent-demo/not-a-valid-key").status_code == 404
    offline = knowledge.export_review(restored, key).read_text(encoding="utf-8")
    assert "<video controls" in offline and "../../resources/objects/" in offline
    raw = retained_demo(restored, key)
    (restored.root / raw["object"]).write_bytes(b"corrupt")
    with TestClient(knowledge.create_app(restored, "unused")) as client:
        assert client.get("/silent-demo/" + key).status_code == 503
