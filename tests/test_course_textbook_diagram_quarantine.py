import json

import pytest

from wechat_decrypt_tool import course_textbook_authoring as author
from wechat_decrypt_tool import course_textbook_composition as composition
from wechat_decrypt_tool import course_textbook_diagram_quarantine as module
from wechat_decrypt_tool import course_textbook_manuscript_continue as continuation
from wechat_decrypt_tool import course_textbook_section_repair as repair
from test_course_textbook_authoring import archive as archive
from test_course_textbook_composition import disputed_archive as disputed_archive
from test_course_textbook_manuscript_continue import (
    exited,
    missing_primary,
    schema_response,
)
from test_course_textbook_provisional_continue import failed_review


def stopped_invalid_diagram(request, monkeypatch, *, invalid_mapping=False):
    root, key, parent, calls, folder = failed_review(request, monkeypatch)
    original = schema_response(composition.AuthoringJob.generate_response)

    def reply(self, messages, *, schema):
        raw, metrics = original(self, messages, schema=schema)
        value = json.loads(raw)
        if messages[0]["content"] == composition.PROMPTS[author.WRITE_PROMPT]:
            missing_primary(value, json.loads(messages[1]["content"]))
            value["diagrams"][0]["edges"][0]["to"] = "unknown"
            if invalid_mapping:
                first = next(iter(value["coverage"].values()))
                first["paragraphs"] = [len(value["paragraphs"]) + 3]
        return json.dumps(value), metrics

    monkeypatch.setattr(composition.AuthoringJob, "generate_response", reply)
    with pytest.raises(ValueError):
        continuation.run(root, key, parent["job"], folder.name, "section-0000")
    exited(folder)
    monkeypatch.setattr(composition.AuthoringJob, "generate_response", original)
    return root, key, parent, calls, folder


def test_invalid_graph_is_retained_privately_without_rewriting_body_or_budget(
    request, monkeypatch
):
    root, key, parent, calls, folder = stopped_invalid_diagram(request, monkeypatch)
    attempts = {p: p.read_bytes() for p in (folder / "attempts").iterdir()}
    original = json.loads(
        json.loads((folder / "attempts/section-0001-write-01.json").read_bytes())[
            "raw_response"
        ]
    )
    result = module.run(root, key, parent["job"], folder.name, "section-0000")
    assert result["state"] == "article_requires_editorial_repair"
    private = repair.saved(folder / "private-article.json")["data"]
    section = next(
        s
        for ch in private["chapters"]
        for s in ch["sections"]
        if s["id"] == "section-0001"
    )
    assert section["paragraphs"] == original["paragraphs"]
    assert section["coverage"] == original["coverage"]
    assert section["diagrams"] == []
    proof = json.loads(
        (folder / "private-diagram-quarantines/section-0001.json").read_bytes()
    )
    assert proof["invalid_diagrams"][0]["graph"] == original["diagrams"][0]
    assert proof["model_calls_performed"] == 0
    assert proof["candidate_accepted"] is False
    assert all(p.read_bytes() == raw for p, raw in attempts.items())
    assert len(list((folder / "attempts").glob("section-0001-write-*.json"))) == 2
    assert not (folder / "reading.json").exists()
    count = len(calls)
    exited(folder)
    assert module.run(root, key, parent["job"], folder.name, "section-0000") == result
    assert len(calls) == count
    assert all(p.read_bytes() == raw for p, raw in attempts.items())


def test_invalid_point_mapping_cannot_be_hidden_by_quarantining_a_graph(
    request, monkeypatch
):
    root, key, parent, calls, folder = stopped_invalid_diagram(
        request, monkeypatch, invalid_mapping=True
    )
    with pytest.raises(ValueError, match="complete_body_with_valid_mapping"):
        module.run(root, key, parent["job"], folder.name, "section-0000")
    assert not (folder / "private-article.json").exists()
    assert not list((folder / "private-diagram-quarantines").glob("section-*.json"))


def test_only_invalid_graphs_are_quarantined_and_valid_graphs_survive():
    graph = {
        "kind": "flow",
        "title": "Fixture",
        "caption": "Fixture relation.",
        "nodes": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
        "edges": [{"from": "a", "to": "b", "label": "Fixture relation"}],
    }
    bad = {
        **graph,
        "edges": [{"from": "a", "to": "unknown", "label": "Fixture relation"}],
    }
    value = {
        "paragraphs": [{"text": "Fixture unchanged."}],
        "coverage": {"p": [0]},
        "diagrams": [graph, bad],
    }
    revised, invalid = module.quarantine_graphs(value)
    assert revised == {**value, "diagrams": [graph]}
    assert invalid[0]["graph"] == bad
    assert value["diagrams"] == [graph, bad]
