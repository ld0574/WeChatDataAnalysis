import copy
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import pytest

from wechat_decrypt_tool import course_textbook_ready_batch as m
from wechat_decrypt_tool.course_knowledge import atomic_json
from test_course_textbook_fresh_authoring import fake_job


def test_short_first_order_retains_the_entire_original_selected_scope(monkeypatch, tmp_path):
    rows = [{"lesson": str(n), "groups": g, "characters": c, "records": n + 1}
        for n, g, c in [(0, 24, 200), (1, 5, 100), (2, 22, 300)]]
    monkeypatch.setattr(m.original, "selected_inputs", lambda root, limit: copy.deepcopy(rows))
    actual = m.select(tmp_path, 3)
    assert [r["lesson"] for r in actual] == ["1", "2", "0"]
    assert sorted(actual, key=lambda r: r["lesson"]) == rows


def waiting(tmp_path, monkeypatch, state="writing_original_unused_second_article"):
    plan = {"predecessor_directory": "previous", "predecessor_controller": {"pid": 10, "created": 20},
        "predecessor_batch": "previous", "predecessor_execution_sha256": m.digest({})}
    target = tmp_path / "previous"
    target.mkdir()
    atomic_json(target / "progress.json", {**plan["predecessor_controller"], "state": state})
    atomic_json(target / "result.json", {"original_courses_completed": 2,
        "original_unused_second_completed": 1, "second_writer": "second"})
    monkeypatch.setattr(m.guard, "checkpoint", lambda *args: None)
    monkeypatch.setattr(m.previous, "load", lambda *args: (None, None, target, None, None, None,
        SimpleNamespace(job=SimpleNamespace(key="first")), {}))

    def book(key):
        return {"summary": {"job": key, "verified_units": 4, "total_units": 4},
            "private": {}, "proof": {"synthetic": key}, "issues": [{"kind": "unresolved"}]}

    monkeypatch.setattr(m.reviewed, "reconstruct", lambda *args: book("first"))
    monkeypatch.setattr(m.previous, "verify_writer", lambda *args: book("second"))
    return plan, target


def test_wait_requires_exact_process_exit_even_when_status_looks_finished(tmp_path, monkeypatch):
    plan, _ = waiting(tmp_path, monkeypatch,
        "original_two_course_private_manuscripts_require_coverage_and_editorial_review")
    live = iter([True, False])
    sleeps = []
    result = m.wait(tmp_path, plan, live=lambda row: next(live), sleep=sleeps.append)
    assert sleeps == [15]
    assert result["model_calls"] == 0 and result["semantic_acceptance"] is False


@pytest.mark.parametrize("defect", ["stopped", "paused", "pausing", "identity", "incomplete", "partial_book"])
def test_failed_or_incomplete_predecessor_never_authorizes_new_roles(defect, tmp_path, monkeypatch):
    plan, target = waiting(tmp_path, monkeypatch,
        "original_two_course_private_manuscripts_require_coverage_and_editorial_review")
    row = m.whole.read(target / "progress.json")
    if defect == "identity":
        row["created"] += 1
    elif defect == "incomplete":
        row["state"] = "writing_original_unused_second_article"
    elif defect == "partial_book":
        monkeypatch.setattr(m.previous, "verify_writer", lambda *args: {
            "summary": {"job": "second", "verified_units": 3, "total_units": 4}, "private": {}, "proof": {}})
    else:
        row["state"] = defect
    atomic_json(target / "progress.json", row)
    with pytest.raises(ValueError):
        m.wait(tmp_path, plan, live=lambda *args: False,
            sleep=lambda *args: pytest.fail("Must fail instead of retrying"))


def test_control_marker_is_checked_before_waiting_or_gpu_work(tmp_path, monkeypatch):
    plan, _ = waiting(tmp_path, monkeypatch)

    def checkpoint(*args):
        raise InterruptedError("Synthetic retained pause")

    monkeypatch.setattr(m.guard, "checkpoint", checkpoint)
    with pytest.raises(InterruptedError):
        m.wait(tmp_path, plan, live=lambda *args: pytest.fail("Stop before polling"))


