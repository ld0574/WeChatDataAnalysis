import copy
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import pytest

from wechat_decrypt_tool import course_textbook_ready_gap_editor as m
import test_course_textbook_corpus_gap_editor as corpus_tests


@pytest.fixture
def sample(monkeypatch):
    return corpus_tests.sample.__wrapped__(monkeypatch)


def test_only_exact_duplicate_reference_ids_change_before_review(sample):
    units, context, raw = sample
    raw["additions"]["N000"]["citations"] = ["S0", "S0", "S0"]
    before = copy.deepcopy(raw)
    with m.installed():
        compiled, proof = m.candidate(raw, context, units)
    assert raw == before
    assert compiled["additions"]["N000"]["citations"] == ["S0"]
    assert compiled["additions"]["N000"]["text"] == raw["additions"]["N000"]["text"]
    assert not proof["prose_changed"] and not proof["source_sets_changed"]
    assert proof["exact_duplicate_reference_changes"] == [{"addition": "N000", "original_count": 3, "unique_count": 1}]
    assert compiled["groups"][0]["clauses"] == ["C0", "C1"]


@pytest.mark.parametrize("error", ["unknown_reference", "empty_references", "short_paragraph", "unmapped", "missing_group"])
def test_canonicalization_does_not_weaken_any_other_gate(sample, error):
    units, context, raw = sample
    if error == "unknown_reference":
        raw["additions"]["N000"]["citations"] = ["unknown", "unknown"]
    elif error == "empty_references":
        raw["additions"]["N000"]["citations"] = []
    elif error == "short_paragraph":
        raw["additions"]["N000"]["text"] = "Short synthetic text."
    elif error == "unmapped":
        raw["source_groups"]["R0000"]["last_group"]["article_items"] = ["A0"]
    else:
        raw["source_groups"].clear()
    with m.installed(), pytest.raises((ValueError, jsonschema.ValidationError)):
        m.candidate(raw, context, units)


def test_scoped_first_use_adapter_restores_all_frozen_functions_after_failure():
    before = m.original.bindings, m.original.wire_schema, m.original.compile_candidate, m.original.EDIT
    with pytest.raises(RuntimeError):
        with m.installed():
            assert m.original.EDIT.endswith(m.NOTICE)
            assert m.original.bindings()["original"] == before[0]()
            raise RuntimeError("Synthetic controlled failure")
    assert (m.original.bindings, m.original.wire_schema, m.original.compile_candidate, m.original.EDIT) == before


def test_existing_registration_is_rejected_before_loading_or_spending_any_role(tmp_path, monkeypatch):
    parent = "a" * 32
    path = m.original.registration(tmp_path, parent)
    m.retained(path, {"old_budget": "retained"})
    monkeypatch.setattr(m.guard, "checkpoint", lambda root: None)
    monkeypatch.setattr(m.ready, "load", lambda *a: pytest.fail("Existing parent budget must not be renewed"))
    with pytest.raises(ValueError, match="one_editorial_registration"):
        m.prepare(tmp_path, parent, tmp_path / "tokenizer.json")
    assert m.whole.read(path) == {"old_budget": "retained"}


