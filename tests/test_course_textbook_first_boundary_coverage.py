import copy
import json
from types import SimpleNamespace

import pytest

from wechat_decrypt_tool import course_textbook_first_boundary_coverage as m
from wechat_decrypt_tool.course_knowledge import atomic_json
from test_course_textbook_ordered_boundary_tail import stage_fixture as original_fixture


def stage_fixture(tmp_path, monkeypatch, **kwargs):
    job, inventory, plan, items, calls = original_fixture(tmp_path, monkeypatch, **kwargs)
    job.__class__ = m.BoundaryJob
    plan["batches"] = plan["batches"][:1]
    return job, inventory, plan, items, calls


def readonly(job):
    del job.slot, job.generate_response
    root = job.root
    job.__class__ = m.ReadOnlyBoundaryJob
    job.expected_root, job._root = root.resolve(), root.resolve()


def test_first_role_uses_complete_sources_and_body_both_weights_and_replays_without_mutation(tmp_path, monkeypatch):
    job, inventory, plan, items, calls = stage_fixture(tmp_path, monkeypatch)
    prompts = m.coverage.REVIEW, m.coverage.VERIFY
    result = m.coverage.review_batch(job, inventory, plan, items, plan["batches"][0], 0)
    assert all(r["resolved"] for r in result.values())
    assert [model for model, _ in calls] == ["course-defiant:q4", "qwen3.5:9b"]
    assert calls[0][1]["sources"]["O00000"]["text"] == inventory["sources"]["O00000"]["text"]
    assert list(calls[0][1]["article"]) == list(items)
    assert calls[1][1]["candidate_derivation"]["program_unassessed_clauses"] == []
    before = {p: p.read_bytes() for p in job.root.rglob("*") if p.is_file()}
    readonly(job)
    assert m.coverage.review_batch(job, inventory, plan, items, plan["batches"][0], 0) == result
    assert before == {p: p.read_bytes() for p in job.root.rglob("*") if p.is_file()}
    assert len(calls) == 2 and len(job.authenticated_stages) == 2
    assert (m.coverage.REVIEW, m.coverage.VERIFY) == prompts


@pytest.mark.parametrize("change", ["schema", "derivation", "cache", "raw"])
def test_tampered_or_missing_cached_proof_never_triggers_new_inference(tmp_path, monkeypatch, change):
    job, inventory, plan, items, calls = stage_fixture(tmp_path, monkeypatch)
    m.coverage.review_batch(job, inventory, plan, items, plan["batches"][0], 0)
    stem = "batch-0000-body-0000-review"
    if change == "cache":
        (job.root / (stem + ".json")).unlink()
    else:
        path = job.root / (stem + m.DERIVATION if change == "derivation" else "response-1.json")
        value = m.whole.read(path)
        if change == "raw":
            value["response"]["message"]["content"] = "{}"
        else:
            value["compiled_candidate_sha256" if change == "derivation" else "schema_sha256"] = "f" * 64
        atomic_json(path, value)
    readonly(job)
    with pytest.raises(ValueError):
        m.coverage.review_batch(job, inventory, plan, items, plan["batches"][0], 0)
    assert len(calls) == 2


def test_first_use_two_attempt_budget_cannot_be_refreshed_by_calling_worker_again(tmp_path, monkeypatch):
    job, inventory, plan, items, calls = stage_fixture(tmp_path, monkeypatch, malformed=True)
    for _ in range(2):
        with pytest.raises(ValueError, match="stage_attempt_budget_exhausted"):
            m.coverage.review_batch(job, inventory, plan, items, plan["batches"][0], 0)
        assert len(calls) == 2


def test_negative_independent_review_preserves_unresolved_teaching(tmp_path, monkeypatch):
    job, inventory, plan, items, calls = stage_fixture(tmp_path, monkeypatch, negative=True)
    result = m.coverage.review_batch(job, inventory, plan, items, plan["batches"][0], 0)
    assert all(not s["resolved"] and s["teaching_detected"] for s in result.values())
    assert len(calls) == 2


def test_any_existing_parent_budget_is_rejected_before_parent_or_model_load(tmp_path, monkeypatch):
    key = "a" * 32
    path = tmp_path / "wiki/textbook-whole-authoring-jobs" / key / m.coverage.CONTRACT
    path.parent.mkdir(parents=True)
    atomic_json(path, {"synthetic_prior_recipe": True})
    monkeypatch.setattr(m.guard, "checkpoint", lambda *args: None)
    monkeypatch.setattr(m, "parent", lambda *args: pytest.fail("Existing parent budget must not be refreshed"))
    with pytest.raises(ValueError, match="parent_has_no_previous_coverage_budget"):
        m.prepare(tmp_path, key, tmp_path / "tokenizer.json")


@pytest.mark.parametrize("provider", ["index-reviewed", "original-second-course"])
@pytest.mark.parametrize("change", ["partial", "different"])
def test_parent_provider_requires_the_exact_actual_complete_manuscript(monkeypatch, provider, change):
    key = "a" * 32
    result = {"summary": {"job": "b" * 32 if change == "different" else key, "verified_units": 1,
        "total_units": 2 if change == "partial" else 1}, "private": None if change == "partial" else {}}
    monkeypatch.setattr(m.reviewed, "reconstruct", lambda *args: result)
    monkeypatch.setattr(m.second, "verify_writer", lambda *args: result)
    with pytest.raises(ValueError, match="actual_complete_same_parent_manuscript"):
        m.parent(None, key, provider, "c" * 32 if provider == "original-second-course" else None)


