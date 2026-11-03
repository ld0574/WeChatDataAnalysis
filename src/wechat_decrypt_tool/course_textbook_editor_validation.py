"""Check each editorial operation without changing historical job contracts."""

from __future__ import annotations

import unicodedata

from .course_library import canonical


def normalized(value):
    if isinstance(value, str):
        return "".join(unicodedata.normalize("NFKC", value).split())
    if isinstance(value, dict):
        return {key: normalized(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalized(item) for item in value]
    return value


def operation_effects(book, candidate, editor):
    """Report real changes individually; a changed quiz cannot mask unchanged prose."""
    actual = editor.e.additions.final.all_items(book)
    existing_paragraphs = {
        normalized(text) for key, text in actual.items() if key.startswith("p")
    }
    existing_quizzes = {canonical(normalized(value)) for value in book["quiz"]}
    effects = {}
    for key, operation in editor.operations(candidate).items():
        paragraph = operation["type"] == "paragraph"
        value = operation["text"] if paragraph else operation["value"]
        if operation["kind"] == "replace":
            target = operation["paragraph_id"] if paragraph else operation["question_id"]
            changed = normalized(value) != normalized(actual[target])
        else:
            changed = (
                normalized(value) not in existing_paragraphs
                if paragraph
                else canonical(normalized(value)) not in existing_quizzes
            )
        effects[key] = {"type": operation["type"], "changed": changed}
        if paragraph:
            existing_paragraphs.add(normalized(value))
        else:
            existing_quizzes.add(canonical(normalized(value)))
    return effects


def validate_operations(candidate, book, schema, editor):
    editor.check_write(candidate, book, schema)
    effects = operation_effects(book, candidate, editor)
    unchanged = [key for key, effect in effects.items() if not effect["changed"]]
    if unchanged:
        raise ValueError("requires_editor_actual_operation_change_" + "_".join(unchanged))
    return candidate
