from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from wechat_decrypt_tool import course_textbook_ready_resume as m
from wechat_decrypt_tool.course_knowledge import atomic_json


def waiting(tmp_path, monkeypatch):
    folder = tmp_path / "continuation"
    (folder / "guard").mkdir(parents=True)
    worker = tmp_path / "wiki/textbook-whole-authoring-jobs/writer/progress.json"
    worker.parent.mkdir(parents=True)
    execution = {
        "predecessor_directory": "continuation",
        "predecessor_writer": "writer",
        "predecessor_guard": {"pid": 10, "created": 20},
        "predecessor_worker": {"pid": 11, "created": 21},
    }
    atomic_json(
        folder / "guard/guard-progress.json",
        {"pid": 10, "created": 20, "state": "worker_exited", "returncode": 0},
    )
    atomic_json(
        worker,
        {
            "pid": 11,
            "created": 21,
            "state": "conceptual_article_requires_actual_source_and_editorial_review",
            "stage": "private_article_assembled",
        },
    )
    calls = []
    monkeypatch.setattr(m.guard, "checkpoint", lambda *args: None)
    monkeypatch.setattr(
        m.predecessor,
        "verify",
        lambda *args: (
            calls.append(args)
            or {
                "summary": {"job": "writer", "verified_units": 4, "total_units": 4},
                "private": {},
                "proof": {"actual": "complete-private"},
            }
        ),
    )
    return execution, folder, worker, calls


def test_predecessor_must_really_exit_and_complete_its_own_verifier(
    tmp_path, monkeypatch
):
    execution, _, _, calls = waiting(tmp_path, monkeypatch)
    live = iter([False, True, False, False])
    sleeps = []
    result = m.wait(
        tmp_path, execution, live=lambda row: next(live), sleep=sleeps.append
    )
    assert sleeps == [15] and calls == [(tmp_path, "writer")]
    assert result["original_predecessor"] == {"actual": "complete-private"}
    assert result["model_calls"] == 0 and result["reader_publication_allowed"] is False


@pytest.mark.parametrize(
    "defect",
    [
        "guard_identity",
        "worker_identity",
        "paused",
        "failed",
        "stopped_worker",
        "not_assembled",
        "partial_result",
    ],
)
def test_failed_or_ambiguous_predecessor_never_starts_new_course_roles(
    defect, tmp_path, monkeypatch
):
    execution, folder, worker, calls = waiting(tmp_path, monkeypatch)
    gp = folder / "guard/guard-progress.json"
    state, status = m.whole.read(gp), m.whole.read(worker)
    if defect == "guard_identity":
        state["created"] += 1
    elif defect == "worker_identity":
        status["created"] += 1
    elif defect == "paused":
        state["state"] = "paused"
    elif defect == "failed":
        state["returncode"] = 1
    elif defect == "stopped_worker":
        status["state"] = "stopped"
    elif defect == "not_assembled":
        status["stage"] = "concepts-00-0002"
    else:
        monkeypatch.setattr(
            m.predecessor,
            "verify",
            lambda *args: {
                "summary": {"job": "writer", "verified_units": 3, "total_units": 4},
                "private": {},
                "proof": {},
            },
        )
    atomic_json(gp, state)
    atomic_json(worker, status)
    with pytest.raises(ValueError):
        m.wait(
            tmp_path,
            execution,
            live=lambda row: False,
            sleep=lambda n: pytest.fail("Do not wait on a terminal failure"),
        )
    assert not calls


def test_new_pause_is_respected_before_polling_or_starting_roles(tmp_path, monkeypatch):
    execution, _, _, _ = waiting(tmp_path, monkeypatch)

    def stop(*args):
        raise InterruptedError("Synthetic retained pause")

    monkeypatch.setattr(m.guard, "checkpoint", stop)
    with pytest.raises(InterruptedError):
        m.wait(
            tmp_path, execution, live=lambda row: pytest.fail("No polling after pause")
        )


def test_new_controller_waits_without_taking_full_course_lock_and_preserves_original_failure(
    tmp_path, monkeypatch
):
    folder, old = tmp_path / "new", tmp_path / "old"
    folder.mkdir()
    old.mkdir()
    failure = {"state": "stopped", "completed_courses": 0}
    atomic_json(old / "progress.json", failure)
    atomic_json(
        folder / "independent-input-restore.json",
        {
            "state": "ready_continuation_inputs_independently_restored",
            "batch": "batch",
            "original_root_reads": 0,
            "model_calls": 0,
        },
    )
    plan = {"items": [{"lesson": "lesson", "inventory": "inventory"}]}
    flights = [{"job": "ledger"}]
    monkeypatch.setattr(
        m, "load", lambda *args: (tmp_path, folder, old, plan, None, flights, {})
    )
    monkeypatch.setattr(m.ready, "unused", lambda *args: None)
    leases = []

    @contextmanager
    def lease(path, **kwargs):
        leases.append(path.name)
        yield

    monkeypatch.setattr(m, "schema_lease", lease)

    def wait(*args):
        assert leases == ["controller.lock"]
        assert m.whole.read(old / "progress.json") == failure
        raise InterruptedError("Synthetic predecessor pause")

    monkeypatch.setattr(m, "wait", wait)
    with pytest.raises(InterruptedError):
        m.run(tmp_path, "batch")
    assert m.whole.read(old / "progress.json") == failure
    assert m.whole.read(folder / "progress.json")["completed_courses"] == 0
    with pytest.raises(ValueError, match="once"):
        m.run(tmp_path, "batch")


def test_writer_requires_unused_roles_and_uses_original_ledger_not_a_new_one(
    tmp_path, monkeypatch
):
    job = tmp_path / "writer"
    job.mkdir()
    item, flight = {"inventory": "inventory"}, {"job": "original-ledger"}
    monkeypatch.setattr(
        m,
        "writer_bound",
        lambda *args: (
            tmp_path,
            None,
            item,
            flight,
            "tokenizer",
            SimpleNamespace(root=job),
            {},
            {},
            {},
        ),
    )
    monkeypatch.setattr(m.guard, "checkpoint", lambda *args: None)
    monkeypatch.setattr(
        m, "writer_type", lambda *args, **kwargs: m.writing.AuthoringJob
    )
    calls = []
    monkeypatch.setattr(m.writing, "run", lambda *args: calls.append(args))
    m.writer_worker(tmp_path, "batch", 0)
    assert calls == [(tmp_path, "inventory", "original-ledger", "tokenizer")]
    atomic_json(job / "progress.json", {"state": "started"})
    with pytest.raises(ValueError, match="first_role"):
        m.writer_worker(tmp_path, "batch", 0)


def test_source_bindings_are_explicit_and_keep_the_original_code_frozen():
    bound = m.bindings()
    assert bound["original"] == m.ready.bindings()
    assert bound["predecessor"] == m.predecessor.bindings()
    assert bound["planning"] == m.planning.binding()
