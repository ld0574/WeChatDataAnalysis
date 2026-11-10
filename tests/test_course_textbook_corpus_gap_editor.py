import copy
from types import SimpleNamespace

import jsonschema
import pytest

from wechat_decrypt_tool import course_textbook_corpus_gap_editor as m


@pytest.fixture
def sample(monkeypatch):
    monkeypatch.setattr(m, "ADDITIONS", 1)
    units = {"C0": {"reference": "S0", "ordinal": 0},
             "C1": {"reference": "S0", "ordinal": 1}}
    context = {"target_clauses": {key: {"reference": "S0"} for key in units},
        "article": {"A0": {"kind": "paragraph", "content": "Synthetic old explanation"}},
        "sources": {"S0": {"text": "Synthetic complete evidence"}},
        "source_conflicts": {"S0": {"old_claims_not_facts": {}}}}
    raw = {"addition_count": 1, "additions": {"N000": {"text": "Synthetic explanation. " * 8,
        "heading": "Synthetic concept", "anchor": "A0", "citations": ["S0"]}},
        "source_groups": {"R0000": {"prefix_groups": [], "last_group": {
            "kind": "covered", "article_items": ["N000"], "reason": "Synthetic candidate only"}}}}
    return units, context, raw


def positive(candidate, context):
    return {"groups": {f"G{i:04d}": {"passed": True, "reason": "Synthetic independent check"}
        for i in range(len(candidate["groups"]))},
        "additions": {key: {"passed": True, "reason": "Synthetic independent check"} for key in candidate["additions"]},
        "source_conflicts": {key: {"passed": True, "reason": "Synthetic independent check"} for key in context["source_conflicts"]}}


def test_targets_keep_unresolved_outside_old_chapters_and_only_actual_conflicts():
    plan = {"units": {"C0": {"reference": "mapped"}, "C1": {"reference": "unmapped"},
        "C2": {"reference": "conflicting"}, "C3": {"reference": "accepted"}}}
    result = {"coverage": {key: {"resolved": key not in {"C0", "C1"}} for key in plan["units"]},
        "classification_conflicts": ["conflicting"]}
    assert m.targets(result, plan) == ["C0", "C1", "C2"]


def test_incomplete_original_coverage_is_not_a_complete_source_inventory():
    with pytest.raises(ValueError, match="all_original_clause_states"):
        m.targets({"coverage": {}, "classification_conflicts": []}, {"units": {"C0": {}}})


def test_boundary_compilation_preserves_actual_members_and_model_prose(sample):
    units, context, raw = sample
    saved = copy.deepcopy(raw)
    result = m.compile_candidate(raw, context, units)
    assert result["groups"][0]["clauses"] == ["C0", "C1"]
    assert result["additions"] == raw["additions"] and raw == saved


def test_optional_object_size_keywords_are_not_needed_for_required_coverage(sample):
    _, context, _ = sample
    schema = m.wire_schema(context, sample[0])

    def check(node):
        if isinstance(node, dict):
            assert "minProperties" not in node and "maxProperties" not in node
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for value in node:
                check(value)

    check(schema)


@pytest.mark.parametrize("bad", ["missing_run", "wrong_new_item", "unknown_source", "orphan", "empty_text", "wrong_count"])
def test_invalid_wire_candidate_cannot_consume_an_independent_review(sample, bad):
    units, context, raw = sample
    if bad == "missing_run":
        raw["source_groups"].clear()
    elif bad == "wrong_new_item":
        raw["source_groups"]["R0000"]["last_group"]["article_items"] = ["N001"]
    elif bad == "unknown_source":
        raw["additions"]["N000"]["citations"] = ["unknown"]
    elif bad == "orphan":
        raw["source_groups"]["R0000"]["last_group"]["article_items"] = ["A0"]
    elif bad == "empty_text":
        raw["additions"]["N000"]["text"] = " " * 100
    else:
        raw["addition_count"] = 0
    with pytest.raises((ValueError, jsonschema.ValidationError)):
        m.compile_candidate(raw, context, units)


