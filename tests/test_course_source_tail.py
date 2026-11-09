from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import os

import pytest

from wechat_decrypt_tool import course_source_tail as m
from test_course_source_batch import Store, source


@contextmanager
def client(**kwargs):
    yield object()


def fixture(tmp_path, monkeypatch):
    root = tmp_path / "archive"
    key = "a" * 32
    folder = root / "wiki/source-batches" / key
    folder.mkdir(parents=True)
    items = [source(tmp_path / f"source-{n}.mp4") for n in range(3)]
    rows = [{"video": m.original.item_id(item), "item": item} for item in items]
    plan = {"sources": rows, "asr_model": "synthetic-model", "knowledge_model": "qwen3.5:9b"}
    status = {"pid": 987654, "created": 1.0, "state": "stopped",
        "reason_code": "requires_source_batch_prior_guard_exited_successfully", "completed_sources": [rows[0]["video"]]}
    m.atomic_json(folder / "plan.json", plan)
    m.atomic_json(folder / "progress.json", status)
    m.atomic_json(folder / "started.json", {"pid": 987654})
    m.atomic_json(folder / (rows[0]["video"] + ".json"), {"video": rows[0]["video"], "export": {"checked": rows[0]["video"]}})
    failures = folder / "failures"
    failures.mkdir()
    m.atomic_json(failures / "original.json", {"state": "stopped", "error_type": "ValueError"})
    store = Store([rows[0]["video"]])
    published = {rows[0]["video"]}
    monkeypatch.setattr(m.original, "load_plan", lambda *args: (folder, plan))
    monkeypatch.setattr(m.original, "checkpoint", lambda root: None)
    monkeypatch.setattr(m.original, "checked_sample", lambda root: {})
    monkeypatch.setattr(m.original, "identity_live", lambda row: row["pid"] == os.getpid())
    monkeypatch.setattr(m.original, "ReadOnlyArchive", lambda root: store)
    monkeypatch.setattr(m.original, "retained_inputs", lambda *args: {})
    monkeypatch.setattr(m.original, "validate_lesson", lambda store, video, *args: {"checked": video})
    monkeypatch.setattr(m.whole, "backup", lambda *args: None)
    monkeypatch.setattr(m.original.httpx, "Client", client)
    monkeypatch.setattr(m.publication, "Library", lambda root: SimpleNamespace(catalog=lambda: {
        "lessons": [{"id": video, "state": "draft_available"} for video in published]}))
    monkeypatch.setattr(m.publication, "publish_once", lambda *args: {"state": "new_complete_source_notes_published_and_http_checked"})
    calls = []

    def guarded(root, private, command):
        video = m.original.item_id(next(item for item in items if item["path"] == command[command.index("--video") + 1]))
        calls.append((video, private, command))
        store.registered.add(video)
        published.add(video)
        lesson = root / "lessons" / video
        lesson.mkdir(parents=True)
        for name in ("evidence.json", "graph.json"):
            m.atomic_json(lesson / name, {})
        m.atomic_json(root / "job-progress.json", {"stage": "batch_finished", "processed": 1, "failures": 0})
        m.atomic_json(root / "run-plan.json", {})
        # The original in-process guard remains owned by this live controller.
        m.atomic_json(root / "gpu-thermal-guard.json", {"pid": os.getpid(), "created": 2.0,
            "state": "worker_exited", "returncode": 0})
        return {"state": "private_worker_exited", "returncode": 0}

    monkeypatch.setattr(m.guard, "run", guarded)
    return root, key, folder, plan, store, calls


def test_both_unused_sources_run_once_even_while_controller_pid_is_live(tmp_path, monkeypatch):
    root, key, folder, plan, _, calls = fixture(tmp_path, monkeypatch)
    old = {p: p.read_bytes() for p in folder.rglob("*.json")}
    assert m.prepare(root, key)["completed_original_sources_reused"] == 1
    result = m.run(root, key, "http://127.0.0.1:3003")
    assert result["new_sources"] == 2
    assert [call[0] for call in calls] == [row["video"] for row in plan["sources"][1:]]
    assert len({call[1] for call in calls}) == 2
    for _, private, command in calls:
        assert private.is_relative_to(folder / m.DIRECTORY)
        assert command[command.index("--asr-batch-size") + 1] == "4"
        assert command[command.index("--limit") + 1] == "1"
    assert all(p.read_bytes() == raw for p, raw in old.items())
    with pytest.raises(ValueError, match="controller_once_no_automatic_retry"):
        m.run(root, key, "http://127.0.0.1:3003")
    assert len(calls) == 2


@pytest.mark.parametrize("failure", ["registered", "changed_media", "old_failure_changed", "code_changed"])
def test_changed_or_previously_attempted_inputs_never_launch_a_worker(tmp_path, monkeypatch, failure):
    root, key, folder, plan, store, calls = fixture(tmp_path, monkeypatch)
    m.prepare(root, key)
    if failure == "registered":
        store.registered.add(plan["sources"][1]["video"])
    elif failure == "changed_media":
        Path(plan["sources"][1]["item"]["path"]).write_bytes(b"Changed synthetic media")
    elif failure == "old_failure_changed":
        m.atomic_json(folder / "failures/original.json", {"state": "changed"})
    else:
        monkeypatch.setattr(m, "bindings", lambda: {"synthetic_changed_code": True})
    with pytest.raises(ValueError):
        m.run(root, key, "http://127.0.0.1:3003")
    assert calls == []


def test_thermal_pause_keeps_next_source_unattempted_and_does_not_auto_resume(tmp_path, monkeypatch):
    root, key, folder, _, _, calls = fixture(tmp_path, monkeypatch)
    m.prepare(root, key)

    def pause(root, private, command):
        calls.append(command)
        return {"state": "paused_without_automatic_resume"}

    monkeypatch.setattr(m.guard, "run", pause)
    with pytest.raises(InterruptedError, match="paused_no_auto_resume"):
        m.run(root, key, "http://127.0.0.1:3003")
    assert len(calls) == 1
    status = m.whole.read(folder / m.DIRECTORY / "progress.json")
    assert not status["automatic_retry_allowed"] and not status["automatic_thermal_resume_allowed"]
    assert list((folder / m.DIRECTORY).glob("failure-*.json"))


@pytest.mark.parametrize("fault", ["live", "wrong_reason", "non_prefix", "export_changed"])
def test_old_prefix_must_be_terminal_complete_published_and_contiguous(tmp_path, monkeypatch, fault):
    root, key, folder, plan, _, calls = fixture(tmp_path, monkeypatch)
    status = m.whole.read(folder / "progress.json")
    if fault == "live":
        status["pid"] = os.getpid()
    elif fault == "wrong_reason":
        status["reason_code"] = "another_failure"
    elif fault == "non_prefix":
        status["completed_sources"] = [plan["sources"][1]["video"]]
    else:
        monkeypatch.setattr(m.original, "validate_lesson", lambda *args: {"changed_export": True})
    m.atomic_json(folder / "progress.json", status)
    with pytest.raises(ValueError):
        m.prepare(root, key)
    assert not (folder / m.DIRECTORY).exists() and calls == []
