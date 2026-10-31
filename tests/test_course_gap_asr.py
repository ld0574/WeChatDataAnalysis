import hashlib
import json
from pathlib import Path

import pytest

from wechat_decrypt_tool import course_gap_asr as module
from wechat_decrypt_tool import course_knowledge as knowledge
from wechat_decrypt_tool.course_audio_archive import file_digest
from wechat_decrypt_tool.course_gap_audio import recover_gap_audio


@pytest.fixture
def recovered(tmp_path):
    """Real damaged AAC with readable sound on both sides of a bad packet."""
    import av
    import numpy as np

    path = tmp_path / "source.mp4"
    with av.open(str(path), "w") as media:
        stream = media.add_stream("aac", rate=48000)
        stream.layout = "mono"
        for n in range(48):
            frame = av.AudioFrame.from_ndarray(
                np.full((1, 1024), 0.1, dtype="float32"), format="fltp", layout="mono"
            )
            frame.sample_rate, frame.pts = 48000, n * 1024
            for packet in stream.encode(frame):
                media.mux(packet)
        for packet in stream.encode():
            media.mux(packet)
    with av.open(str(path)) as media:
        packets = [p for p in media.demux(audio=0) if p.size]
        bad = packets[15]
        position, size = bad.pos, bad.size
    data = bytearray(path.read_bytes())
    data[position : position + size] = b"\0" * size
    path.write_bytes(data)
    item = {
        "path": str(path),
        "bytes": path.stat().st_size,
        "mtime_ns": path.stat().st_mtime_ns,
        "duration_seconds": 1.024,
        "sha256": file_digest(path),
        "audio_streams": [{"codec": "aac"}],
    }
    store = knowledge.KnowledgeStore(tmp_path / "kb")
    key = store.register(item)
    value = recover_gap_audio(store, item)
    assert len(value["clips"]) >= 2 and value["gaps"]
    with store.connect() as db:
        video = dict(db.execute("SELECT * FROM videos WHERE id=?", (key,)).fetchone())
    return store, video, value


class Backend:
    is_loaded = False
    precision = "float32"
    calls = 0

    def __init__(self, *args, **kwargs):
        assert args[2] == "float32" and kwargs["idle_timeout"] == 900

    def transcribe_audio_batch(self, paths, language, **kwargs):
        type(self).calls += len(paths)
        return [{"text": "gap_asr_fixture"} for _ in paths]

    def close(self):
        pass


def test_gap_asr_uses_independent_pcm_windows_and_absolute_source_timestamps(
    tmp_path, recovered, monkeypatch
):
    import numpy as np
    import wave

    store, video, value = recovered
    binding = module.bind_source(store, video, [])
    assert binding["complete_original_audio_verified"] is False
    plan = binding["windows"]
    assert len(plan) == len(value["clips"])
    for (n, start, samples), row in zip(
        module.audio_windows(store, video["id"], binding), plan
    ):
        assert n == row["n"] and start == row["start"]
        with wave.open(
            str(store.root / "lessons" / video["id"] / row["file"]), "rb"
        ) as reader:
            expected = np.frombuffer(
                reader.readframes(reader.getnframes()), dtype="<i2"
            )
        np.testing.assert_array_equal(samples, expected.astype("float32") / 32768)
    monkeypatch.setattr("wechat_decrypt_tool.asr_worker.ProcessBackend", Backend)
    Backend.calls = 0
    knowledge.transcribe(store, video["id"], tmp_path / "model", batch_size=4)
    with store.connect() as db:
        rows = list(
            db.execute(
                "SELECT * FROM segments WHERE video=? ORDER BY n", (video["id"],)
            )
        )
        state = dict(
            db.execute("SELECT * FROM videos WHERE id=?", (video["id"],)).fetchone()
        )
    module.validate_segments(rows, plan, complete=True)
    assert rows[1]["start"] > rows[0]["end"]
    assert state["status"] == "transcribed_with_gaps"
    assert state["decoded_seconds"] == pytest.approx(binding["recovered_seconds"])
    ids = [r["id"] for r in rows]
    knowledge.transcribe(store, video["id"], tmp_path / "model", batch_size=4)
    assert Backend.calls == len(plan)
    with store.connect() as db:
        assert [
            r[0]
            for r in db.execute(
                "SELECT id FROM segments WHERE video=? ORDER BY n", (video["id"],)
            )
        ] == ids


