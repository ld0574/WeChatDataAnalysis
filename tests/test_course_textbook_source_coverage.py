import copy

import jsonschema
import pytest

from wechat_decrypt_tool import course_textbook_source_coverage as module
from test_course_textbook_authoring import archive as archive


@pytest.mark.parametrize(
    "text",
    [
        "",
        " \n ",
        "number 1.25 is preserved. Next?",
        "条件，示例。\n限制；",
        "a(x,y);\n  return x+y",
        "trailing no punctuation",
    ],
)
def test_complete_original_reconstruction_preserves_all_characters(text):
    units = module.clauses(text)
    pieces = [text[value["start"] : value["end"]] for value in units.values()]
    assert "".join(pieces) == text
    assert sum(len(part) for part in pieces) == len(text)
    assert all(
        value["sha256"] == module.sha(piece.encode("utf-8"))
        for value, piece in zip(units.values(), pieces, strict=True)
    )


def test_plan_rejects_omitted_changed_or_falsely_verified_material(request):
    _, _, inventory, _ = request.getfixturevalue("archive")
    plan = module.plan(inventory)
    assert plan["characters"] == inventory["original_characters"]
    assert not plan["clauses_are_verified_teaching"]
    for change in [
        "omitted-source",
        "omitted-clause",
        "changed-range",
        "claim-accuracy",
    ]:
        bad = copy.deepcopy(plan)
        ref = next(iter(bad["units"]))
        if change == "omitted-source":
            del bad["units"][ref]
        elif change == "omitted-clause":
            bad["units"][ref].pop(next(iter(bad["units"][ref])))
        elif change == "changed-range":
            bad["units"][ref][next(iter(bad["units"][ref]))]["start"] += 1
        else:
            bad["semantic_accuracy_verified"] = True
        with pytest.raises(ValueError, match="requires_source_coverage_"):
            module.validate_plan(bad, inventory)


def test_missing_condition_cannot_hide_behind_a_covered_example():
    text = "Only integer inputs are allowed, input 3 returns 9."
    units = module.clauses(text)
    assert len(units) == 2
    ids = list(units)
    result = {
        "clauses": {
            ids[0]: {
                "teaching": True,
                "covered": False,
                "paragraph_ids": [],
                "reason": "The article omits the integer-only condition.",
            },
            ids[1]: {
                "teaching": True,
                "covered": True,
                "paragraph_ids": ["P0"],
                "reason": "The article explains the complete numerical example.",
            },
        }
    }
    module.validate_review(result, ids, ["P0"])
    assert module.missing(result) == [ids[0]]
    for change in [
        "missing-key",
        "unmapped-pass",
        "invented-paragraph",
        "non-teaching-pass",
    ]:
        bad = copy.deepcopy(result)
        if change == "missing-key":
            del bad["clauses"][ids[0]]
        elif change == "unmapped-pass":
            bad["clauses"][ids[0]]["covered"] = True
        elif change == "invented-paragraph":
            bad["clauses"][ids[1]]["paragraph_ids"] = ["invented"]
        else:
            bad["clauses"][ids[1]]["teaching"] = False
        with pytest.raises((ValueError, jsonschema.ValidationError)):
            module.validate_review(bad, ids, ["P0"])


def test_no_article_paragraph_can_still_reveal_missing_teaching():
    value = {
        "clauses": {
            "C0": {
                "teaching": True,
                "covered": False,
                "paragraph_ids": [],
                "reason": "No paragraph teaches this condition.",
            }
        }
    }
    module.validate_review(value, ["C0"], [])
    assert module.missing(value) == ["C0"]
    value["clauses"]["C0"]["paragraph_ids"] = ["P0"]
    with pytest.raises(jsonschema.ValidationError):
        module.validate_review(value, ["C0"], [])
