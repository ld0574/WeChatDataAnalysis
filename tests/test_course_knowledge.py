import pytest

from wechat_decrypt_tool.course_knowledge import KnowledgeStore, validate_note


def register(store, tmp_path):
    video = tmp_path / "source.mp4"
    video.write_bytes(b"source identity")
    return store.register(
        {"path": str(video), "title": "安装 Python", "duration_seconds": 50}
    )


def test_resume_does_not_duplicate_committed_segments_and_searches_cjk(tmp_path):
    store = KnowledgeStore(tmp_path / "kb")
    key = register(store, tmp_path)
    store.save_segment(key, 0, 0, 25, "安装 Python 需要配置环境变量 PATH。")
    store.save_segment(key, 0, 0, 25, "安装 Python 需要配置环境变量 PATH。")
    resumed = KnowledgeStore(tmp_path / "kb")
    hits = resumed.search("环境变量")
    assert len(hits) == 1
    assert hits[0]["start"] == 0 and hits[0]["end"] == 25
    assert resumed.status()["segments"] == 1
    assert resumed.status()["deletion_ready"] is False


def test_changed_source_cannot_overwrite_old_archive(tmp_path):
    store = KnowledgeStore(tmp_path / "kb")
    register(store, tmp_path)
    (tmp_path / "source.mp4").write_bytes(b"changed source bytes")
    with pytest.raises(RuntimeError, match="Source video changed"):
        store.register({"path": str(tmp_path / "source.mp4"), "duration_seconds": 50})


def test_changed_segment_is_not_silently_accepted(tmp_path):
    store = KnowledgeStore(tmp_path / "kb")
    key = register(store, tmp_path)
    store.save_segment(key, 0, 0, 25, "原始识别")
    with pytest.raises(RuntimeError, match="refusing to overwrite"):
        store.save_segment(key, 0, 0, 25, "不同内容")


def test_hallucinated_evidence_and_graph_endpoints_are_rejected():
    note = {
        "summary": "摘要",
        "concepts": [{"name": "Python", "explanation": "语言", "evidence": [1]}],
        "relations": [],
        "questions": [],
    }
    assert validate_note(note, {1}) == note
    note["concepts"][0]["evidence"] = [999]
    with pytest.raises(ValueError, match="nonexistent evidence"):
        validate_note(note, {1})
    note["concepts"][0]["evidence"] = [1]
    note["relations"] = [
        {"from": "Python", "to": "invented", "relation": "requires", "evidence": [1]}
    ]
    with pytest.raises(ValueError, match="no concept node"):
        validate_note(note, {1})


def test_visual_search_update_scope_and_script_safe_review(tmp_path):
    from wechat_decrypt_tool.course_knowledge import create_app, export_review, tokens
    from fastapi.testclient import TestClient

    store = KnowledgeStore(tmp_path / "kb")
    key = register(store, tmp_path)
    store.save_segment(key, 0, 0, 25, "安装 Python </script><script>alert(1)</script>")
    with store.connect() as c:
        c.execute(
            "INSERT INTO frames(video,time,path) VALUES(?,?,?)",
            (key, 0, f"lessons/{key}/frames/0.jpg"),
        )
        c.execute(
            "UPDATE frames SET description=?,search_text=?,status='ready'",
            ("环境变量 PATH", " ".join(tokens("环境变量 PATH"))),
        )
    assert store.search("环境变量", video_id=key)[0]["kind"] == "frame"
    assert not store.search("环境变量", video_id="other-course")
    page = export_review(store, key).read_text(encoding="utf-8")
    assert "&lt;/script&gt;" in page and "</script><script>alert" not in page
    with TestClient(create_app(store, "unused")) as client:
        assert (
            client.get("/search", params={"q": "环境变量", "video_id": key}).json()[0][
                "id"
            ]
            == -1
        )
        assert client.get("/frame/1").status_code == 404
        assert (
            client.get("/evidence/1", follow_redirects=False)
            .headers["location"]
            .endswith("#s1")
        )
        assert client.post("/pause").json()["requested"] is True


