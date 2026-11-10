from types import SimpleNamespace

import pytest

from wechat_decrypt_tool import course_textbook_ready_guard_exit as m
from wechat_decrypt_tool.course_knowledge import atomic_json


@pytest.fixture
def original_failure(tmp_path, monkeypatch):
    key = "a" * 32
    old, original = tmp_path / "old", tmp_path / "original"
    original.mkdir()
    old.mkdir()
    (tmp_path / "wiki").mkdir()
    state = {"pid": 42, "created": 15, "state": "stopped", "completed_courses": 0, "total_courses": 3,
        "reason_code": "requires_tail_ready_resume_actual_original_course_writer_guard_success"}
    protected = {"pid": 42, "created": 15, "state": "worker_exited", "returncode": 0}
    for name in ["execution.json", "started.json", "progress.json", "independent-input-restore.json", "predecessor-proof.json"]:
        atomic_json(original / name, state if name == "progress.json" else {"pid": 42, "created": 15})
    for role in ("ledger", "writer"):
        (original / f"item-00-{role}-guard").mkdir()
        atomic_json(original / f"item-00-{role}-guard/guard-progress.json", protected)
        atomic_json(original / f"item-00-{role}-guard/guard-started.json", {"pid": 42, "created": 15})
    atomic_json(original / "failure-1.json", {"error_type": "ValueError", "reason_code": state["reason_code"]})
    actual_path = tmp_path / "actual-first-result.json"
    atomic_json(actual_path, {"synthetic": "first complete result"})
    actual = {"summary": {"job": "first", "verified_units": 4, "total_units": 4, "unresolved_issues": 3, "model_calls": 0},
        "private": {}, "proof": {"files": {actual_path.name: m.whole.sha(actual_path.read_bytes())}}}
    flights = [{"job": f"original-{i}", "processing_groups": n} for i, n in enumerate([5, 22, 24])]
    plan = {"items": [{"inventory": f"source-{i}"} for i in range(3)]}
    execution = {"original_frozen_execution": True}
    monkeypatch.setattr(m.previous, "load", lambda *args: (tmp_path, original, None, old, plan, None, flights, {}, execution))
    monkeypatch.setattr(m.previous, "verify_writer", lambda *args: actual)
    monkeypatch.setattr(m, "bindings", lambda: {"synthetic": "frozen"})
    monkeypatch.setattr(m.writing, "identity_live", lambda row: False)
    monkeypatch.setattr(m.guard, "checkpoint", lambda *args: None)
    unused_calls = []
    monkeypatch.setattr(m.ready, "unused", lambda *args: unused_calls.append(args))
    return key, original, old, state, actual, flights, unused_calls


def test_preserves_completed_first_result_and_all_failures_without_repeating_it(tmp_path, original_failure):
    key, original, old, _, actual, flights, calls = original_failure
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    result = m.prepare(tmp_path, key)
    assert result["first_private_manuscript_units"] == 4 and result["first_unresolved_issues"] == 3
    assert result["remaining_original_courses"] == 2 and result["model_calls"] == 0
    proof = m.load(tmp_path, key)[-1]
    assert proof["remaining_original_items"] == [1, 2]
    assert proof["remaining_original_ledger_groups"] == [22, 24]
    assert proof["first_proof_sha256"] == m.digest(actual["proof"])
    assert calls == []
    assert all(p.read_bytes() == raw for p, raw in before.items())
    with pytest.raises(ValueError, match="single_original_unused_two_course_continuation"):
        m.prepare(tmp_path, key)


@pytest.mark.parametrize("wrong", ["different_guard", "live", "bad_exit", "another_failure", "partial", "spent_remaining", "spent_remaining_ledger"])
def test_only_the_exact_self_guard_failure_can_release_untouched_reservations(tmp_path, original_failure, monkeypatch, wrong):
    key, original, _, state, actual, _, _ = original_failure
    if wrong == "different_guard":
        atomic_json(original / "item-00-writer-guard/guard-progress.json", {
            "pid": 43, "created": 15, "state": "worker_exited", "returncode": 0})
    elif wrong == "live":
        monkeypatch.setattr(m.writing, "identity_live", lambda row: True)
    elif wrong == "bad_exit":
        atomic_json(original / "item-00-writer-guard/guard-progress.json", {
            "pid": 42, "created": 15, "state": "worker_exited", "returncode": 1})
    elif wrong == "another_failure":
        state["reason_code"] = "requires_something_else"
        atomic_json(original / "progress.json", state)
    elif wrong == "partial":
        actual["summary"]["verified_units"] = 3
    elif wrong == "spent_remaining":
        (original / "item-01-writer-guard").mkdir()
    else:
        job = tmp_path / "wiki/textbook-keyed-jobs/original-1/attempts"
        job.mkdir(parents=True)
        atomic_json(job / "already-used.json", {"spent_original_role": True})
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    with pytest.raises(ValueError):
        m.prepare(tmp_path, key)
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    assert not m.folder_for(tmp_path, key).exists()


