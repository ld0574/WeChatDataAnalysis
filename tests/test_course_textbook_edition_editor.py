import copy
from types import SimpleNamespace

import jsonschema
import pytest

from test_course_textbook_gap_additions import packet
from wechat_decrypt_tool import course_textbook_edition_editor as module


def fixture():
    value, _, _, _ = packet()
    pending = {"fact": {"kind": "facts", "actual_article_items": ["p000"]}}
    quiz = value["book"]["quiz"][0]
    candidate = {"paragraph_changes": [{"kind": "replace", "paragraph_id": "p000",
        "text": "An expanded synthetic explanation with the necessary conditions and a complete example. " * 2,
        "source_keys": ["old"]}, {"kind": "append", "section_id": "s",
        "text": "A distinct worked example of the supported principle with clear scope and limitations. " * 2,
        "source_keys": ["gap1"]}],
        "quiz_changes": [{"kind": "append", "value": {**copy.deepcopy(quiz), "question": "Scenario " + str(i)},
                          "source_keys": ["old"]} for i in range(3)]}
    return value, pending, candidate


def test_mandatory_factual_target_cannot_be_ignored_or_replaced_by_an_addition():
    value, pending, candidate = fixture()
    schema = module.write_schema(value["book"], pending, value["sources"])
    module.check_write(candidate, value["book"], schema)
    changed = copy.deepcopy(candidate)
    changed["paragraph_changes"] = changed["paragraph_changes"][1:]
    with pytest.raises(jsonschema.ValidationError):
        module.check_write(changed, value["book"], schema)


def test_partial_merge_applies_only_reviewed_items_and_keeps_parent_immutable():
    value, _, candidate = fixture()
    before = copy.deepcopy(value["book"])
    actual = module.apply(value["book"], candidate, {"edit000", "edit003"})
    assert value["book"] == before
    assert actual["chapters"][0]["sections"][0]["paragraphs"] == [candidate["paragraph_changes"][0]["text"]]
    assert len(actual["quiz"]) == 2 and actual["quiz"][-1] == candidate["quiz_changes"][1]["value"]
    assert "source_keys" not in str(actual)


def test_overlapping_replacements_and_duplicate_tests_fail_closed():
    value, pending, candidate = fixture()
    changed = copy.deepcopy(candidate)
    changed["paragraph_changes"].append(copy.deepcopy(changed["paragraph_changes"][0]))
    with pytest.raises(ValueError, match="nonoverlapping_replacements"):
        module.apply(value["book"], changed)
    changed = copy.deepcopy(candidate)
    changed["quiz_changes"][1] = copy.deepcopy(changed["quiz_changes"][0])
    with pytest.raises(ValueError, match="distinct_self_tests"):
        module.check_write(changed, value["book"], module.write_schema(value["book"], pending, value["sources"]))


def test_labels_preserve_all_paragraphs_diagrams_and_quiz_content():
    value, _, _ = fixture()
    before = copy.deepcopy(value["book"])
    labelled = module.labelled_article(value["book"])
    section = labelled["chapters"][0]["sections"][0]
    assert section["paragraphs"][0] == {"id": "p000", "text": before["chapters"][0]["sections"][0]["paragraphs"][0]}
    assert section["diagrams"][0]["graph"] == before["chapters"][0]["sections"][0]["diagrams"][0]
    assert labelled["quiz"]["q00"] == before["quiz"][0]
    assert value["book"] == before


def test_editorial_input_keeps_every_complete_original_and_all_negative_targets():
    value, pending, _ = fixture()
    context = module.write_context(value, value["book"], {"gap1": {"kind": "missing"}}, pending,
                                   {"depth": {"passed": False}})
    assert context["all_original_sources"] == value["sources"]
    assert context["current_missing_sources"]["gap1"]["original"] == value["sources"]["gap1"]
    assert context["required_factual_paragraphs"]["p000"] == value["book"]["chapters"][0]["sections"][0]["paragraphs"][0]
    assert context["remaining_prior_issues"] == pending and context["reading_negative_reviews"]["depth"]["passed"] is False


def test_positive_review_requires_real_evidence_and_actual_body_positions():
    value, pending, candidate = fixture()
    schema = module.review_schema(module.operations(candidate), pending, module.apply(value["book"], candidate), value["sources"])
    verdict = {"operations": {k: {"verdict": "accepted", "source_keys": ["old"], "reason": "Synthetic support"}
                              for k in module.operations(candidate)},
        "prior_issues": {"fact": {"verdict": "resolved", "source_keys": ["old"], "article_items": ["p000"], "reason": "Resolved"}},
        "reading": {k: {"verdict": "passed", "reason": "Synthetic review"} for k in
                    ["organisation", "depth", "coherence", "figures", "self_tests"]}}
    jsonschema.validate(verdict, schema)
    verdict["prior_issues"]["fact"]["article_items"] = []
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(verdict, schema)
    verdict["prior_issues"]["fact"]["article_items"] = ["p000"]
    verdict["operations"]["edit000"]["source_keys"] = []
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(verdict, schema)


def test_oversized_complete_input_is_rejected_without_truncation():
    job = object.__new__(module.EditorJob)
    job.tokenizer = SimpleNamespace(encode=lambda _: SimpleNamespace(ids=range(140000)))
    with pytest.raises(ValueError, match="complete_editorial_input_fits"):
        job.budget([{"content": "Complete input"}], "editorial-write")
