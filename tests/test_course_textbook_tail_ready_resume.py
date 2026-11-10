from types import SimpleNamespace

import pytest

from wechat_decrypt_tool import course_textbook_tail_ready_resume as m
from wechat_decrypt_tool.course_knowledge import atomic_json


def waiting(tmp_path, monkeypatch):
    folder = tmp_path / "graph"
    (folder / "guard").mkdir(parents=True)
    worker = tmp_path / "wiki/textbook-whole-authoring-jobs/writer/progress.json"
    worker.parent.mkdir(parents=True)
    execution = {"predecessor_directory": "graph", "predecessor_writer": "writer",
        "predecessor_guard": {"pid": 10, "created": 20}, "predecessor_worker": {"pid": 11, "created": 21},
        "predecessor_total_units": 16, "predecessor_execution_sha256": "actual_finite_tail"}
    atomic_json(folder / "guard/guard-progress.json", {"pid": 10, "created": 20, "state": "worker_exited", "returncode": 0})
    atomic_json(worker, {"pid": 11, "created": 21, "state": "conceptual_article_requires_actual_source_and_editorial_review",
        "stage": "private_article_assembled"})
    monkeypatch.setattr(m.guard, "checkpoint", lambda *args: None)
    calls = []

    def verify(*args):
        calls.append(args)
        return {"private": {}, "summary": {"job": "writer", "verified_units": 16, "total_units": 16}, "proof": {"actual": "graph_recovery", "original_graph_tail_sha256": "actual_finite_tail"}}

    monkeypatch.setattr(m.predecessor, "verify", verify)
    return execution, folder, worker, calls


def test_exact_graph_worker_and_guard_must_exit_and_rebuild_the_entire_manuscript(tmp_path, monkeypatch):
    execution, _, _, calls = waiting(tmp_path, monkeypatch)
    identities = iter([False, True, False, False])
    sleeps = []
    result = m.wait(tmp_path, execution, live=lambda row: next(identities), sleep=sleeps.append)
    assert sleeps == [15]
    assert calls == [(tmp_path, "writer")]
    assert result["original_predecessor"] == {"actual": "graph_recovery", "original_graph_tail_sha256": "actual_finite_tail"}
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
    key, writer = "a" * 32, "c" * 32
    stopped, old, target = tmp_path / "second", tmp_path / "old", tmp_path / "tail"
    third, graph = tmp_path / "third", tmp_path / "old_graph"
    for folder in [stopped, old, target / "guard", third, graph]:
        folder.mkdir(parents=True)
    state = {"pid": 1, "created": 2, "state": "stopped", "completed_courses": 0, "total_courses": 3,
        "reason_code": "requires_graph_ready_resume_predecessor_failed_or_paused_no_automatic_resume"}
    for name in ["execution.json", "progress.json", "started.json", "independent-input-restore.json", "failure-original.json"]:
        atomic_json(third / name, state if name == "progress.json" else {"synthetic": name})
    atomic_json(stopped / "progress.json", {**state, "reason_code": "requires_ready_resume_predecessor_failed_or_paused_no_automatic_resume"})
    old_graph = {"writer": writer, "original_continuation_sha256": "same_original_parent"}
    atomic_json(graph / "execution.json", old_graph)
    tail = {"policy": m.predecessor.POLICY, "writer": writer, "code": m.predecessor.bindings(),
        "previous_execution_sha256": m.digest(old_graph), "total_units": 16}
    for name in ["execution.json", "started.json", "independent-input-restore.json", "guard/guard-started.json"]:
        atomic_json(target / name, tail if name == "execution.json" else {"synthetic": name})
    atomic_json(target / "guard/guard-progress.json", {"pid": 10, "created": 20, "state": "running"})
    worker = tmp_path / "wiki/textbook-whole-authoring-jobs" / writer / "progress.json"
    worker.parent.mkdir(parents=True)
    atomic_json(worker, {"pid": 11, "created": 21, "state": "generating", "stage": "section-0012-repair"})
    plan = {"predecessor_writer": writer, "models": {"qwen3.5:9b": "weights"},
        "items": [{"lesson": f"synthetic-{i}", "records": i+1, "characters": 10*(i+1)} for i in range(3)]}
    flights = [{"job": f"synthetic-{i}", "groups": n} for i, n in enumerate([5, 22, 24])]
    prior = {"predecessor_writer": writer, "predecessor_execution_sha256": "same_original_parent"}
    monkeypatch.setattr(m.original, "load", lambda *args: (tmp_path, stopped, old, plan, None, flights, prior))
    monkeypatch.setattr(m.previous, "folder_for", lambda *args: third)
    monkeypatch.setattr(m.predecessor, "folder_for", lambda *args: target)
    monkeypatch.setattr(m.predecessor.prior, "folder_for", lambda *args: graph)
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
    with pytest.raises(ValueError, match="single_explicit_original_three_course_registration"):
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


