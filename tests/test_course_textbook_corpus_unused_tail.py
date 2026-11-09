import copy
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import pytest

from wechat_decrypt_tool import course_textbook_corpus_unused_tail as m
from wechat_decrypt_tool.course_knowledge import atomic_json
from test_course_textbook_fresh_authoring import fake_job


def unused_fixture(tmp_path, monkeypatch):
    folder = tmp_path / "batch"
    folder.mkdir()
    flights = [{"job": "first"}, {"job": "second"}]
    job = tmp_path / "wiki/textbook-keyed-jobs/second"
    job.mkdir(parents=True)
    plan = {"items": [{"inventory": "first"}, {"inventory": "second"}]}
    monkeypatch.setattr(m.original, "check_item", lambda *args: None)
    return folder, flights, job, plan


def test_existing_first_course_never_counts_as_unattempted_second(tmp_path, monkeypatch):
    folder, flights, _, plan = unused_fixture(tmp_path, monkeypatch)
    atomic_json(folder / "item-00-ledger-started.json", {"synthetic": True})
    (tmp_path / "wiki/textbook-keyed-jobs/first").mkdir()
    atomic_json(tmp_path / "wiki/textbook-keyed-jobs/first/progress.json", {"synthetic": True})
    assert m.unused(tmp_path, folder, plan, flights) is None


@pytest.mark.parametrize("name", ["progress.json", "ledger.json", "group-0000.json", "attempts/role-01.json",
    "whole-source-authoring-execution.json", "../batch-marker"])
def test_any_actual_second_role_or_writer_reservation_blocks_new_run(name, tmp_path, monkeypatch):
    folder, flights, job, plan = unused_fixture(tmp_path, monkeypatch)
    if name == "../batch-marker":
        path = folder / "item-01-ledger-started.json"
    else:
        path = job / name
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(path, {"synthetic": True})
    with pytest.raises(ValueError, match="original_second_course_all_roles_unused"):
        m.unused(tmp_path, folder, plan, flights)


def waiting(tmp_path, monkeypatch, *, state="worker_exited", returncode=0):
    contract = {"first_guard_path": "guard.json", "first_guard": {"pid": 10, "created": 20},
        "first_worker": {"pid": 11, "created": 21}}
    atomic_json(tmp_path / "guard.json", {**contract["first_guard"], "state": state, "returncode": returncode})
    monkeypatch.setattr(m.guard, "checkpoint", lambda *args: None)
    return contract


def test_guard_state_alone_does_not_launch_second_while_first_process_is_live(tmp_path, monkeypatch):
    contract = waiting(tmp_path, monkeypatch)
    calls, sleeps = [], []

    def live(row):
        calls.append(row["pid"])
        return len(calls) == 1

    m.wait(tmp_path, contract, live=live, sleep=sleeps.append)
    assert sleeps == [15] and calls == [10, 10, 11]


@pytest.mark.parametrize("defect", ["paused", "failed", "replaced_identity"])
def test_failed_paused_or_replaced_predecessor_cannot_authorize_second(defect, tmp_path, monkeypatch):
    contract = waiting(tmp_path, monkeypatch)
    path = tmp_path / "guard.json"
    row = m.whole.read(path)
    if defect == "paused":
        row["state"] = "paused"
    elif defect == "failed":
        row["returncode"] = 1
    else:
        row["created"] = 999
    atomic_json(path, row)
    with pytest.raises(ValueError):
        m.wait(tmp_path, contract, live=lambda *args: False,
            sleep=lambda *args: pytest.fail("Must stop instead of silently retrying"))


def test_control_marker_checkpoint_propagates_without_silent_resume(tmp_path, monkeypatch):
    contract = waiting(tmp_path, monkeypatch)

    def stop(root):
        raise InterruptedError("Synthetic retained pause")

    monkeypatch.setattr(m.guard, "checkpoint", stop)
    with pytest.raises(InterruptedError):
        m.wait(tmp_path, contract, live=lambda *args: False)


def test_first_second_writer_receives_real_count_bound_schema_and_full_originals(tmp_path, monkeypatch):
    _, inventory, working, packet, context, raw = fake_job(tmp_path, monkeypatch)
    extra = {"synthetic": "count format"}
    monkeypatch.setattr(m, "writer_execution", lambda *args: extra)
    job = m.writer_type({"synthetic_contract": True}, extra, {}, read_only=False)(None, inventory, working, None)
    raw["paragraph_count"] = 2
    raw["paragraphs"].append({"text": "Synthetic independent full explanation", "citations": ["O00002"]})
    calls, saved = [], {}

    def generate(self, name, prompt, schema, actual_context, validate, **kwargs):
        calls.append(name)
        assert kwargs["evidence_packet"] == m.fresh.continuation.scope_for(["U0"], working, inventory, context, packet)["expanded"]
        jsonschema.validate(raw, schema)
        invalid = copy.deepcopy(raw)
        invalid["coverage"]["U0"]["paragraphs"] = [2]
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(invalid, schema)
        validate(raw)
        saved[name] = copy.deepcopy(raw)
        atomic_json(tmp_path / (name + ".json"), {"synthetic_raw": raw})
        return raw

    monkeypatch.setattr(m.fresh.BASE_JOB, "stage", generate)
    monkeypatch.setattr(job.reader, "stage", lambda name, *args, **kwargs: saved[name])
    result = job.stage("section-0000-write", m.writing.author.WRITE_PROMPT,
        m.writing.section_schema(["U0"], working), context, lambda row: None, evidence_packet=packet)
    assert calls == ["section-0000-write"] and result["coverage"] == raw["coverage"]
    assert "paragraph_count" not in result and m.whole.read(tmp_path / "section-0000-write.json")["synthetic_raw"] == raw
    assert tmp_path / m.WRITER_CONTRACT in job.reader.paths


