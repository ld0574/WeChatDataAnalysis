import copy
import json

import pytest

from wechat_decrypt_tool import course_textbook_whole_unit_repair as m
from test_course_textbook_visible_context import fixture, history


def verdict(schema, *, passed=True):
    props = schema["properties"]
    if "passed" in props:
        return {"passed": passed, "reason": "Synthetic independent judgment"}
    return {key: verdict(value, passed=passed) for key, value in props.items()}


@pytest.mark.parametrize("initial_passed,final_passed", [(True, True), (False, True), (False, False)])
def test_three_original_roles_include_mandatory_repair_and_keep_actual_judgments(initial_passed, final_passed):
    inventory, working, packet, context, original = fixture()
    original["quiz"]["citations"] = ["O00004"]
    before = copy.deepcopy(original)
    scope = m.visible.visible_scope(["U0"], working, inventory, context, packet)
    repaired = copy.deepcopy(original)
    repaired["paragraphs"][0]["citations"] = ["O00001", "O00002"]
    repaired["quiz"]["citations"] = ["O00002"]
    calls = []

    class Job:
        def stage(self, name, prompt, schema, actual_context, validate, **kwargs):
            calls.append(name)
            assert kwargs["evidence_packet"] == scope["expanded"]
            assert actual_context["sources"] == context["sources"]
            assert actual_context["context_sources"] == context["context_sources"]
            if name.endswith("-repair"):
                assert actual_context["candidate"] == before
                assert all(m.writing.author.verdicts(actual_context["review"])) == initial_passed
                value = repaired
            else:
                value = verdict(schema, passed=final_passed if name.endswith("-repair-review") else initial_passed)
            validate(value)
            return copy.deepcopy(value)

    candidate, initial, final = m.review_and_repair(Job(), "section-0007", working, inventory, context, scope, original,
        [{"kind": "citation_outside_complete_assigned_sources"}])
    assert calls == ["section-0007-review", "section-0007-repair", "section-0007-repair-review"]
    assert candidate == repaired and original == before
    assert all(m.writing.author.verdicts(initial)) == initial_passed
    assert all(m.writing.author.verdicts(final)) == final_passed
    assert m.visible.writing.composition.original_packet is m.visible.ORIGINAL_PACKET


def test_repair_cannot_retain_citations_outside_the_actual_complete_input():
    inventory, working, packet, context, original = fixture()
    original["quiz"]["citations"] = ["O00004"]
    scope = m.visible.visible_scope(["U0"], working, inventory, context, packet)
    calls = []

    class Job:
        def stage(self, name, prompt, schema, actual_context, validate, **kwargs):
            calls.append(name)
            value = original if name.endswith("-repair") else verdict(schema)
            validate(value)
            return value

    with pytest.raises(m.jsonschema.ValidationError):
        m.review_and_repair(Job(), "section-0007", working, inventory, context, scope, original, [])
    assert calls == ["section-0007-review", "section-0007-repair"]
    assert m.visible.writing.composition.original_packet is m.visible.ORIGINAL_PACKET


def history_args(tmp_path, monkeypatch):
    args = history(tmp_path, monkeypatch)
    job, name, schema, context, packet, working, inventory = args
    for path in (tmp_path / "attempts").glob("*.json"):
        value = json.loads(path.read_bytes())
        value["model_metrics"] = {"model_digest": job.models["qwen3.5:9b"]}
        path.write_text(json.dumps(value), "utf-8")
    return job, name.removesuffix("-write"), schema, context, packet, inventory


def test_original_failed_candidate_and_all_requests_are_preserved(tmp_path, monkeypatch):
    args = history_args(tmp_path, monkeypatch)
    before = {p: p.read_bytes() for p in (tmp_path / "attempts").iterdir()}
    value, proof = m.original_candidate(*args)
    raw = json.loads(json.loads((tmp_path / "attempts/section-0005-write-01.json").read_bytes())["raw_response"])
    assert value == raw and proof["selected_attempt"] == 1
    assert proof["candidate_sha256"] == m.digest(raw)
    assert all(p.read_bytes() == data for p, data in before.items())
    assert not (tmp_path / "section-0005-write.json").exists()


@pytest.mark.parametrize("changed", ["request", "input", "model", "accepted", "missing"])
def test_changed_original_identity_or_budget_cannot_get_new_roles(tmp_path, monkeypatch, changed):
    args = history_args(tmp_path, monkeypatch)
    path = tmp_path / "attempts/section-0005-write-01.json"
    if changed == "missing":
        path.unlink()
    else:
        value = json.loads(path.read_bytes())
        field = {"request": "request_sha256", "input": "input_sha256", "model": "model_digest", "accepted": "accepted_structure"}[changed]
        value[field] = True if changed == "accepted" else "changed"
        path.write_text(json.dumps(value), "utf-8")
    with pytest.raises(ValueError):
        m.original_candidate(*args)
