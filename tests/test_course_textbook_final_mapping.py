import copy

import pytest

from wechat_decrypt_tool import course_textbook_final_mapping as module


def test_uniform_index_proposal_keeps_prose_and_original_mapping_immutable():
    value = {
        "paragraphs": [{"text": "虚构解释一"}, {"text": "虚构解释二"}],
        "coverage": {
            "P0": {"paragraphs": [1, 2], "reason": "映射"},
            "U0": {"paragraphs": [], "teaching": False},
        },
    }
    original = copy.deepcopy(value)
    result = module.uniform_zero_based(value)
    assert value == original
    assert result["paragraphs"] == original["paragraphs"]
    assert result["coverage"]["P0"]["paragraphs"] == [0, 1]
    assert result["coverage"]["U0"]["paragraphs"] == []
    assert result["coverage"]["P0"]["reason"] == "映射"


@pytest.mark.parametrize("indices", [[0, 2], [1, 3], [1], [], [-1, 2], [True, 2]])
def test_ambiguous_mixed_or_invalid_indices_are_never_clamped_or_guessed(indices):
    value = {"paragraphs": [{}, {}], "coverage": {"P0": {"paragraphs": indices}}}
    with pytest.raises(ValueError, match="unambiguous_uniform_one_based_mapping"):
        module.uniform_zero_based(value)


def test_mapping_adapter_does_not_bypass_a_negative_factual_review(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    calls = []
    monkeypatch.setattr(
        module.citations,
        "authenticated",
        lambda *a: calls.append(a[1]) or {"passed": False},
    )
    result = module.authenticated(
        SimpleNamespace(root=tmp_path),
        "section-0001-actual-body-review",
        "prompt",
        {},
        {},
        lambda v: None,
        {},
    )
    assert result["passed"] is False
    assert calls == ["section-0001-actual-body-review"]


def test_navigation_disambiguator_keeps_body_and_valid_mapping():
    original = {
        "heading": "虚构主题",
        "paragraphs": [{"text": "解释"}],
        "coverage": {"P0": {"paragraphs": [0]}},
    }
    value, offset, changed = module.metadata_proposal(
        original, "section-0001-whole-edit", {"previous_heading": "虚构主题"}
    )
    assert offset == 0 and changed
    assert value["heading"] == "虚构主题（续2）"
    assert value["paragraphs"] == original["paragraphs"]
    assert value["coverage"] == original["coverage"]
    assert original["heading"] == "虚构主题"


def test_metadata_proposal_keeps_distinct_heading_and_converts_only_uniform_base():
    value = {
        "heading": "另一主题",
        "paragraphs": [{"text": "解释"}],
        "coverage": {"P0": {"paragraphs": [1]}},
    }
    result, offset, changed = module.metadata_proposal(
        value, "section-0001-whole-edit", {"previous_heading": "虚构主题"}
    )
    assert offset == -1 and not changed
    assert result["heading"] == value["heading"]
    assert result["coverage"]["P0"]["paragraphs"] == [0]
