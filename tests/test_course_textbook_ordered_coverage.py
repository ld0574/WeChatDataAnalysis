import copy
import json
from types import SimpleNamespace

import pytest

from wechat_decrypt_tool import course_textbook_ordered_coverage as m
from wechat_decrypt_tool.course_knowledge import atomic_json
from test_course_textbook_whole_coverage import real_stage_job
from test_course_textbook_whole_coverage_assignments import wire_fixture


def ordered_fixture():
    units, ids, expected, wire = wire_fixture()
    for row in wire["source_groups"].values():
        row["assignments"] = list(row["assignments"].values())
    return units, ids, expected, wire


def test_ordered_array_transcription_keeps_all_original_members_and_judgments():
    units, ids, expected, raw = ordered_fixture()
    before = copy.deepcopy(raw)
    assert m.compile_wire(raw, ids, ["A0"], units) == expected
    assert raw == before
    order = m.assignments.by_source(ids, units)
    assert [cid for keys in order.values() for cid in keys] == ids
    schema = m.wire_schema(ids, ["A0"], units)
    for ref, keys in order.items():
        array = schema["properties"]["source_groups"]["properties"][ref]["properties"]["assignments"]
        assert array["minItems"] == array["maxItems"] == len(keys)


@pytest.mark.parametrize("change", ["short", "long", "source", "unused_group", "noncontiguous", "out_of_range", "invented_article", "string", "object"])
def test_missing_or_reordered_members_and_invented_destinations_are_rejected(change):
    units, ids, _, raw = ordered_fixture()
    ref = next(iter(raw["source_groups"]))
    row = raw["source_groups"][ref]
    if change == "short":
        row["assignments"].pop()
    elif change == "long":
        row["assignments"].append(0)
    elif change == "source":
        raw["source_groups"].pop(ref)
    elif change in {"unused_group", "noncontiguous"}:
        row["groups"].append(copy.deepcopy(row["groups"][0]))
        if change == "noncontiguous":
            row["assignments"][1] = 1
    elif change == "out_of_range":
        row["assignments"][0] = 99
    elif change == "invented_article":
        row["groups"][0]["article_items"] = ["unknown"]
    elif change == "string":
        row["assignments"][0] = "0"
    else:
        row["assignments"] = {cid: 0 for cid in ids[:3]}
    with pytest.raises((ValueError, m.coverage.jsonschema.ValidationError)):
        m.compile_wire(raw, ids, ["A0"], units)


def test_256_decisions_keep_exact_source_order_without_repeating_ids_in_model_output():
    inventory = {"sources": {"O00000": {"text": " ".join(f"Term {i}," for i in range(256))}}}
    units = m.coverage.source_units(inventory)
    ids = list(units)
    raw = {"source_groups": {"O00000": {"assignments": [0] * len(ids),
        "groups": [{"kind": "missing", "article_items": [], "reason": "Synthetic unrepresented teaching"}]}}}
    groups = m.compile_wire(raw, ids, ["A0"], units)
    assert groups["groups"][0]["clauses"] == ids
    assert all(cid not in json.dumps(raw) for cid in ids)
    assert "".join(inventory["sources"]["O00000"]["text"][u["start"]:u["end"]] for u in units.values()) == inventory["sources"]["O00000"]["text"]


def stage_fixture(tmp_path, monkeypatch, *, malformed=False, negative=False):
    job, inventory, plan, items, calls = real_stage_job(tmp_path, monkeypatch)
    job.__class__ = m.OrderedJob
    job.authenticated_stages = set()

    def generate(messages, *, schema):
        context = json.loads(messages[1]["content"])
        calls.append((job.model, copy.deepcopy(context)))
        if malformed:
            value = {}
        elif job.model == "course-defiant:q4":
            assert context["sources"]["O00000"]["text"] == inventory["sources"]["O00000"]["text"]
            assert list(context["article"]) == list(items)
            assert context["ordered_clause_ids"] == m.assignments.by_source(list(context["clauses"]), plan["units"])
            value = {"source_groups": {ref: {"assignments": [0] * len(keys), "groups": [{"kind": "covered",
                "article_items": list(context["article"]), "reason": "Synthetic complete support"}]}
                for ref, keys in context["ordered_clause_ids"].items()}}
        else:
            assert not context["candidate_encoding"]["is_factual_evidence"]
            value = {"groups": {gid: {"passed": not negative, "reason": "Synthetic independent complete check"}
                for gid in context["groups"]}}
        raw = json.dumps(value)
        metrics = {"model_digest": job.model_digest, "done_reason": "stop", "size_vram": 1,
            "requested_context": 65536, "measured_context": 65536, "expected_content_tokens": 20,
            "prompt_eval_count": 20, "reserved_output": 4096}
        atomic_json(job.root / f"response-{len(calls)}.json", {"stage": job.current, "request_sha256": m.digest(messages),
            "schema_sha256": m.digest(schema), "response": {"message": {"content": raw}}, "metrics": metrics})
        return raw, metrics

    job.generate_response = generate
    return job, inventory, plan, items, calls


