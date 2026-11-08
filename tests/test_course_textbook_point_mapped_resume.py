import copy
import json
from types import SimpleNamespace

import jsonschema
import pytest

from wechat_decrypt_tool import course_textbook_point_mapped_resume as m
from wechat_decrypt_tool.course_knowledge import atomic_json
from test_course_textbook_fresh_authoring import fake_job
from test_course_textbook_visible_context import fixture
from test_course_textbook_whole_unit_repair import verdict


def mapped_fixture():
    inventory, working, packet, context, candidate = fixture()
    scope = m.fresh.continuation.scope_for(["U0"], working, inventory, context, packet)
    raw = copy.deepcopy(candidate)
    raw["paragraphs"][0]["point_ids"] = ["U0"]
    del raw["coverage"]["U0"]["paragraphs"]
    return inventory, working, scope, raw


def test_schema_and_derivation_use_actual_paragraph_positions_preserving_prose_and_dispositions():
    inventory, working, scope, raw = mapped_fixture()
    raw["paragraphs"].insert(0, {"text": "Synthetic introduction", "citations": ["O00002"], "point_ids": []})
    before = copy.deepcopy(raw)
    candidate, metadata = m.point_candidate(raw, ["U0"], working, inventory, scope)
    assert candidate["coverage"]["U0"]["paragraphs"] == [1]
    assert [p["text"] for p in candidate["paragraphs"]] == [p["text"] for p in raw["paragraphs"]]
    assert candidate["coverage"]["U0"]["teaching"] is True
    assert metadata["paragraph_indices_derived_from_actual_point_ids"] == {"U0": [1]}
    assert metadata["semantic_acceptance"] is False
    assert metadata["citation_proposals"]
    assert raw == before


@pytest.mark.parametrize("change", ["unknown", "duplicate", "missing", "old_indices", "excluded_with_paragraph", "unmapped_teaching", "unknown_citation"])
def test_invalid_ids_dispositions_and_scope_still_fail(change):
    inventory, working, scope, raw = mapped_fixture()
    if change == "unknown":
        raw["paragraphs"][0]["point_ids"] = ["U999"]
    elif change == "duplicate":
        raw["paragraphs"][0]["point_ids"] = ["U0", "U0"]
    elif change == "missing":
        del raw["paragraphs"][0]["point_ids"]
    elif change == "old_indices":
        raw["coverage"]["U0"]["paragraphs"] = [99]
    elif change == "excluded_with_paragraph":
        raw["coverage"]["U0"]["teaching"] = False
    elif change == "unmapped_teaching":
        raw["paragraphs"][0]["point_ids"] = []
    else:
        raw["overview"]["citations"] = ["O00004"]
    with pytest.raises((ValueError, jsonschema.ValidationError)):
        m.point_candidate(raw, ["U0"], working, inventory, scope)


def test_all_non_teaching_branch_remains_available_without_inventing_prose():
    inventory, working, scope, _ = mapped_fixture()
    raw = {"kind": "non_teaching", "coverage": {"U0": {"teaching": False, "paragraphs": [], "reason": "Synthetic exclusion"}}}
    assert m.point_candidate(raw, ["U0"], working, inventory, scope) == (raw, {})


def test_known_teaching_points_cannot_be_dropped_even_without_pending_card():
    inventory, working, scope, raw = mapped_fixture()
    working["pending_source_cards"] = {}
    del raw["kind"]
    del raw["coverage"]["U0"]["teaching"]
    raw["paragraphs"][0]["point_ids"] = []
    with pytest.raises((ValueError, jsonschema.ValidationError)):
        m.point_candidate(raw, ["U0"], working, inventory, scope)


def test_format_hooks_restore_both_prompts_and_original_grammar_after_exception():
    before = copy.deepcopy(m.writing.composition.PROMPTS)
    with pytest.raises(RuntimeError):
        with m.mapped_format():
            assert m.fresh.section_schema is m.point_schema
            assert m.FORMAT_NOTICE in m.writing.composition.PROMPTS[m.writing.author.REPAIR_PROMPT]
            raise RuntimeError("Synthetic failure")
    assert m.fresh.section_schema is m.ORIGINAL_SCHEMA
    assert m.fresh.citation_candidate is m.ORIGINAL_CANDIDATE
    assert m.writing.composition.PROMPTS == before


