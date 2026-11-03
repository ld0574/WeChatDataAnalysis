import copy
import json
from types import SimpleNamespace

import jsonschema
import pytest

from wechat_decrypt_tool import course_textbook_gap_write as w
from wechat_decrypt_tool.course_library import canonical
from test_course_textbook_gap_plan import decisions, inputs, topics, write_parent


def packet():
    value = inputs()
    plan = w.planmod.compile_plan(value, topics(), decisions())
    return value, plan


def candidate(unit):
    quiz = {**copy.deepcopy(inputs()["book"]["quiz"][0]), "source_keys": [unit["source_ids"][0]]}
    if "self_test" in unit["target"]:
        return {"self_test": quiz}
    return {"paragraphs": [{"text": ("Synthetic complete explanation. " * 5) + str(i),
                            "source_keys": [unit["source_ids"][0]]} for i in range(2)],
            "diagrams": [], "self_test": quiz}


def review(value, unit, passed=True):
    return {"facts": {k: {"passed": passed, "reason": "Synthetic judgment",
                          "source_keys": [unit["source_ids"][0]]} for k in w.claims_of(value)},
            "coverage": {p["point_id"]: {"passed": passed, "reason": "Synthetic judgment",
                                         "paragraph_indices": [0] if passed else []} for p in unit["gap_sources"]},
            "retention": {f"old{i:03d}": {"passed": passed, "reason": "Synthetic judgment"}
                          for i in range(len(unit.get("existing_section", {}).get("paragraphs", [])))}}


def test_assembly_replaces_only_target_and_strips_private_references():
    value, plan = packet()
    original = copy.deepcopy(value)
    candidates = {u["unit"]: candidate(u) for u in plan["units"]}
    book = w.assemble(value, plan, candidates)
    assert len(book["chapters"][0]["sections"][0]["paragraphs"]) == 2
    assert len(book["quiz"]) == 2
    assert book["introduction"] == value["book"]["introduction"]
    assert book["related_courses"] == value["book"]["related_courses"]
    assert "source_keys" not in json.dumps(book)
    assert "complete_sources" not in json.dumps(book)
    assert value == original
    with pytest.raises(ValueError, match="every_unit_written"):
        w.assemble(value, plan, {})


def test_new_chapter_inserts_at_actual_dependency_position():
    value, _ = packet()
    groups = topics()
    groups["t1"]["placement"] = {"after_chapter": "start"}
    plan = w.planmod.compile_plan(value, groups, decisions())
    book = w.assemble(value, plan, {u["unit"]: candidate(u) for u in plan["units"]})
    assert book["chapters"][0]["id"].startswith("addition-")
    assert book["chapters"][1]["id"] == "c"


def test_positive_review_requires_real_support_and_actual_paragraphs():
    _, plan = packet()
    unit = plan["units"][0]
    value = candidate(unit)
    result = review(value, unit)
    w.validate_review(result, value, unit)
    result["facts"]["p000"]["source_keys"] = []
    with pytest.raises(ValueError, match="positive_fact_actual_support"):
        w.validate_review(result, value, unit)
    result = review(value, unit)
    next(iter(result["coverage"].values()))["paragraph_indices"] = []
    with pytest.raises(ValueError, match="positive_coverage_actual_paragraph"):
        w.validate_review(result, value, unit)
    result = review(value, unit)
    next(iter(result["coverage"].values()))["paragraph_indices"] = [200]
    with pytest.raises(jsonschema.ValidationError):
        w.validate_review(result, value, unit)


def test_bounded_semantic_failure_saves_private_draft_without_acceptance(tmp_path):
    value, plan = packet()
    unit = plan["units"][0]
    calls = []
    class Job:
        root = tmp_path
        inputs = value
        def stage(self, name, prompt, schema, context, validate, **kwargs):
            calls.append(name)
            reply = review(candidate(unit), unit, passed=False) if "review" in name else candidate(unit)
            validate(reply)
            return reply
    _, proof = w.write_unit(Job(), unit)
    assert len(calls) == 4
    assert calls[-1].endswith("review-1")
    assert not proof["checks_passed"]
    assert not proof["reader_publication_allowed"]
    assert w.read(tmp_path / (unit["unit"] + "-private-result.json")) == proof


