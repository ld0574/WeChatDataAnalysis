import pytest

from wechat_decrypt_tool import course_textbook_ready_wrapper_resume as m
from wechat_decrypt_tool.course_knowledge import atomic_json


def waiting(tmp_path, monkeypatch):
    key = "a" * 32
    prior = m.previous.folder_for(tmp_path, key)
    old = m.previous.previous.folder_for(tmp_path, key)
    prior.mkdir(parents=True)
    (old / "item-01-ledger-guard").mkdir(parents=True)
    proof = {"observation": {"controller": {"pid": 10, "created": 20},
        "launcher": {"pid": 11, "created": 21}, "guard": {"pid": 12, "created": 22},
        "worker": {"pid": 13, "created": 23}},
        "expected_previous_failure": "requires_ready_guard_exit_actual_distinct_original_guard_successfully_exited_before_reconstruction"}
    atomic_json(prior / "progress.json", {"pid": 10, "created": 20, "state": "stopped", "reason_code": proof["expected_previous_failure"]})
    atomic_json(prior / "failure-actual.json", {"reason_code": proof["expected_previous_failure"]})
    atomic_json(old / "item-01-ledger-guard/guard-progress.json", {"pid": 12, "created": 22, "state": "worker_exited", "returncode": 0})
    monkeypatch.setattr(m.guard, "checkpoint", lambda root: None)
    return key, prior, old, proof


def test_waits_for_exact_running_ledger_without_relaunch_or_holding_global_worker_lock(tmp_path, monkeypatch):
    key, prior, old, proof = waiting(tmp_path, monkeypatch)
    calls, sleeps = [True], []
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    result = m.wait_previous(tmp_path, key, proof, live=lambda row: calls.pop() if calls else False, sleep=sleeps.append)
    assert sleeps == [5] and result["ledger_restarted"] is False and result["new_model_calls"] == 0
    assert result["successful_original_guard"]["pid"] == 12
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*.json")}


@pytest.mark.parametrize("bad", ["other_failure", "replaced", "paused", "worker_failed"])
def test_only_exact_original_wrapper_error_after_success_can_continue(tmp_path, monkeypatch, bad):
    key, prior, old, proof = waiting(tmp_path, monkeypatch)
    if bad == "other_failure":
        atomic_json(prior / "progress.json", {"pid": 10, "created": 20, "state": "stopped", "reason_code": "requires_other_failure"})
    else:
        row = m.whole.read(old / "item-01-ledger-guard/guard-progress.json")
        if bad == "replaced":
            row["created"] += 1
        elif bad == "paused":
            row["state"] = "paused"
        else:
            row["returncode"] = 1
        atomic_json(old / "item-01-ledger-guard/guard-progress.json", row)
    with pytest.raises(ValueError):
        m.wait_previous(tmp_path, key, proof, live=lambda row: False,
            sleep=lambda seconds: pytest.fail("Cannot resume a paused or failed ledger"))


@pytest.mark.parametrize("bad", ["writer", "third_ledger", "third_guard"])
def test_spent_writers_or_final_course_roles_cannot_be_refreshed(tmp_path, bad):
    old = tmp_path / "old"
    old.mkdir()
    flights = [{"job": f"ledger-{i}"} for i in range(3)]
    if bad == "writer":
        p = tmp_path / "wiki/textbook-keyed-jobs/ledger-1/whole-source-authoring-execution.json"
        p.parent.mkdir(parents=True)
        atomic_json(p, {"original_writer_used": True})
    elif bad == "third_ledger":
        p = tmp_path / "wiki/textbook-keyed-jobs/ledger-2/attempts/spent.json"
        p.parent.mkdir(parents=True)
        atomic_json(p, {"original_role_used": True})
    else:
        (old / "item-02-ledger-guard").mkdir()
    with pytest.raises(ValueError):
        m.unstarted(tmp_path, old, flights)


def test_prior_completed_ledger_is_allowed_but_not_restarted(tmp_path):
    old = tmp_path / "old"
    old.mkdir()
    flights = [{"job": f"ledger-{i}"} for i in range(3)]
    p = tmp_path / "wiki/textbook-keyed-jobs/ledger-1/ledger.json"
    p.parent.mkdir(parents=True)
    atomic_json(p, {"completed_original_ledger": True})
    before = p.read_bytes()
    m.unstarted(tmp_path, old, flights)
    assert p.read_bytes() == before
