import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from wechat_decrypt_tool import course_textbook_fresh_dispatch as m
from wechat_decrypt_tool.course_knowledge import atomic_json


def predecessor_fixture(tmp_path):
    plan = {"predecessor_job": "a" * 32, "predecessor_inventory": "b" * 64,
        "predecessor_guard": {"pid": 101, "created": 1.0}, "predecessor_worker": {"pid": 102, "created": 2.0}}
    guard = {**plan["predecessor_guard"], "state": "worker_exited", "returncode": 0}
    status = {**plan["predecessor_worker"], "job": plan["predecessor_job"], "inventory": plan["predecessor_inventory"],
        "state": "finite_courseware_text_relations_checked", "stage": "complete"}
    folder = tmp_path / "wiki/whole-courseware-relation-jobs" / plan["predecessor_job"]
    folder.mkdir(parents=True)
    atomic_json(tmp_path / "review-experiment-thermal-guard.json", guard)
    atomic_json(folder / "progress.json", status)
    return plan, guard, status, folder


def test_predecessor_waits_for_both_exact_identities_to_exit_before_accepting_terminal(tmp_path, monkeypatch):
    plan, _, _, _ = predecessor_fixture(tmp_path)
    for live_pid in (101, 102):
        monkeypatch.setattr(m.fresh.writing, "identity_live", lambda value: value["pid"] == live_pid)
        assert m.predecessor(tmp_path, plan) is False
    monkeypatch.setattr(m.fresh.writing, "identity_live", lambda value: False)
    assert m.predecessor(tmp_path, plan) is True


@pytest.mark.parametrize("change", ["guard_pid", "guard_created", "worker_pid", "worker_created", "inventory", "job",
    "guard_paused", "worker_failed", "returncode", "incomplete", "not_terminal"])
def test_predecessor_identity_change_pause_failure_or_stale_state_stops_dispatch(tmp_path, monkeypatch, change):
    plan, guard, status, folder = predecessor_fixture(tmp_path)
    monkeypatch.setattr(m.fresh.writing, "identity_live", lambda value: False)
    if change == "guard_pid":
        guard["pid"] += 1
    elif change == "guard_created":
        guard["created"] += 1
    elif change == "worker_pid":
        status["pid"] += 1
    elif change == "worker_created":
        status["created"] += 1
    elif change == "inventory":
        status["inventory"] = "c" * 64
    elif change == "job":
        status["job"] = "d" * 32
    elif change == "guard_paused":
        guard["state"] = "paused"
    elif change == "worker_failed":
        status["state"] = "stopped"
    elif change == "returncode":
        guard["returncode"] = 1
    elif change == "incomplete":
        status["stage"] = "cell-0001-verify"
    else:
        guard["state"] = "running"
    atomic_json(tmp_path / "review-experiment-thermal-guard.json", guard)
    atomic_json(folder / "progress.json", status)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    with pytest.raises(ValueError):
        m.predecessor(tmp_path, plan)
    assert all(p.read_bytes() == content for p, content in before.items())


def worker_fixture(tmp_path, monkeypatch):
    key = "c" * 32
    folder = tmp_path / "wiki/fresh-authoring-dispatch" / key
    folder.mkdir(parents=True)
    tokenizer = tmp_path / "tokenizer.json"
    tokenizer.write_text("{}", encoding="utf-8")
    contract = {"synthetic": "contract"}
    plan = {"policy": m.POLICY, "job": key, "inventory": "d" * 64, "ledger_job": "e" * 32,
        "fresh_execution_sha256": m.fresh.digest(contract), "predecessor_job": "a" * 32,
        "predecessor_inventory": "b" * 64, "module_sha256": m.whole.sha(Path(m.__file__).read_bytes()),
        "fresh_module_sha256": m.whole.sha(Path(m.fresh.__file__).read_bytes()),
        "predecessor_module_sha256": m.whole.sha(Path(m.courseware.__file__).read_bytes()),
        "tokenizer_sha256": m.whole.sha(tokenizer.read_bytes())}
    atomic_json(folder / "plan.json", plan)
    monkeypatch.setattr(m.fresh, "bound", lambda *args: (tmp_path, {}, {}, SimpleNamespace(key=key), contract))

    def checkpoint(root):
        m.require(not (root / "pause.flag").exists() and not (root / "supervisor-stop.flag").exists(), "synthetic_stop_preserved")

    monkeypatch.setattr(m, "checkpoint", checkpoint)
    monkeypatch.setattr(m.whole, "backup", lambda *args: None)
    return key, folder, tokenizer, plan