def test_full_job_keeps_negative_result_private(tmp_path, monkeypatch):
    value, plan = packet()
    monkeypatch.setattr(w, "TextbookStore", lambda root: SimpleNamespace(root=root, checkpoint=lambda: None))
    monkeypatch.setattr(w, "load_plan", lambda *args: (value, plan, {"bound": True}))
    monkeypatch.setattr(w, "local_model_digest", lambda _: value["model_digest"])
    class Job:
        def __init__(self, *args):
            self.root = tmp_path / "job"
            self.root.mkdir(exist_ok=True)
            self.key = "c" * 32
            self.inputs = value
            w.atomic_json(self.root / "manifest.json", {"test": "bound"})
        def progress(self, *args, **kwargs):
            pass
        def stage(self, name, prompt, schema, context, validate, **kwargs):
            if name.startswith("whole-sources"):
                reply = {"sources": {k: {"kind": "missing", "reason": "Synthetic remaining gap"}
                                     for k in context["sources"]}}
            elif name == "whole-reading":
                reply = {k: {"passed": True, "reason": "Synthetic"} for k in schema["properties"]}
            else:
                unit = next(u for u in plan["units"] if name.startswith(u["unit"]))
                reply = review(candidate(unit), unit) if "review" in name else candidate(unit)
            validate(reply)
            return reply
    monkeypatch.setattr(w, "WritingJob", Job)
    parent = tmp_path / "wiki/textbook-gap-plan-jobs" / ("d" * 32)
    parent.mkdir(parents=True)
    result = w.run(tmp_path, "d" * 32, tmp_path / "tokenizer")
    assert result["state"] == "expanded_draft_requires_changes"
    assert result["all_sources_checked"] == 4 and result["missing_sources"] == 4
    assert not result["reader_publication_allowed"] and not result["goal_complete"]
    assert (tmp_path / "job/expanded-reading.json").is_file()
    assert not (tmp_path / "wiki/whole-lesson-narrative-20261104/current-reading-projection.json").exists()


def test_changed_plan_is_rejected_without_inference(tmp_path, monkeypatch):
    key, parent = write_parent(tmp_path)
    monkeypatch.setattr(w.planmod, "identity_live", lambda _: False)
    value = w.planmod.parent_inputs(tmp_path, key)
    groups = {f"t{i:03d}": v for i, v in enumerate(topics().values())}
    rows = decisions()
    for row in rows.values():
        for point in row["parts"]:
            point["topic"] = {"t0": "t000", "t1": "t001"}[point["topic"]]
    plan = w.planmod.compile_plan(value, groups, rows)
    recipe = {"test": "immutable"}
    plan_key = w.sha(canonical(recipe))[:32]
    folder = tmp_path / "wiki/textbook-gap-plan-jobs" / plan_key
    folder.mkdir(parents=True)
    for name, data in {"manifest.json": {"id": plan_key, "recipe": recipe},
        "progress.json": {"stage": "complete"}, "result.json": {"state": "private_knowledge_gap_plan_ready", "parent": key},
        "private-authoring-plan.json": plan, "topic-proposal.json": {"data": {"topics": list(groups.values())}}}.items():
        w.atomic_json(folder / name, data)
    assert w.load_plan(tmp_path, plan_key)[1] == plan
    plan["units"][0]["complete_sources"]["gap1"]["text"] = "Tampered"
    w.atomic_json(folder / "private-authoring-plan.json", plan)
    with pytest.raises(ValueError, match="reproducible_plan"):
        w.load_plan(tmp_path, plan_key)
