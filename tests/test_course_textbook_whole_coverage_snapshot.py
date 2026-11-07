import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

from wechat_decrypt_tool import course_textbook_whole_coverage_snapshot as m
from wechat_decrypt_tool.course_knowledge import atomic_json

from test_course_textbook_whole_coverage import real_stage_job


def read_only_job(tmp_path, monkeypatch):
    job, inventory, plan, items, calls = real_stage_job(tmp_path, monkeypatch)
    first = m.coverage.review_batch(job, inventory, plan, items, plan["batches"][0], 0)
    folder = job.root
    del job.slot, job.generate_response
    job.__class__ = m.ReadOnlyCoverageJob
    job.expected_root, job._root, job.authenticated_stages = folder.resolve(), folder.resolve(), set()
    return job, inventory, plan, items, calls, first


def test_actual_request_and_gpu_receipts_replay_without_inference_or_execution_writes(tmp_path, monkeypatch):
    job, inventory, plan, items, calls, first = read_only_job(tmp_path, monkeypatch)
    before = {path: path.read_bytes() for path in job.root.rglob("*") if path.is_file()}
    assert m.coverage.review_batch(job, inventory, plan, items, plan["batches"][0], 0) == first
    assert len(calls) == 2 and len(job.authenticated_stages) == 2
    assert {path: path.read_bytes() for path in job.root.rglob("*") if path.is_file()} == before


def test_missing_accepted_cache_preserves_attempts_and_never_enters_inference(tmp_path, monkeypatch):
    job, inventory, plan, items, calls, _ = read_only_job(tmp_path, monkeypatch)
    cache = job.root / "batch-0000-body-0000-review.json"
    cache.rename(job.root / "retained-cache-for-test.json")
    before = {path: path.read_bytes() for path in job.root.rglob("*") if path.is_file()}
    with pytest.raises(ValueError, match="original_accepted_stage_cache_required"):
        m.coverage.review_batch(job, inventory, plan, items, plan["batches"][0], 0)
    assert len(calls) == 2
    assert {path: path.read_bytes() for path in job.root.rglob("*") if path.is_file()} == before


def test_changed_job_identity_cannot_create_another_manifest(tmp_path):
    job = m.ReadOnlyCoverageJob.__new__(m.ReadOnlyCoverageJob)
    job.expected_root = (tmp_path / "expected").resolve()
    wrong = tmp_path / "changed"
    with pytest.raises(ValueError, match="existing_exact_execution_manifest"):
        job.root = wrong
    assert not wrong.exists()


def completed_fixture(tmp_path, monkeypatch):
    parent, key = "a" * 32, "b" * 32
    folder = tmp_path / "wiki/whole-manuscript-coverage-jobs" / key
    folder.mkdir(parents=True)
    plan = {"sources": 1, "clauses": 1, "batches": [{"clauses": ["C0000000"]}]}
    manuscript = {"summary": {"verified_units": 1, "total_units": 1}, "private": {}, "working": {},
        "inventory": {}, "proof": {"files": {}}, "issues": [{"kind": "retained_negative_judgment"}]}
    rows = {"C0000000": {"kind": "covered", "resolved": True}}
    result = {"job": key, "parent_writer": parent, "state": "automatic_whole_source_coverage_complete",
        "covered_clauses": 1, "unresolved_clauses": 0, "coverage": rows,
        "inherited_manuscript_issues": [{"reason": "Synthetic private diagnostic, never public"}],
        "semantic_accuracy_verified": False, "reader_publication_allowed": False, "source_deletion_allowed": False}
    status = {"job": key, "pid": 100, "created": 1.0, "stage": "complete", "state": result["state"],
        "completed_batches": 1, "completed_clauses": 1, "unresolved_clauses": 0}
    helpers = {"helper": "c" * 64}
    contract = {"policy": m.coverage.POLICY, "job": key, "parent_writer": parent,
        "snapshot_sha256": m.coverage.digest(manuscript["proof"]), "plan_sha256": m.coverage.digest(plan),
        "module_sha256": m.coverage.writing.whole.sha(Path(m.coverage.__file__).read_bytes()),
        "helpers_sha256": helpers, "semantic_repairs": 0, "old_budgets_reset": False, "reader_publication_allowed": False}
    contract_path = tmp_path / "wiki/textbook-whole-authoring-jobs" / parent / m.coverage.CONTRACT
    contract_path.parent.mkdir(parents=True)
    atomic_json(contract_path, contract)
    for name, value in {"manifest.json": {}, "review-plan.json": plan, "parent-snapshot.json": manuscript["proof"],
            "progress.json": status, "batch-0000-result.json": rows, "coverage-result.json": result}.items():
        atomic_json(folder / name, value)
    monkeypatch.setattr(m.coverage.writing, "identity_live", lambda row: False)
    monkeypatch.setattr(m.coverage.writing, "TextbookStore", lambda root: SimpleNamespace(root=root))
    monkeypatch.setattr(m.coverage.continuation, "reconstruct", lambda *args, **kwargs: copy.deepcopy(manuscript))
    monkeypatch.setattr(m.coverage.snapshot, "reconstruct", lambda *args, **kwargs: copy.deepcopy(manuscript))
    monkeypatch.setattr(m.coverage, "make_plan", lambda *args: copy.deepcopy(plan))
    monkeypatch.setattr(m.coverage, "article_items", lambda *args: {})
    monkeypatch.setattr(m.coverage, "review_batch", lambda *args: copy.deepcopy(rows))
    monkeypatch.setattr(m.coverage, "compile_result", lambda *args: copy.deepcopy(result))
    monkeypatch.setattr(m.coverage, "bindings", lambda: helpers)
    monkeypatch.setattr(m, "Tokenizer", SimpleNamespace(from_file=lambda path: object()))
    monkeypatch.setattr(m, "ReadOnlyCoverageJob", lambda *args: SimpleNamespace(key=key, authenticated_stages=set()))
    return parent, folder, status