def test_resources_are_idempotent_read_only_and_skip_unsafe_archive_members(tmp_path):
    import zipfile
    from wechat_decrypt_tool.course_knowledge import index_courseware

    source = tmp_path / "course"
    source.mkdir()
    original = b'api_key="not-a-real-secret"\nprint("Python environment")'
    path = source / "demo.py"
    path.write_bytes(original)
    with zipfile.ZipFile(source / "samples.zip", "w") as z:
        z.writestr("../escape.py", "escape_marker")
        z.writestr("node_modules/dependency.js", "dependency_marker")
        z.writestr("lesson/hello.py", 'print("course_marker")')
    store = KnowledgeStore(tmp_path / "kb")
    for _ in range(2):
        report = index_courseware(store, source)
    assert report["errors"] == [] and path.read_bytes() == original
    assert store.status()["document_chunks"] == 2
    assert not store.search("escape_marker") and not store.search("dependency_marker")
    with store.connect() as c:
        content = " ".join(r[0] for r in c.execute("SELECT text FROM documents"))
    assert "not-a-real-secret" not in content and "[REDACTED]" in content


def test_only_byte_identical_videos_are_aliased(tmp_path):
    import json
    from wechat_decrypt_tool.course_knowledge import prepare_inventory

    rows = []
    for i, data in enumerate((b"aaa", b"aaa", b"bbb")):
        p = tmp_path / f"{i}.mp4"
        p.write_bytes(data)
        s = p.stat()
        rows.append(
            {
                "path": str(p),
                "title": "same-title",
                "bytes": s.st_size,
                "mtime_ns": s.st_mtime_ns,
                "duration_seconds": 10,
                "audio_streams": [{}],
            }
        )
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps({"videos": rows}), encoding="utf-8")
    store = KnowledgeStore(tmp_path / "kb")
    result = prepare_inventory(store, inventory)
    assert (
        result["unique_readable_videos"] == 2
        and result["verified_duplicate_paths"] == 1
    )
    for row in result["videos"]:
        store.register(row)
    assert store.status()["verified_duplicate_video_paths"] == 1


def test_answer_validates_combined_citations_and_rejects_nonexistent_ones(
    tmp_path, monkeypatch
):
    from wechat_decrypt_tool import course_knowledge as module

    store = KnowledgeStore(tmp_path / "kb")
    key = register(store, tmp_path)
    store.save_segment(key, 0, 0, 25, "Python 配置 PATH。")
    store.save_segment(key, 1, 25, 50, "Python 漏选时手动配置。")
    monkeypatch.setattr(
        module, "local_chat", lambda *a, **kw: "Python 需要配置。[证据 S1, S2]"
    )
    value = module.answer(store, "Python", "unused", key)
    assert set(value["citations"]) == {1, 2} and len(value["sources"]) == 2
    monkeypatch.setattr(
        module, "local_chat", lambda *a, **kw: "Python 不存在的证据。[S999]"
    )
    with pytest.raises(RuntimeError, match="引用检查"):
        module.answer(store, "Python", "unused", key)


def test_exclusive_gpu_lease_is_released_after_exception(tmp_path):
    from wechat_decrypt_tool.course_knowledge import gpu_lease

    store = KnowledgeStore(tmp_path / "kb")
    with pytest.raises(ValueError):
        with gpu_lease(store):
            with pytest.raises(RuntimeError, match="GPU"):
                with gpu_lease(store):
                    pass
            raise ValueError("release")
    with gpu_lease(store):
        pass


def test_checkpoint_pauses_before_disk_exhaustion(tmp_path, monkeypatch):
    import shutil

    store = KnowledgeStore(tmp_path / "kb")
    monkeypatch.setattr(
        shutil, "disk_usage", lambda path: shutil._ntuple_diskusage(10, 9, 1)
    )
    with pytest.raises(InterruptedError, match="8 GiB"):
        store.checkpoint()


def test_atomic_status_retries_windows_reader_sharing_error(tmp_path, monkeypatch):
    from pathlib import Path
    from wechat_decrypt_tool.course_knowledge import atomic_json

    original = Path.replace
    calls = []

    def replace(path, target):
        calls.append(1)
        if len(calls) == 1:
            raise PermissionError("Windows reader has not closed yet")
        return original(path, target)

    monkeypatch.setattr(Path, "replace", replace)
    target = tmp_path / "progress.json"
    atomic_json(target, {"stage": "done"})
    assert "done" in target.read_text(encoding="utf-8") and len(calls) == 2
    assert not list(tmp_path.glob("*.tmp"))