def test_recovered_writer_requires_both_real_exits_and_uses_its_actual_verifier(tmp_path, monkeypatch):
    plan, target = waiting(tmp_path, monkeypatch)
    plan.update(predecessor_kind="unspent-writer-dispatch", predecessor_worker={"pid": 11, "created": 21},
        predecessor_writer="writer")
    atomic_json(target / "guard-progress.json", {**plan["predecessor_controller"], "state": "worker_exited", "returncode": 0})
    live = iter([False, True, False, False])
    sleeps, verified = [], []
    monkeypatch.setattr(m.recovered, "verify", lambda *args: verified.append(args) or {"proof": {"actual": "original writer"}})
    result = m.wait(tmp_path, plan, live=lambda row: next(live), sleep=sleeps.append)
    assert sleeps == [15] and verified == [(tmp_path, "writer")]
    assert result["original_second"] == {"actual": "original writer"}
    assert result["reader_publication_allowed"] is False


@pytest.mark.parametrize("defect", ["paused", "failed", "identity"])
def test_failed_recovered_dispatch_never_authorizes_next_courses(defect, tmp_path, monkeypatch):
    plan, target = waiting(tmp_path, monkeypatch)
    plan.update(predecessor_kind="unspent-writer-dispatch", predecessor_worker={"pid": 11, "created": 21},
        predecessor_writer="writer")
    row = {**plan["predecessor_controller"], "state": "worker_exited", "returncode": 0}
    if defect == "paused":
        row["state"] = "paused"
    elif defect == "failed":
        row["returncode"] = 1
    else:
        row["created"] += 1
    atomic_json(target / "guard-progress.json", row)
    monkeypatch.setattr(m.recovered, "verify", lambda *args: pytest.fail("No reconstruction of failed dispatch"))
    with pytest.raises(ValueError):
        m.wait(tmp_path, plan, live=lambda *args: False)


@pytest.mark.parametrize("artifact", ["progress.json", "ledger.json", "group-0000.json",
    "attempts/group-0000-extract-01.json", "whole-source-authoring-execution.json", "item_result"])
def test_any_original_attempt_or_result_prevents_batch_restart(artifact, tmp_path):
    folder = tmp_path / "batch"
    folder.mkdir()
    job = tmp_path / "wiki/textbook-keyed-jobs/ledger"
    job.mkdir(parents=True)
    path = folder / "item-00-result.json" if artifact == "item_result" else job / artifact
    path.parent.mkdir(exist_ok=True)
    atomic_json(path, {"synthetic": True})
    with pytest.raises(ValueError):
        m.unused(tmp_path, folder, [{"job": "ledger"}])


def test_actual_first_write_uses_count_bound_schema_without_changing_prose(tmp_path, monkeypatch):
    _, inventory, working, packet, context, raw = fake_job(tmp_path, monkeypatch)
    extra = {"synthetic": True}
    monkeypatch.setattr(m, "writer_execution", lambda *args: extra)
    job = m.writer_type({"synthetic_contract": True}, extra, "batch", 0, read_only=False)(None, inventory, working, None)
    raw["paragraph_count"] = 2
    raw["paragraphs"].append({"text": "Synthetic complete explanation", "citations": ["O00002"]})
    saved, calls = {}, []

    def generate(self, name, prompt, schema, actual_context, validate, **kwargs):
        calls.append(name)
        expected = m.fresh.continuation.scope_for(["U0"], working, inventory, context, packet)["expanded"]
        assert kwargs["evidence_packet"] == expected
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
    actual = job.stage("section-0000-write", m.writing.author.WRITE_PROMPT,
        m.writing.section_schema(["U0"], working), context, lambda *args: None, evidence_packet=packet)
    assert calls == ["section-0000-write"]
    assert "paragraph_count" not in actual
    assert [p["text"] for p in actual["paragraphs"]] == [p["text"] for p in raw["paragraphs"]]
    assert m.whole.read(tmp_path / "section-0000-write.json")["synthetic_raw"] == raw
    assert tmp_path / m.COUNT_CONTRACT in job.reader.paths


def test_readonly_reconstruction_forbids_inference_and_gpu_slots(tmp_path, monkeypatch):
    _, inventory, working, _, _, _ = fake_job(tmp_path, monkeypatch, read_only=True)
    monkeypatch.setattr(m, "writer_execution", lambda *args: {})
    job = m.writer_type({"synthetic_contract": True}, {}, "batch", 0, read_only=True)(None, inventory, working, None)
    with pytest.raises(ValueError, match="inference_forbidden"):
        job.generate_response()
    with pytest.raises(ValueError, match="gpu_acquisition_forbidden"):
        job.slot()


