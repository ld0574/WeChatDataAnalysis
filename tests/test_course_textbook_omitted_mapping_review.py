import copy
from types import SimpleNamespace

import pytest

from wechat_decrypt_tool import course_textbook_omitted_mapping_review as m
from test_course_textbook_point_mapped_resume import mapped_fixture


def omitted_fixture():
    inventory, working, scope, raw = mapped_fixture()
    raw["paragraphs"][0]["point_ids"] = []
    return SimpleNamespace(context={"assigned_points": working["points"]}, working=working,
        inventory=inventory, scope=scope), raw


def test_omission_is_explicit_untrusted_full_body_scope_not_an_invented_paragraph_or_semantic_approval():
    bundle, raw = omitted_fixture()
    raw["paragraphs"].append({"text": "Synthetic other explanation", "citations": ["O00002"], "point_ids": []})
    before = copy.deepcopy(raw)
    candidate, metadata = m.proposal(raw, bundle)
    assert candidate["coverage"]["U0"]["paragraphs"] == [0, 1]
    assert metadata["omitted_point_ids"] == ["U0"]
    assert metadata["mapping_proposals_are_factual_evidence"] is False
    assert metadata["whole_original_coverage_review_still_required"] is True
    assert [p["text"] for p in candidate["paragraphs"]] == [p["text"] for p in raw["paragraphs"]]
    assert all("O00001" in p["citations"] for p in candidate["paragraphs"])
    assert raw == before


def test_no_missing_mapping_cannot_be_used_to_prepare_another_repair():
    bundle, raw = omitted_fixture()
    raw["paragraphs"][0]["point_ids"] = ["U0"]
    with pytest.raises(ValueError, match="actual_omitted_point_mapping"):
        m.proposal(raw, bundle)


def test_explicit_non_teaching_disposition_is_never_promoted_to_teaching():
    bundle, raw = omitted_fixture()
    raw["coverage"]["U0"]["teaching"] = False
    canonical, missing = m.canonical_mapping(raw, ["U0"], bundle.working)
    assert missing == []
    assert canonical["coverage"]["U0"] == {"teaching": False, "reason": raw["coverage"]["U0"]["reason"], "paragraphs": []}
    with pytest.raises(ValueError, match="actual_omitted_point_mapping"):
        m.proposal(raw, bundle)


def test_future_validator_gives_specific_missing_ids_instead_of_repeating_generic_schema_failure():
    bundle, raw = omitted_fixture()
    with pytest.raises(ValueError, match="requires_point_mapped_missing_teaching_ids_U0"):
        m.checked_future_candidate(raw, ["U0"], bundle.working, bundle.inventory, bundle.scope)
    raw["paragraphs"][0]["point_ids"] = ["U0"]
    candidate, _ = m.checked_future_candidate(raw, ["U0"], bundle.working, bundle.inventory, bundle.scope)
    assert candidate["coverage"]["U0"]["paragraphs"] == [0]


def test_unknown_id_and_other_actual_structure_errors_remain_failures():
    bundle, raw = omitted_fixture()
    raw["paragraphs"][0]["point_ids"] = ["unknown"]
    with pytest.raises(ValueError, match="only_assigned_ids"):
        m.canonical_mapping(raw, ["U0"], bundle.working)


def test_hook_restores_every_original_function_even_on_failure():
    previous = m.fresh.section_schema, m.fresh.citation_candidate, copy.deepcopy(m.writing.composition.PROMPTS)
    with pytest.raises(RuntimeError):
        with m.future_format():
            assert m.fresh.citation_candidate is m.checked_future_candidate
            raise RuntimeError("Synthetic failure")
    assert (m.fresh.section_schema, m.fresh.citation_candidate, m.writing.composition.PROMPTS) == previous


def test_whole_original_coverage_obligation_cannot_be_disappeared_by_positive_final_factual_review(monkeypatch):
    inventory = {"synthetic": True}
    bundle = SimpleNamespace(contract={"inventory": m.digest(inventory)}, prefix="section-0007",
        metadata={"omitted_point_ids": ["P0"]}, candidate={"synthetic": "prose"})
    def original(*args):
        return [{"kind": "prior_negative_review"}]
    monkeypatch.setattr(m.writing, "final_issues", original)
    with m.retained_obligation(bundle):
        issues = m.writing.final_issues(inventory, {}, [])
        assert issues[0]["kind"] == "prior_negative_review"
        assert issues[1]["kind"] == "omitted_point_mapping_proposal_requires_whole_original_coverage"
        assert issues[1]["point_ids"] == ["P0"]
    assert m.writing.final_issues is original