def test_note_retries_invalid_relationship_without_inventing_nodes(monkeypatch):
    import json
    from wechat_decrypt_tool import course_knowledge as module

    invalid = {
        "summary": "课程摘要",
        "concepts": [{"name": "Python", "explanation": "课程用语言", "evidence": [1]}],
        "relations": [
            {"from": "Python", "to": "invented", "relation": "uses", "evidence": [1]}
        ],
        "questions": [],
    }
    valid = {**invalid, "relations": []}
    responses = iter((invalid, valid))
    requests = []

    def chat(model, messages, **kw):
        requests.append(list(messages))
        return json.dumps(next(responses))

    monkeypatch.setattr(module, "local_chat", chat)
    assert (
        module.generate_note("local", [{"id": 1, "text": "Python"}], [], {1}) == valid
    )
    assert (
        len(requests) == 2
        and "Graph edge has no concept node" in requests[-1][-1]["content"]
    )


def test_question_words_do_not_hide_an_explicit_technical_anchor(tmp_path, monkeypatch):
    from wechat_decrypt_tool import course_knowledge as module

    store = KnowledgeStore(tmp_path / "kb")
    key = register(store, tmp_path)
    store.save_segment(key, 0, 0, 25, "下一节讲 Mac OS 环境搭建。")
    calls = []

    def chat(model, messages, **kwargs):
        calls.append(messages)
        return "本节未提供 Mac 的具体步骤，讲师预告下一节介绍。[证据 S1]"

    monkeypatch.setattr(module, "local_chat", chat)
    value = module.answer(
        store, "本课是否给出了 Mac 安装的具体操作步骤？", "local", key
    )
    assert len(calls) == 1 and value["citations"] == [1]


def test_legacy_metadata_encoding_does_not_exclude_readable_video(tmp_path):
    av = pytest.importorskip("av")
    np = pytest.importorskip("numpy")
    from wechat_decrypt_tool.course_knowledge import inspect_video, capture_frames

    source = tmp_path / "legacy.mp4"
    marker = b"legacy-comment-marker"
    with av.open(str(source), "w") as media:
        media.metadata["comment"] = marker.decode()
        stream = media.add_stream("mpeg4", rate=1)
        stream.width = 32
        stream.height = 32
        stream.pix_fmt = "yuv420p"
        for n in range(2):
            frame = av.VideoFrame.from_ndarray(
                np.full((32, 32, 3), n * 100, dtype="uint8"), format="rgb24"
            )
            for packet in stream.encode(frame):
                media.mux(packet)
        for packet in stream.encode():
            media.mux(packet)
    data = source.read_bytes()
    assert marker in data
    source.write_bytes(data.replace(marker, b"\xff" + marker[1:]))
    with pytest.raises(UnicodeDecodeError):
        av.open(str(source))
    item = inspect_video(source, tmp_path)
    assert "error" not in item and item["video_streams"][0]["width"] == 32
    store = KnowledgeStore(tmp_path / "kb")
    key = store.register(item)
    capture_frames(store, key, step=1)
    with store.connect() as c:
        assert (
            c.execute("SELECT COUNT(*) FROM frames WHERE video=?", (key,)).fetchone()[0]
            == 2
        )


def test_graph_repair_limits_endpoints_and_preserves_claims(monkeypatch):
    import json
    from wechat_decrypt_tool import course_knowledge as module

    note = {
        "summary": "synthetic",
        "concepts": [
            {"name": "A", "explanation": "synthetic A", "evidence": [7]},
            {"name": "B", "explanation": "synthetic B", "evidence": [7]},
        ],
        "relations": [
            {"from": "alias A", "to": "B", "relation": "synthetic", "evidence": [7]}
        ],
        "questions": [],
    }
    calls = []

    def chat(model, messages, schema):
        calls.append(schema)
        if len(calls) == 1:
            return json.dumps(note)
        props = schema["properties"]["relations"]["items"]["properties"]
        assert props["from"]["enum"] == ["A", "B"] == props["to"]["enum"]
        assert props["evidence"]["items"]["enum"] == [7]
        return json.dumps({"relations": [{**note["relations"][0], "from": "A"}]})

    monkeypatch.setattr(module, "local_chat", chat)
    result = module.generate_note("local", [], [], {7})
    assert (
        result["concepts"] == note["concepts"] and result["relations"][0]["from"] == "A"
    )
    assert len(calls) == 2


