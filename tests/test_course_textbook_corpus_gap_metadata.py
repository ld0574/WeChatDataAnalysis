import copy
import json
from types import SimpleNamespace

import pytest

from wechat_decrypt_tool import course_textbook_corpus_gap_metadata as m


@pytest.fixture
def sample(monkeypatch):
    monkeypatch.setattr(m.original, "ADDITIONS", 1)
    units = {"C0": {"reference": "S0", "ordinal": 0}}
    context = {"target_clauses": {"C0": {"reference": "S0"}},
        "article": {"A0": {}}, "sources": {"S0": {"text": "Synthetic complete evidence"}},
        "source_conflicts": {"S0": {}}}
    raw = {"addition_count": 1, "additions": {"N000": {"text": "Synthetic explanation. " * 8,
        "heading": "Synthetic concept", "anchor": "A0", "citations": ["S0", "S0"]}},
        "source_groups": {"R0000": {"prefix_groups": [], "last_group": {
            "kind": "covered", "article_items": ["N000"], "reason": "Synthetic candidate"}}}}
    return units, context, raw


def audit(candidate, context, *, passed=True):
    verdict = {"passed": passed, "reason": "Synthetic independent judgment"}
    return {"groups": {f"G{i:04d}": dict(verdict) for i in range(len(candidate["groups"]))},
        "additions": {key: dict(verdict) for key in candidate["additions"]},
        "source_conflicts": {key: dict(verdict) for key in context["source_conflicts"]}}


def test_identical_citations_are_removed_without_source_selection_prose_or_judgment_changes(sample):
    units, context, raw = sample
    saved = copy.deepcopy(raw)
    result, proof = m.normalize(raw, context, units)
    assert raw == saved
    expected = copy.deepcopy(raw["additions"])
    expected["N000"]["citations"] = ["S0"]
    assert result["additions"] == expected
    assert result["groups"][0]["clauses"] == ["C0"]
    assert proof["prose_and_source_sets_changed"] is proof["coverage_judgments_and_members_changed"] is False
    assert len(proof["changes"]) == 1 and proof["semantic_acceptance"] is False


@pytest.mark.parametrize("other", ["empty_text", "source", "anchor", "count", "missing_run", "group_duplicate"])
def test_any_additional_error_prevents_lossless_recovery(sample, other):
    units, context, raw = sample
    if other == "empty_text":
        raw["additions"]["N000"]["text"] = " " * 90
    elif other == "source":
        raw["additions"]["N000"]["citations"] += ["unknown"]
    elif other == "anchor":
        raw["additions"]["N000"]["anchor"] = "invented"
    elif other == "count":
        raw["addition_count"] = 0
    elif other == "missing_run":
        raw["source_groups"].clear()
    else:
        raw["source_groups"]["R0000"]["last_group"]["article_items"] = ["N000", "N000"]
    with pytest.raises(ValueError, match="only_exact_duplicate_addition_citations"):
        m.normalize(raw, context, units)


def test_valid_edits_cannot_opt_into_a_new_execution(sample):
    units, context, raw = sample
    raw["additions"]["N000"]["citations"] = ["S0"]
    with pytest.raises(ValueError, match="actual_rejected_metadata_or_unmapped_explanations"):
        m.normalize(raw, context, units)


@pytest.mark.parametrize("duplicate", [True, False])
def test_unmapped_new_explanations_remain_explicit_defects_not_program_links(sample, duplicate):
    units, context, raw = sample
    raw["source_groups"]["R0000"]["last_group"]["article_items"] = ["A0"]
    if not duplicate:
        raw["additions"]["N000"]["citations"] = ["S0"]
    result, proof = m.normalize(raw, context, units)
    assert proof["unmapped_addition_ids"] == ["N000"]
    assert proof["candidate_original_structural_validation_passed"] is False
    assert result["groups"][0]["article_items"] == ["A0"]
    assert result["additions"]["N000"]["text"] == raw["additions"]["N000"]["text"]


@pytest.mark.parametrize("passed", [True, False])
def test_first_failed_edit_is_not_called_or_accepted_and_review_is_independent(tmp_path, sample, passed):
    units, context, raw = sample
    candidate, proof = m.normalize(raw, context, units)
    calls = []

    def stage(name, prompt, schema, actual_context, validate):
        calls.append(name)
        assert name == "batch-0000-review" and prompt == m.original.REVIEW
        assert actual_context["untrusted_metadata_normalization"] == proof
        value = audit(candidate, context, passed=passed)
        validate(value)
        return value

    job = SimpleNamespace(stage=stage, continuation=tmp_path, read_only=False, key="editor",
        completed_batches=0, progress=lambda *args: None)
    data = {"contexts": [context], "units": units, "original_unresolved": ["C0"],
        "original_source_conflicts": ["S0"], "inherited_parent_issues": [{"kind": "original_failure"}]}
    result = m.stages(job, data, candidate, {"conversion": proof})
    assert calls == ["batch-0000-review"]
    assert result["remaining_original_unresolved"] == (0 if passed else 1)
    assert result["remaining_source_conflicts"] == (0 if passed else 1)
    assert result["accepted_failed_edit_cache_created"] is result["reader_publication_allowed"] is False
    assert result["original_parent_issues_retained"] == 1