def wait_fixture(tmp_path, monkeypatch):
    root = tmp_path
    folder = root / "new-dispatch"
    folder.mkdir()
    protected = root / "old-dispatch"
    protected.mkdir()
    job, key = "a" * 32, "b" * 32
    directory = root / "wiki/whole-manuscript-coverage-jobs" / job
    directory.mkdir(parents=True)
    original_contract = root / "old-contract.json"
    atomic_json(original_contract, {"synthetic_original_execution": True})
    status = {"pid": 100, "created": 1.0, "state": "whole_manuscript_requires_coverage_or_classification_repair",
        "completed_batches": 2, "total_batches": 2}
    state = {"pid": 200, "created": 2.0, "state": "worker_exited", "returncode": 0}
    atomic_json(directory / "progress.json", status)
    atomic_json(protected / "guard-progress.json", state)
    descriptor = {"kind": "ordered-boundary-tail", "parent_writer": key, "job": job,
        "worker": {"pid": 100, "created": 1.0}, "guard": {"pid": 200, "created": 2.0},
        "guard_directory": "old-dispatch", "contract_path": "old-contract.json",
        "contract_sha256": m.whole.sha(original_contract.read_bytes())}
    bundle = SimpleNamespace(root=root, folder=folder, contract={"predecessor": descriptor}, tokenizer=root / "tokenizer.json")
    monkeypatch.setattr(m.guard, "checkpoint", lambda *args: None)
    calls = []

    def verify(*args):
        calls.append(args)
        return {"job": job, "verified_batches": 2, "model_calls": 0}

    monkeypatch.setattr(m.boundaries, "verify", verify)
    return bundle, directory, protected, calls


def test_waits_for_both_exact_predecessors_and_then_reconstructs_real_complete_results(tmp_path, monkeypatch):
    bundle, _, _, calls = wait_fixture(tmp_path, monkeypatch)
    live, slept = {100: True, 200: True}, []

    def sleep(seconds):
        slept.append(seconds)
        live[100 if live[100] else 200] = False

    result = m.wait(bundle, live=lambda row: live[row["pid"]], sleep=sleep)
    assert slept == [15, 15] and len(calls) == 1
    assert m.whole.read(bundle.folder / "predecessor-complete-proof.json") == result


@pytest.mark.parametrize("change", ["pid", "failed", "paused", "partial", "guard", "contract"])
def test_bad_predecessor_never_triggers_verification_or_fresh_model_work(tmp_path, monkeypatch, change):
    bundle, directory, protected, calls = wait_fixture(tmp_path, monkeypatch)
    status = m.whole.read(directory / "progress.json")
    if change == "pid":
        status["created"] = 3.0
    elif change in {"failed", "paused"}:
        status["state"] = "stopped" if change == "failed" else "paused"
    elif change == "partial":
        status["completed_batches"] = 1
    elif change == "guard":
        atomic_json(protected / "guard-progress.json", {"pid": 200, "created": 2.0, "state": "worker_exited", "returncode": 1})
    else:
        atomic_json(bundle.root / "old-contract.json", {"changed": True})
    atomic_json(directory / "progress.json", status)
    with pytest.raises(ValueError):
        m.wait(bundle, live=lambda row: False)
    assert not calls and not (bundle.folder / "predecessor-complete-proof.json").exists()


def test_worker_cannot_bypass_required_predecessor_proof(tmp_path, monkeypatch):
    folder = tmp_path / "wiki/new-dispatch"
    folder.mkdir(parents=True)
    job = SimpleNamespace(root=folder / "job")
    job.root.mkdir()
    bundle = SimpleNamespace(job=job, folder=folder, contract={"predecessor": {"synthetic": True}})
    monkeypatch.setattr(m.guard, "checkpoint", lambda *args: None)
    monkeypatch.setattr(m, "load", lambda *args, **kwargs: bundle)
    with pytest.raises(ValueError, match="predecessor_verified_before_worker"):
        m.run(tmp_path, "a" * 32, tmp_path / "tokenizer.json")
    assert not (folder / "worker-started.json").exists()


def test_complete_input_preflight_retains_all_source_article_and_order_values_without_inference(tmp_path, monkeypatch):
    job, inventory, plan, items, calls = stage_fixture(tmp_path, monkeypatch)
    captured, budget = [], job.budget

    def measured(messages, stage):
        captured.append(copy.deepcopy(messages))
        return budget(messages, stage)

    monkeypatch.setattr(job, "budget", measured)
    monkeypatch.setattr(m.coverage, "article_items", lambda *args: items)
    result = m.preflight(job, {"inventory": inventory, "private": {}}, plan)
    actual = json.loads(captured[0][1]["content"])
    assert actual["sources"]["O00000"]["text"] == inventory["sources"]["O00000"]["text"]
    assert list(actual["article"]) == list(items)
    assert all(actual["article"][key]["content"] == row["content"]
        and actual["article"][key]["kind"] == row["kind"] for key, row in items.items())
    assert actual["ordered_clause_ids"] == m.boundaries.assignments.by_source(plan["batches"][0]["clauses"], plan["units"])
    assert result["complete_proposal_requests"] == 1 and result["model_calls"] == 0 and not calls