def test_waiting_gpu_is_resumable_and_releases_after_exception(tmp_path, monkeypatch):
    from wechat_decrypt_tool import course_knowledge as module

    store = KnowledgeStore(tmp_path / "kb")
    holder = module.gpu_lease(store)
    holder.__enter__()
    sleeps = []

    def release_once(_):
        sleeps.append(1)
        holder.__exit__(None, None, None)

    monkeypatch.setattr(module.time, "sleep", release_once)
    with pytest.raises(ValueError, match="synthetic"):
        with module.wait_gpu_lease(store):
            raise ValueError("synthetic")
    assert len(sleeps) == 1
    with module.gpu_lease(store):
        pass
    (store.root / "pause.flag").write_text("test")
    with pytest.raises(InterruptedError):
        with module.wait_gpu_lease(store):
            pass


def test_only_validated_partial_media_survives_inventory_refresh(tmp_path, monkeypatch):
    import hashlib
    import json
    from wechat_decrypt_tool import course_knowledge as module

    source = tmp_path / "source"
    source.mkdir()
    path = source / "lesson.mp4.downloading"
    path.write_bytes(b"synthetic-media")
    (source / "unverified.mp4.downloading").write_bytes(b"partial")
    store = KnowledgeStore(tmp_path / "kb")
    proof = {
        "path": str(path),
        "bytes": path.stat().st_size,
        "mtime_ns": path.stat().st_mtime_ns,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "continuous_declared_audio": True,
        "source_unchanged": True,
    }
    (store.root / "recovered-media.json").write_text(json.dumps([proof]))
    monkeypatch.setattr(
        module, "inspect_video", lambda p, _: {"path": str(p), "audio_streams": [{}]}
    )
    result = module.inventory_videos(store, source)
    assert (
        len(result["videos"]) == 1 and result["videos"][0]["sha256"] == proof["sha256"]
    )
    path.write_bytes(b"changed")
    result = module.inventory_videos(store, source)
    assert (
        "changed" in result["videos"][0]["error"]
        and "sha256" not in result["videos"][0]
    )


def test_run_releases_gpu_between_asr_and_cpu_capture(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    from wechat_decrypt_tool import course_knowledge as module

    source = tmp_path / "source.mp4"
    source.write_bytes(b"synthetic")
    store = KnowledgeStore(tmp_path / "kb")
    inventory = tmp_path / "inventory.json"
    inventory.write_text(
        json.dumps(
            {
                "videos": [
                    {"path": str(source), "duration_seconds": 25, "audio_streams": [{}]}
                ]
            }
        )
    )
    phases = []

    def transcribe(*args, **kwargs):
        assert kwargs["batch_size"] == 2
        with pytest.raises(module.GpuBusyError):
            with module.gpu_lease(store):
                pass
        phases.append("asr")

    def capture(*args):
        with module.gpu_lease(store):
            pass
        phases.append("cpu_capture")

    def enrich(*args, cooperative):
        assert cooperative
        with module.gpu_lease(store):
            pass
        phases.append("cooperative_enrichment")

    monkeypatch.setattr(module, "transcribe", transcribe)
    monkeypatch.setattr(module, "capture_frames", capture)
    monkeypatch.setattr(module, "enrich", enrich)
    monkeypatch.setattr(module, "unload_model", lambda _: None)
    module.run_courses(
        store,
        SimpleNamespace(
            inventory=inventory,
            deduplicate=False,
            video=None,
            model="local",
            asr_model=tmp_path / "model",
            enrich=True,
            limit=0,
        ),
    )
    assert phases == ["asr", "cpu_capture", "cooperative_enrichment"]


def test_remote_model_rejected_before_any_course_chat_request(monkeypatch):
    from wechat_decrypt_tool import course_knowledge as module

    def reject(_):
        raise RuntimeError("Cloud model forbidden for course OCR")

    monkeypatch.setattr("wechat_decrypt_tool.course_pdf_ocr.local_model_digest", reject)
    monkeypatch.setattr(
        "httpx.Client",
        lambda *args, **kwargs: pytest.fail(
            "Course chat sent before local model validation"
        ),
    )
    with pytest.raises(RuntimeError, match="Cloud model forbidden"):
        module.local_chat(
            "remote", [{"role": "user", "content": "synthetic confidential marker"}]
        )


def test_local_chat_recovers_transient_server_error_with_bounded_retry(monkeypatch):
    import httpx
    from wechat_decrypt_tool import course_knowledge as module

    calls = []
    delays = []
    guards = []
    monkeypatch.setattr(
        "wechat_decrypt_tool.course_pdf_ocr.local_model_digest",
        lambda model: guards.append(model) or "local-weights",
    )
    monkeypatch.setattr(module.time, "sleep", delays.append)

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, json):
            calls.append(json)
            return httpx.Response(
                500 if len(calls) == 1 else 200,
                request=httpx.Request("POST", url),
                json={
                    "done_reason": "stop",
                    "message": {"content": "synthetic recovered reply"},
                },
            )

    monkeypatch.setattr(httpx, "Client", Client)
    assert (
        module.local_chat("local", [{"role": "user", "content": "synthetic input"}])
        == "synthetic recovered reply"
    )
    assert (
        len(calls) == 2 and calls[0] == calls[1] and delays == [1] and len(guards) == 2
    )