@pytest.mark.parametrize("number", [-1, True, None, 3])
def test_invalid_item_number_cannot_select_a_different_course(number):
    with pytest.raises(ValueError, match="item_number"):
        m.item_at({"items": [{}, {}, {}]}, [{}, {}, {}], number)


def controller(tmp_path, monkeypatch):
    folder = tmp_path / "batch"
    folder.mkdir()
    (tmp_path / "wiki").mkdir()
    plan = {"items": [{"lesson": "lesson", "inventory": "inventory"}]}
    flights = [{"job": "ledger"}]
    monkeypatch.setattr(m, "load", lambda *args: (tmp_path, folder, plan, None, flights))
    monkeypatch.setattr(m.guard, "checkpoint", lambda *args: None)
    monkeypatch.setattr(m.original, "check_item", lambda *args: None)
    atomic_json(folder / "independent-input-restore.json", {"state": "ready_course_inputs_independently_restored",
        "batch": "batch", "model_calls": 0, "original_root_reads": 0})
    return folder


def test_failed_predecessor_stops_before_global_worker_lock_and_preserves_failure(tmp_path, monkeypatch):
    folder = controller(tmp_path, monkeypatch)
    monkeypatch.setattr(m, "unused", lambda *args: None)

    def failure(*args):
        assert not (tmp_path / "wiki/full-course-batch-worker.lock").exists()
        raise ValueError("requires_ready_course_batch_synthetic_predecessor_failure")

    monkeypatch.setattr(m, "wait", failure)
    monkeypatch.setattr(m.guard, "run", lambda *args: pytest.fail("No GPU work after failed predecessor"))
    with pytest.raises(ValueError, match="synthetic_predecessor_failure"):
        m.run(tmp_path, "batch")
    assert m.whole.read(folder / "progress.json")["state"] == "stopped"
    assert list(folder.glob("failure-*.json"))
    with pytest.raises(ValueError, match="controller_once"):
        m.run(tmp_path, "batch")


def test_negative_reviews_remain_private_after_successful_original_stages(tmp_path, monkeypatch):
    folder = controller(tmp_path, monkeypatch)
    monkeypatch.setattr(m, "unused", lambda *args: None)
    monkeypatch.setattr(m, "wait", lambda *args: {"synthetic": "predecessor"})
    calls = []
    monkeypatch.setattr(m.guard, "run", lambda root, path, command:
        calls.append((path, command)) or {"state": "private_worker_exited"})
    monkeypatch.setattr(m.writing, "load_inputs", lambda *args: None)
    monkeypatch.setattr(m.fresh, "prepare", lambda *args: None)
    job = SimpleNamespace(root=tmp_path / "writer", key="writer")
    monkeypatch.setattr(m.fresh, "bound", lambda *args: (tmp_path, {}, {}, job, {}))
    monkeypatch.setattr(m, "verify_writer", lambda *args: {"issues": [{"kind": "negative_review"}]})

    def snapshot(root, value):
        path = root / "wiki/snapshot.json"
        atomic_json(path, {"synthetic": True})
        return path

    monkeypatch.setattr(m.original.mapped.snapshot, "save", snapshot)
    result = m.run(tmp_path, "batch")
    assert [p.name for p, _ in calls] == ["item-00-ledger-guard", "item-00-authoring-guard"]
    assert all(not p.is_relative_to(job.root) for p, _ in calls)
    assert result["completed_courses"] == 1 and result["reader_publication_allowed"] is False
    assert m.whole.read(folder / "item-00-result.json")["unresolved_issues"] == 1
    assert result["source_deletion_allowed"] is False


def test_inputs_must_stay_inside_the_restored_root(tmp_path):
    with pytest.raises(ValueError, match="contained_input"):
        m.source_path(tmp_path, "../escape.json")


def test_bindings_are_stable_for_import_and_cli():
    assert Path(m.__file__).stem in m.bindings()["entrypoints"]
    assert "__main__" not in m.bindings()["entrypoints"]