@pytest.fixture
def completed(tmp_path, monkeypatch):
    root, parent = tmp_path.resolve(), "a" * 32
    folder, job_folder = root / "dispatch", root / "coverage"
    plan, manuscript = {"batches": [{}], "clauses": 2}, {"full_manuscript": "Synthetic complete material", "issues": [1, 2, 3]}
    result = {"job": "b" * 32, "parent_writer": parent, "unresolved_clauses": 1}
    status = {"job": result["job"], "stage": "complete", "completed_batches": 1, "completed_clauses": 2}
    protected = {"state": "worker_exited", "returncode": 0}
    tokenizer = root / "tokenizer.json"
    snapshot = m.ready.snapshot_path(root, parent)
    for path, value in [(job_folder / "coverage-result.json", result), (job_folder / "progress.json", status),
            (job_folder / "review-plan.json", plan), (job_folder / m.ready.first.CONTRACT, {"original_budget": 1}),
            (snapshot, manuscript), (tokenizer, {"synthetic_tokenizer": True}), (folder / "guard-progress.json", protected)]:
        m.retained(path, value)
    selected = [job_folder / name for name in ("coverage-result.json", "progress.json", "review-plan.json", m.ready.first.CONTRACT)]
    selected += [snapshot, tokenizer]
    backup = root / "backup"
    index = {"mode": "results", "actual_result": {"unresolved_clauses": 1}, "files": {
        "output/course-knowledge/" + p.relative_to(root).as_posix(): {"sha256": m.whole.sha(p.read_bytes())} for p in selected}}
    m.retained(backup / "manifest.json", index)
    restored = {"state": "ready_first_full_coverage_results_independently_reconstructed", "job": result["job"],
        "parent_writer": parent, "actual_coverage_roles_replayed": True, "original_root_reads": 0, "new_model_calls": 0,
        "plan_sha256": m.digest(plan), "manuscript_sha256": m.digest(manuscript), "result": index["actual_result"],
        "backup": str(backup), "manifest_sha256": m.whole.sha((backup / "manifest.json").read_bytes())}
    final_state = "actual_first_full_coverage_independently_restored_not_reader_accepted"
    m.retained(folder / "independent-result-restore.json", restored)
    m.retained(folder / "cpu-finalization/actual-result-proof.json", restored["result"])
    m.retained(folder / "cpu-finalization/result.json", {"state": final_state, "job": result["job"],
        "backup": restored, "result": restored["result"]})
    m.retained(folder / "cpu-finalization/progress.json", {"state": final_state})
    monkeypatch.setattr(m.writing, "identity_live", lambda value: value.get("live", False))
    bundle = SimpleNamespace(root=root, folder=folder, job=SimpleNamespace(root=job_folder, key=result["job"]),
        contract={"parent_writer": parent}, plan=plan, manuscript=manuscript)
    return bundle, tokenizer, restored


def test_exact_complete_results_and_independent_restore_are_required(completed):
    bundle, tokenizer, restored = completed
    result, paths = m.finished_inputs(bundle, tokenizer)
    assert result["unresolved_clauses"] == 1
    assert Path(restored["backup"]) / "manifest.json" in paths


@pytest.mark.parametrize("bad", ["live_worker", "live_finalizer", "incomplete", "failed_guard", "inputs_only", "changed_result", "changed_snapshot", "changed_manifest"])
def test_bad_or_unrestored_terminals_cannot_reserve_new_editorial_roles(completed, bad):
    bundle, tokenizer, _ = completed
    if bad in {"live_worker", "incomplete", "changed_result"}:
        path = bundle.job.root / ("coverage-result.json" if bad == "changed_result" else "progress.json")
    elif bad == "live_finalizer":
        path = bundle.folder / "cpu-finalization/progress.json"
    elif bad == "failed_guard":
        path = bundle.folder / "guard-progress.json"
    elif bad == "inputs_only":
        path = bundle.folder / "independent-result-restore.json"
    elif bad == "changed_snapshot":
        path = m.ready.snapshot_path(bundle.root, bundle.contract["parent_writer"])
    else:
        path = Path(m.whole.read(bundle.folder / "independent-result-restore.json")["backup"]) / "manifest.json"
    obj = m.whole.read(path)
    if bad.startswith("live_"):
        obj["live"] = True
    elif bad == "incomplete":
        obj["completed_clauses"] = 1
    elif bad == "failed_guard":
        obj["returncode"] = 1
    elif bad == "inputs_only":
        obj["state"] = "ready_first_full_coverage_inputs_independently_restored"
    else:
        obj["changed"] = True
    m.original.atomic_json(path, obj)
    with pytest.raises(ValueError):
        m.finished_inputs(bundle, tokenizer)