@pytest.mark.parametrize("status,expected_attempts", [(500, 3), (400, 1)])
def test_local_chat_stops_retrying_permanent_failures(
    monkeypatch, status, expected_attempts
):
    import httpx
    from wechat_decrypt_tool import course_knowledge as module

    calls = []
    monkeypatch.setattr(
        "wechat_decrypt_tool.course_pdf_ocr.local_model_digest",
        lambda _: "local-weights",
    )
    monkeypatch.setattr(module.time, "sleep", lambda _: None)

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, json):
            calls.append(1)
            return httpx.Response(
                status,
                request=httpx.Request("POST", url),
                json={"error": "synthetic server error"},
            )

    monkeypatch.setattr(httpx, "Client", Client)
    with pytest.raises(httpx.HTTPStatusError):
        module.local_chat("local", [{"role": "user", "content": "synthetic input"}])
    assert len(calls) == expected_attempts


def test_asr_batch4_memory_retry_preserves_every_window_without_duplicates(
    tmp_path, monkeypatch
):
    import wave
    from pathlib import Path
    from wechat_decrypt_tool import course_knowledge as module

    source = tmp_path / "silence.wav"
    with wave.open(str(source), "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(16000)
        file.writeframes(b"\0\0" * (16000 * 150))
    store = KnowledgeStore(tmp_path / "kb")
    key = store.register({"path": str(source), "duration_seconds": 150})
    calls = []

    class Backend:
        precision = "float32"
        is_loaded = False
        last_batch_metrics = {}

        def __init__(self, *args, **kwargs):
            assert kwargs.get("idle_timeout") == 900
            pass

        def transcribe_audio_batch(self, paths, language, *, batch_size, gpu_precision):
            assert gpu_precision == "float32"
            calls.append((len(paths), batch_size))
            if batch_size == 4:
                raise RuntimeError("CUDA out of memory")
            return [{"text": "synthetic_" + Path(p).stem} for p in paths]

        def close(self):
            pass

    monkeypatch.setattr("wechat_decrypt_tool.asr_worker.ProcessBackend", Backend)
    module.transcribe(store, key, tmp_path / "model", batch_size=4)
    assert calls == [(4, 4), (4, 2), (2, 2)]
    with store.connect() as db:
        rows = db.execute(
            "SELECT n,start,end FROM segments WHERE video=? ORDER BY n", (key,)
        ).fetchall()
        assert [row["n"] for row in rows] == list(range(6))
        assert rows[0]["start"] == 0 and rows[-1]["end"] == 150
        assert (
            db.execute("SELECT status FROM videos WHERE id=?", (key,)).fetchone()[0]
            == "transcribed"
        )
        assert (
            db.execute(
                "SELECT count(*) FROM events WHERE kind='asr_batch_fallback'"
            ).fetchone()[0]
            == 1
        )


def test_repeat_limit_server_error_is_classified_before_blind_retry(monkeypatch):
    import httpx
    from wechat_decrypt_tool import course_knowledge as module

    calls = []
    monkeypatch.setattr(
        "wechat_decrypt_tool.course_pdf_ocr.local_model_digest",
        lambda _: "local-weights",
    )
    monkeypatch.setattr(
        module.time,
        "sleep",
        lambda _: pytest.fail(
            "Deterministic repeat abort must be subdivided, not blindly retried"
        ),
    )

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, json):
            calls.append(1)
            return httpx.Response(
                500,
                request=httpx.Request("POST", url),
                json={"error": "prediction aborted, token repeat limit reached"},
            )

    monkeypatch.setattr(httpx, "Client", Client)
    with pytest.raises(module.ModelRepetitionError):
        module.local_chat("local", [{"role": "user", "content": "synthetic input"}])
    assert len(calls) == 1
