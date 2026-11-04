import copy

import jsonschema
import pytest

from wechat_decrypt_tool import course_textbook_affiliation_scope as module


def test_retrieval_keeps_literal_sources_and_does_not_assert_affiliation():
    sources = {
        "D0": {"kind": "document", "text": "迭代器与生成器 yield iterator generator"},
        "S0": {"kind": "speech", "text": "用户界面和网络请求 routing"},
        "S1": {"kind": "speech", "text": "生成器通过 yield 返回 iterator 的下一个值"},
        "S2": {"kind": "speech", "text": "另一段相邻上下文"},
        "V0": {"kind": "frame", "text": "unrelated transport protocol"},
    }
    before = copy.deepcopy(sources)
    value = module.scope(sources, "D0", candidates=1)
    assert value["retrieval"]["seed_ids"] == ["S1"]
    assert value["retrieval"]["searched_lesson_sources"] == 4
    assert not value["retrieval"]["ranking_is_affiliation_evidence"]
    assert not value["retrieval"]["unretrieved_sources_are_excluded"]
    assert set(value["lesson_sources"]) == {"S0", "S1", "S2"}
    assert all(row == sources[key] for key, row in value["lesson_sources"].items())
    assert sources == before


def test_selection_cannot_cite_courseware_or_invent_a_lesson_reference():
    context = {"lesson_sources": {"S0": {"kind": "speech", "text": "Original"}}}
    schema = module.selection_schema(context)
    jsonschema.validate({"kind": "candidate_link", "lesson_sources": ["S0"], "reason": "Specific match"}, schema)
    for ids in [["D0"], ["invented"], ["S0", "S0"], []]:
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"kind": "candidate_link", "lesson_sources": ids, "reason": "Claim"}, schema)


def test_no_overlap_leaves_item_unresolved_and_cannot_prove_absence():
    sources = {"D0": {"kind": "document", "text": "iterator"},
               "S0": {"kind": "speech", "text": "transport"}}
    context = module.scope(sources, "D0")
    assert not context["lesson_sources"]
    schema = module.selection_schema(context)
    jsonschema.validate({"kind": "unresolved", "reason": "Insufficient local candidates"}, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"kind": "candidate_link", "lesson_sources": ["S0"], "reason": "Guess"}, schema)


def test_real_reference_is_not_enough_without_full_independent_support():
    review = {"whole_item_supported": True, "specific_teaching_not_just_terminology": True,
              "context_sufficient": True, "reason": "Independently checked full context"}
    jsonschema.validate(review, module.review_schema())
    assert module.accepted(review)
    for key in ["whole_item_supported", "specific_teaching_not_just_terminology", "context_sufficient"]:
        assert not module.accepted({**review, key: False})