def test_resolved_holes_in_original_evidence_must_not_be_reasked_or_merged(sample):
    units, context, raw = sample
    units["C1"]["ordinal"] = 2
    mapped, runs = m.runs(list(units), units)
    assert runs == {"R0000": ["C0"], "R0001": ["C1"]}
    assert mapped["C1"]["reference"] != mapped["C0"]["reference"]
    with pytest.raises(jsonschema.ValidationError):
        m.compile_candidate(raw, context, units)
    raw["source_groups"]["R0001"] = copy.deepcopy(raw["source_groups"]["R0000"])
    result = m.compile_candidate(raw, context, units)
    assert [g["clauses"] for g in result["groups"]] == [["C0"], ["C1"]]


def test_boundary_cannot_leave_an_empty_last_group(sample):
    units, context, raw = sample
    raw["source_groups"]["R0000"]["prefix_groups"] = [{
        **copy.deepcopy(raw["source_groups"]["R0000"]["last_group"]), "through_position": 2}]
    with pytest.raises(jsonschema.ValidationError):
        m.compile_candidate(raw, context, units)


def test_zero_additions_can_retain_existing_explanations_without_fabricating_text(sample):
    units, context, raw = sample
    raw["addition_count"], raw["additions"] = 0, {}
    raw["source_groups"]["R0000"]["last_group"]["article_items"] = ["A0"]
    result = m.compile_candidate(raw, context, units)
    assert result["additions"] == {}


@pytest.mark.parametrize("kind", ["missing", "non_teaching"])
def test_a_missing_or_rejected_exclusion_is_never_silently_resolved(sample, kind):
    units, context, raw = sample
    raw["addition_count"], raw["additions"] = 0, {}
    raw["source_groups"]["R0000"]["last_group"].update(kind=kind, article_items=[])
    result = m.compile_candidate(raw, context, units)
    audit = positive(result, context)
    audit["groups"]["G0000"]["passed"] = kind == "missing"
    outcome = m.assess(result, audit, context)
    assert outcome["resolved_target_clauses"] == [] and outcome["cleared_source_conflicts"] == []


@pytest.mark.parametrize("negative", [None, "paragraph", "group", "conflict"])
def test_actual_independent_verdicts_determine_separate_remaining_obligations(sample, negative):
    units, context, raw = sample
    result = m.compile_candidate(raw, context, units)
    audit = positive(result, context)
    if negative:
        table, key = {"paragraph": ("additions", "N000"), "group": ("groups", "G0000"),
            "conflict": ("source_conflicts", "S0")}[negative]
        audit[table][key]["passed"] = False
    outcome = m.assess(result, audit, context)
    assert outcome["resolved_target_clauses"] == ([] if negative in {"paragraph", "group"} else ["C0", "C1"])
    assert outcome["cleared_source_conflicts"] == ([] if negative else ["S0"])


def test_batches_allocate_all_target_sources_once_even_when_no_old_unit_exists(monkeypatch):
    units = {f"C{i}": {"reference": f"S{i}", "ordinal": 0} for i in range(5)}
    result = {"coverage": {key: {"resolved": key == "C4"} for key in units}, "classification_conflicts": []}
    monkeypatch.setattr(m, "SOURCE_LIMIT", 2)
    monkeypatch.setattr(m, "context_for", lambda manuscript, plan, result, ids, negative: {"target_clauses": dict.fromkeys(ids)})
    monkeypatch.setattr(m.coverage, "tokens", lambda *args: 1)
    contexts = m.make_batches({}, {"units": units}, result, {}, None)
    assert [list(c["target_clauses"]) for c in contexts] == [["C0", "C1"], ["C2", "C3"]]


def test_a_second_prepare_cannot_refresh_the_original_parent_budget(tmp_path, monkeypatch):
    parent = "a" * 32
    m.retained(m.registration(tmp_path, parent), {"original_reservation": True})
    monkeypatch.setattr(m.guard, "checkpoint", lambda root: None)
    monkeypatch.setattr(m.first, "load", lambda *a: pytest.fail("Do not reload to create a new budget"))
    with pytest.raises(ValueError, match="one_final_gap_edit_registration"):
        m.prepare(tmp_path, parent, tmp_path / "tokenizer.json")