def test_plan_splits_long_clips_at_integer_samples_without_crossing_gap():
    value = {
        "clips": [
            {
                "start_rational": [1, 3],
                "samples": 400001,
                "file": "gap-audio/00000.wav",
            },
            {"start_rational": [30, 1], "samples": 16, "file": "gap-audio/00001.wav"},
        ]
    }
    plan = module.window_plan(value)
    assert [r["samples"] for r in plan] == [400000, 1, 16]
    assert plan[0]["end"] == plan[1]["start"]
    assert plan[2]["start"] == 30 and plan[1]["end"] < 30
    with pytest.raises(RuntimeError, match="missing planned"):
        module.validate_segments(plan[:2], plan, complete=True)
    with pytest.raises(RuntimeError, match="immutable clip plan"):
        module.validate_segments([{**plan[1], "start": plan[0]["end"] + 0.1}], plan)


def test_binding_refuses_unbound_segments_changed_manifest_and_missing_recovery(
    recovered,
):
    store, video, value = recovered
    with pytest.raises(RuntimeError, match="no gap-source binding"):
        module.bind_source(store, video, [{"n": 0, "start": 0, "end": 0.1}])
    module.bind_source(store, video, [])
    path = store.root / "lessons" / video["id"] / "audio-gap-recovery.json"
    value["updated"] += 1
    path.write_text(json.dumps(value), "utf-8")
    with pytest.raises(RuntimeError, match="binding differs"):
        module.bind_source(store, video, [])
    path.unlink()
    with pytest.raises(RuntimeError, match="recovery is missing"):
        module.bind_source(store, video, [])


def test_gap_review_search_playback_and_portable_restore(
    tmp_path, recovered, monkeypatch
):
    from fastapi.testclient import TestClient
    from wechat_decrypt_tool.course_backup import create_snapshot, restore_snapshot

    store, video, value = recovered
    key = video["id"]
    monkeypatch.setattr("wechat_decrypt_tool.asr_worker.ProcessBackend", Backend)
    knowledge.transcribe(store, key, tmp_path / "model")
    offline = knowledge.export_review(store, key).read_text("utf-8")
    assert module.NOTICE in offline and "缺失区间" in offline
    assert 'src="gap-audio/00001.wav"' in offline
    snapshot = create_snapshot(store, tmp_path / "backup", reserve_gib=0)
    restored = knowledge.KnowledgeStore(
        restore_snapshot(snapshot["snapshot"], tmp_path / "restored", reserve_gib=0)
    )
    Path(video["path"]).unlink()
    folder = restored.root / "lessons" / key
    assert (
        json.loads((folder / "asr-gap-source.json").read_text("utf-8"))["method"]
        == module.METHOD
    )
    hits = restored.search("gap_asr_fixture")
    assert hits and len(hits) == len(value["clips"])
    assert all(hit["audio_coverage_notice"] == module.NOTICE for hit in hits)
    with TestClient(knowledge.create_app(restored, "unused")) as client:
        page = client.get("/lesson/" + key)
        assert page.status_code == 200 and module.NOTICE in page.text
        assert "缺失区间" in page.text and "播放对应恢复片段" in page.text
        assert client.get(
            "/evidence/" + str(hits[0]["id"]), follow_redirects=False
        ).status_code in (302, 307)
        for index, clip in enumerate(value["clips"]):
            response = client.get(f"/gap-audio/{key}/{index}")
            assert (
                response.status_code == 200
                and hashlib.sha256(response.content).hexdigest() == clip["sha256"]
            )
        assert client.get(f"/gap-audio/{key}/-1").status_code == 404
        assert client.get(f"/gap-audio/{key}/9999").status_code == 404
        assert client.get("/gap-audio/not-a-key/0").status_code == 404
        assert client.get("/audio/" + key).status_code == 404
        assert client.get("/audio-gap-recovery/" + key).json() == value
        assert (
            hashlib.sha256(client.get("/gap-damaged-audio/" + key).content).hexdigest()
            == value["original_packets"]["sha256"]
        )
        path = folder / value["clips"][0]["file"]
        data = bytearray(path.read_bytes())
        data[-1] ^= 1
        path.write_bytes(data)
        assert client.get(f"/gap-audio/{key}/0").status_code == 404


