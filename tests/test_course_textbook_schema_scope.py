import copy

import jsonschema
import pytest

from wechat_decrypt_tool import course_textbook_schema_scope as m
from wechat_decrypt_tool import course_wiki_writer as writer
from test_course_textbook_visible_context import fixture


def test_actual_pending_teaching_schema_gets_scoped_citations_inside_oneof():
    from wechat_decrypt_tool.course_textbook_visible_context import writing

    inventory, working, _, _, candidate = fixture()
    schema = writing.section_schema(["U0"], working)
    packet = {"sources": {key: inventory["sources"][key] for key in ["O00001", "O00002"]}}
    original = copy.deepcopy(schema)
    # Reproduce the frozen helper's actual branch gap without changing it.
    outside = copy.deepcopy(candidate)
    outside["quiz"]["citations"] = ["O00004"]
    jsonschema.validate(outside, writer.evidence_schema(schema, packet))
    corrected = m.bind(schema, packet)
    jsonschema.validate(candidate, corrected)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(outside, corrected)
    assert schema == original and corrected == m.bind(corrected, packet)
    non_teaching = {"kind": "non_teaching", "coverage": {"U0": {"teaching": False, "paragraphs": [], "reason": "Synthetic administrative source"}}}
    jsonschema.validate(non_teaching, corrected)


@pytest.mark.parametrize("branch", ["oneOf", "anyOf", "allOf"])
def test_combinator_nested_array_sources_are_constrained(branch):
    claim = {"type": "object", "properties": {"citations": {"type": "array", "items": {"type": "string"}}}}
    schema = {branch: [{"type": "array", "items": claim}]}
    corrected = m.bind(schema, {"sources": {"known": {}}})
    jsonschema.validate([{"citations": ["known"]}], corrected)
    for refs in [[], ["unknown"]]:
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate([{"citations": refs}], corrected)


def test_shared_arrays_empty_code_refs_and_literal_data_do_not_change():
    strings = {"type": "array", "items": {"type": "string"}}
    literal = {"properties": {"citations": {"items": {"const": "literal data"}}}}
    schema = {"type": "object", "properties": {"citations": strings, "code_citations": strings, "objectives": strings,
        "image": {"type": "string"}, "literal": {"const": literal}}}
    before = copy.deepcopy(schema)
    corrected = m.bind(schema, {"sources": {"a": {}, "frame": {"image_sha256": "f" * 64}}})
    assert schema == before
    assert corrected["properties"]["objectives"] == strings
    assert corrected["properties"]["code_citations"].get("minItems", 0) == 0
    assert corrected["properties"]["image"]["enum"] == ["", "frame"]
    assert corrected["properties"]["literal"]["const"] == literal
    jsonschema.validate({"citations": ["a"], "code_citations": [], "objectives": ["free prose"], "image": "frame", "literal": literal}, corrected)


def test_definitions_and_conditionals_are_traversed_without_changing_other_constraints():
    claim = {"type": "object", "additionalProperties": False, "properties": {"citations": {"type": "array", "items": {"type": "string"}}}}
    schema = {"$defs": {"claim": claim}, "if": {"type": "object"}, "then": {"$ref": "#/$defs/claim"}}
    corrected = m.bind(schema, {"sources": {"known": {}}})
    assert corrected["$defs"]["claim"]["additionalProperties"] is False
    jsonschema.validate({"citations": ["known"]}, corrected)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"citations": ["outside"]}, corrected)


def test_empty_source_packet_is_refused():
    with pytest.raises(ValueError, match="nonempty_original_source_ids"):
        m.bind({}, {"sources": {}})