def test_actual_model_receives_new_schema_and_reviewer_receives_derived_mapping_and_unverified_proposals(tmp_path, monkeypatch):
    job, inventory, working, packet, context, _ = fake_job(tmp_path, monkeypatch)
    _, _, _, raw = mapped_fixture()
    values, generated = {}, []

    def generate(self, name, prompt, schema, supplied, validate, **kwargs):
        generated.append(name)
        if name.endswith("-repair"):
            assert schema["oneOf"][0]["properties"]["paragraphs"]["items"]["properties"]["point_ids"]["items"]["enum"] == ["U0"]
            assert m.FORMAT_NOTICE in m.writing.composition.PROMPTS[prompt]
            value = raw
        else:
            assert supplied["candidate"]["coverage"]["U0"]["paragraphs"] == [0]
            assert supplied["unverified_citation_proposals"]
            assert supplied["citation_proposals_are_factual_evidence"] is False
            value = verdict(schema, passed=False)
        validate(value)
        values[name] = copy.deepcopy(value)
        atomic_json(tmp_path / (name + ".json"), {"synthetic_actual_response": value})
        return value

    monkeypatch.setattr(m.fresh.BASE_JOB, "stage", generate)
    monkeypatch.setattr(job.reader, "stage", lambda name, *args, **kwargs: copy.deepcopy(values[name]))
    with m.mapped_format():
        candidate = job.stage("section-0007-repair", m.writing.author.REPAIR_PROMPT,
            m.writing.section_schema(["U0"], working), context, lambda row: None, evidence_packet=packet)
    schema = m.writing.review_schema(candidate, ["U0"])
    audit = job.stage("section-0007-repair-review", m.writing.author.REVIEW_PROMPT, schema,
        {**context, "candidate": candidate}, lambda row: m.writing.author.validate_review(row, schema), evidence_packet=packet)
    assert not all(m.writing.author.verdicts(audit))
    assert generated == ["section-0007-repair", "section-0007-repair-review"]
    assert json.loads((tmp_path / "section-0007-repair.json").read_bytes())["synthetic_actual_response"] == raw
    assert (tmp_path / "section-0007-repair-prospective-derived.json").is_file()


def test_repair_is_mandatory_even_when_initial_factual_audit_is_positive(tmp_path, monkeypatch):
    inventory, working, packet, context, candidate = fixture()
    original = copy.deepcopy(candidate)
    original["coverage"]["U0"]["paragraphs"] = [99]
    bundle = SimpleNamespace(original={}, root=tmp_path, folder=tmp_path / "resume", contract={},
        count=7, units=[None] * 14, inventory=inventory, working=working, context=context,
        prefix="section-0007", scope={"original": packet}, defects=[{"kind": "paragraph_mapping_out_of_bounds"}],
        original_candidate=original)
    stages = []

    class SyntheticJob:
        root = tmp_path
        reader = SimpleNamespace(paths=set())

        def stage(self, name, prompt, schema, supplied, validate, **kwargs):
            stages.append(name)
            assert supplied["candidate_is_untrusted"] is True
            if name.endswith("-repair"):
                assert supplied["candidate"] == original
                assert all(m.writing.author.verdicts(supplied["review"]))
                result = candidate
            else:
                result = verdict(schema, passed=name.endswith("-review") and not name.endswith("-repair-review"))
            validate(result)
            return result

    monkeypatch.setattr(m, "ORIGINAL_FACTORY", lambda *args, **kwargs: lambda *args: SyntheticJob())
    monkeypatch.setattr(m.writing, "TextbookStore", lambda root: None)
    monkeypatch.setattr(m.visible, "packet_scope", lambda scope: __import__("contextlib").nullcontext())
    for row in [candidate["paragraphs"][0], candidate["overview"], candidate["takeaway"], candidate["quiz"]]:
        row["citations"] = ["O00001"]
    result = m.repair_target(bundle, tmp_path / "tokenizer", read_only=False)
    assert stages == ["section-0007-review", "section-0007-repair", "section-0007-repair-review"]
    assert result["final_checks_passed"] is False
    assert original["coverage"]["U0"]["paragraphs"] == [99]
    assert not (tmp_path / "section-0007-write.json").exists()


def test_derived_orchestration_preserves_negative_final_review_and_never_creates_a_model_write_cache(tmp_path):
    inventory, working, packet, context, candidate = fixture()
    scope = m.fresh.continuation.scope_for(["U0"], working, inventory, context, packet)
    bundle = SimpleNamespace(prefix="section-0007", context=context, working=working,
        scope=scope, contract={}, folder=tmp_path)
    audit = verdict(m.writing.review_schema(candidate, ["U0"]), passed=False)
    result = {"candidate": candidate, "final_review": audit}
    schema = m.writing.review_schema(candidate, ["U0"])
    value, path = m.derived_stage(bundle, result, "section-0007-review", m.writing.author.REVIEW_PROMPT,
        schema, {**context, "candidate": candidate}, lambda row: m.writing.author.validate_review(row, schema),
        {"evidence_packet": packet}, read_only=False)
    assert value == audit and not all(m.writing.author.verdicts(value))
    assert json.loads(path.read_bytes())["actual_model_stage"] == "section-0007-repair-review"
    assert not (tmp_path / "section-0007-write.json").exists()
    before = path.read_bytes()
    value, _ = m.derived_stage(bundle, result, "section-0007-review", m.writing.author.REVIEW_PROMPT,
        schema, {**context, "candidate": candidate}, lambda row: None, {"evidence_packet": packet}, read_only=True)
    assert value == audit and path.read_bytes() == before


def test_missing_derived_receipt_cannot_be_created_by_readonly_reconstruction(tmp_path):
    _, working, packet, context, candidate = fixture()
    bundle = SimpleNamespace(prefix="section-0007", context=context, working=working,
        scope={"original": packet}, contract={}, folder=tmp_path)
    result = {"candidate": candidate, "final_review": {}}
    with pytest.raises(ValueError, match="existing_artifact_without_inference"):
        m.derived_stage(bundle, result, "section-0007-write", m.writing.author.WRITE_PROMPT,
            m.writing.section_schema(["U0"], working), context, lambda row: None,
            {"evidence_packet": packet}, read_only=True)
    assert not list(tmp_path.rglob("*.json"))