def test_actual_input_restore_is_required_before_any_role_or_started_marker(tmp_path, monkeypatch):
    key = "a" * 32
    folder = tmp_path / "execution"
    folder.mkdir()
    monkeypatch.setattr(m, "load", lambda *a: (tmp_path, folder, {}, {}))
    monkeypatch.setattr(m.guard, "checkpoint", lambda root: None)
    monkeypatch.setattr(m, "Job", lambda *a: pytest.fail("No model job before restore"))
    m.retained(folder / "independent-input-restore.json", {"state": "wrong"})
    with pytest.raises(ValueError, match="actual_independent_input_restore"):
        m.worker(tmp_path, key)
    assert not (folder / "started.json").exists()


@pytest.mark.parametrize("operation", ["generate_response", "slot"])
def test_readonly_replay_cannot_infer_or_acquire_gpu(operation):
    job = object.__new__(m.Job)
    job.read_only = True
    with pytest.raises(ValueError, match="forbidden_in_reconstruction"):
        getattr(job, operation)()


def test_original_evidence_and_recipe_bytes_are_checked_without_reinference(tmp_path, monkeypatch):
    parent = "b" * 32
    original = tmp_path / "original.json"
    original.write_text("original", encoding="utf-8")
    monkeypatch.setattr(m, "bindings", lambda: {"code": "synthetic_frozen_code"})
    data = {"complete_inputs": True}
    recipe = {"policy": m.POLICY, "parent": parent, "code": m.bindings(),
        "inputs_sha256": m.digest(data), "files": {"original.json": m.whole.sha(original.read_bytes())}}
    key = m.digest(recipe)[:32]
    folder = m.folder_for(tmp_path, key)
    m.retained(folder / "recipe.json", recipe)
    m.retained(folder / "inputs.json", data)
    m.retained(m.registration(tmp_path, parent), {"parent": parent, "job": key, "recipe_sha256": m.digest(recipe)})
    assert m.load(tmp_path, key)[3] == data
    original.write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="all_original_evidence_failures_and_requests_unchanged"):
        m.load(tmp_path, key)


def test_original_failures_and_new_negative_checks_are_retained_in_final_result(monkeypatch):
    context = {"target_clauses": {"C0": {}}, "source_conflicts": {"S0": {}}}
    data = {"contexts": [context], "units": {"C0": {}}, "original_unresolved": ["C0"],
        "original_source_conflicts": ["S0"], "inherited_parent_issues": [{"kind": "original_failure"}]}
    candidate = {"additions": {}, "groups": []}
    monkeypatch.setattr(m, "wire_schema", lambda *a: {})
    monkeypatch.setattr(m, "compile_candidate", lambda *a: candidate)
    monkeypatch.setattr(m, "review_schema", lambda *a: {})
    monkeypatch.setattr(m, "assess", lambda *a: {"resolved_target_clauses": [], "cleared_source_conflicts": [],
        "negative_checks": 2, "new_explanations": 0})
    monkeypatch.setattr(m, "retained", lambda *a, **k: None)
    job = SimpleNamespace(stage=lambda *a: {}, key="original_editor", root=None, read_only=True,
        completed=0, completed_batches=0, progress=lambda *a: None)
    # The result path is never opened by this fake read-only job.
    job.root = type("PathOnly", (), {"__truediv__": lambda self, name: name})()
    result = m.stages(job, data)
    assert result["remaining_original_unresolved"] == result["remaining_source_conflicts"] == 1
    assert result["original_parent_issues_retained"] == 1 and result["negative_checks"] == 2
    assert result["reader_publication_allowed"] is result["source_deletion_allowed"] is False


def test_model_role_counts_do_not_misreport_completed_editorial_batches(tmp_path):
    job = object.__new__(m.Job)
    job.read_only, job.root, job.key, job.identity = False, tmp_path, "editor", {"pid": 1, "created": 2}
    job.completed, job.completed_batches = 9, 2
    job.progress("generating", "batch-0002-review")
    assert m.whole.read(tmp_path / "progress.json")["completed_batches"] == 2
