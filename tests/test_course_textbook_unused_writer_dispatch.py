from pathlib import Path
from types import SimpleNamespace

import pytest

from wechat_decrypt_tool import course_textbook_read_lease as reads
from wechat_decrypt_tool import course_textbook_unused_writer_dispatch as m
from wechat_decrypt_tool.course_knowledge import atomic_json


def test_wait_applies_only_to_immutable_ledger_reads_and_retains_other_mutex_timeouts():
    calls = []
    def original(path, timeout=300):
        calls.append((Path(path).name, timeout))
    writing = SimpleNamespace(schema_lease=original)
    with reads.bounded_ledger_reads(writing, seconds=17):
        writing.schema_lease(Path("ledger-worker.lock"), timeout=0)
        writing.schema_lease(Path("authoring-worker.lock"), timeout=0)
        writing.schema_lease(Path("ledger-worker.lock"), timeout=5)
    assert calls == [("ledger-worker.lock", 17), ("authoring-worker.lock", 0), ("ledger-worker.lock", 5)]
    assert writing.schema_lease is original


def test_read_lease_is_restored_after_exception():
    def original(*args):
        return None
    writing = SimpleNamespace(schema_lease=original)
    with pytest.raises(InterruptedError):
        with reads.bounded_ledger_reads(writing):
            raise InterruptedError("Synthetic pause")
    assert writing.schema_lease is original


@pytest.mark.parametrize("seconds", [0, 301, True, None])
def test_read_wait_cannot_become_unbounded(seconds):
    with pytest.raises(ValueError):
        with reads.bounded_ledger_reads(SimpleNamespace(), seconds=seconds):
            pytest.fail("Invalid wait")


@pytest.mark.parametrize("name", ["progress.json", "attempts/unit-01.json", "response-0000.json",
    "section-0000-write.json", "concepts-00-0000.json", "private-working-inputs.json"])
def test_any_real_original_role_blocks_unspent_dispatch(name, tmp_path):
    path = tmp_path / name
    path.parent.mkdir(exist_ok=True)
    atomic_json(path, {"synthetic": True})
    with pytest.raises(ValueError, match="all_original_writer_roles_still_unused"):
        m.unspent(tmp_path)


def test_worker_runs_the_original_frozen_writer_once_without_repeating_ledger(tmp_path, monkeypatch):
    folder = tmp_path / "dispatch"
    folder.mkdir()
    contract = {"batch": "batch", "ledger_reader_wait_seconds": 10}
    monkeypatch.setattr(m, "load", lambda *args: (tmp_path, folder, contract))
    monkeypatch.setattr(m.guard, "checkpoint", lambda *args: None)
    calls = []
    original = m.writing.schema_lease

    def worker(root, batch):
        assert m.writing.schema_lease is not original
        calls.append((root, batch))
        return {"synthetic": True}

    monkeypatch.setattr(m.original, "writer_worker", worker)
    assert m.worker(tmp_path, "a" * 32) == {"synthetic": True}
    assert calls == [(tmp_path, "batch")] and m.writing.schema_lease is original
    with pytest.raises(ValueError, match="worker_once"):
        m.worker(tmp_path, "a" * 32)


@pytest.mark.parametrize("bad", ["paused", "failed", "live_guard", "partial_manuscript"])
def test_verify_does_not_treat_failed_or_partial_work_as_finished(bad, tmp_path, monkeypatch):
    folder = tmp_path / "dispatch"
    folder.mkdir()
    atomic_json(folder / "guard-progress.json", {"state": "paused" if bad == "paused" else "worker_exited",
        "returncode": 1 if bad == "failed" else 0})
    monkeypatch.setattr(m, "load", lambda *args: (tmp_path, folder, {"batch": "batch"}))
    monkeypatch.setattr(m.writing, "identity_live", lambda row: bad == "live_guard")
    monkeypatch.setattr(m.original, "verify_writer", lambda *args: {
        "summary": {"job": "writer", "verified_units": 3, "total_units": 4}, "private": {}, "proof": {}})
    with pytest.raises(ValueError):
        m.verify(tmp_path, "writer")


def test_binding_names_are_stable_for_cli_and_import():
    assert Path(m.__file__).stem in m.bindings()["entrypoints"]
    assert "__main__" not in m.bindings()["entrypoints"]
