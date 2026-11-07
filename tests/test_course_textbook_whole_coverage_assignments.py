import copy
from contextlib import contextmanager
import json
from types import SimpleNamespace

import pytest

from wechat_decrypt_tool import course_textbook_whole_coverage_assignments as m
from wechat_decrypt_tool.course_knowledge import atomic_json
from test_course_textbook_whole_coverage import group_fixture, new_states, real_stage_job


def wire_fixture():
    units, ids, value = group_fixture()
    wire = {"source_groups": {}}
    for ref, keys in m.by_source(ids, units).items():
        original = next(row for row in value["groups"] if keys[0] in row["clauses"])
        wire["source_groups"][ref] = {"assignments": {cid: 0 for cid in keys},
            "groups": [{k: v for k, v in original.items() if k != "clauses"}]}
    return units, ids, value, wire


def test_required_decoding_assignments_preserve_every_actual_clause_and_group_judgment():
    units, ids, value, wire = wire_fixture()
    before = copy.deepcopy(wire)
    assert m.compile_wire(wire, ids, ["A0"], units) == value
    assert wire == before
    for ref, keys in m.by_source(ids, units).items():
        for cid in keys:
            incomplete = copy.deepcopy(wire)
            incomplete["source_groups"][ref]["assignments"].pop(cid)
            with pytest.raises(m.coverage.jsonschema.ValidationError):
                m.compile_wire(incomplete, ids, ["A0"], units)


@pytest.mark.parametrize("change", ["source", "unknown_clause", "out_of_range", "unused_group", "noncontiguous", "invented_body"])
def test_assignments_reject_missing_sources_wrong_groups_or_body_ids(change):
    units, ids, _, wire = wire_fixture()
    ref = next(iter(wire["source_groups"]))
    row = wire["source_groups"][ref]
    if change == "source":
        wire["source_groups"].pop(ref)
    elif change == "unknown_clause":
        row["assignments"]["C9999999"] = 0
    elif change == "out_of_range":
        row["assignments"][ids[0]] = 99
    elif change == "invented_body":
        row["groups"][0]["article_items"] = ["unknown"]
    else:
        row["groups"].append(copy.deepcopy(row["groups"][0]))
        if change == "noncontiguous":
            row["assignments"][ids[1]] = 1
    with pytest.raises((ValueError, m.coverage.jsonschema.ValidationError)):
        m.compile_wire(wire, ids, ["A0"], units)


def test_partial_recovery_splits_without_changing_any_existing_member_judgment():
    units, ids, value = group_fixture()
    partial = {"groups": [{**value["groups"][0], "clauses": [ids[0], ids[2], ids[3]]}]}
    original = copy.deepcopy(partial)
    candidate, receipt = m.complete_partial(partial, ids, ["A0"], units)
    assert partial == original
    m.coverage.validate_groups(candidate, ids, ["A0"], units)
    for row in candidate["groups"]:
        if ids[1] in row["clauses"]:
            assert row["kind"] == "missing" and row["article_items"] == []
        else:
            assert {k: v for k, v in row.items() if k != "clauses"} == {k: v for k, v in partial["groups"][0].items() if k != "clauses"}
    assert receipt["program_unassessed_clauses"] == [ids[1]]
    assert receipt["original_members"] == 3 and not receipt["omissions_count_as_covered"]
    states = new_states(ids)
    groups = {f"G{i}": row for i, row in enumerate(candidate["groups"])}
    audit = {"groups": {key: {"passed": True, "reason": "Synthetic independent judgment"} for key in groups}}
    m.coverage.merge_assessments(states, groups, audit, "original-independent-role")
    assert not states[ids[1]]["resolved"] and states[ids[1]]["kind"] == "missing"
    assert states[ids[1]]["teaching_detected"]


