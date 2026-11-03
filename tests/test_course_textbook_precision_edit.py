import copy

import pytest

from wechat_decrypt_tool import course_textbook_precision_edit as m
from test_course_textbook_gap_plan import inputs


def task(book, refs, key="fix000"):
    actual = m.final.all_items(book)
    return {"task": key, "target_ids": refs, "actual_content": actual[refs[0]],
            "kind": refs[0][0], "negative_checks": {"pending000": {"passed": False}}}


def operation(value, replacement):
    return {"task": value, "candidate": {"action": "replace", "value": replacement}, "accepted": True}


def test_only_actual_unresolved_integrated_facts_become_edit_tasks():
    book = inputs()["book"]
    pending = {"a": {"kind": "facts", "actual_content": "Existing explanation", "actual_article_items": ["p000"], "original_negative_review": {"passed": False}},
               "b": {"kind": "coverage", "original_negative_review": {"passed": False}},
               "c": {"kind": "facts", "actual_content": "Existing explanation", "actual_article_items": ["p000"], "original_negative_review": {"passed": False}}}
    reviews = {"a": {"passed": False}, "b": {"passed": False}, "c": {"passed": True}}
    before = copy.deepcopy((pending, reviews))
    result = m.tasks_from(book, pending, reviews)
    assert len(result) == 1
    assert result[0]["target_ids"] == ["p000"]
    assert set(result[0]["negative_checks"]) == {"a"}
    assert (pending, reviews) == before
    with pytest.raises(ValueError, match="every_integrated_judgment"):
        m.tasks_from(book, pending, {})
    pending["a"]["actual_content"] = "Changed"
    with pytest.raises(ValueError, match="same_actual_negative_fact"):
        m.tasks_from(book, pending, reviews)


def test_paragraph_replacement_preserves_all_other_content_and_rejects_stale_target():
    book = inputs()["book"]
    before = copy.deepcopy(book)
    op = operation(task(book, ["p000"]), "Correct explanation")
    result = m.apply_operations(book, [op])
    assert result["chapters"][0]["sections"][0]["paragraphs"] == ["Correct explanation"]
    expected = copy.deepcopy(before)
    expected["chapters"][0]["sections"][0]["paragraphs"] = ["Correct explanation"]
    assert result == expected and book == before
    op["task"]["actual_content"] = "Stale"
    with pytest.raises(ValueError, match="exact_original_edit_target"):
        m.apply_operations(book, [op])


def test_unaccepted_and_overlapping_edits_are_never_applied():
    book = inputs()["book"]
    op = operation(task(book, ["p000"]), "Correct explanation")
    with pytest.raises(ValueError, match="nonoverlapping_edits"):
        m.apply_operations(book, [op, op])
    op["accepted"] = False
    with pytest.raises(ValueError, match="only_checked_edits"):
        m.apply_operations(book, [op])


def test_multiple_quiz_omissions_keep_original_indices_and_combined_floor():
    book = inputs()["book"]
    original = book["quiz"][0]
    book["quiz"] = [{**original, "question": f"Question {i}"} for i in range(7)]
    omissions = [{"task": task(book, [f"q{i:02d}"], f"fix{i:03d}"),
                  "candidate": {"action": "omit", "reason": "No valid original teaching"}, "accepted": True} for i in [0, 2, 4]]
    result = m.apply_operations(book, omissions)
    assert [q["question"] for q in result["quiz"]] == ["Question 1", "Question 3", "Question 5", "Question 6"]
    omissions.append({"task": task(book, ["q06"], "fix004"), "candidate": {"action": "omit"}, "accepted": True})
    with pytest.raises(ValueError, match="aggregate_omission_floors"):
        m.apply_operations(book, omissions)


def test_positive_checks_require_support_and_omissions_require_retained_teaching():
    book = inputs()["book"]
    sources = inputs()["sources"]
    value = {"facts": True, "issue_resolved": True, "valid_teaching_retained": True,
             "no_valid_original_teaching": False, "reason": "Synthetic judgment", "source_keys": [], "article_items": []}
    with pytest.raises(ValueError, match="positive_replacement_support"):
        m.check_review(value, {"action": "replace"}, book, sources)
    with pytest.raises(ValueError, match="omission_preserves_actual_teaching"):
        m.check_review(value, {"action": "omit"}, book, sources)
    value["source_keys"], value["article_items"] = ["old"], ["p000"]
    m.check_review(value, {"action": "omit"}, book, sources)


def test_semantic_repair_is_bounded_and_failed_candidate_stays_unaccepted():
    book = inputs()["book"]
    defect = task(book, ["p000"])
    calls = []
    class Job:
        inputs = {"sources": inputs()["sources"]}
        def stage(self, name, prompt, schema, context, validate):
            calls.append(name)
            if "review" in name:
                value = {"facts": False, "issue_resolved": False, "valid_teaching_retained": True,
                         "no_valid_original_teaching": False, "reason": "Unresolved", "source_keys": [], "article_items": []}
            else:
                value = {"action": "replace", "value": "Still incomplete", "source_keys": ["old"]}
            validate(value)
            return value
    proof = m.edit_task(Job(), defect, book)
    assert len(calls) == 4 and calls[-1].endswith("review-1")
    assert proof["accepted"] is False
    assert proof["global_revalidation_required"]
