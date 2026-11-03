import copy
import json
from types import SimpleNamespace

import jsonschema
import pytest

from wechat_decrypt_tool import course_textbook_gap_plan as m
from wechat_decrypt_tool.course_library import canonical


def inputs():
    graph = {"kind": "flow", "title": "Flow", "caption": "A then B",
             "nodes": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
             "edges": [{"from": "a", "to": "b", "label": "then"}]}
    book = {"format": "course-textbook-reading-v1", "course_id": "a" * 24,
            "title": "Synthetic lesson", "introduction": ["Intro"], "objectives": ["Learn"],
            "prerequisites": [], "chapters": [{"id": "c", "heading": "Chapter", "sections": [
                {"id": "s", "heading": "Section", "paragraphs": ["Existing explanation"],
                 "diagrams": [graph]}]}], "takeaways": ["Recall"], "related_courses": [],
            "quiz": [{"question": "Why?", "answer": "Because", "explanation": "Reason", "criteria": ["Explain"]}]}
    sources = {k: {"kind": "transcript", "text": text} for k, text in {
        "gap1": "First concept and second concept. " * 80,
        "gap2": "A second source for the first concept.",
        "old": "Existing explanation support.", "graph": "A then B support.",
    }.items()}
    coverage = {k: {"teaching": True, "covered": k in {"old", "graph"},
                    "paragraph_ids": ["p000"] if k == "old" else [], "reason": "synthetic"}
                for k in sources}
    # Diagram support is retained by factual closure, independently of prose coverage.
    coverage["graph"] = {"teaching": False, "covered": False, "paragraph_ids": [], "reason": "visual"}
    facts = {"items": {"p000": {"passed": True, "source_ids": ["old"], "reason": "ok"},
                       "d00": {"passed": True, "source_ids": ["graph"], "reason": "ok"},
                       "q00": {"passed": False, "source_ids": ["old"], "reason": "incomplete"}}}
    problems = {"reading": {}, "facts": {"q00": facts["items"]["q00"]},
                "missing_sources": {k: coverage[k] for k in ["gap1", "gap2"]}}
    body, locations, diagrams = m.body_index(book)
    return {"parent": "b" * 32, "book": book, "sources": sources,
            "audit": {"article_sha256": m.sha(canonical(book)), "facts": facts,
                      "source_coverage": coverage, "reading": {}, "problems": problems},
            "body": body, "locations": locations, "diagrams": diagrams,
            "bindings": {"test": "bound"}, "model_digest": "local-model"}


def topics():
    return {k: {"heading": k, "purpose": "Explain " + k,
                "placement": {"existing_section": "s"}} for k in ["t0", "t1"]}


def decisions():
    return {"gap1": {"kind": "gap", "parts": [
                {"topic": "t0", "knowledge": "concept one"},
                {"topic": "t1", "knowledge": "concept two"}]},
            "gap2": {"kind": "gap", "parts": [{"topic": "t0", "knowledge": "example"}]}}


def test_complete_evidence_and_multi_topic_merge_keep_old_diagram_support():
    value = inputs()
    before = copy.deepcopy(value)
    result = m.compile_plan(value, topics(), decisions())
    section = next(u for u in result["units"] if "section" in u["target"])
    assert len(section["gap_sources"]) == 3
    assert section["source_ids"] == ["gap1", "gap2", "graph", "old"]
    assert section["complete_sources"]["gap1"]["text"] == value["sources"]["gap1"]["text"]
    assert section["existing_section"]["diagrams"]
    assert len(result["units"]) == 2
    assert result["all_prior_negative_reviews"] == value["audit"]["problems"]
    assert not result["original_gaps_resolved_by_plan"]
    assert not result["reader_publication_allowed"]
    assert value == before


