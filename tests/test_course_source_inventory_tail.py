from contextlib import contextmanager
import copy
import sqlite3
from types import SimpleNamespace

import pytest

from wechat_decrypt_tool import course_source_inventory_tail as m
from wechat_decrypt_tool.course_knowledge import atomic_json


def item(tmp_path, number, **extra):
    path = tmp_path / f"synthetic-{number}.mp4"
    path.write_bytes(b"synthetic media stat only")
    stat = path.stat()
    return {"path": str(path), "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns,
        "audio_streams": 1, "duration_seconds": 1, **extra}


def test_inventory_partition_never_selects_registered_reserved_failed_or_partial_sources(tmp_path, monkeypatch):
    rows = [item(tmp_path, n) for n in range(6)]
    rows[5]["audio_streams"] = 0
    keys = [m.original.item_id(row) for row in rows]
    dbpath = tmp_path / "registry.sqlite"
    with sqlite3.connect(dbpath) as db:
        db.execute("CREATE TABLE videos(id TEXT PRIMARY KEY,status TEXT)")
        db.execute("INSERT INTO videos VALUES (?,?)", (keys[0], "failed"))
    monkeypatch.setattr(m.original, "ReadOnlyArchive", lambda root: SimpleNamespace(connect=lambda: sqlite3.connect(dbpath)))
    old = tmp_path / "wiki/source-batches/old"
    old.mkdir(parents=True)
    atomic_json(old / "plan.json", {"sources": [{"video": keys[1]}]})
    partial = tmp_path / "lessons" / keys[3]
    partial.mkdir(parents=True)
    (partial / "unaccepted.json").write_text("{}")
    inventory = {"videos": rows}
    before = copy.deepcopy(inventory)
    selected, excluded = m.select(tmp_path, inventory, {"sources": [{"video": keys[1]}]})
    assert inventory == before
    assert [r["video"] for r in selected] == [keys[2], keys[4]]
    assert {r["video"]: r["code"] for r in excluded} == {
        keys[0]: "registered_source_not_retried", keys[1]: "reserved_by_original_source_plan",
        keys[3]: "existing_output_requires_separate_recovery", keys[5]: "not_recoverable_audio_source"}
    assert len(selected) + len(excluded) == len(rows)
    # A previously frozen inventory queue is also a reservation, even if it failed.
    reserved = tmp_path / "wiki/inventory-source-tails/earlier"
    reserved.mkdir(parents=True)
    atomic_json(reserved / "plan.json", {"sources": [{"video": keys[2]}]})
    next_rows, _ = m.select(tmp_path, inventory, {"sources": [{"video": keys[1]}]})
    assert [r["video"] for r in next_rows] == [keys[4]]
    rows[4]["bytes"] += 1
    with pytest.raises(ValueError, match="unchanged_unattempted_media_stat"):
        m.select(tmp_path, inventory, {"sources": [{"video": keys[1]}]})


def wait_fixture(tmp_path, monkeypatch):
    folder = tmp_path / "predecessor"
    folder.mkdir()
    (folder / "item-00-guard").mkdir()
    status = {"pid": 100, "created": 1.0, "state": "original_unattempted_source_tail_finished", "completed_sources": ["v1"]}
    atomic_json(folder / "progress.json", status)
    atomic_json(folder / "v1.json", {"export": {"verified": True}})
    atomic_json(folder / "item-00-guard/guard-progress.json", {"pid": 100, "created": 1.0, "state": "worker_exited", "returncode": 0})
    monkeypatch.setattr(m.guard, "checkpoint", lambda root: None)
    monkeypatch.setattr(m.publication, "Library", lambda root: SimpleNamespace(catalog=lambda: {"synthetic": True}))
    monkeypatch.setattr(m.publication, "available", lambda catalog: {"v1"})
    monkeypatch.setattr(m.original, "ReadOnlyArchive", lambda root: None)
    monkeypatch.setattr(m.original, "validate_lesson", lambda *args: {"verified": True})
    return folder, {"predecessor_controller": {"pid": 100, "created": 1.0}}, {"sources": [{"video": "v1", "item": {}}]}


def test_waits_for_the_exact_controller_then_validates_all_real_receipts(tmp_path, monkeypatch):
    folder, plan, previous = wait_fixture(tmp_path, monkeypatch)
    alive, sleeps = [True], []

    def sleep(seconds):
        sleeps.append(seconds)
        alive[0] = False

    m.wait(tmp_path, plan, folder, previous, live=lambda row: alive[0], sleep=sleep)
    assert sleeps == [15]


