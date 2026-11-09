import copy

import pytest

from wechat_decrypt_tool import course_source_partial_continue as m


def prepared(tmp_path, monkeypatch):
    root = (tmp_path / "archive").resolve()
    store = m.knowledge.KnowledgeStore(root)
    source = tmp_path / "synthetic.mp4"
    source.write_bytes(b"synthetic input never decoded")
    stat = source.stat()
    item = {"path": str(source), "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns,
        "duration_seconds": 10, "audio_streams": [{"codec": "synthetic"}]}
    target = m.queue.original.item_id(item)
    monkeypatch.setattr(m, "TARGET", target)
    lesson = root / "lessons" / target
    (lesson / "frames").mkdir(parents=True)
    for name in ("frames-completed.json", "audio.json", "evidence.json", "graph.json"):
        m.knowledge.atomic_json(lesson / name, {"synthetic": name})
    with store.write() as db:
        db.execute("INSERT INTO videos(id,path,title,size,mtime_ns,duration,status,error) VALUES(?,?,?,?,?,?,?,?)",
            (target, str(source), "synthetic", stat.st_size, stat.st_mtime_ns, 10, "transcribed", ""))
        db.execute("INSERT INTO segments(video,n,start,end,text,search_text) VALUES(?,?,?,?,?,?)",
            (target, 0, 0, 10, "synthetic complete ASR", "synthetic"))
        for index, state in ((1, "ready"), (2, "pending")):
            path = lesson / "frames" / f"{index}.jpg"
            path.write_bytes(b"synthetic retained pixels" + bytes([index]))
            db.execute("INSERT INTO frames(id,video,time,path,status,model,description) VALUES(?,?,?,?,?,?,?)",
                (index, target, index, path.relative_to(root).as_posix(), state,
                    m.MODEL if state == "ready" else "", "synthetic accepted OCR" if state == "ready" else ""))
    before = m.exports.read_rows(m.exports.ReadOnlyArchive(root), target)
    audit_folder = root / "wiki/partial-source-integrity-audits" / m.AUDIT
    audit_folder.mkdir(parents=True)
    m.knowledge.atomic_json(audit_folder / "manifest.json", {"synthetic": True})
    m.knowledge.atomic_json(audit_folder / "original-rows.json", before)
    frames = [{"id": index, "ready": index == 1, "cache_path": f"lessons/{target}/frame-ocr-cache/{index}",
        "cache_files": {}} for index in (1, 2)]
    audit = {"model_digest": "d" * 64, "ready_frames": 1, "pending_frames": 1, "frames": frames}
    monkeypatch.setattr(m, "audit_load", lambda *_: (audit_folder, audit, before))
    previous_key = "a" * 32
    previous_folder = root / "wiki/recovered-source-tails" / previous_key
    previous_folder.mkdir(parents=True)
    status = {"pid": 7, "created": 1, "queue": previous_key,
        "state": "waiting_for_exact_existing_inventory_queue", "completed_sources": []}
    previous = {"sources": []}
    for name, value in [("plan.json", previous), ("progress.json", status), ("started.json", m.identity(status))]:
        m.knowledge.atomic_json(previous_folder / name, value)
    monkeypatch.setattr(m.queue, "load", lambda *_: (root, previous_folder, previous, None, None))
    monkeypatch.setattr(m.queue.original, "identity_live", lambda _: True)
    monkeypatch.setattr(m.guard, "checkpoint", lambda _: None)
    monkeypatch.setattr(m.whole, "local_model_digest", lambda _: "d" * 64)
    m.knowledge.atomic_json(root / "inventory-unique.json", {"videos": [item] + [{"path": str(tmp_path / f"unused-{n}")} for n in range(339)]})
    result = m.prepare(root, previous_key, "http://127.0.0.1:3003")
    _, folder, plan = m.load(root, result["job"])
    return root, folder, plan, before, store


def test_prepare_and_readonly_load_preserve_inputs_and_do_not_call_models(tmp_path, monkeypatch):
    root, folder, plan, before, _ = prepared(tmp_path, monkeypatch)
    assert m.check_inputs(root, plan) == before
    assert not plan["asr_reexecution_allowed"] and not plan["frame_capture_allowed"]
    assert not plan["old_ready_frame_reexecution_allowed"] and not plan["model_budgets_reset"]
    old = {path: path.read_bytes() for path in root.rglob("*.json")}
    monkeypatch.setattr(m.whole, "local_model_digest", lambda _: pytest.fail("readonly contacted model"))
    monkeypatch.setattr(m.guard, "checkpoint", lambda _: pytest.fail("readonly wrote checkpoint"))
    assert m.load(root, folder.name)[2] == plan and m.check_inputs(root, plan) == before
    assert all(path.read_bytes() == raw for path, raw in old.items())