def test_finite_dispatch_requires_predecessor_reconstruction_then_starts_once_and_reconstructs_new_article(tmp_path, monkeypatch):
    key, folder, tokenizer, plan = worker_fixture(tmp_path, monkeypatch)
    events, ready = [], iter([False, True, True])
    monkeypatch.setattr(m, "predecessor", lambda *args: next(ready))

    def predecessor_run(root, inventory, actual_tokenizer, *, read_only):
        assert inventory == plan["predecessor_inventory"] and actual_tokenizer == tokenizer and read_only is True
        events.append("predecessor_zero_model_reconstruction")
        return {"state": "courseware_text_relations_reconstructed_without_inference", "job": plan["predecessor_job"], "model_calls": 0}

    def start(*args):
        events.append("new_guard")
        assert (folder / "predecessor-reconstruction.json").is_file()
        atomic_json(tmp_path / "review-experiment-thermal-guard.json", {"state": "worker_exited", "returncode": 0})

    monkeypatch.setattr(m.courseware, "run", predecessor_run)
    monkeypatch.setattr(m.fresh, "guard", start)

    def reconstruct(*args):
        events.append("new_zero_model_reconstruction")
        return {"summary": {"job": key, "model_calls": 0, "unresolved_issues": 5}, "proof": {"synthetic": True}}

    monkeypatch.setattr(m.fresh, "reconstruct", reconstruct)
    monkeypatch.setattr(m.fresh.snapshot, "save", lambda *args: tmp_path / "synthetic-proof.json")
    output = m.worker(tmp_path, key, tokenizer, sleep=lambda value: events.append("wait"))
    assert events == ["wait", "predecessor_zero_model_reconstruction", "new_guard", "new_zero_model_reconstruction"]
    assert output["reader_publication_allowed"] is False
    assert json.loads((folder / "result.json").read_bytes())["unresolved_issues"] == 5
    with pytest.raises(ValueError, match="dispatch_once_no_automatic_retry"):
        m.worker(tmp_path, key, tokenizer)
    assert events.count("new_guard") == 1


@pytest.mark.parametrize("failure", ["predecessor", "verification", "guard", "pause", "supervisor"])
def test_dispatch_preserves_failures_and_control_flags_without_launching_later_stages(tmp_path, monkeypatch, failure):
    key, folder, tokenizer, plan = worker_fixture(tmp_path, monkeypatch)
    calls = []

    def predecessor(*args):
        if failure == "predecessor":
            raise ValueError("requires_fresh_dispatch_synthetic_predecessor_failed")
        if failure in {"pause", "supervisor"}:
            flag = tmp_path / ("pause.flag" if failure == "pause" else "supervisor-stop.flag")
            flag.write_text("Synthetic retained stop", encoding="utf-8")
            return False
        return True

    monkeypatch.setattr(m, "predecessor", predecessor)
    checked = {"state": "courseware_text_relations_reconstructed_without_inference", "job": plan["predecessor_job"], "model_calls": 0}

    def verify(*args, **kwargs):
        calls.append("predecessor_verification")
        if failure == "verification":
            raise ValueError("requires_fresh_dispatch_synthetic_verification_failed")
        return copy.deepcopy(checked)

    def guard(*args):
        calls.append("guard")
        atomic_json(tmp_path / "review-experiment-thermal-guard.json", {"state": "worker_exited", "returncode": 1})

    monkeypatch.setattr(m.courseware, "run", verify)
    monkeypatch.setattr(m.fresh, "guard", guard)
    monkeypatch.setattr(m.fresh, "reconstruct", lambda *args: pytest.fail("Must not advance after a failure"))
    with pytest.raises(ValueError):
        m.worker(tmp_path, key, tokenizer, sleep=lambda seconds: None)
    assert (folder / "started.json").is_file()
    assert list((folder / "failures").glob("*.json"))
    assert not (folder / "result.json").exists()
    if failure in {"pause", "supervisor"}:
        assert (tmp_path / ("pause.flag" if failure == "pause" else "supervisor-stop.flag")).read_text(encoding="utf-8") == "Synthetic retained stop"
        assert not calls
    elif failure != "guard":
        assert "guard" not in calls
