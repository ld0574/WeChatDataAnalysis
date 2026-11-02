import copy

import pytest
from jsonschema import Draft202012Validator, ValidationError

from wechat_decrypt_tool import course_wiki_check_constraints as constraints
from wechat_decrypt_tool import course_wiki_completeness as omissions
from wechat_decrypt_tool import course_wiki_review as review


def forward_request():
    return review.review_request(
        [{"target": "quiz-0", "citations": ["a"], "text": "Synthetic claim"}],
        {"a": {"text": "Synthetic literal source."}, "b": {"text": "Other source."}},
    )


def reverse_context():
    return {
        "windows": {"a@0": {"source": "a"}},
        "article_targets": {
            "section-0-p-0": {"citations": ["a"]},
            "section-0-code": {"citations": ["a"]},
            "section-1-p-0": {"citations": ["b"]},
        },
    }


@pytest.mark.parametrize("verdict", ["supported", "partial"])
def test_forward_grammar_rejects_observed_empty_evidence_failure(verdict):
    request = forward_request()
    schema = constraints.forward(request)
    Draft202012Validator.check_schema(schema)
    value = {
        "reviews": [
            {"target": "quiz-0", "verdict": verdict, "reason": "Test", "evidence": []}
        ]
    }
    Draft202012Validator(review.selection_schema(request)).validate(value)
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(value)
    value["reviews"][0]["evidence"] = [{"witness": "a@0"}]
    Draft202012Validator(schema).validate(value)
    resolved = review.resolve_witnesses(value, request["witnesses"])
    review.validate(
        resolved, request["targets"], {"a": {"text": "Synthetic literal source."}}
    )
    value["reviews"][0]["evidence"] = [{"witness": "b@0"}]
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(value)


def test_forward_keeps_honest_unsupported_and_unclear_without_forced_evidence():
    for verdict in ["unsupported", "unclear"]:
        value = {
            "reviews": [
                {
                    "target": "quiz-0",
                    "verdict": verdict,
                    "reason": "Test",
                    "evidence": [],
                }
            ]
        }
        Draft202012Validator(constraints.forward(forward_request())).validate(value)


@pytest.mark.parametrize("verdict", ["covered", "partial"])
def test_reverse_requires_evidence_from_the_windows_own_source(verdict):
    context = reverse_context()
    schema = constraints.reverse(context)
    Draft202012Validator.check_schema(schema)
    value = {
        "reviews": [
            {
                "window": "a@0",
                "coverage": verdict,
                "reason": "Test",
                "article_evidence": [],
            }
        ]
    }
    Draft202012Validator(omissions.selection_schema(context)).validate(value)
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(value)
    value["reviews"][0]["article_evidence"] = ["section-0-p-0"]
    Draft202012Validator(schema).validate(value)
    omissions.validate(value, context)
    value["reviews"][0]["article_evidence"] = ["section-1-p-0"]
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(value)


def test_reverse_preserves_no_evidence_verdicts_and_does_not_certify_code():
    context = reverse_context()
    frozen = copy.deepcopy(context)
    schema = constraints.reverse(context)
    assert context == frozen
    for verdict in ["missing", "not_essential", "unclear"]:
        value = {
            "reviews": [
                {
                    "window": "a@0",
                    "coverage": verdict,
                    "reason": "Test",
                    "article_evidence": [],
                }
            ]
        }
        Draft202012Validator(schema).validate(value)
        omissions.validate(value, context)
    value["reviews"][0].update(coverage="covered", article_evidence=["section-0-code"])
    # A grammar cannot decide whether selected prose explains the source. The
    # unchanged production validator remains mandatory, including code-only.
    Draft202012Validator(schema).validate(value)
    with pytest.raises(ValueError, match="code_alone"):
        omissions.validate(value, context)
    del context["article_targets"]["section-0-p-0"]
    with pytest.raises(ValidationError):
        Draft202012Validator(constraints.reverse(context)).validate(value)


def test_uncited_source_cannot_gain_supported_or_covered_by_constraint():
    request = forward_request()
    request["targets"][0]["citations"] = ["absent"]
    schema = constraints.forward(request)
    assert {
        b["properties"]["verdict"]["enum"][0]
        for b in schema["properties"]["reviews"]["items"]["oneOf"]
    } == {"unsupported", "unclear"}
    context = reverse_context()
    context["windows"]["a@0"]["source"] = "absent"
    schema = constraints.reverse(context)
    assert {
        b["properties"]["coverage"]["enum"][0]
        for b in schema["properties"]["reviews"]["items"]["oneOf"]
    } == {"missing", "not_essential", "unclear"}
