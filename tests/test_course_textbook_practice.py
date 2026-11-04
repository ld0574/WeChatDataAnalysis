import copy

import pytest

from wechat_decrypt_tool import course_textbook_practice as practice
from wechat_decrypt_tool import course_textbook_reader_presentation as presentation


def book():
    return {"format": "course-textbook-reading-v1", "course_id": "a" * 24, "title": "Synthetic article", "introduction": ["A readable introduction."],
        "objectives": ["Explain a prerequisite."], "prerequisites": [],
        "chapters": [{"id": "chapter-a", "heading": "Synthetic chapter", "sections": [{"id": "section-a",
            "heading": "Synthetic section", "paragraphs": ["A prerequisite controls eligibility."], "diagrams": []}]}],
        "takeaways": ["Check the prerequisite."], "quiz": [{"question": "Legacy open question",
            "answer": "LEGACY_ANSWER", "explanation": "LEGACY_EXPLANATION", "criteria": ["Explain applicability."]}],
        "related_courses": []}


def questions():
    rows = []
    for index, kind in enumerate(["single", "multiple"], 1):
        rows.append({"id": f"Q{index}", "type": kind, "question": "Which synthetic conditions apply?",
            "options": {"A": "Prerequisite holds", "B": "Ignore the prerequisite", "C": "Check the boundary", "D": "Always apply"},
            "correct": ["A"] if kind == "single" else ["A", "C"], "explanation": "EXPLANATION_AFTER_SUBMISSION",
            "support": [{"section": "section-a", "quote": "A prerequisite controls eligibility."}]})
    return {"questions": rows}


def pack():
    return {**questions(), "pack_sha256": "b" * 64, "article_sha256": "c" * 64}


def test_choice_contract_rejects_six_items_and_ambiguous_answer_types():
    practice.validate(questions(), book())
    invalid = questions()
    invalid["questions"] *= 3
    with pytest.raises(practice.jsonschema.ValidationError):
        practice.validate(invalid, book())
    invalid = questions()
    invalid["questions"][0]["correct"] = ["A", "B"]
    with pytest.raises(ValueError, match="consistent_answer_type"):
        practice.validate(invalid, book())


def test_support_must_be_in_the_exact_current_article_and_options_distinct():
    value = questions()
    value["questions"][0]["support"][0]["quote"] = "Not in this article"
    with pytest.raises(ValueError, match="literal_article_support"):
        practice.validate(value, book())
    value = questions()
    value["questions"][0]["options"]["B"] = value["questions"][0]["options"]["A"]
    with pytest.raises(ValueError, match="distinct_options"):
        practice.validate(value, book())


def test_public_questions_and_initial_html_never_include_keys_or_explanations():
    value = practice.public(pack())
    assert all(set(row) == {"id", "type", "question", "options"} for row in value["questions"])
    document = presentation.reading_html(book(), pack())
    assert "EXPLANATION_AFTER_SUBMISSION" not in document
    assert "LEGACY_ANSWER" not in document and "LEGACY_EXPLANATION" not in document
    assert document.count("<fieldset ") == 2
    assert 'type="radio"' in document and 'type="checkbox"' in document
    assert document.count('class="choice-feedback"') == document.count(' hidden>') == 2
    assert "support" not in value["questions"][0]


def test_grading_requires_all_answers_and_exact_multiple_choice_sets():
    submission = {"pack_sha256": "b" * 64, "answers": {"Q1": ["A"]}}
    with pytest.raises(ValueError, match="all_questions_answered"):
        practice.grade(pack(), submission)
    submission["answers"]["Q2"] = ["C", "A"]
    assert practice.grade(pack(), submission)["score"] == 2
    submission["answers"]["Q2"] = ["A"]
    result = practice.grade(pack(), submission)
    assert result["score"] == 1 and result["results"][1]["correct"] is False


@pytest.mark.parametrize("selected", [["A", "A"], ["Z"], "A", [], [True]])
def test_malformed_selections_never_return_answers(selected):
    with pytest.raises(ValueError):
        practice.grade(pack(), {"pack_sha256": "b" * 64, "answers": {"Q1": ["A"], "Q2": selected}})


def test_offline_practice_has_no_provenance_or_external_script_dependencies():
    original = book()
    document = presentation.reading_html(original, pack(), offline=True)
    assert 'id="offline-practice-key"' in document
    assert "EXPLANATION_AFTER_SUBMISSION" in document
    assert 'src="/assets/' not in document
    assert '"support"' not in document and '"proof"' not in document
    assert book() == original


def test_fenced_markdown_tables_render_as_tables_and_keep_all_cells():
    renderer = presentation.markdown_renderer()
    result = renderer.render("```\n| A | B | C |\n|---|---|---|\n| X | O | X |\n| O | X | O |\n```\n")
    assert result.count("<table>") == 1
    assert result.count("<td>") == 6
    assert result.count('<th scope="col">') == 3
    assert "<pre" not in result and "table-scroll" in result


def test_fenced_tables_with_captions_and_repeated_separators_are_readable():
    renderer = presentation.markdown_renderer()
    text = "```text\nSynthetic grid:\n| A | B | C |\n|---|---|---|\n| X | O | X |\n|---|---|---|\n| O | X | O |\n```"
    result = renderer.render(text)
    assert "Synthetic grid:" in result
    assert result.count("<table>") == 1 and result.count("<td>") == 6
    assert "---" not in result


def test_row_label_grid_with_missing_separator_column_preserves_header_and_empty_cells():
    text = "```\nGrid:\nr | A | B | C |\n|---|---|---|\ns |   | X |   \n|---|---|---|\nt |   |   |   \n```"
    result = presentation.markdown_renderer().render(text)
    assert result.count("<table>") == 1
    assert result.count('<th scope="col">') == 4
    assert result.count("<td>") == 8
    assert "<td>s</td>" in result and "<td>t</td>" in result
    assert "<td>X</td>" in result and "<pre" not in result


def test_actual_code_fences_are_preserved_and_active_markup_is_escaped():
    renderer = presentation.markdown_renderer()
    code = "```python\nprint('| A | B |')\n# |---|---|\n```"
    result = renderer.render(code + '\n<script>alert(1)</script>\n![alt](https://example.invalid/private.png)')
    assert "<pre><code" in result and "<table>" not in result
    assert "<script>" not in result and "&lt;script&gt;" in result
    assert "<img" not in result and "example.invalid" not in result


def test_rendering_does_not_change_original_article_or_legacy_learning_revision():
    original = book()
    before = copy.deepcopy(original)
    presentation.reading_html(original, pack())
    assert original == before


def test_program_binds_actual_sources_without_model_quote_copying_or_rewriting_questions():
    from wechat_decrypt_tool import course_textbook_practice_build as build

    raw = questions()
    for question in raw["questions"]:
        for support in question["support"]:
            support.pop("quote")
    original = copy.deepcopy(raw)
    compiled = build.compile_candidate(raw, book())
    assert raw == original
    for before, after in zip(raw["questions"], compiled["questions"], strict=True):
        assert {k: v for k, v in before.items() if k != "support"} == {k: v for k, v in after.items() if k != "support"}
        assert after["support"][0]["quote"] == book()["chapters"][0]["sections"][0]["paragraphs"][0]
    assert build.review_candidate(compiled) == raw


def test_program_binding_rejects_unknown_sections_without_inventing_source_text():
    from wechat_decrypt_tool import course_textbook_practice_build as build

    raw = questions()
    for question in raw["questions"]:
        question["support"] = [{"section": "unknown-section"}]
    with pytest.raises(ValueError, match="selected_article_section_exists"):
        build.compile_candidate(raw, book())