def test_readonly_writer_forbids_both_inference_and_gpu_acquisition(tmp_path, monkeypatch):
    _, inventory, working, _, _, _ = fake_job(tmp_path, monkeypatch, read_only=True)
    monkeypatch.setattr(m, "writer_execution", lambda *args: {})
    job = m.writer_type({"synthetic_contract": True}, {}, {}, read_only=True)(None, inventory, working, None)
    with pytest.raises(ValueError, match="inference_forbidden"):
        job.generate_response()
    with pytest.raises(ValueError, match="gpu_acquisition_forbidden"):
        job.slot()


def test_first_worker_once_refuses_existing_progress_before_installation(tmp_path, monkeypatch):
    atomic_json(tmp_path / "progress.json", {"synthetic": True})
    monkeypatch.setattr(m, "writer_bound", lambda *args: (tmp_path, None, "inventory", "ledger", None,
        SimpleNamespace(root=tmp_path), {}, {}, {}))
    monkeypatch.setattr(m.guard, "checkpoint", lambda *args: None)
    monkeypatch.setattr(m.fresh, "installed", lambda *args: pytest.fail("Existing writer must not be restarted"))
    with pytest.raises(ValueError, match="first_second_writer_worker_only"):
        m.writer_worker(tmp_path, "batch")


def controller_fixture(tmp_path, monkeypatch, *, complete):
    folder, target = tmp_path / "batch", tmp_path / "tail"
    folder.mkdir()
    target.mkdir()
    (tmp_path / "wiki").mkdir()
    bundle = SimpleNamespace(job=SimpleNamespace(key="first"))
    plan = {"items": [{"inventory": "first"}, {"inventory": "second"}]}
    flights = [{"job": "first"}, {"job": "second"}]
    monkeypatch.setattr(m, "load", lambda *args: (tmp_path, folder, target, plan, None, flights, bundle, {}))
    monkeypatch.setattr(m, "unused", lambda *args: None)
    monkeypatch.setattr(m, "wait", lambda *args: None)
    manuscript = {"summary": {"verified_units": 14 if complete else 13, "total_units": 14},
        "private": {}, "proof": {"synthetic": True}, "issues": [{"kind": "negative_preserved"}]}
    monkeypatch.setattr(m.reviewed, "reconstruct", lambda *args: copy.deepcopy(manuscript))
    return target, manuscript


def test_partial_predecessor_cannot_be_replaced_with_successful_exit_flag(tmp_path, monkeypatch):
    target, _ = controller_fixture(tmp_path, monkeypatch, complete=False)
    monkeypatch.setattr(m.guard, "run", lambda *args: pytest.fail("Partial manuscript must not authorize ledger"))
    with pytest.raises(ValueError, match="actual_complete_first_course_before_successor"):
        m.run(tmp_path, "batch")
    assert m.whole.read(target / "progress.json")["state"] == "stopped"
    assert list(target.glob("failure-*.json"))
    with pytest.raises(ValueError, match="controller_once_no_automatic_retry"):
        m.run(tmp_path, "batch")


def test_successor_uses_only_original_second_ledger_and_preserves_private_quality_issues(tmp_path, monkeypatch):
    target, manuscript = controller_fixture(tmp_path, monkeypatch, complete=True)
    calls = []

    def save(root, value):
        path = root / "wiki/snapshot.json"
        atomic_json(path, value["proof"])
        return path

    def guarded(root, folder, command):
        calls.append((folder.name, command))
        return {"state": "private_worker_exited"}

    monkeypatch.setattr(m.original.mapped.snapshot, "save", save)
    monkeypatch.setattr(m.guard, "run", guarded)
    monkeypatch.setattr(m.writing, "load_inputs", lambda *args: None)
    monkeypatch.setattr(m.fresh, "prepare", lambda *args: None)
    job = SimpleNamespace(root=tmp_path / "writer", key="writer")
    monkeypatch.setattr(m.fresh, "bound", lambda *args: (tmp_path, {}, {}, job, {}))
    monkeypatch.setattr(m.whole, "backup", lambda *args: None)
    monkeypatch.setattr(m, "verify_writer", lambda *args: copy.deepcopy(manuscript))
    result = m.run(tmp_path, "batch")
    assert [name for name, _ in calls] == ["item-01-ledger-guard", "item-01-authoring-guard"]
    assert calls[0][1][-3:] == ["--item", "1", "ledger-worker"]
    assert result["original_courses_completed"] == 2 and result["unresolved_issues"] == 1
    assert result["reader_publication_allowed"] is False and result["source_deletion_allowed"] is False
    assert m.whole.read(target / "first-course-result.json")["unresolved_issues"] == 1


def test_code_binding_names_are_stable_between_cli_and_import():
    assert Path(m.__file__).stem in m.bindings() and "__main__" not in m.bindings()