@pytest.mark.parametrize("change", ["identity", "failed", "paused", "partial", "export", "guard"])
def test_failed_paused_recycled_or_incomplete_predecessor_never_starts_new_source(tmp_path, monkeypatch, change):
    folder, plan, previous = wait_fixture(tmp_path, monkeypatch)
    status = m.whole.read(folder / "progress.json")
    if change == "identity":
        status["created"] = 2.0
    elif change in {"failed", "paused"}:
        status["state"] = "stopped" if change == "failed" else "paused"
    elif change == "partial":
        status["completed_sources"] = []
    elif change == "export":
        atomic_json(folder / "v1.json", {"export": {"verified": False}})
    else:
        atomic_json(folder / "item-00-guard/guard-progress.json", {"state": "worker_exited", "returncode": 1})
    atomic_json(folder / "progress.json", status)
    with pytest.raises(ValueError):
        m.wait(tmp_path, plan, folder, previous, live=lambda row: False)


def run_fixture(tmp_path, monkeypatch, *, outcome="ok"):
    folder = tmp_path / "wiki/inventory-source-tails" / ("a" * 32)
    folder.mkdir(parents=True)
    rows = [{"video": f"v{i}", "item": {}, "retained_audio": {}} for i in range(2)]
    plan = {"sources": rows, "excluded": [], "asr_model": "synthetic", "knowledge_model": "synthetic"}
    monkeypatch.setattr(m, "load", lambda *args: (tmp_path, folder, plan, tmp_path / "previous", {}))
    monkeypatch.setattr(m, "wait", lambda *args: None)
    monkeypatch.setattr(m.guard, "checkpoint", lambda root: None)
    monkeypatch.setattr(m.prior, "check_new", lambda *args: None)
    monkeypatch.setattr(m.original, "worker_command", lambda root, plan, row: [row["video"]])
    monkeypatch.setattr(m.original, "ReadOnlyArchive", lambda root: None)
    monkeypatch.setattr(m.original, "validate_lesson", lambda *args: {"verified": True})
    monkeypatch.setattr(m.prior, "publish_verified", lambda *args: {"state": "verified"})
    monkeypatch.setattr(m.whole, "backup", lambda *args: None)

    @contextmanager
    def client(**kwargs):
        yield None

    monkeypatch.setattr(m.original.httpx, "Client", client)
    calls = []

    def worker(root, private, command):
        calls.append(command)
        if outcome == "pause":
            (root / "pause.flag").write_text("Synthetic new thermal stop")
            return {"state": "paused_without_automatic_resume"}
        atomic_json(root / "job-progress.json", {"stage": "batch_finished", "processed": 1, "failures": 1 if outcome == "failure" else 0})
        return {"state": "private_worker_exited", "returncode": 0}

    monkeypatch.setattr(m.guard, "run", worker)
    return folder, calls


def test_each_frozen_source_is_validated_published_and_runs_once_only(tmp_path, monkeypatch):
    folder, calls = run_fixture(tmp_path, monkeypatch)
    result = m.run(tmp_path, "a" * 32, "http://127.0.0.1:3003")
    assert result["completed_new_sources"] == 2 and calls == [["v0"], ["v1"]]
    assert m.whole.read(folder / "progress.json")["completed_sources"] == ["v0", "v1"]
    assert not result["reader_article_published"] and not result["source_deletion_allowed"]
    with pytest.raises(ValueError, match="controller_once"):
        m.run(tmp_path, "a" * 32, "http://127.0.0.1:3003")
    assert len(calls) == 2


@pytest.mark.parametrize("outcome", ["pause", "failure"])
def test_new_pause_or_failed_source_is_retained_and_no_later_source_or_restart_runs(tmp_path, monkeypatch, outcome):
    folder, calls = run_fixture(tmp_path, monkeypatch, outcome=outcome)
    with pytest.raises((InterruptedError, ValueError)):
        m.run(tmp_path, "a" * 32, "http://127.0.0.1:3003")
    assert calls == [["v0"]]
    state = m.whole.read(folder / "progress.json")
    assert state["state"] == ("paused" if outcome == "pause" else "stopped")
    assert state["completed_sources"] == [] and list(folder.glob("failure-*.json"))
    if outcome == "pause":
        assert (tmp_path / "pause.flag").read_text() == "Synthetic new thermal stop"
    with pytest.raises(ValueError, match="controller_once"):
        m.run(tmp_path, "a" * 32, "http://127.0.0.1:3003")
    assert len(calls) == 1
