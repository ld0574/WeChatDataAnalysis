import copy

import jsonschema
import pytest

from wechat_decrypt_tool import course_textbook_numbered_graphs as m
from wechat_decrypt_tool.course_knowledge import atomic_json
from wechat_decrypt_tool.course_textbook_article import GRAPH, validate_graph
from test_course_textbook_fresh_authoring import fake_job
from test_course_textbook_visible_context import fixture


def graph(count=3):
    return {"node_count": count, "kind": "flow", "title": "Synthetic graph", "caption": "Synthetic explanation",
        "nodes": {f"n{i}": f"Synthetic label {i}" for i in reversed(range(count))},
        "edges": [{"from": f"n{i}", "to": f"n{i+1}", "label": "Synthetic"} for i in range(count-1)]}


@pytest.mark.parametrize("count", [2, 3, 9, 16])
def test_exact_required_nodes_and_bound_endpoints_preserve_model_meanings(count):
    raw = graph(count)
    before = copy.deepcopy(raw)
    jsonschema.validate(raw, m.graph_schema(GRAPH))
    result = m.compile_graph(raw)
    validate_graph(result)
    assert result["edges"] == raw["edges"]
    assert result["nodes"] == [{"id": f"n{i}", "label": raw["nodes"][f"n{i}"]} for i in range(count)]
    assert raw == before


@pytest.mark.parametrize("bad", ["missing_node", "extra_node", "unknown_endpoint", "count", "empty_label"])
def test_real_decoding_schema_rejects_known_identity_errors(bad):
    raw = graph()
    if bad == "missing_node":
        del raw["nodes"]["n1"]
    elif bad == "extra_node":
        raw["nodes"]["n3"] = "Synthetic extra"
    elif bad == "unknown_endpoint":
        raw["edges"][0]["to"] = "missing"
    elif bad == "count":
        raw["node_count"] = 2
    else:
        raw["nodes"]["n1"] = ""
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(raw, m.graph_schema(GRAPH))


def section():
    inv, working, packet, context, raw = fixture()
    raw["paragraphs"].append(dict(raw["paragraphs"][0], text="Synthetic second explanation"))
    raw["paragraph_count"] = 2
    raw["diagrams"] = [{**graph(), "citations": ["O00001"]}]
    scope = m.fresh.continuation.scope_for(["U0"], working, inv, context, packet)
    return inv, working, context, scope, raw


def test_complete_section_retains_prose_tasks_edges_and_citation_proposals():
    inv, working, _, scope, raw = section()
    before = copy.deepcopy(raw)
    schema = m.section_schema(m.writing.section_schema(["U0"], working), scope["expanded"], working["pending_source_cards"])
    jsonschema.validate(raw, schema)
    value, metadata = m.candidate(raw, ["U0"], working, inv, scope)
    assert value["coverage"] == raw["coverage"]
    assert [p["text"] for p in value["paragraphs"]] == [p["text"] for p in raw["paragraphs"]]
    assert value["diagrams"][0]["edges"] == raw["diagrams"][0]["edges"]
    assert metadata["graph_semantics_changed_by_adapter"] is False
    assert raw == before


def test_unconnected_graph_is_rejected_with_only_a_whitelisted_safe_feedback_code():
    inv, working, _, scope, raw = section()
    raw["diagrams"][0]["edges"] = raw["diagrams"][0]["edges"][:1]
    before = copy.deepcopy(raw)
    with pytest.raises(ValueError, match="^requires_numbered_graph_textbook_diagram_unknown_or_unconnected_node$"):
        m.candidate(raw, ["U0"], working, inv, scope)
    assert raw == before


def test_format_never_exposes_unrecognized_error_text_and_restores_globals(monkeypatch):
    inv, working, _, scope, raw = section()

    def fail(*args):
        raise ValueError("Synthetic private validator detail")

    monkeypatch.setattr(m.original, "tagged_candidate", fail)
    with pytest.raises(ValueError, match="^Synthetic private validator detail$"):
        m.candidate(raw, ["U0"], working, inv, scope)
    before = m.fresh.section_schema, m.fresh.citation_candidate, copy.deepcopy(m.writing.composition.PROMPTS)
    with pytest.raises(RuntimeError):
        with m.format_scope():
            assert m.NOTICE in m.writing.composition.PROMPTS[m.writing.author.WRITE_PROMPT]
            raise RuntimeError("Synthetic interruption")
    assert (m.fresh.section_schema, m.fresh.citation_candidate, m.writing.composition.PROMPTS) == before


def test_prospective_writer_uses_actual_numbered_schema_and_retains_parent_planning(tmp_path, monkeypatch):
    job = fake_job(tmp_path, monkeypatch)[0]
    monkeypatch.setattr(m.fresh.BASE_JOB, "key", "a" * 32, raising=False)
    contract = {"writer": "a" * 32, "format": m.binding()}
    atomic_json(tmp_path / m.CONTRACT, contract)
    inv, working, context, scope, raw = section()
    generated = {}

    class Parent(type(job)):
        def stage(self, name, *args, **kwargs):
            assert not name.endswith(("-write", "-repair")), "Old body format must not overwrite the new representation"
            return "original_parent_planning"

    def generate(self, name, prompt, schema, supplied, validate, **kwargs):
        assert m.NOTICE in m.writing.composition.PROMPTS[prompt]
        jsonschema.validate(raw, schema)
        validate(raw)
        generated[name] = raw
        atomic_json(tmp_path / (name + ".json"), {"synthetic": True})
        return raw

    monkeypatch.setattr(m.fresh.BASE_JOB, "stage", generate)
    monkeypatch.setattr(m.fresh.StrictReader, "stage", lambda self, name, *args, **kwargs: copy.deepcopy(generated[name]))
    actual = m.job_type(Parent, contract, read_only=False)(None, inv, working, None)
    value = actual.stage("section-0000-write", m.writing.author.WRITE_PROMPT,
        m.writing.section_schema(["U0"], working), context, lambda row: None, evidence_packet=scope["original"])
    assert value["diagrams"][0]["nodes"][0]["id"] == "n0"
    assert actual.stage("concepts-00-0000", None, {}, {}, None) == "original_parent_planning"
    assert (tmp_path / "section-0000-write-prospective-request.json").exists()


def test_readonly_missing_first_role_cannot_create_receipt_or_infer(tmp_path, monkeypatch):
    job = fake_job(tmp_path, monkeypatch, read_only=True)[0]
    monkeypatch.setattr(m.fresh.BASE_JOB, "key", "a" * 32, raising=False)
    contract = {"writer": "a" * 32, "format": m.binding()}
    atomic_json(tmp_path / m.CONTRACT, contract)
    inv, working, context, scope, _ = section()
    actual = m.job_type(type(job), contract, read_only=True)(None, inv, working, None)
    monkeypatch.setattr(m.fresh.BASE_JOB, "stage", lambda *args, **kwargs: pytest.fail("Unexpected inference"))
    with pytest.raises(ValueError, match="existing_artifact_without_inference"):
        actual.stage("section-0000-write", m.writing.author.WRITE_PROMPT,
            m.writing.section_schema(["U0"], working), context, lambda row: None, evidence_packet=scope["original"])
    assert not (tmp_path / "section-0000-write-prospective-request.json").exists()
