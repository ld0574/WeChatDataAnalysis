import copy

import pytest

from test_course_textbook_edition_editor import fixture
from wechat_decrypt_tool import course_textbook_edition_editor as editor
from wechat_decrypt_tool.course_textbook_editor_validation import (
    operation_effects,
    validate_operations,
)


def test_quiz_changes_cannot_mask_an_unchanged_required_paragraph():
    value, pending, candidate = fixture()
    value["book"]["chapters"][0]["sections"][0]["paragraphs"][0] = "Existing supported explanation. " * 8
    candidate["paragraph_changes"][0]["text"] = value["book"]["chapters"][0]["sections"][0]["paragraphs"][0]
    schema = editor.write_schema(value["book"], pending, value["sources"])
    editor.check_write(candidate, value["book"], schema)
    assert operation_effects(value["book"], candidate, editor)["edit000"]["changed"] is False
    with pytest.raises(ValueError, match="actual_operation_change_edit000"):
        validate_operations(candidate, value["book"], schema, editor)


def test_whitespace_changes_and_repeated_appends_are_not_edits():
    value, _, candidate = fixture()
    original = value["book"]["chapters"][0]["sections"][0]["paragraphs"][0]
    candidate["paragraph_changes"][0]["text"] = "\n  " + original + "  \n"
    candidate["paragraph_changes"][1]["text"] = original
    effects = operation_effects(value["book"], candidate, editor)
    assert not effects["edit000"]["changed"] and not effects["edit001"]["changed"]


def test_each_real_edit_passes_and_does_not_mutate_parent():
    value, pending, candidate = fixture()
    before = copy.deepcopy(value["book"])
    validate_operations(candidate, value["book"], editor.write_schema(before, pending, value["sources"]), editor)
    assert all(item["changed"] for item in operation_effects(before, candidate, editor).values())
    assert value["book"] == before


def test_replacing_and_appending_an_existing_quiz_both_report_no_change():
    value, _, candidate = fixture()
    original = copy.deepcopy(value["book"]["quiz"][0])
    candidate["quiz_changes"][0] = {"kind": "replace", "question_id": "q00", "value": original, "source_keys": ["old"]}
    candidate["quiz_changes"][1]["value"] = original
    effects = operation_effects(value["book"], candidate, editor)
    assert not effects["edit002"]["changed"] and not effects["edit003"]["changed"]
