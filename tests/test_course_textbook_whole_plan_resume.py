import copy

import pytest

from wechat_decrypt_tool import course_textbook_whole_plan_resume as resume


def sample():
    cards = {key: {"label": key, "teaching": "Synthetic teaching", "category": "concept"} for key in "abcdefgh"}
    value = {"title": "Synthetic article", "groups": [{"heading": "Prerequisites", "purpose": "Understand conditions"},
        {"heading": "Unused group", "purpose": "No assigned inputs"}, {"heading": "Applications", "purpose": "Apply the concept"}],
        "assignments": {key: {"group": 0 if i < 4 else 2, "order": i % 3} for i, key in enumerate(cards)}}
    return cards, value


def test_repair_preserves_all_meanings_and_strict_order_relations():
    cards, value = sample()
    original = copy.deepcopy(value)
    repaired = resume.normalize(value, cards)
    assert value == original and set(repaired["assignments"]) == set(cards)
    assert repaired["groups"] == [value["groups"][0], value["groups"][2]]
    for key in cards:
        assert repaired["groups"][repaired["assignments"][key]["group"]] == value["groups"][value["assignments"][key]["group"]]
        for other in cards:
            a, b = value["assignments"][key], value["assignments"][other]
            if a["group"] == b["group"] and a["order"] < b["order"]:
                assert repaired["assignments"][key]["order"] < repaired["assignments"][other]["order"]
    assert repaired["assignments"]["a"]["order"] < repaired["assignments"]["d"]["order"]


@pytest.mark.parametrize("mutation", ["missing", "out_of_range", "duplicate_heading", "blank_purpose", "no_unused"])
def test_repair_rejects_ambiguous_or_different_structural_failures(mutation):
    cards, value = sample()
    if mutation == "missing":
        value["assignments"].pop("a")
    elif mutation == "out_of_range":
        value["assignments"]["a"]["group"] = 3
    elif mutation == "duplicate_heading":
        value["groups"][1]["heading"] = value["groups"][0]["heading"]
    elif mutation == "blank_purpose":
        value["groups"][1]["purpose"] = " "
    else:
        value["assignments"]["a"]["group"] = 1
    with pytest.raises((ValueError, resume.jsonschema.ValidationError)):
        resume.normalize(value, cards)


def test_unique_order_numbers_remain_exact_even_when_group_indices_shift():
    cards, value = sample()
    for i, key in enumerate(cards):
        value["assignments"][key]["order"] = i * 3
    repaired = resume.normalize(value, cards)
    assert {key: row["order"] for key, row in repaired["assignments"].items()} == {key: row["order"] for key, row in value["assignments"].items()}


def test_high_level_plan_is_never_reinterpreted_as_original_point_grouping():
    with pytest.raises(ValueError, match="original_point_grouping_stage"):
        resume.context_for({"points": {}}, "concepts-01-0000")