def test_later_original_roles_execute_once_without_an_extra_first_write(tmp_path, sample):
    units, context, raw = sample
    candidate, proof = m.normalize(raw, context, units)
    calls = []
    valid = copy.deepcopy(raw)
    valid["additions"]["N000"]["citations"] = ["S0"]

    def stage(name, prompt, schema, actual_context, validate):
        calls.append(name)
        value = audit(candidate, context) if name.endswith("review") else valid
        validate(value)
        return value

    job = SimpleNamespace(stage=stage, continuation=tmp_path, read_only=False, key="editor",
        completed_batches=0, progress=lambda *args: None)
    data = {"contexts": [context, context], "units": units, "original_unresolved": ["C0"],
        "original_source_conflicts": ["S0"], "inherited_parent_issues": []}
    m.stages(job, data, candidate, {"conversion": proof})
    assert calls == ["batch-0000-review", "batch-0001-edit", "batch-0001-review"]


def test_review_failure_does_not_retry_or_enter_the_next_original_batch(tmp_path, sample):
    units, context, raw = sample
    candidate, proof = m.normalize(raw, context, units)
    calls = []

    def stage(name, *args):
        calls.append(name)
        raise ValueError("requires_original_single_role_failed")

    job = SimpleNamespace(stage=stage, continuation=tmp_path, read_only=False, key="editor",
        completed_batches=0, progress=lambda *args: None)
    with pytest.raises(ValueError, match="original_single_role_failed"):
        m.stages(job, {"contexts": [context, context]}, candidate, {"conversion": proof})
    assert calls == ["batch-0000-review"]


def test_later_same_fault_uses_its_unused_review_without_regenerating_edit(tmp_path, sample, monkeypatch):
    units, context, raw = sample
    raw["source_groups"]["R0000"]["last_group"]["article_items"] = ["A0"]
    candidate, conversion = m.normalize(raw, context, units)
    calls = []
    failed = {"conversion": conversion, "actual_original_wire_authenticated": True}
    monkeypatch.setattr(m, "failed_candidate", lambda *args: (candidate, failed, []))

    def stage(name, prompt, schema, actual_context, validate):
        calls.append(name)
        if name == "batch-0001-edit":
            raise ValueError("wiki_stage_attempt_budget_exhausted_batch-0001-edit")
        value = audit(candidate, context)
        validate(value)
        return value

    job = SimpleNamespace(stage=stage, root=tmp_path / "old", original_recipe={}, continuation=tmp_path,
        read_only=False, key="editor", completed_batches=0, progress=lambda *args: None)
    data = {"contexts": [context, context], "units": units, "original_unresolved": ["C0"],
        "original_source_conflicts": ["S0"], "inherited_parent_issues": []}
    result = m.stages(job, data, candidate, {"conversion": conversion})
    assert calls == ["batch-0000-review", "batch-0001-edit", "batch-0001-review"]
    assert result["remaining_unmapped_additions"] == 2
    assert m.whole.read(tmp_path / "batch-0001-rejected-edit-proof.json") == failed


def test_failed_original_progress_remains_byte_identical(tmp_path):
    old, new = tmp_path / "old", tmp_path / "continuation"
    old.mkdir()
    new.mkdir()
    m.retained(old / "progress.json", {"state": "stopped", "original_failure": True})
    prior = (old / "progress.json").read_bytes()
    job = object.__new__(m.Job)
    job.root, job.continuation, job.read_only = old, new, False
    job.identity, job.key, job.completed_batches = {"pid": 1, "created": 2}, "editor", 0
    job.progress("generating", "batch-0000-review")
    assert (old / "progress.json").read_bytes() == prior
    assert m.whole.read(new / "progress.json")["original_failed_progress_preserved"] is True


def test_a_fake_accepted_edit_cache_is_rejected(tmp_path, sample, monkeypatch):
    units, context, raw = sample
    key = "a" * 32
    old = tmp_path / "old"
    old.mkdir()
    recipe = {"original": True}
    data = {"contexts": [context], "units": units}
    _, conversion = m.normalize(raw, context, units)
    m.retained(old / "attempts/batch-0000-edit-01.json", {"raw_response": json.dumps(raw)})
    monkeypatch.setattr(m, "bindings", lambda: {"code": "synthetic_frozen_code"})
    monkeypatch.setattr(m.original, "load", lambda *args: (tmp_path, old, recipe, data))
    proof = {"policy": m.POLICY, "job": key, "code": m.bindings(), "files": {},
        "conversion": conversion, "original_recipe_sha256": m.digest(recipe)}
    m.retained(m.folder_for(tmp_path, key) / "execution.json", proof)
    assert m.load(tmp_path, key)[5]["additions"]["N000"]["citations"] == ["S0"]
    m.retained(old / "batch-0000-edit.json", {"forged": True})
    with pytest.raises(ValueError, match="failed_edit_is_never_promoted"):
        m.load(tmp_path, key)


def test_independent_restore_is_required_before_using_original_review(tmp_path, monkeypatch):
    key = "a" * 32
    folder, old = tmp_path / "new", tmp_path / "old"
    folder.mkdir()
    old.mkdir()
    monkeypatch.setattr(m, "load", lambda *args: (tmp_path, folder, old, {}, {}, {}, {}))
    monkeypatch.setattr(m.guard, "checkpoint", lambda root: None)
    monkeypatch.setattr(m, "Job", lambda *args: pytest.fail("No role before independent restore"))
    m.retained(folder / "independent-input-restore.json", {"state": "wrong"})
    with pytest.raises(ValueError, match="actual_independent_input_restore"):
        m.worker(tmp_path, key)
    assert not (folder / "started.json").exists()