@pytest.mark.parametrize("wrong", ["other_contract", "other_size"])
def test_a_different_complete_tail_cannot_release_the_same_reservations(tmp_path, monkeypatch, wrong):
    execution, _, _, _ = waiting(tmp_path, monkeypatch)
    if wrong == "other_contract":
        execution["predecessor_execution_sha256"] = "different_finite_tail"
    else:
        execution["predecessor_total_units"] = 15
    with pytest.raises(ValueError, match="entire_actual_original_tail_manuscript"):
        m.wait(tmp_path, execution, live=lambda row: False, sleep=lambda seconds: pytest.fail("Unexpected resume"))


def terminal_proof():
    return {"state": "original_graph_tail_terminal_authenticated_not_a_complete_manuscript",
        "writer": "writer", "verified_prefix_units": 12, "total_units": 16,
        "complete_manuscript": False, "original_tail_execution_sha256": "actual_finite_tail", "files": {}}


def test_explicit_retained_terminal_is_not_reported_as_a_complete_parent(tmp_path, monkeypatch):
    execution, _, _, calls = waiting(tmp_path, monkeypatch)
    proof = terminal_proof()
    execution.update(predecessor_mode="retained_incomplete_original_tail", terminal_proof_sha256=m.digest(proof))
    monkeypatch.setattr(m, "retained_terminal", lambda *args: proof)
    result = m.wait(tmp_path, execution, live=lambda row: False, sleep=lambda seconds: pytest.fail("No automatic resume"))
    assert result["retained_incomplete_predecessor"] == proof
    assert result["original_predecessor_complete"] is False and not calls
    assert result["model_calls"] == 0 and result["reader_publication_allowed"] is False


def test_retained_terminal_cannot_replace_a_still_running_exact_parent(tmp_path, monkeypatch):
    execution, _, _, _ = waiting(tmp_path, monkeypatch)
    execution.update(predecessor_mode="retained_incomplete_original_tail")
    monkeypatch.setattr(m, "retained_terminal", lambda *args: pytest.fail("Cannot inspect a live parent as terminal"))
    with pytest.raises(ValueError, match="same_exact_exited_incomplete_predecessor_not_a_resume"):
        m.wait(tmp_path, execution, live=lambda row: True)


@pytest.mark.parametrize("wrong", ["proof", "original_tail"])
def test_incomplete_parent_must_match_the_explicit_original_terminal(tmp_path, monkeypatch, wrong):
    execution, _, _, _ = waiting(tmp_path, monkeypatch)
    proof = terminal_proof()
    execution.update(predecessor_mode="retained_incomplete_original_tail", terminal_proof_sha256=m.digest(proof))
    if wrong == "proof":
        execution["terminal_proof_sha256"] = "changed"
    else:
        execution["predecessor_execution_sha256"] = "changed"
    monkeypatch.setattr(m, "retained_terminal", lambda *args: proof)
    with pytest.raises(ValueError, match="same_independently_retained_original_terminal"):
        m.wait(tmp_path, execution, live=lambda row: False)


@pytest.mark.parametrize("wrong", [None, "infer", "fallback", "complete", "prefix", "proof"])
def test_terminal_disposition_requires_real_independent_restore_of_the_same_incomplete_proof(tmp_path, monkeypatch, wrong):
    proof = terminal_proof()
    row = {"state": "original_graph_tail_terminal_independently_restored", "writer": "writer",
        "terminal_proof_sha256": m.digest(proof), "original_root_reads": 0, "model_calls": 0,
        "verified_prefix_units": 12, "complete_manuscript": False}
    if wrong == "infer":
        row["model_calls"] = 1
    elif wrong == "fallback":
        row["original_root_reads"] = 1
    elif wrong == "complete":
        row["complete_manuscript"] = True
    elif wrong == "prefix":
        row["verified_prefix_units"] = 16
    elif wrong == "proof":
        row["terminal_proof_sha256"] = "different_proof"
    atomic_json(tmp_path / "independent-terminal-restore.json", row)
    atomic_json(tmp_path / "terminal-authenticated-proof.json", proof)
    monkeypatch.setattr(m.predecessor, "folder_for", lambda *args: tmp_path)
    monkeypatch.setattr(m.terminal, "inspect", lambda *args: proof)
    if wrong is None:
        assert m.retained_terminal(tmp_path, "writer") == proof
    else:
        with pytest.raises(ValueError, match="actual_independent_terminal_restore_stays_explicitly_incomplete"):
            m.retained_terminal(tmp_path, "writer")