def test_real_stage_uses_two_weights_complete_sources_and_exact_schema_then_replays_without_inference(tmp_path, monkeypatch):
    job, inventory, plan, items, calls = stage_fixture(tmp_path, monkeypatch)
    prompts = m.coverage.REVIEW, m.coverage.VERIFY
    result = m.coverage.review_batch(job, inventory, plan, items, plan["batches"][0], 0)
    assert [model for model, _ in calls] == ["course-defiant:q4", "qwen3.5:9b"]
    assert all(s["resolved"] for s in result.values())
    assert (m.coverage.REVIEW, m.coverage.VERIFY) == prompts
    path = job.root / "batch-0000-body-0000-review-ordered-derivation.json"
    artifact = m.whole.read(path)
    assert artifact["ordered_clause_ids_sha256"] == m.digest(calls[0][1]["ordered_clause_ids"])
    folder = job.root
    del job.slot, job.generate_response
    job.__class__ = m.ReadOnlyJob
    job.expected_root, job._root, job.authenticated_stages = folder.resolve(), folder.resolve(), set()
    before = {p: p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    assert m.coverage.review_batch(job, inventory, plan, items, plan["batches"][0], 0) == result
    assert len(calls) == 2 and len(job.authenticated_stages) == 2
    assert before == {p: p.read_bytes() for p in folder.rglob("*") if p.is_file()}


@pytest.mark.parametrize("change", ["schema", "order_proof", "missing_cache"])
def test_cached_wire_and_order_tampering_or_missing_stage_never_generates_again(tmp_path, monkeypatch, change):
    job, inventory, plan, items, calls = stage_fixture(tmp_path, monkeypatch)
    m.coverage.review_batch(job, inventory, plan, items, plan["batches"][0], 0)
    folder = job.root
    if change == "missing_cache":
        (folder / "batch-0000-body-0000-review.json").unlink()
    else:
        path = folder / ("response-1.json" if change == "schema" else "batch-0000-body-0000-review-ordered-derivation.json")
        value = m.whole.read(path)
        value["schema_sha256" if change == "schema" else "ordered_clause_ids_sha256"] = "f" * 64
        atomic_json(path, value)
    del job.slot, job.generate_response
    job.__class__ = m.ReadOnlyJob
    job.expected_root, job._root, job.authenticated_stages = folder.resolve(), folder.resolve(), set()
    with pytest.raises(ValueError):
        m.coverage.review_batch(job, inventory, plan, items, plan["batches"][0], 0)
    assert len(calls) == 2


def test_negative_independent_judgments_leave_all_proposed_teaching_unresolved(tmp_path, monkeypatch):
    job, inventory, plan, items, calls = stage_fixture(tmp_path, monkeypatch, negative=True)
    result = m.coverage.review_batch(job, inventory, plan, items, plan["batches"][0], 0)
    assert all(not s["resolved"] and s["teaching_detected"] for s in result.values())
    assert len(calls) == 2


def test_two_original_attempts_are_not_refreshed_after_failure(tmp_path, monkeypatch):
    job, inventory, plan, items, calls = stage_fixture(tmp_path, monkeypatch, malformed=True)
    for _ in range(2):
        with pytest.raises(ValueError, match="stage_attempt_budget_exhausted"):
            m.coverage.review_batch(job, inventory, plan, items, plan["batches"][0], 0)
        assert len(calls) == 2


def test_existing_parent_coverage_contract_blocks_a_new_budget_before_parent_load(tmp_path, monkeypatch):
    key = "a" * 32
    path = tmp_path / "wiki/textbook-whole-authoring-jobs" / key / m.coverage.CONTRACT
    path.parent.mkdir(parents=True)
    atomic_json(path, {"synthetic": "existing different coverage"})
    monkeypatch.setattr(m.private_guard, "checkpoint", lambda *args: None)
    monkeypatch.setattr(m, "authenticated_parent", lambda *args: pytest.fail("Existing budget must stop before parent/model initialization"))
    with pytest.raises(ValueError, match="parent_has_no_previous_coverage_budget"):
        m.prepare(tmp_path, key, tmp_path / "tokenizer.json")


@pytest.mark.parametrize("change", ["failed", "paused", "live"])
def test_tail_requires_the_actual_successful_exited_pilot_guard(tmp_path, monkeypatch, change):
    key = "a" * 32
    path = tmp_path / "wiki/ordered-coverage-dispatch" / key / "pilot/guard-progress.json"
    path.parent.mkdir(parents=True)
    atomic_json(path, {"state": "paused" if change == "paused" else "worker_exited", "returncode": 1 if change == "failed" else 0})
    monkeypatch.setattr(m.coverage.writing, "identity_live", lambda *args: change == "live")
    with pytest.raises(ValueError, match="actual_successful_exited_pilot_guard"):
        m.prior_pilot_guard(tmp_path, key)


def test_parent_provider_rejects_a_partial_manuscript(monkeypatch):
    monkeypatch.setattr(m.parent, "reconstruct", lambda *args, **kwargs: {"private": None,
        "summary": {"verified_units": 1, "total_units": 14}})
    with pytest.raises(ValueError, match="actual_complete_parent_not_prefix"):
        m.authenticated_parent(None, None, None)


def test_pilot_counts_preserve_negative_unresolved_results_without_publishing():
    states = {"C0": {"resolved": True, "kind": "covered"}, "C1": {"resolved": False, "kind": "unresolved"}}
    result = m.pilot_result({"batches": [{}] * 50}, SimpleNamespace(key="a" * 32), states, 3)
    assert result["total_batches"] == 50 and result["completed_batches"] == 3
    assert result["covered_clauses"] == 1 and result["unresolved_clauses"] == 1
    assert not result["reader_publication_allowed"]
