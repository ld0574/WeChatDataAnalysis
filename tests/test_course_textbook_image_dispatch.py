import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from wechat_decrypt_tool import course_textbook_image_dispatch as m
from wechat_decrypt_tool import course_gpu_guard
from wechat_decrypt_tool.course_knowledge import atomic_json


def predecessor_fixture(tmp_path):
    plan = {"coverage_job": "a" * 32, "coverage_guard": {"pid": 101, "created": 1.0},
        "coverage_worker": {"pid": 102, "created": 2.0}}
    guard = {**plan["coverage_guard"], "state": "worker_exited", "returncode": 0}
    status = {**plan["coverage_worker"], "job": plan["coverage_job"], "stage": "complete",
        "state": "whole_manuscript_requires_coverage_or_classification_repair", "completed_batches": 61,
        "total_batches": 61, "completed_clauses": 15534, "total_clauses": 15534}
    folder = tmp_path / "wiki/whole-manuscript-coverage-jobs" / plan["coverage_job"]
    folder.mkdir(parents=True)
    atomic_json(tmp_path / "wiki-revision-thermal-guard.json", guard)
    atomic_json(folder / "progress.json", status)
    return plan, guard, status, folder


def test_unresolved_semantic_terminal_is_not_passed_but_can_release_exact_guard_for_separate_image_scope(tmp_path, monkeypatch):
    plan, _, _, _ = predecessor_fixture(tmp_path)
    for live_pid in (101, 102):
        monkeypatch.setattr(m.images.courseware, "live", lambda value: value["pid"] == live_pid)
        assert m.predecessor(tmp_path, plan) is False
    monkeypatch.setattr(m.images.courseware, "live", lambda value: False)
    assert m.predecessor(tmp_path, plan) is True


@pytest.mark.parametrize("change", ["guard_pid", "guard_created", "worker_pid", "worker_created", "job", "paused",
    "failed", "returncode", "stage", "batches", "clauses"])
def test_exact_coverage_terminal_rejects_identity_changes_partial_work_failure_and_pause(tmp_path, monkeypatch, change):
    plan, guard, status, folder = predecessor_fixture(tmp_path)
    monkeypatch.setattr(m.images.courseware, "live", lambda value: False)
    if change.startswith("guard_"):
        guard[change.removeprefix("guard_")] += 1
    elif change.startswith("worker_"):
        status[change.removeprefix("worker_")] += 1
    elif change == "job":
        status["job"] = "b" * 32
    elif change == "paused":
        guard["state"] = "paused"
    elif change == "failed":
        status["state"] = "stopped"
    elif change == "returncode":
        guard["returncode"] = 1
    elif change == "stage":
        status["stage"] = "batch-0013"
    elif change == "batches":
        status["completed_batches"] -= 1
    else:
        status["completed_clauses"] -= 1
    atomic_json(tmp_path / "wiki-revision-thermal-guard.json", guard)
    atomic_json(folder / "progress.json", status)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    with pytest.raises(ValueError):
        m.predecessor(tmp_path, plan)
    assert all(path.read_bytes() == content for path, content in before.items())


def worker_fixture(tmp_path, monkeypatch):
    key = "c" * 32
    folder = tmp_path / "wiki/notebook-image-dispatch" / key
    folder.mkdir(parents=True)
    token = tmp_path / "tokenizer.json"
    token.write_text("{}", encoding="utf-8")
    contract = tmp_path / "image-contract.json"
    atomic_json(contract, {"synthetic_budget": 2})
    plan = {"policy": m.POLICY, "job": key, "inventory": "d" * 64, "coverage_parent": "e" * 32,
        "coverage_job": "a" * 32, "image_execution_sha256": m.images.digest({"synthetic_budget": 2}),
        "module_sha256": m.whole.sha(Path(m.__file__).read_bytes()),
        "image_module_sha256": m.whole.sha(Path(m.images.__file__).read_bytes()),
        "coverage_adapter_sha256": m.whole.sha(Path(m.assignments.__file__).read_bytes()),
        "tokenizer_sha256": m.whole.sha(token.read_bytes())}
    atomic_json(folder / "plan.json", plan)
    monkeypatch.setattr(m.images, "prepared", lambda *args, **kwargs: ({}, {}, {}, SimpleNamespace(key=key), contract))

    def checkpoint(root):
        m.require(not (root / "pause.flag").exists() and not (root / "supervisor-stop.flag").exists(), "synthetic_stop_preserved")

    monkeypatch.setattr(m, "checkpoint", checkpoint)
    monkeypatch.setattr(m.whole, "backup", lambda *args: None)
    return key, folder, token, plan


