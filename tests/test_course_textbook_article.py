import copy
import json
import xml.etree.ElementTree as ET

import jsonschema
import pytest

from wechat_decrypt_tool.course_textbook_article import (
    diagram_svg,
    reading_book,
    reading_bytes,
    standalone_html,
    validate_book,
    validate_graph,
    wrapped_lines,
)


@pytest.fixture
def private():
    secret = "PRIVATE_RAW_EVIDENCE_NOT_FOR_READERS"
    claim = {
        "text": "An authored explanation of the fixture principle.",
        "citations": ["private-reference"],
        "source_text": secret,
    }
    graph = {
        "kind": "flow",
        "title": "Fixture process",
        "caption": "An authored explanatory diagram.",
        "nodes": [
            {"id": "first", "label": "Check the condition", "ocr": secret},
            {"id": "second", "label": "Apply the operation"},
        ],
        "edges": [
            {
                "from": "first",
                "to": "second",
                "label": "when satisfied",
                "origin": secret,
            }
        ],
        "citations": ["private-reference"],
        "image_sha256": secret,
    }
    return {
        "course_id": "a" * 24,
        "title": "Fixture article",
        "introduction": [claim],
        "objectives": ["Explain a condition before applying an operation."],
        "prerequisites": [],
        "chapters": [
            {
                "id": "chapter-1",
                "heading": "Understand the principle",
                "sections": [
                    {
                        "id": "section-1",
                        "heading": "Conditions and consequences",
                        "paragraphs": [claim],
                        "diagrams": [graph],
                        "raw_frames": secret,
                    }
                ],
                "transcript": secret,
            }
        ],
        "takeaways": [claim],
        "quiz": [
            {
                "question": "Why check the condition first?",
                "answer": "It governs when the fixture operation applies.",
                "explanation": "The operation follows only after the condition is satisfied.",
                "criteria": ["State the condition and explain the dependency."],
                "citations": ["private-reference"],
                "raw_text": secret,
            }
        ],
        "related_courses": [],
        "evidence": secret,
    }


def test_reading_projection_never_serializes_raw_evidence_or_image_references(private):
    before = copy.deepcopy(private)
    book = reading_book(private)
    serialized = json.dumps(book)
    for private_field in (
        "PRIVATE_RAW_EVIDENCE",
        "private-reference",
        "citations",
        "image_sha256",
        "raw_frames",
        "transcript",
        "source_text",
    ):
        assert private_field not in serialized
        assert private_field not in standalone_html(book)
    assert json.loads(reading_bytes(private)) == book
    assert private == before
    book["objectives"].append("Changed reading copy")
    assert private["objectives"] == before["objectives"]


def test_public_contract_rejects_private_fields_even_when_nested(private):
    for level in ("book", "paragraph", "graph"):
        book = reading_book(private)
        if level == "book":
            book["evidence"] = "raw"
        elif level == "paragraph":
            book["chapters"][0]["sections"][0]["paragraphs"] = [
                {"text": "authored", "transcript": "raw"}
            ]
        else:
            book["chapters"][0]["sections"][0]["diagrams"][0]["image_sha256"] = "raw"
        with pytest.raises(jsonschema.ValidationError):
            validate_book(book)


def test_svg_and_export_escape_text_without_active_html_or_external_assets(private):
    private["title"] = "<script>alert('fixture')</script>"
    private["chapters"][0]["sections"][0]["paragraphs"][0]["text"] = (
        '<img src="https://external.invalid" onerror="run()">'
    )
    private["chapters"][0]["sections"][0]["diagrams"][0]["nodes"][0]["label"] = (
        "</text><script>run()</script>"
    )
    book = reading_book(private)
    result = standalone_html(book)
    assert "<script>" not in result
    assert '<img src="' not in result
    assert "&lt;script&gt;" in result
    assert 'href="/library/frame/' not in result
    assert "<script src=" not in result
    svg = diagram_svg(book["chapters"][0]["sections"][0]["diagrams"][0])
    root = ET.fromstring(svg)
    assert not root.findall(".//{http://www.w3.org/2000/svg}script")
    assert "run()" in "".join(root.itertext())


def test_diagrams_require_known_connected_nodes_and_unique_ids(private):
    graph = reading_book(private)["chapters"][0]["sections"][0]["diagrams"][0]
    validate_graph(graph)
    for mutation in ("unknown", "duplicate", "isolated"):
        bad = copy.deepcopy(graph)
        if mutation == "unknown":
            bad["edges"][0]["to"] = "missing"
        elif mutation == "duplicate":
            bad["nodes"][1]["id"] = "first"
        else:
            bad["nodes"].append({"id": "orphan", "label": "Unexplained node"})
        with pytest.raises(ValueError):
            validate_graph(bad)


def test_long_node_labels_are_wrapped_without_character_loss(private):
    text = "支持完整说明的图示标签" * 20
    assert "".join(wrapped_lines(text)) == text
    graph = reading_book(private)["chapters"][0]["sections"][0]["diagrams"][0]
    graph["nodes"][0]["label"] = text
    svg = ET.fromstring(diagram_svg(graph))
    assert text in "".join(svg.itertext())


def test_duplicate_anchors_and_self_related_course_are_rejected(private):
    book = reading_book(private)
    book["chapters"][0]["sections"][0]["id"] = book["chapters"][0]["id"]
    with pytest.raises(ValueError, match="duplicate_reading_anchor"):
        validate_book(book)
    book = reading_book(private)
    book["related_courses"] = [book["course_id"]]
    with pytest.raises(ValueError, match="related_course_is_self"):
        validate_book(book)


def test_long_valid_section_anchor_still_exports_with_unique_svg_ids(private):
    private["chapters"][0]["sections"][0]["id"] = "s" * 64
    private["chapters"][0]["sections"][0]["diagrams"] *= 2
    result = standalone_html(reading_book(private))
    assert 'id="diagram-0-arrow"' in result
    assert 'id="diagram-1-arrow"' in result
    book = reading_book(private)
    book["chapters"][0]["id"] = "reading-self-test"
    with pytest.raises(ValueError, match="duplicate_reading_anchor"):
        validate_book(book)
