import copy
import json

import pytest

from wechat_decrypt_tool.course_textbook_evidence import (
    MAX_PAGE_TEXT,
    inventory,
    notebook_sources,
    sha,
    validate_inventory,
)


def lesson(texts):
    return {
        "id": "a" * 24,
        "source_export_sha256": "b" * 64,
        "evidence": {
            str(i): {
                "kind": "speech" if i % 2 == 0 else "frame",
                "start": i * 5,
                "end": i * 5 + 5,
                "text": text,
            }
            for i, text in enumerate(texts)
        },
    }


def test_lossless_whole_lesson_includes_unreferenced_and_empty_originals():
    texts = ["  \n", "A🙂汉字\n" * 1100, "", "last teaching fact"]
    value = inventory(lesson(texts), lesson_artifact="c" * 64)
    assert len(value["pages"]) > 1
    assert value["original_characters"] == sum(map(len, texts))
    assert value["reader_payload"] is False
    assert value["scheduling_pages_are_article_chapters"] is False
    assert value["attachment_scope"]["unresolved"] is True
    assert value["semantic_accuracy_verified"] is False
    for index, original in enumerate(texts):
        parts = [
            p
            for p in value["sources"].values()
            if p["text_origin"]["original"] == index
        ]
        assert "".join(p["text"] for p in parts) == original
        assert parts[0]["text_origin"]["start"] == 0
        assert parts[-1]["text_origin"]["end"] == len(original)
    for page in value["pages"]:
        assert sum(len(value["sources"][ref]["text"]) for ref in page) <= MAX_PAGE_TEXT
    validate_inventory(value)


@pytest.mark.parametrize(
    "mutation", ["text", "offset", "page_missing", "page_duplicate", "original_missing"]
)
def test_inventory_rejects_lost_changed_or_double_allocated_characters(mutation):
    value = inventory(
        lesson(["original source", "another teaching source"]), lesson_artifact="c" * 64
    )
    if mutation == "text":
        value["sources"]["E00000_000"]["text"] = "changed source"
    elif mutation == "offset":
        value["sources"]["E00000_000"]["text_origin"]["start"] = 1
    elif mutation == "page_missing":
        value["pages"][0].pop()
    elif mutation == "page_duplicate":
        value["pages"][0].append(value["pages"][0][0])
    else:
        value["originals"].pop()
    with pytest.raises(ValueError):
        validate_inventory(value)


def test_notebook_preserves_all_textual_cell_sources_and_outputs_without_execution():
    value = {
        "nbformat": 4,
        "cells": [
            {"cell_type": "markdown", "source": ["# Fixture\n", "Complete paragraph."]},
            {
                "cell_type": "code",
                "source": "do_not_execute()",
                "outputs": [
                    {"text": ["first\n", "second\n"]},
                    {
                        "data": {
                            "text/plain": ["result"],
                            "text/html": "<b>result</b>",
                            "image/png": "opaque-retained-in-archive",
                        }
                    },
                ],
            },
            {"cell_type": "raw", "source": []},
        ],
    }
    raw = json.dumps(value).encode()
    sources = notebook_sources(raw, archive_sha256="d" * 64, member="fixture.ipynb")
    assert [s["text"] for s in sources] == [
        "# Fixture\nComplete paragraph.",
        "do_not_execute()",
        "first\nsecond\n",
        "result",
        "<b>result</b>",
        "",
    ]
    assert all(s["origin"]["member_sha256"] == sha(raw) for s in sources)
    assert {s["origin"]["cell"] for s in sources} == {0, 1, 2}


def test_notebook_rejects_unknown_cells_and_nontext_content():
    for cell in (
        {"cell_type": "unexpected", "source": "x"},
        {"cell_type": "code", "source": [42]},
    ):
        raw = json.dumps({"nbformat": 4, "cells": [cell]}).encode()
        with pytest.raises(ValueError):
            notebook_sources(raw, archive_sha256="d" * 64, member="fixture.ipynb")


def test_supplemental_material_is_bound_in_the_same_lossless_inventory():
    base = inventory(lesson(["spoken fact"]), lesson_artifact="c" * 64)
    extra = {
        "kind": "document",
        "text": "entire supplemental condition",
        "origin": {"member": "fixture.ipynb"},
    }
    combined = inventory(
        lesson(["spoken fact"]), lesson_artifact="c" * 64, supplements=[extra]
    )
    assert combined["originals"][-1] == extra
    assert combined["original_characters"] == base["original_characters"] + len(
        extra["text"]
    )
    changed = copy.deepcopy(combined)
    changed["originals"][-1]["text"] += "missing"
    with pytest.raises(ValueError):
        validate_inventory(changed)