def test_successful_dispatch_reconstructs_all_coverage_then_runs_once_and_reconstructs_native_pixels(tmp_path, monkeypatch):
    key, folder, token, plan = worker_fixture(tmp_path, monkeypatch)
    ready, events = iter([False, True, True]), []
    monkeypatch.setattr(m, "predecessor", lambda *args: next(ready))

    def reconstruct(root, parent, tokenizer, section):
        assert parent == plan["coverage_parent"] and tokenizer == token and section == "section-0007"
        events.append("coverage_zero_inference")
        return {"summary": {"state": "complete_whole_coverage_reconstructed_without_inference",
            "job": plan["coverage_job"], "model_calls": 0, "unresolved_clauses": 5}, "proof": {"synthetic": True}}

    monkeypatch.setattr(m.assignments, "reconstruct", reconstruct)
    monkeypatch.setattr(m.assignments.checked, "save", lambda *args: tmp_path / "coverage-proof.json")

    def guarded(root, command, **kwargs):
        events.append("native_image_guard")
        assert (folder / "coverage-reconstruction.json").is_file()
        assert kwargs["guard_name"] == "wiki-revision-thermal-guard" and kwargs["maximum_temperature"] == 70
        assert command[-1] == "worker"
        atomic_json(root / "wiki-revision-thermal-guard.json", {"state": "worker_exited", "returncode": 0})

    monkeypatch.setattr(course_gpu_guard, "run", guarded)

    def check_images(root, inventory, tokenizer, *, read_only):
        events.append("native_image_zero_inference")
        assert inventory == plan["inventory"] and read_only is True
        return {"state": "native_notebook_image_checks_reconstructed_without_inference", "job": key,
            "model_calls": 0, "unresolved": 2, "different_model_pixel_review": False}

    monkeypatch.setattr(m.images, "run", check_images)
    result = m.worker(tmp_path, key, token, sleep=lambda seconds: events.append("wait"))
    assert events == ["wait", "coverage_zero_inference", "native_image_guard", "native_image_zero_inference"]
    assert result["reader_publication_allowed"] is False and result["different_model_pixel_review"] is False
    assert json.loads((folder / "result.json").read_bytes())["unresolved"] == 2
    with pytest.raises(ValueError, match="dispatch_once_without_retry"):
        m.worker(tmp_path, key, token)
    assert events.count("native_image_guard") == 1


@pytest.mark.parametrize("failure", ["coverage", "verify", "guard", "pause", "supervisor"])
def test_failed_or_paused_dependency_preserves_record_and_prevents_advance(tmp_path, monkeypatch, failure):
    key, folder, token, plan = worker_fixture(tmp_path, monkeypatch)
    calls = []

    def predecessor(*args):
        if failure == "coverage":
            raise ValueError("requires_image_dispatch_synthetic_coverage_failed")
        if failure in {"pause", "supervisor"}:
            (tmp_path / ("pause.flag" if failure == "pause" else "supervisor-stop.flag")).write_text("Synthetic retained stop", encoding="utf-8")
            return False
        return True

    def reconstruct(*args):
        calls.append("coverage_verification")
        if failure == "verify":
            raise ValueError("requires_image_dispatch_synthetic_verification_failed")
        return {"summary": {"state": "complete_whole_coverage_reconstructed_without_inference", "job": plan["coverage_job"],
            "model_calls": 0}, "proof": {"synthetic": True}}

    def guarded(*args, **kwargs):
        calls.append("image_guard")
        atomic_json(tmp_path / "wiki-revision-thermal-guard.json", {"state": "worker_exited", "returncode": 1})

    monkeypatch.setattr(m, "predecessor", predecessor)
    monkeypatch.setattr(m.assignments, "reconstruct", reconstruct)
    monkeypatch.setattr(m.assignments.checked, "save", lambda *args: tmp_path / "synthetic-proof.json")
    monkeypatch.setattr(course_gpu_guard, "run", guarded)
    monkeypatch.setattr(m.images, "run", lambda *args, **kwargs: pytest.fail("Must not advance"))
    with pytest.raises(ValueError):
        m.worker(tmp_path, key, token, sleep=lambda seconds: None)
    assert (folder / "started.json").is_file() and list((folder / "failures").glob("*.json"))
    assert not (folder / "result.json").exists()
    if failure in {"pause", "supervisor"}:
        assert not calls and (tmp_path / ("pause.flag" if failure == "pause" else "supervisor-stop.flag")).exists()
    elif failure != "guard":
        assert "image_guard" not in calls