def failed_fixture(tmp_path):
    units, ids, value = group_fixture()
    partial = {"groups": [{**value["groups"][0], "clauses": [ids[0], ids[2], ids[3]]}]}
    context = {"clauses": {cid: {"reference": units[cid]["reference"], "text": "Synthetic complete clause"} for cid in ids},
        "article": {"A0": {"content": "Synthetic full teaching body"}}}
    packet = {"sources": {"O00000": {"text": "Synthetic original"}}}
    schema = m.coverage.schema_for(ids, ["A0"])
    binding = "d" * 64
    job = SimpleNamespace(root=tmp_path, plan={"units": units}, models={"course-defiant:q4": "b" * 64},
        stage_input=lambda *a, **k: (schema, binding))
    (tmp_path / "attempts").mkdir()
    previous = ""
    for number in (1, 2):
        messages = [{"role": "system", "content": m.coverage.REVIEW}, {"role": "user", "content": json.dumps(
            {**context, "previous_validation_failure": previous}, ensure_ascii=False)}]
        error = "requires_whole_coverage_every_target_clause_exactly_once" if number == 1 else "requires_gap_plan_complete_model_output"
        raw = json.dumps(partial) if number == 1 else "{unfinished"
        metrics = {"model_digest": "b" * 64, "done_reason": "stop" if number == 1 else "length", "size_vram": 1,
            "requested_context": 65536, "measured_context": 65536, "expected_content_tokens": 20,
            "prompt_eval_count": 20, "reserved_output": 4096}
        row = {"stage": m.FIRST, "attempt": number, "input_sha256": binding, "model_digest": "b" * 64,
            "request_sha256": m.digest(messages), "raw_response": raw if number == 1 else "",
            "validation_error": error, "model_metrics": metrics if number == 1 else {}, "accepted_structure": False}
        atomic_json(tmp_path / "attempts" / f"{m.FIRST}-{number:02d}.json", row)
        atomic_json(tmp_path / f"response-{number}.json", {"stage": m.FIRST, "request_sha256": row["request_sha256"],
            "schema_sha256": m.digest(schema), "response": {"message": {"content": raw}}, "metrics": metrics})
        previous = error
    return job, context, packet