@pytest.mark.parametrize("change", ["asr", "image", "pending_cache", "checkpoint", "ready_frame"])
def test_changed_or_newly_attempted_inputs_cannot_enter_remaining_enrichment(tmp_path, monkeypatch, change):
    root, _, plan, _, store = prepared(tmp_path, monkeypatch)
    if change in {"asr", "ready_frame"}:
        with store.write() as db:
            db.execute("UPDATE segments SET text='different synthetic'" if change == "asr"
                else "UPDATE frames SET description='different synthetic' WHERE id=1")
    elif change == "image":
        (root / f"lessons/{m.TARGET}/frames/2.jpg").write_bytes(b"different synthetic pixels")
    elif change == "checkpoint":
        m.knowledge.atomic_json(root / f"lessons/{m.TARGET}/frames-completed.json", {"changed": True})
    else:
        cache = root / f"lessons/{m.TARGET}/frame-ocr-cache/2"
        cache.mkdir(parents=True)
        m.knowledge.atomic_json(cache / "attempt.json", {"synthetic": True})
    with pytest.raises(ValueError):
        m.check_inputs(root, plan)


def test_worker_never_retranscribes_or_captures_and_cannot_repeat(tmp_path, monkeypatch):
    root, folder, plan, before, store = prepared(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(m.knowledge, "transcribe", lambda *_a, **_k: pytest.fail("ASR repeated"))
    monkeypatch.setattr(m.knowledge, "capture_frames", lambda *_a, **_k: pytest.fail("capture repeated"))
    monkeypatch.setattr(m.knowledge, "export_review", lambda *_: None)
    monkeypatch.setattr(m.exports, "validate_lesson", lambda *_: {"frames": 2, "note_windows": 1})

    def enrich(actual, key, model, **kwargs):
        calls.append((key, model, kwargs))
        with actual.write() as db:
            db.execute("UPDATE frames SET status='ready',model=?,description='synthetic new OCR' WHERE id=2", (m.MODEL,))
    monkeypatch.setattr(m.knowledge, "enrich", enrich)
    result = m.worker(root, folder.name)
    assert result["state"] == "remaining_enrichment_full_export_checked"
    assert calls == [(m.TARGET, m.MODEL, {"cooperative": True})]
    m.check_preserved_result(before, m.exports.read_rows(m.exports.ReadOnlyArchive(root), m.TARGET))
    with pytest.raises(ValueError, match="worker_once"):
        m.worker(root, folder.name)
    assert len(calls) == 1 and plan["reader_article_published"] is False


@pytest.mark.parametrize("change", ["asr", "accepted", "missing_frame", "unfinished", "clock", "video"])
def test_result_rejects_changes_to_prior_successes_and_source_scope(tmp_path, monkeypatch, change):
    _, _, _, before, _ = prepared(tmp_path, monkeypatch)
    current = copy.deepcopy(before)
    current["frames"][1].update(status="ready", model=m.MODEL, description="synthetic new OCR")
    if change == "asr":
        current["segments"][0]["text"] += " changed"
    elif change == "accepted":
        current["frames"][0]["description"] += " changed"
    elif change == "missing_frame":
        current["frames"].pop()
    elif change == "unfinished":
        current["frames"][1]["status"] = "pending"
    elif change == "clock":
        current["frames"][1]["time"] += 2
    else:
        current["video"]["error"] = "synthetic error"
    with pytest.raises(ValueError):
        m.check_preserved_result(before, current)


@pytest.mark.parametrize("state", ["live", "success", "failed", "replaced", "missing_terminal", "incomplete"])
def test_only_exact_successful_reserved_predecessor_allows_dispatch(tmp_path, monkeypatch, state):
    root, _, plan, _, _ = prepared(tmp_path, monkeypatch)
    path = root / "wiki/recovered-source-tails" / plan["predecessor"] / "progress.json"
    status = m.whole.read(path)
    status["state"] = "seven_preserved_audio_sources_finished_existing_unresolved_scope_retained"
    if state == "failed":
        status["state"] = "stopped"
    elif state == "replaced":
        status["created"] += 1
    elif state == "missing_terminal":
        status["state"] = "waiting_for_exact_existing_inventory_queue"
    elif state == "incomplete":
        status["completed_sources"] = ["unexpected"]
    m.knowledge.atomic_json(path, status)
    monkeypatch.setattr(m.queue.original, "identity_live", lambda _: state == "live")
    checks = []
    monkeypatch.setattr(m.queue, "validate_exports", lambda *_a, **_k: checks.append(True))
    if state in {"live", "success"}:
        assert m.predecessor_finished(root, plan) == (state == "success")
        assert checks == ([True] if state == "success" else [])
    else:
        with pytest.raises(ValueError):
            m.predecessor_finished(root, plan)
        assert not checks


def test_failed_predecessor_stops_once_without_guard_or_publication(tmp_path, monkeypatch):
    root, folder, _, _, _ = prepared(tmp_path, monkeypatch)
    m.knowledge.atomic_json(folder / "independent-input-restore.json", {"state": "partial_source_inputs_independently_restored",
        "job": folder.name, "model_calls": 0, "original_archive_reads": 0})
    monkeypatch.setattr(m, "predecessor_finished", lambda *_: (_ for _ in ()).throw(ValueError("synthetic_predecessor_failure")))
    monkeypatch.setattr(m.guard, "run", lambda *_a, **_k: pytest.fail("failed predecessor launched"))
    monkeypatch.setattr(m.queue.prior, "publish_verified", lambda *_: pytest.fail("failed predecessor published"))
    with pytest.raises(ValueError, match="synthetic_predecessor"):
        m.run(root, folder.name)
    assert m.whole.read(folder / "progress.json")["state"] == "stopped"
    assert (folder / "dispatch-failure.json").is_file()
    with pytest.raises(ValueError, match="dispatch_once"):
        m.run(root, folder.name)
