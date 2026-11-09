from types import SimpleNamespace

import pytest

from wechat_decrypt_tool import course_textbook_graph_ready_resume as m
from wechat_decrypt_tool.course_knowledge import atomic_json


def waiting(tmp_path, monkeypatch):
    folder = tmp_path / "graph"
    (folder / "guard").mkdir(parents=True)
    worker = tmp_path / "wiki/textbook-whole-authoring-jobs/writer/progress.json"
    worker.parent.mkdir(parents=True)
    execution = {"predecessor_directory": "graph", "predecessor_writer": "writer",
        "predecessor_guard": {"pid": 10, "created": 20}, "predecessor_worker": {"pid": 11, "created": 21}}
    atomic_json(folder / "guard/guard-progress.json", {"pid": 10, "created": 20, "state": "worker_exited", "returncode": 0})
    atomic_json(worker, {"pid": 11, "created": 21, "state": "conceptual_article_requires_actual_source_and_editorial_review",
        "stage": "private_article_assembled"})
    monkeypatch.setattr(m.guard, "checkpoint", lambda *args: None)
    calls = []

    def verify(*args):
        calls.append(args)
        return {"private": {}, "summary": {"job": "writer", "verified_units": 16, "total_units": 16}, "proof": {"actual": "graph_recovery"}}

    monkeypatch.setattr(m.predecessor, "verify", verify)
    return execution, folder, worker, calls


def test_exact_graph_worker_and_guard_must_exit_and_rebuild_the_entire_manuscript(tmp_path, monkeypatch):
    execution, _, _, calls = waiting(tmp_path, monkeypatch)
    identities = iter([False, True, False, False])
    sleeps = []
    result = m.wait(tmp_path, execution, live=lambda row: next(identities), sleep=sleeps.append)
    assert sleeps == [15]
    assert calls == [(tmp_path, "writer")]
    assert result["original_predecessor"] == {"actual": "graph_recovery"}
    assert result["model_calls"] == 0 and result["reader_publication_allowed"] is False


@pytest.mark.parametrize("bad", ["guard_pid", "worker_pid", "guard_paused", "worker_stopped", "returncode", "partial_stage", "partial_verify"])
def test_no_failed_paused_replaced_or_partial_predecessor_can_release_reservations(tmp_path, monkeypatch, bad):
    execution, folder, worker, calls = waiting(tmp_path, monkeypatch)
    g = m.whole.read(folder / "guard/guard-progress.json")
    w = m.whole.read(worker)
    if bad == "guard_pid":
        g["pid"] += 1
    elif bad == "worker_pid":
        w["created"] += 1
    elif bad == "guard_paused":
        g["state"] = "paused"
    elif bad == "worker_stopped":
        w["state"] = "stopped"
    elif bad == "returncode":
        g["returncode"] = 1
    elif bad == "partial_stage":
        w["stage"] = "section-0015-write"
    else:
        monkeypatch.setattr(m.predecessor, "verify", lambda *args: {
            "private": None, "summary": {"job": "writer", "verified_units": 15, "total_units": 16}})
    atomic_json(folder / "guard/guard-progress.json", g)
    atomic_json(worker, w)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*.json")}
    with pytest.raises(ValueError):
        m.wait(tmp_path, execution, live=lambda row: False, sleep=lambda seconds: pytest.fail("Unexpected automatic resume"))
    assert before == {path: path.read_bytes() for path in tmp_path.rglob("*.json")}
    if bad != "partial_verify":
        assert calls == []


def preparing(tmp_path, monkeypatch):
    key = "a" * 32
    stopped, old, target = tmp_path / "stopped", tmp_path / "old", tmp_path / "graph"
    for folder in [stopped, old, target / "guard"]:
        folder.mkdir(parents=True)
    state = {"pid": 1, "created": 2, "state": "stopped", "completed_courses": 0, "total_courses": 3,
        "reason_code": "requires_ready_resume_predecessor_failed_or_paused_no_automatic_resume"}
    for name in ["execution.json", "progress.json", "started.json", "independent-input-restore.json", "failure-original.json"]:
        atomic_json(stopped / name, state if name == "progress.json" else {"synthetic": name})
    for name in ["execution.json", "started.json", "independent-input-restore.json", "guard/guard-started.json"]:
        atomic_json(target / name, {"synthetic": name})
    atomic_json(target / "guard/guard-progress.json", {"pid": 10, "created": 20, "state": "running"})
    worker = tmp_path / "wiki/textbook-whole-authoring-jobs/writer/progress.json"
    worker.parent.mkdir(parents=True)
    atomic_json(worker, {"pid": 11, "created": 21, "state": "generating", "stage": "section-0010-write"})
    plan = {"predecessor_writer": "writer", "models": {"qwen3.5:9b": "weights"},
        "items": [{"lesson": f"synthetic-{i}", "records": i+1, "characters": 10*(i+1)} for i in range(3)]}
    flights = [{"job": f"synthetic-{i}", "groups": n} for i, n in enumerate([5, 22, 24])]
    prior = {"predecessor_writer": "writer", "predecessor_execution_sha256": "same_original_parent"}
    monkeypatch.setattr(m.original, "load", lambda *args: (tmp_path, stopped, old, plan, None, flights, prior))
    monkeypatch.setattr(m.predecessor, "contract", lambda *args: (tmp_path, target, {"original_continuation_sha256": "same_original_parent"}))
    monkeypatch.setattr(m.guard, "checkpoint", lambda *args: None)
    monkeypatch.setattr(m.writing, "identity_live", lambda row: row["pid"] in {10, 11})
    monkeypatch.setattr(m, "bindings", lambda: {"synthetic_code": True})
    unused = []
    monkeypatch.setattr(m.ready, "unused", lambda *args: unused.append(args))
    monkeypatch.setattr(m, "calibration", lambda *args: ({"model_digest": "weights", "probe": "b" * 64}, []))
    return key, stopped, old, plan, flights, unused