def test_original_failure_or_first_manuscript_mutation_invalidates_continuation(tmp_path, original_failure):
    key, original, *_ = original_failure
    m.prepare(tmp_path, key)
    atomic_json(original / "failure-1.json", {"rewritten": True})
    with pytest.raises(ValueError, match="original_failures_first_manuscript_and_spent_roles_preserved"):
        m.load(tmp_path, key)


def test_real_independent_restore_is_required_before_any_remaining_role(tmp_path, monkeypatch):
    folder = tmp_path / "new"
    folder.mkdir()
    (tmp_path / "wiki").mkdir()
    monkeypatch.setattr(m, "load", lambda *args: (tmp_path, folder, None, None, {}, None, [], {"first_summary": {
        "job": "first", "unresolved_issues": 3}, "first_proof_sha256": "proof"}))
    atomic_json(folder / "independent-input-restore.json", {"state": "wrong"})
    monkeypatch.setattr(m, "unused", lambda *args: pytest.fail("No work before real restore"))
    with pytest.raises(ValueError, match="actual_independent_first_result_and_remaining_input_restore"):
        m.run(tmp_path, "a" * 32)
    assert not (folder / "started.json").exists()


def guard_fixture(tmp_path, monkeypatch, status=None):
    folder, original = tmp_path / "continuation", tmp_path / "original"
    folder.mkdir()
    original.mkdir()
    monkeypatch.setattr(m, "load", lambda *args: (tmp_path, folder, original, None, None, None, None, None))
    monkeypatch.setattr(m.writing, "identity_live", lambda row: False)
    calls, sleeps, events = [], [], []
    child = SimpleNamespace(pid=2_000_000_000)
    values = iter([None, 0])
    child.poll = lambda: next(values)

    def wait():
        events.append("joined")
        return 0
    child.wait = wait

    def launch(command, **kwargs):
        calls.append(command)
        assert kwargs["env"]["HF_HUB_OFFLINE"] == "1"
        atomic_json(original / "item-01-writer-guard/guard-progress.json", status or {
            "pid": child.pid, "created": 25, "state": "worker_exited", "returncode": 0})
        return child
    return folder, original, calls, sleeps, events, launch


def test_distinct_guard_is_actually_joined_before_success_is_checked(tmp_path, monkeypatch):
    folder, _, calls, sleeps, events, launch = guard_fixture(tmp_path, monkeypatch)
    identity = m.run_guard(tmp_path, "a" * 32, 1, "writer", launch=launch,
        process=lambda pid: SimpleNamespace(create_time=lambda: 25), sleep=sleeps.append)
    assert sleeps == [5] and events == ["joined"]
    assert identity == {"pid": 2_000_000_000, "created": 25}
    assert len(calls) == 1 and calls[0][1:3] == ["-m", m.MODULE]
    assert m.whole.read(folder / "item-01-writer-guard-process.json") == identity
    with pytest.raises(ValueError, match="one_original_guard_launch_without_retry"):
        m.run_guard(tmp_path, "a" * 32, 1, "writer", launch=launch)
    assert len(calls) == 1


@pytest.mark.parametrize("wrong", ["guard_identity", "paused", "worker_failed", "still_live"])
def test_guard_failure_pause_replacement_or_live_identity_never_releases_verification(tmp_path, monkeypatch, wrong):
    status = {"pid": 2_000_000_000, "created": 25, "state": "worker_exited", "returncode": 0}
    if wrong == "guard_identity":
        status["created"] = 26
    elif wrong == "paused":
        status["state"] = "paused"
    elif wrong == "worker_failed":
        status["returncode"] = 1
    _, _, calls, sleeps, events, launch = guard_fixture(tmp_path, monkeypatch, status)
    if wrong == "still_live":
        monkeypatch.setattr(m.writing, "identity_live", lambda row: True)
    with pytest.raises(ValueError, match="actual_distinct_original_guard_successfully_exited"):
        m.run_guard(tmp_path, "a" * 32, 1, "writer", launch=launch,
            process=lambda pid: SimpleNamespace(create_time=lambda: 25), sleep=sleeps.append)
    assert len(calls) == 1 and events == ["joined"]


def test_completed_first_course_cannot_be_launched_by_the_new_controller(tmp_path, monkeypatch):
    _, _, calls, _, _, launch = guard_fixture(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="only_original_remaining_roles"):
        m.run_guard(tmp_path, "a" * 32, 0, "writer", launch=launch)
    assert calls == []
