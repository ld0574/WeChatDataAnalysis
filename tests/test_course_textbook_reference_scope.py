import pytest
import jsonschema

from wechat_decrypt_tool import course_textbook_reference_scope as module


def candidate():
    return {**{key: True for key in module.CHECKS}, "coverage_contradiction_found": False,
        "lesson_source_ids": ["L1"], "article_item_ids": ["p001"], "reason": "Specific reference relation"}


def test_supported_supplement_never_becomes_a_lecture_or_whole_notebook_claim():
    result = module.assess(candidate(), {"L1"}, {"p001"})
    assert result["reference_relation"] == "supported_supplement"
    assert all(value is False for key, value in result.items() if key != "reference_relation")


@pytest.mark.parametrize("key", module.CHECKS)
def test_any_negative_semantic_finding_preserves_unresolved(key):
    row = candidate()
    row[key] = False
    assert module.assess(row, {"L1"}, {"p001"})["reference_relation"] == "unresolved"


@pytest.mark.parametrize("key", ["lesson_source_ids", "article_item_ids"])
def test_no_source_or_article_mapping_is_not_accepted(key):
    row = candidate()
    row[key] = []
    assert module.assess(row, {"L1"}, {"p001"})["reference_relation"] == "unresolved"


def test_new_coverage_contradiction_cannot_be_hidden_by_a_reference_relation():
    row = candidate()
    row["coverage_contradiction_found"] = True
    assert module.assess(row, {"L1"}, {"p001"})["reference_relation"] == "unresolved"


def test_fabricated_source_id_is_rejected():
    row = candidate()
    row["lesson_source_ids"] = ["fabricated"]
    with pytest.raises(jsonschema.ValidationError):
        module.assess(row, {"L1"}, {"p001"})
