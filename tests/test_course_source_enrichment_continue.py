import json

import pytest

from wechat_decrypt_tool import course_source_enrichment_continue as module


def fixture(tmp_path, monkeypatch):
    root = tmp_path / "archive"
    store = module.knowledge.KnowledgeStore(root)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"synthetic media, never decoded")
    stat = source.stat()
    item = {"path": str(source), "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    video, key = "a" * 24, "b" * 32
    with store.write() as db:
        db.execute("INSERT INTO videos(id,path,title,size,mtime_ns,status,error) VALUES(?,?,?,?,?,?,?)",
            (video, str(source), "synthetic", stat.st_size, stat.st_mtime_ns, "transcribed", module.recovery.LEGACY_FAILURE))
        db.execute("INSERT INTO segments(video,n,start,end,text,search_text) VALUES(?,?,?,?,?,?)",
            (video, 0, 0, 10, "synthetic ASR", "synthetic"))
        db.execute("INSERT INTO frames(id,video,path,status,model,description) VALUES(?,?,?,?,?,?)",
            (1, video, "synthetic-prior.png", "ready", module.recovery.MODEL, "synthetic prior OCR"))
        db.execute("INSERT INTO frames(id,video,path,status) VALUES(?,?,?,?)", (2, video, "synthetic-recovered.png", "pending"))
    before = module.exports.read_rows(module.exports.ReadOnlyArchive(root), video)
    with store.write() as db:
        db.execute("UPDATE frames SET status='ready',model=?,description='synthetic checked recovery' WHERE id=2", (module.recovery.MODEL,))
    folder = root / "wiki/source-enrichment-continuations" / key
    folder.mkdir(parents=True)
    frame_folder = root / "wiki/frame-context-recoveries" / ("c" * 32)
    frame_folder.mkdir(parents=True)
    module.knowledge.atomic_json(frame_folder / "applied.json", {"video": video, "source_failure_unchanged": True})
    plan = {"video": video, "frame_job": "c" * 32, "rows_before_recovery": before, "source_item": item,
        "predecessor_guard": {"pid": 41, "created": 1}, "predecessor_worker": {"pid": 42, "created": 2}}
    monkeypatch.setattr(module, "load", lambda *_: (root, folder, plan))
    monkeypatch.setattr(module.recovery, "load", lambda *_: (root, frame_folder, {"video": video, "original_source": before["video"]}))
    monkeypatch.setattr(module.recovery, "checkpoint", lambda _: None)
    return root, folder, frame_folder, plan, store


def test_current_error_resolved_only_after_checked_export_with_original_error_retained(tmp_path, monkeypatch):
    root, folder, _, plan, store = fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(module.knowledge, "export_review", lambda *_: None)
    monkeypatch.setattr(module.exports, "validate_lesson", lambda *_: {"frames": 2, "note_windows": 1})
    result = module.complete_source(store, folder, plan)
    current = module.exports.read_rows(module.exports.ReadOnlyArchive(root), plan["video"])
    assert current["video"]["error"] == "" and current["segments"] == plan["rows_before_recovery"]["segments"]
    assert result["frames"] == 2 and (folder / "original-failure-retained.json").is_file()
    with store.connect() as db:
        events = [json.loads(row[0]) for row in db.execute("SELECT data FROM events")]
    assert events[0]["original_error"] == module.recovery.LEGACY_FAILURE


def test_failed_final_validation_restores_current_error_and_keeps_failure_history(tmp_path, monkeypatch):
    root, folder, _, plan, store = fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(module.knowledge, "export_review", lambda *_: None)

    def fail(*_):
        raise ValueError("synthetic_validation_failed")

    monkeypatch.setattr(module.exports, "validate_lesson", fail)
    with pytest.raises(ValueError, match="synthetic_validation_failed"):
        module.complete_source(store, folder, plan)
    current = module.exports.read_rows(module.exports.ReadOnlyArchive(root), plan["video"])
    assert current["video"]["error"] == module.recovery.LEGACY_FAILURE
    assert (folder / "original-failure-retained.json").is_file() and not (folder / "source-export-checked.json").exists()


@pytest.mark.parametrize("change", ["asr", "old_success", "unfinished_frame"])
def test_changed_asr_old_success_or_pending_frame_cannot_resolve_source_error(tmp_path, monkeypatch, change):
    root, folder, _, plan, store = fixture(tmp_path, monkeypatch)
    with store.write() as db:
        if change == "asr":
            db.execute("UPDATE segments SET text='changed synthetic' WHERE video=?", (plan["video"],))
        elif change == "old_success":
            db.execute("UPDATE frames SET description='changed synthetic' WHERE id=1")
        else:
            db.execute("UPDATE frames SET status='pending' WHERE id=2")
    with pytest.raises(ValueError, match="requires_source_enrichment_continue_"):
        module.complete_source(store, folder, plan)
    with store.connect() as db:
        assert db.execute("SELECT error FROM videos WHERE id=?", (plan["video"],)).fetchone()[0] == module.recovery.LEGACY_FAILURE
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


def test_enrichment_reuses_full_asr_without_capture_or_transcription_and_runs_once(tmp_path, monkeypatch):
    root, folder, _, plan, _ = fixture(tmp_path, monkeypatch)
    calls = []
    original_status = {"stage": "ocr_inference", "pid": 999}
    module.knowledge.atomic_json(root / "job-progress.json", original_status)
    monkeypatch.setattr(module.knowledge, "transcribe", lambda *_: pytest.fail("ASR reran"))
    monkeypatch.setattr(module.knowledge, "capture_frames", lambda *_: pytest.fail("frame capture reran"))
    monkeypatch.setattr(module.knowledge, "enrich", lambda store, video, model, **kwargs: calls.append((video, model, kwargs)))
    monkeypatch.setattr(module, "complete_source", lambda *_: {"frames": 2, "note_windows": 1})
    result = module.enrich_one(root, folder.name)
    assert result["video"] == plan["video"] and calls == [(plan["video"], module.recovery.MODEL, {"cooperative": True})]
    assert module.whole.read(root / "job-progress.json") == original_status
    with pytest.raises(ValueError, match="one_enrichment_without_retry"):
        module.enrich_one(root, folder.name)
    assert len(calls) == 1


@pytest.mark.parametrize("state", ["waiting", "success", "failed", "paused", "identity_changed", "incomplete_exit"])
def test_only_exact_successful_full_predecessor_can_continue(tmp_path, monkeypatch, state):
    root, _, frame_folder, plan, _ = fixture(tmp_path, monkeypatch)
    guard = {**plan["predecessor_guard"], "state": "running" if state == "waiting" else "worker_exited", "returncode": 0}
    status = {**plan["predecessor_worker"], "stage": "generating" if state in {"waiting", "incomplete_exit"} else "all_tiles_saved"}
    if state == "failed":
        guard["returncode"], status["stage"] = 1, "stopped"
    elif state == "paused":
        guard["state"] = "paused"
    elif state == "identity_changed":
        guard["created"] += 10
    module.knowledge.atomic_json(root / "gpu-thermal-guard.json", guard)
    module.knowledge.atomic_json(frame_folder / "progress.json", status)
    checked = []
    monkeypatch.setattr(module.recovery, "live", lambda _: state == "waiting")
    monkeypatch.setattr(module.recovery, "verify", lambda *_: checked.append(True))
    if state in {"waiting", "success"}:
        assert module.predecessor(root, plan) == (state == "success")
        assert checked == ([True] if state == "success" else [])
    else:
        with pytest.raises(ValueError, match="requires_source_enrichment_continue_"):
            module.predecessor(root, plan)
        assert checked == []


def test_failed_predecessor_stops_dispatch_and_does_not_apply_or_launch(tmp_path, monkeypatch):
    root, folder, _, _, _ = fixture(tmp_path, monkeypatch)

    def fail(*_):
        raise ValueError("requires_source_enrichment_continue_predecessor_not_failed_or_paused")

    monkeypatch.setattr(module, "predecessor", fail)
    monkeypatch.setattr(module.recovery, "apply", lambda *_: pytest.fail("failed predecessor applied"))
    monkeypatch.setattr(module, "guarded", lambda *_a, **_k: pytest.fail("failed predecessor launched"))
    with pytest.raises(ValueError, match="predecessor_not_failed"):
        module.dispatch(root, folder.name)
    assert module.whole.read(folder / "progress.json")["state"] == "stopped"
    assert (folder / "dispatch-failure.json").exists()
    with pytest.raises(ValueError, match="dispatch_once_without_retry"):
        module.dispatch(root, folder.name)