def test_reconstructed_proof_has_no_private_coverage_or_diagnostic_values(tmp_path, monkeypatch):
    parent, folder, _ = completed_fixture(tmp_path, monkeypatch)
    before = {path: path.read_bytes() for path in folder.rglob("*") if path.is_file()}
    result = m.reconstruct(tmp_path, parent, tmp_path / "tokenizer.json", "continuation")
    assert result["summary"]["model_calls"] == 0
    assert result["summary"]["covered_clauses"] == 1
    assert result["summary"]["inherited_manuscript_issues"] == 1
    assert "coverage" not in result["proof"]
    assert "Synthetic private diagnostic" not in str(result["proof"])
    assert {path: path.read_bytes() for path in folder.rglob("*") if path.is_file()} == before
    saved = m.save(tmp_path, result)
    assert m.save(tmp_path, result) == saved


@pytest.mark.parametrize("target", ["batch-0000-result.json", "coverage-result.json", "review-plan.json"])
def test_modified_results_or_plan_cannot_be_authenticated(tmp_path, monkeypatch, target):
    parent, folder, _ = completed_fixture(tmp_path, monkeypatch)
    atomic_json(folder / target, {"changed": True})
    with pytest.raises(ValueError, match="requires_coverage_snapshot_"):
        m.reconstruct(tmp_path, parent, tmp_path / "tokenizer.json", "original")


def test_live_worker_is_rejected_before_reconstructing_its_parent(tmp_path, monkeypatch):
    parent, _, _ = completed_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(m.coverage.writing, "identity_live", lambda row: True)
    monkeypatch.setattr(m.coverage.continuation, "reconstruct", lambda *args, **kwargs: pytest.fail("Live execution must not be inspected"))
    with pytest.raises(ValueError, match="actually_finished_coverage_worker"):
        m.reconstruct(tmp_path, parent, tmp_path / "tokenizer.json", "continuation")


def test_terminal_counts_must_match_the_reconstructed_actual_results(tmp_path, monkeypatch):
    parent, folder, status = completed_fixture(tmp_path, monkeypatch)
    atomic_json(folder / "progress.json", {**status, "completed_clauses": 999})
    with pytest.raises(ValueError, match="actual_terminal_counts"):
        m.reconstruct(tmp_path, parent, tmp_path / "tokenizer.json", "original")


def test_execution_status_cannot_change_during_read_only_reconstruction(tmp_path, monkeypatch):
    parent, folder, status = completed_fixture(tmp_path, monkeypatch)
    previous = m.coverage.review_batch

    def changing(*args):
        atomic_json(folder / "progress.json", {**status, "updated": 2})
        return previous(*args)

    monkeypatch.setattr(m.coverage, "review_batch", changing)
    with pytest.raises(ValueError, match="stable_finished_execution"):
        m.reconstruct(tmp_path, parent, tmp_path / "tokenizer.json", "original")