@pytest.mark.parametrize("role", ["edit", "review"])
def test_invalid_independent_batch_is_parked_and_later_first_roles_still_run(sample, tmp_path, monkeypatch, role):
    units, context, raw = sample
    second = copy.deepcopy(context)
    second["target_clauses"] = {"C2": {"reference": "S1"}, "C3": {"reference": "S1"}}
    second["sources"], second["source_conflicts"] = {"S1": {"text": "Synthetic other full source"}}, {"S1": {}}
    units.update({"C2": {"reference": "S1", "ordinal": 0}, "C3": {"reference": "S1", "ordinal": 1}})
    data = {"contexts": [context, second], "units": units, "original_unresolved": list(units),
        "original_source_conflicts": ["S0", "S1", "unmapped_conflict"], "inherited_parent_issues": [1, 2, 3]}
    invalid_stage = "batch-0000-" + role
    authenticated = []

    def authenticate(*args):
        authenticated.append(args[4])
        return {"stage": invalid_stage, "attempt_budget": 1, "new_model_calls": 0, "files": {}}

    monkeypatch.setattr(m.failures, "rejected", authenticate)

    class Job:
        root, key, read_only = tmp_path, "b" * 32, False

        def __init__(self):
            self.calls = []

        def stage(self, name, prompt, schema, actual, validate):
            self.calls.append(name)
            if name == invalid_stage:
                if self.read_only:
                    pytest.fail("Never rerun an invalid role in reconstruction")
                raise ValueError("wiki_stage_attempt_budget_exhausted_" + name)
            if name.endswith("-edit"):
                result = copy.deepcopy(raw)
                result["additions"]["N000"]["citations"] = list(actual["sources"]) * 2
            else:
                result = {"groups": {key: {"passed": True, "reason": "Synthetic independent check"} for key in actual["candidate"]["groups"]},
                    "additions": {key: {"passed": True, "reason": "Synthetic independent check"} for key in actual["candidate"]["additions"]},
                    "source_conflicts": {key: {"passed": True, "reason": "Synthetic independent check"} for key in actual["source_conflicts"]}}
                assert actual["candidate"]["additions"]["N000"]["citations"] == list(actual["sources"])
            validate(result)
            return result

        def progress(self, *args, **kwargs):
            pass

    job = Job()
    with m.installed():
        result = m.stages(job, {}, data)
    assert result["reviewed_batches"] == result["retained_unresolved_batches"] == 1
    assert result["remaining_original_unresolved"] == 2
    assert result["remaining_source_conflicts"] == 2
    assert result["original_parent_issues_retained"] == 3
    assert not result["reader_publication_allowed"]
    if role == "edit":
        assert "batch-0000-review" not in job.calls
    assert authenticated == [role]
    job.read_only, job.calls = True, []
    with m.installed():
        assert m.stages(job, {}, data) == result
    assert invalid_stage not in job.calls and authenticated == [role, role]


def test_control_or_transport_error_is_not_a_license_to_continue(sample, tmp_path):
    units, context, _ = sample
    job = SimpleNamespace(root=tmp_path, read_only=False,
        stage=lambda *a: (_ for _ in ()).throw(ValueError("requires_gpu_temperature_pause")))
    with m.installed(), pytest.raises(ValueError, match="no_transport_pause"):
        m.stages(job, {}, {"contexts": [context], "units": units})
    assert not list(tmp_path.glob("*-unresolved.json"))


def test_worker_rejects_missing_or_unrelated_input_restore_before_any_model(tmp_path, monkeypatch):
    folder = tmp_path / "job"
    folder.mkdir()
    m.retained(folder / "independent-input-restore.json", {"state": "inputs_only_placeholder"})
    monkeypatch.setattr(m, "load", lambda *a: (tmp_path, folder, {"inputs_sha256": "expected"}, {}))
    monkeypatch.setattr(m.guard, "checkpoint", lambda root: None)
    monkeypatch.setattr(m.original, "Job", lambda *a: pytest.fail("Input restore must be checked before creating a model job"))
    with pytest.raises(ValueError, match="actual_independent_complete_input_restore"):
        m.worker(tmp_path, "b" * 32)
    assert not (folder / "started.json").exists()


def test_portable_editorial_plan_retains_entire_negatives_and_does_not_reask_accepted_targets(tmp_path, monkeypatch):
    plan = {"units": {"C0": {"reference": "S0"}, "C1": {"reference": "S1"}}}
    result = {"coverage": {"C0": {"resolved": False, "assessments": [{"verification_stage": "original-check", "group": "G0"}]},
        "C1": {"resolved": True, "assessments": []}}, "classification_conflicts": []}
    manuscript = {"proof": {"original": "retained"}, "issues": [{"kind": "retained_negative"}]}
    audit = {"passed": False, "reason": "Synthetic complete original negative"}
    m.retained(tmp_path / "original-check.json", {"data": {"groups": {"G0": audit}}})
    captured = []

    def batches(actual, actual_plan, actual_result, negatives, tokenizer):
        assert m.original.EDIT.endswith(m.NOTICE)
        assert (actual, actual_plan, actual_result) == (manuscript, plan, result)
        captured.append(negatives)
        return [{"target_clauses": {"C0": {"reference": "S0"}}}]

    monkeypatch.setattr(m.original, "make_batches", batches)
    monkeypatch.setattr(m.Tokenizer, "from_file", lambda path: object())
    actual = m.editorial_inputs(manuscript, plan, result, tmp_path, tmp_path / "tokenizer.json")
    assert list(actual["units"]) == actual["original_unresolved"] == ["C0"]
    assert actual["inherited_parent_issues"] == manuscript["issues"]
    assert captured == [{"original-check:G0": {**audit, "clauses": ["C0"]}}]