def test_existing_and_excluded_sources_remain_pending_and_invalid_ids_fail():
    rows = {"gap1": {"kind": "existing", "paragraph_ids": ["p000"], "reason": "Claim"},
            "gap2": {"kind": "non_teaching", "reason": "Claim"}}
    result = m.compile_plan(inputs(), topics(), rows)
    assert result["pending_exclusion_or_existing_claim_reviews"] == rows
    rows["gap1"]["paragraph_ids"] = ["invented"]
    with pytest.raises(jsonschema.ValidationError):
        m.compile_plan(inputs(), topics(), rows)
    rows = decisions()
    rows["gap1"]["parts"][0]["topic"] = "invented"
    with pytest.raises(jsonschema.ValidationError):
        m.compile_plan(inputs(), topics(), rows)
    with pytest.raises(ValueError, match="every_prior_gap"):
        m.compile_plan(inputs(), topics(), {})


def write_parent(root):
    value = inputs()
    recipe = {"packet": {"sources": value["sources"]}, "model_digest": value["model_digest"]}
    key = m.sha(canonical(recipe))[:32]
    folder = root / "wiki/whole-lesson-repair-jobs" / key
    folder.mkdir(parents=True)
    for name, data in {"progress.json": {"stage": "complete", "pid": 9999999, "created": 0},
                       "manifest.json": {"id": key, "recipe_sha256": m.sha(canonical(recipe)), "recipe": recipe},
                       "corrected-reading.json": value["book"], "revised-audit.json": value["audit"],
                       "result.json": {"article_sha256": m.sha(canonical(value["book"]))}}.items():
        m.atomic_json(folder / name, data)
    return key, folder


def test_parent_must_be_exited_complete_and_audit_exact(tmp_path, monkeypatch):
    key, folder = write_parent(tmp_path)
    monkeypatch.setattr(m, "identity_live", lambda _: False)
    assert len(m.parent_inputs(tmp_path, key)["sources"]) == 4
    monkeypatch.setattr(m, "identity_live", lambda _: True)
    with pytest.raises(ValueError, match="parent_finished_and_exited"):
        m.parent_inputs(tmp_path, key)
    monkeypatch.setattr(m, "identity_live", lambda _: False)
    audit = m.read(folder / "revised-audit.json")
    audit["problems"]["missing_sources"] = {}
    m.atomic_json(folder / "revised-audit.json", audit)
    with pytest.raises(ValueError, match="negative_reviews_preserved"):
        m.parent_inputs(tmp_path, key)


def test_capacity_refusal_precedes_attempt_or_inference():
    job = m.GapPlanJob.__new__(m.GapPlanJob)
    job.tokenizer = SimpleNamespace(encode=lambda text: SimpleNamespace(ids=range(70000)))
    job.range_notice = ""
    with pytest.raises(ValueError, match="complete_context_fits"):
        job.stage("topic-proposal", "System", {}, {"sources": "Full input"}, lambda _: None)


def test_parent_binding_blocks_fresh_budget_before_model(tmp_path, monkeypatch):
    key, folder = write_parent(tmp_path)
    monkeypatch.setattr(m, "identity_live", lambda _: False)
    monkeypatch.setattr(m, "TextbookStore", lambda root: SimpleNamespace(root=root, checkpoint=lambda: None))
    monkeypatch.setattr(m, "local_model_digest", lambda _: "local-model")
    called = []
    class Job:
        def __init__(self, store, *args, **kwargs):
            self.key = "c" * 32
            self.root = tmp_path / "job"
            self.root.mkdir()
            m.atomic_json(self.root / "manifest.json", {"bound": True})
        def stage(self, *args, **kwargs):
            called.append(True)
    monkeypatch.setattr(m, "GapPlanJob", Job)
    m.atomic_json(folder / "gap-plan-binding.json", {"job": "previous-budget"})
    with pytest.raises(ValueError, match="existing_parent_plan_budget_binding"):
        m.run_plan(tmp_path, key, tmp_path / "unused-tokenizer")
    assert called == []
