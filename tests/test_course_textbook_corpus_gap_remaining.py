import copy
from types import SimpleNamespace

import pytest

from wechat_decrypt_tool import course_textbook_corpus_gap_remaining as m


@pytest.fixture
def sample(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "FAILED", 1)
    units = {f"C{n}": {"reference": "S0", "ordinal": n} for n in range(4)}
    contexts = [{"target_clauses": {f"C{n}": {"reference": "S0"}}, "context_sources": {},
        "article": {"A0": {}}, "sources": {"S0": {"text": "Synthetic complete evidence"}},
        "source_conflicts": {"S0": {}}} for n in range(4)]
    data = {"contexts": contexts, "units": units, "original_unresolved": list(units),
        "original_source_conflicts": ["S0", "Snever"], "inherited_parent_issues": [{"retained": True}]}
    raw = {"addition_count": 0, "additions": {}, "source_groups": {"R0000": {"prefix_groups": [],
        "last_group": {"kind": "covered", "article_items": ["A0"], "reason": "Synthetic candidate"}}}}
    prefix = {"resolved_target_clauses": ["C0"], "cleared_source_conflicts": ["S0"], "negative_checks": 0, "new_explanations": 0}
    proof = {"prefix_outcomes": {"0": prefix}, "prefix_summary": {"remaining_unmapped_additions": 3},
        "remaining_original_batches": [2, 3], "initial_failed_edit": {"stage": "batch-0001-edit", "failed_role_accepted": False}}
    calls = []
    job = SimpleNamespace(root=tmp_path / "old", continuation=tmp_path, original_recipe={}, read_only=False,
        completed_batches=1, key="original", progress=lambda *args, **kwargs: None)

    def stage(name, prompt, schema, context, validate):
        calls.append(name)
        if name.endswith("-edit"):
            value = copy.deepcopy(raw)
        else:
            check = {"passed": True, "reason": "Synthetic independent review"}
            value = {"groups": {"G0000": check}, "additions": {}, "source_conflicts": {"S0": check}}
        validate(value)
        return value
    job.stage = stage
    return job, data, proof, calls, stage


def test_only_later_original_roles_run_and_bad_first_batch_stays_unresolved(sample):
    job, data, proof, calls, _ = sample
    result = m.stages(job, data, proof)
    assert calls == ["batch-0002-edit", "batch-0002-review", "batch-0003-edit", "batch-0003-review"]
    assert result["reviewed_batches"] == 3 and result["retained_unresolved_batches"] == 1
    assert result["accounted_original_batches"] == 4 and result["parked_target_clauses"] == 1
    assert result["remaining_original_unresolved"] == 1
    assert result["remaining_source_conflicts"] == 2
    assert result["remaining_unmapped_additions"] == 3 and result["original_parent_issues_retained"] == 1
    assert result["original_budgets_reset"] is result["reader_publication_allowed"] is False


@pytest.mark.parametrize("role", ["edit", "review"])
def test_authenticated_invalid_role_is_parked_without_retry_and_unrelated_batch_continues(sample, monkeypatch, role):
    job, data, proof, calls, normal = sample

    def stage(name, *args):
        if name == "batch-0002-" + role:
            calls.append(name)
            raise ValueError("wiki_stage_attempt_budget_exhausted_" + name)
        return normal(name, *args)
    job.stage = stage
    monkeypatch.setattr(m.previous, "failed_candidate", lambda *args: (_ for _ in ()).throw(ValueError("requires_original_invalid_prose")))
    authenticated = []

    def reject(old, recipe, actual_data, number, actual_role, context, schema):
        authenticated.append((number, actual_role))
        return {"stage": f"batch-{number:04d}-{actual_role}", "failed_role_accepted": False}
    monkeypatch.setattr(m, "rejected", reject)
    result = m.stages(job, data, proof)
    assert authenticated == [(2, role)]
    assert calls.count("batch-0002-" + role) == 1
    assert ("batch-0002-review" in calls) == (role == "review")
    assert calls[-2:] == ["batch-0003-edit", "batch-0003-review"]
    assert result["reviewed_batches"] == 2 and result["retained_unresolved_batches"] == 2
    assert result["remaining_original_unresolved"] == 2
    before = {p: p.read_bytes() for p in job.continuation.glob("*.json")}
    job.read_only = True
    calls.clear()

    def cached(name, *args):
        assert name != "batch-0002-" + role
        return normal(name, *args)
    job.stage = cached
    replay = m.stages(job, data, proof)
    assert replay == result
    assert before == {p: p.read_bytes() for p in job.continuation.glob("*.json")}


@pytest.mark.parametrize("error", [InterruptedError("textbook_paused_progress_preserved"),
    RuntimeError("transport failed"), ValueError("requires_original_input_changed")])
def test_pause_transport_and_input_changes_do_not_auto_continue_or_park_as_semantic_failures(sample, error):
    job, data, proof, calls, _ = sample

    def stage(name, *args):
        calls.append(name)
        raise error
    job.stage = stage
    with pytest.raises((InterruptedError, RuntimeError, ValueError)):
        m.stages(job, data, proof)
    assert calls == ["batch-0002-edit"]
    assert not (job.continuation / "batch-0002-unresolved.json").exists()


def test_rejected_role_cannot_be_parked_if_actual_request_authentication_fails(sample, monkeypatch):
    job, data, proof, calls, _ = sample

    def stage(name, *args):
        calls.append(name)
        raise ValueError("wiki_stage_attempt_budget_exhausted_" + name)
    job.stage = stage
    monkeypatch.setattr(m.previous, "failed_candidate", lambda *args: (_ for _ in ()).throw(ValueError("requires_bad_request")))
    monkeypatch.setattr(m, "rejected", lambda *args: (_ for _ in ()).throw(ValueError("requires_same_actual_failed_request")))
    with pytest.raises(ValueError, match="same_actual_failed_request"):
        m.stages(job, data, proof)
    assert calls == ["batch-0002-edit"]


def test_known_lossless_metadata_still_uses_original_unused_review_and_keeps_unmapped_defects(sample, monkeypatch):
    job, data, proof, calls, normal = sample
    candidate = m.original.compile_candidate({"addition_count": 0, "additions": {}, "source_groups": {
        "R0000": {"prefix_groups": [], "last_group": {"kind": "covered", "article_items": ["A0"], "reason": "Synthetic"}}}},
        data["contexts"][2], data["units"])
    conversion = {"unmapped_addition_ids": ["N000"], "semantic_acceptance": False}
    monkeypatch.setattr(m.previous, "failed_candidate", lambda *args: (candidate, {"conversion": conversion}, []))

    def stage(name, *args):
        if name == "batch-0002-edit":
            calls.append(name)
            raise ValueError("wiki_stage_attempt_budget_exhausted_" + name)
        if name == "batch-0002-review":
            assert args[2]["untrusted_metadata_normalization"] == conversion
        return normal(name, *args)
    job.stage = stage
    result = m.stages(job, data, proof)
    assert calls == ["batch-0002-edit", "batch-0002-review", "batch-0003-edit", "batch-0003-review"]
    assert result["remaining_unmapped_additions"] == 4
    assert result["semantic_acceptance"] is False