def test_prepare_retains_failures_all_three_reservations_and_exact_new_predecessor(tmp_path, monkeypatch):
    key, stopped, old, plan, flights, unused = preparing(tmp_path, monkeypatch)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*.json")}
    result = m.prepare(tmp_path, key, "b" * 64)
    assert result["courses"] == 3 and result["records"] == 6 and result["model_calls"] == 0
    saved = m.whole.read(m.folder_for(tmp_path, key) / "execution.json")
    assert saved["original_preflights_sha256"] == m.digest(flights)
    assert saved["original_plan_sha256"] == m.digest(plan)
    assert saved["predecessor_guard"] == {"pid": 10, "created": 20}
    assert saved["predecessor_worker"] == {"pid": 11, "created": 21}
    assert saved["original_budgets_reset"] is False
    assert unused == [(tmp_path, old, flights)]
    assert all(path.read_bytes() == raw for path, raw in before.items())
    with pytest.raises(ValueError, match="single_explicit_continuation_registration"):
        m.prepare(tmp_path, key, "b" * 64)


def test_prepare_refuses_consumed_roles_before_creating_a_new_registration(tmp_path, monkeypatch):
    key, _, _, _, _, _ = preparing(tmp_path, monkeypatch)

    def used(*args):
        raise ValueError("requires_original_unused_roles")

    monkeypatch.setattr(m.ready, "unused", used)
    with pytest.raises(ValueError, match="requires_original_unused_roles"):
        m.prepare(tmp_path, key, "b" * 64)
    assert not m.folder_for(tmp_path, key).exists()


def test_dispatch_requires_real_independent_restore_before_started_marker_or_wait(tmp_path, monkeypatch):
    key = "a" * 32
    folder, old = tmp_path / "new", tmp_path / "old"
    folder.mkdir()
    monkeypatch.setattr(m, "load", lambda *args: (tmp_path, folder, None, old, {}, None, [], {}, {}))
    atomic_json(folder / "independent-input-restore.json", {"state": "wrong", "batch": key, "model_calls": 0, "original_root_reads": 0})
    monkeypatch.setattr(m, "wait", lambda *args: pytest.fail("Cannot wait before valid restore"))
    with pytest.raises(ValueError, match="independently_restored_actual_inputs"):
        m.run(tmp_path, key)
    assert not (folder / "started.json").exists()


def test_registration_binds_first_graph_format_to_same_writer_and_original_first_plan():
    job = SimpleNamespace(key="original_writer")
    prior = {"original_first_planning": True}
    execution = {"calibration": {"actual_original_calls": 2}}
    result = m.writer_execution(job, "a" * 32, 1, prior, execution)
    assert result["writer"] == job.key and result["item"] == 1
    assert result["original_first_plan_sha256"] == m.digest(prior)
    assert result["format"] == m.graphs.binding()
    assert result["original_budgets_reset"] is False


@pytest.mark.parametrize("state", ["running", "paused", "failure"])
def test_calibration_must_really_exit_before_any_private_verifier_executes(tmp_path, monkeypatch, state):
    p = tmp_path / "wiki/probe_numbered_graph_20261110.py"
    p.parent.mkdir(parents=True)
    p.write_text("raise AssertionError('Must not execute an unfinished probe')", encoding="utf8")
    plan = {"policy": "actual-writer-two-fixed-numbered-graph-probes-once-v1", "model": "qwen3.5:9b",
        "maximum_calls": 2, "attempts_per_case": 1, "code": {"driver": m.whole.sha(p.read_bytes()),
        "numbered_graphs": m.graphs.binding(), "baseline": m.probes.bindings()}}
    key = m.digest(plan)
    folder = tmp_path / "wiki/schema-numbered-graph-probes" / key
    folder.mkdir(parents=True)
    atomic_json(folder / "manifest.json", plan)
    atomic_json(folder / "guard-progress.json", {"state": "worker_exited" if state == "failure" else state, "returncode": 1})
    with pytest.raises(ValueError, match="actual_calibration_successful_exit"):
        m.calibration(tmp_path, key)