def test_answer_carries_explicit_incomplete_audio_context(recovered, monkeypatch):
    store, video, value = recovered
    row = module.window_plan(value)[0]
    store.save_segment(video["id"], 0, row["start"], row["end"], "gap_asr_fixture")
    prompts = []

    def chat(model, messages, **kwargs):
        prompts.append(messages)
        return "Fixture answer [证据 S1]。"

    monkeypatch.setattr(knowledge, "local_chat", chat)
    result = knowledge.answer(store, "gap_asr_fixture", "unused")
    assert result["audio_coverage_notice"] == module.NOTICE
    assert module.NOTICE in prompts[0][1]["content"]
    assert result["citations"] and result["sources"]


def test_note_groups_never_bridge_missing_audio(recovered):
    _, _, value = recovered
    rows = [
        {**r, "id": r["n"] + 1, "text": "fixture"} for r in module.window_plan(value)
    ]
    pages = module.note_pages(rows, value)
    assert len(pages) == len(value["clips"]) and all(len(page) == 1 for page in pages)
    assert module.note_pages(rows, None) == [rows]


def test_interrupted_gap_asr_keeps_committed_ids_and_resumes_only_missing_windows(
    tmp_path, recovered, monkeypatch
):
    store, video, value = recovered

    class InterruptedBackend(Backend):
        calls = 0

        def transcribe_audio_batch(self, paths, language, **kwargs):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("synthetic interrupted inference")
            return [{"text": "gap_asr_fixture"} for _ in paths]

    monkeypatch.setattr(
        "wechat_decrypt_tool.asr_worker.ProcessBackend", InterruptedBackend
    )
    with pytest.raises(RuntimeError, match="synthetic interrupted"):
        knowledge.transcribe(store, video["id"], tmp_path / "model", batch_size=1)
    with store.connect() as db:
        before = dict(
            db.execute(
                "SELECT * FROM segments WHERE video=?", (video["id"],)
            ).fetchone()
        )
    Backend.calls = 0
    monkeypatch.setattr("wechat_decrypt_tool.asr_worker.ProcessBackend", Backend)
    knowledge.transcribe(store, video["id"], tmp_path / "model", batch_size=1)
    assert Backend.calls == len(value["clips"]) - 1
    with store.connect() as db:
        assert (
            dict(
                db.execute(
                    "SELECT * FROM segments WHERE id=?", (before["id"],)
                ).fetchone()
            )
            == before
        )


def test_preparation_gate_does_not_start_raw_asr_and_reports_dead_worker(
    recovered, monkeypatch
):
    import psutil
    from wechat_decrypt_tool import course_audio_preparation as prep

    store, video, _ = recovered
    folder = store.root / "lessons" / video["id"]
    manifest = folder / "audio-gap-recovery.json"
    saved = manifest.read_bytes()
    manifest.unlink()
    worker = psutil.Process()
    store.write_json(
        "audio-preparation-plan.json",
        {
            "version": 1,
            "sources": [
                {
                    "video": video["id"],
                    "manifest": manifest.name,
                    "worker": {
                        "pid": worker.pid,
                        "created": worker.create_time(),
                        "script": worker.cmdline()[-1],
                    },
                }
            ],
        },
    )
    with pytest.raises(prep.AudioPreparationPending):
        knowledge.transcribe(store, video["id"], store.root / "model")
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM segments").fetchone()[0] == 0
        assert db.execute("SELECT status FROM videos").fetchone()[0] == "pending"
    # A live preparer publishes atomically while the GPU-free waiter yields.
    progress = []
    monkeypatch.setattr(
        store, "progress", lambda stage, **fields: progress.append((stage, fields))
    )
    monkeypatch.setattr(prep.time, "sleep", lambda seconds: manifest.write_bytes(saved))
    prep.await_prepared_audio(store, video["id"])
    assert progress[0][0] == "waiting_audio_preparation"
    assert progress[0][1]["gpu_lease_held"] is False
    manifest.unlink()
    plan_path = store.root / "audio-preparation-plan.json"
    plan = json.loads(plan_path.read_text("utf-8"))
    plan["sources"][0]["worker"]["created"] += 1
    plan_path.write_text(json.dumps(plan), "utf-8")
    with pytest.raises(RuntimeError, match="preparer exited"):
        prep.require_prepared_audio(store, video["id"])
