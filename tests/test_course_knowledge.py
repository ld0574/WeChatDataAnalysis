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