def test_first_recovery_authenticates_both_spent_calls_without_creating_success_cache(tmp_path):
    job, context, packet = failed_fixture(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    artifact = m.first_artifact(job, context, packet)
    assert artifact["receipt"]["original_members"] == 3
    assert len(artifact["receipt"]["program_unassessed_clauses"]) == 1
    assert artifact["receipt"]["new_proposal_model_calls"] == 0
    assert not (tmp_path / (m.FIRST + ".json")).exists()
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*.json")}


@pytest.mark.parametrize("change", ["request", "schema", "model", "input", "budget", "fake_cache"])
def test_changed_original_history_cannot_be_recovered(tmp_path, change):
    job, context, packet = failed_fixture(tmp_path)
    if change == "budget":
        (tmp_path / "attempts" / (m.FIRST + "-02.json")).unlink()
    elif change == "fake_cache":
        atomic_json(tmp_path / (m.FIRST + ".json"), {})
    else:
        path = tmp_path / ("response-1.json" if change in {"schema", "model"} else "attempts/" + m.FIRST + "-01.json")
        value = m.whole.read(path)
        if change == "model":
            value["metrics"]["model_digest"] = "f" * 64
        else:
            field = {"request": "request_sha256", "schema": "schema_sha256", "input": "input_sha256"}[change]
            value[field] = "f" * 64
        atomic_json(path, value)
    with pytest.raises(ValueError):
        m.first_artifact(job, context, packet)


def stage_fixture(tmp_path, monkeypatch, *, malformed=False):
    job, inventory, plan, items, calls = real_stage_job(tmp_path, monkeypatch)
    job.__class__ = m.AssignmentJob

    @contextmanager
    def slot(name):
        job.progress("generating", name)
        yield

    def generate(messages, *, schema):
        context = json.loads(messages[1]["content"])
        calls.append((job.model, copy.deepcopy(context)))
        if malformed:
            value = {}
        elif job.model == "course-defiant:q4":
            value = {"source_groups": {ref: {"assignments": {cid: 0 for cid in keys}, "groups": [{"kind": "covered",
                "article_items": list(context["article"]), "reason": "Synthetic complete source judgment"}]}
                for ref, keys in m.by_source(list(context["clauses"]), plan["units"]).items()}}
        else:
            assert context["candidate_derivation"]["is_factual_evidence"] is False
            value = {"groups": {key: {"passed": True, "reason": "Synthetic independent judgment"} for key in context["groups"]}}
        raw = json.dumps(value)
        metrics = {"model_digest": job.model_digest, "done_reason": "stop", "size_vram": 1,
            "requested_context": 65536, "measured_context": 65536, "expected_content_tokens": 20,
            "prompt_eval_count": 20, "reserved_output": 4096}
        atomic_json(job.root / f"response-{len(calls)}.json", {"stage": job.current, "request_sha256": m.digest(messages),
            "schema_sha256": m.digest(schema), "response": {"message": {"content": raw}}, "metrics": metrics})
        return raw, metrics

    job.slot, job.generate_response = slot, generate
    return job, inventory, plan, items, calls


def test_future_original_roles_use_actual_required_schema_and_independent_model_then_replay_readonly(tmp_path, monkeypatch):
    job, inventory, plan, items, calls = stage_fixture(tmp_path, monkeypatch)
    old_prompts = m.coverage.REVIEW, m.coverage.VERIFY
    result = m.coverage.review_batch(job, inventory, plan, items, plan["batches"][0], 1)
    assert all(row["resolved"] for row in result.values())
    assert [model for model, _ in calls] == ["course-defiant:q4", "qwen3.5:9b"]
    assert (m.coverage.REVIEW, m.coverage.VERIFY) == old_prompts
    name = "batch-0001-body-0000-review"
    assert "source_groups" in m.whole.read(job.root / (name + ".json"))["data"]
    assert "groups" in m.whole.read(job.root / (name + m.SUFFIX))["data"]
    folder = job.root
    del job.slot, job.generate_response
    job.__class__ = m.ReadOnlyAssignmentJob
    job.expected_root, job._root, job.authenticated_stages = folder.resolve(), folder.resolve(), set()
    before = {p: p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    assert m.coverage.review_batch(job, inventory, plan, items, plan["batches"][0], 1) == result
    assert len(calls) == 2 and len(job.authenticated_stages) == 2
    assert before == {p: p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    (folder / (name + ".json")).unlink()
    with pytest.raises(ValueError, match="existing_cache_without_inference"):
        m.coverage.review_batch(job, inventory, plan, items, plan["batches"][0], 1)


def test_first_batch_uses_only_original_unused_independent_checks_and_keeps_omissions_unresolved(tmp_path, monkeypatch):
    job, inventory, plan, items, calls = stage_fixture(tmp_path, monkeypatch)
    ids = plan["batches"][0]["clauses"]
    partial = {"groups": [{"kind": "covered", "clauses": ids[:1], "article_items": list(items),
        "reason": "Synthetic original partial judgment"}]}
    candidate, receipt = m.complete_partial(partial, ids, list(items), plan["units"])
    job.first = {"data": candidate, "receipt": receipt}
    monkeypatch.setattr(m, "first_artifact", lambda *a, **k: copy.deepcopy(job.first))
    result = m.coverage.review_batch(job, inventory, plan, items, plan["batches"][0], 0)
    assert calls and all(model == "qwen3.5:9b" for model, _ in calls)
    assert calls[0][1]["candidate_derivation"]["program_unassessed_clauses"] == ids[1:]
    assert not (job.root / (m.FIRST + ".json")).exists()
    assert all(not result[cid]["resolved"] for cid in ids[1:])
    assert result[ids[0]]["resolved"]


def test_changed_assignment_structure_still_has_only_two_original_attempts_across_restarts(tmp_path, monkeypatch):
    job, inventory, plan, items, calls = stage_fixture(tmp_path, monkeypatch, malformed=True)
    prompts = m.coverage.REVIEW, m.coverage.VERIFY
    for _ in range(2):
        with pytest.raises(ValueError, match="stage_attempt_budget_exhausted"):
            m.coverage.review_batch(job, inventory, plan, items, plan["batches"][0], 1)
        assert len(calls) == 2
        assert (m.coverage.REVIEW, m.coverage.VERIFY) == prompts


def test_reconstructed_proof_includes_failed_originals_and_all_private_derivations(tmp_path, monkeypatch):
    parent, key = "a" * 32, "b" * 32
    folder = tmp_path / "wiki/whole-manuscript-coverage-jobs" / key
    folder.mkdir(parents=True)
    (folder / "attempts").mkdir()
    parent_folder = tmp_path / "wiki/textbook-whole-authoring-jobs" / parent
    parent_folder.mkdir(parents=True)
    atomic_json(parent_folder / m.coverage.CONTRACT, {"job": key})
    files = {}
    for name in [m.FIRST + "-01.json", m.FIRST + "-02.json", "response-1.json", "response-2.json"]:
        path = folder / ("attempts" if name.startswith(m.FIRST) else "") / name
        atomic_json(path, {"synthetic": True})
        files[name] = m.whole.sha(path.read_bytes())
    artifact = {"receipt": {"original_files": files, "program_unassessed_clauses": ["C0000001"]}}
    atomic_json(folder / (m.FIRST + m.SUFFIX), artifact)
    atomic_json(folder / m.CONTRACT, {"synthetic": True})
    atomic_json(folder / ("batch-0001-body-0000-review" + m.SUFFIX), {"synthetic": True})
    monkeypatch.setattr(m.checked, "reconstruct", lambda *a, **k: {"summary": {"model_calls": 0}, "proof": {"files": {}}})
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    result = m.reconstruct(tmp_path, parent, tmp_path / "tokenizer.json", "section-0007")
    assert len(result["proof"]["files"]) == 7
    assert result["proof"]["original_failed_first_candidate_not_promoted_to_cache"]
    assert result["proof"]["program_unassessed_first_clauses"] == 1
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
