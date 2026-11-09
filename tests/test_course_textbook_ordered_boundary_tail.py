import copy
import json
from types import SimpleNamespace

import pytest

from wechat_decrypt_tool import course_textbook_ordered_boundary_tail as m
from wechat_decrypt_tool.course_knowledge import atomic_json
from test_course_textbook_ordered_coverage import ordered_fixture
from test_course_textbook_whole_coverage import group_fixture, manuscript, real_stage_job


def boundary_fixture():
    units, ids, _ = group_fixture()
    order = m.assignments.by_source(ids, units)
    raw = {"source_groups": {}}
    for ref, keys in order.items():
        raw["source_groups"][ref] = {"prefix_groups": [], "last_group": {
            "kind": "missing", "article_items": [], "reason": "Synthetic missing teaching"}}
        if len(keys) > 1:
            raw["source_groups"][ref]["prefix_groups"] = [{"through_position": 1,
                "kind": "covered", "article_items": ["A0"], "reason": "Synthetic complete first clause support"}]
    return units, ids, raw


def test_boundaries_retain_every_member_and_distinct_judgment_including_the_final_clause():
    units, ids, raw = boundary_fixture()
    before = copy.deepcopy(raw)
    result = m.compile_wire(raw, ids, ["A0"], units)
    assert raw == before
    assert [cid for group in result["groups"] for cid in group["clauses"]] == ids
    assert result["groups"][0]["clauses"] == [ids[0]]
    assert result["groups"][0]["kind"] == "covered"
    assert all(g["kind"] == "missing" for g in result["groups"][1:])


@pytest.mark.parametrize("change", ["zero", "end", "duplicate", "decreasing", "last", "source", "article", "extra", "blank"])
def test_invalid_boundaries_or_judgments_cannot_hide_or_duplicate_members(change):
    units, ids, raw = boundary_fixture()
    ref = next(iter(raw["source_groups"]))
    row = raw["source_groups"][ref]
    if change in {"zero", "end"}:
        row["prefix_groups"][0]["through_position"] = 0 if change == "zero" else 3
    elif change in {"duplicate", "decreasing"}:
        row["prefix_groups"].append(copy.deepcopy(row["prefix_groups"][0]))
        if change == "decreasing":
            row["prefix_groups"][0]["through_position"] = 2
    elif change == "last":
        row.pop("last_group")
    elif change == "source":
        raw["source_groups"].pop(ref)
    elif change == "article":
        row["prefix_groups"][0]["article_items"] = ["unknown"]
    elif change == "extra":
        row["last_group"]["through_position"] = 3
    else:
        row["last_group"]["reason"] = " "
    with pytest.raises((ValueError, m.coverage.jsonschema.ValidationError)):
        m.compile_wire(raw, ids, ["A0"], units)


def test_single_and_256_member_sources_have_a_mandatory_final_group_and_no_index_array():
    for count in [1, 256]:
        source = {"sources": {"O00000": {"text": " ".join(f"Term {i}," for i in range(count))}}}
        units = m.coverage.source_units(source)
        ids = list(units)
        raw = {"source_groups": {"O00000": {"prefix_groups": [], "last_group": {
            "kind": "missing", "article_items": [], "reason": "Synthetic same judgment for every member"}}}}
        assert m.compile_wire(raw, ids, ["A0"], units)["groups"][0]["clauses"] == ids
        assert all(cid not in json.dumps(raw) for cid in ids)
        assert "assignments" not in json.dumps(m.wire_schema(ids, ["A0"], units))


def test_invalid_original_source_mappings_become_unassessed_without_relabeling_or_changing_valid_judgments():
    units, ids, expected, raw = ordered_fixture()
    ref, keys = next(iter(m.assignments.by_source(ids, units).items()))
    raw["source_groups"][ref]["assignments"] = [1] * len(keys)
    before = copy.deepcopy(raw)
    result, metadata = m.unassessed_candidate(raw, ids, ["A0"], units)
    assert raw == before
    assert metadata["program_unassessed_clauses"] == keys
    assert metadata["preserved_valid_members"] == len(ids) - len(keys)
    assert not metadata["invalid_assignments_relabelled"]
    invalid = [g for g in result["groups"] if g["clauses"][0] in keys]
    valid = [g for g in result["groups"] if g["clauses"][0] not in keys]
    assert all(g["kind"] == "missing" and not g["article_items"] for g in invalid)
    assert valid == [g for g in expected["groups"] if g["clauses"][0] not in keys]


def stage_fixture(tmp_path, monkeypatch, *, failed=False, negative=False, malformed=False):
    job, inventory, plan, items, calls = real_stage_job(tmp_path, monkeypatch)
    job.__class__ = m.ordered.OrderedJob if failed else m.BoundaryTailJob
    job.authenticated_stages = set()
    first_batch = copy.deepcopy(plan["batches"][0])
    plan["batches"] = [copy.deepcopy(first_batch) for _ in range(7)]

    def generate(messages, *, schema):
        context = json.loads(messages[1]["content"])
        calls.append((job.model, copy.deepcopy(context)))
        assert context["sources"]["O00000"]["text"] == inventory["sources"]["O00000"]["text"]
        assert list(context["article"]) == list(items)
        if malformed:
            value = {}
        elif job.model == "course-defiant:q4":
            order = m.assignments.by_source(list(context["clauses"]), plan["units"])
            assert context["ordered_clause_ids"] == order
            if failed:
                value = {"source_groups": {ref: {"assignments": [1] * len(keys), "groups": [{
                    "kind": "covered", "article_items": list(items), "reason": "Synthetic invalid mapping"}]}
                    for ref, keys in order.items()}}
            else:
                value = {"source_groups": {ref: {"prefix_groups": [], "last_group": {
                    "kind": "covered", "article_items": list(items), "reason": "Synthetic complete teaching support"}}
                    for ref in order}}
        else:
            assert not context["candidate_derivation"]["is_factual_evidence"]
            value = {"groups": {gid: {"passed": not negative, "reason": "Synthetic independent full member review"}
                for gid in context["groups"]}}
        raw = json.dumps(value)
        metrics = {"model_digest": job.model_digest, "done_reason": "stop", "size_vram": 1,
            "requested_context": 65536, "measured_context": 65536, "expected_content_tokens": 20,
            "prompt_eval_count": 20, "reserved_output": 4096}
        atomic_json(job.root / f"response-{len(calls)}.json", {"stage": job.current,
            "request_sha256": m.digest(messages), "schema_sha256": m.digest(schema),
            "response": {"message": {"content": raw}}, "metrics": metrics})
        return raw, metrics

    job.generate_response = generate
    return job, inventory, plan, items, calls


def make_read_only(job):
    del job.slot, job.generate_response
    folder = job.root
    job.__class__ = m.ReadOnlyBoundaryTailJob
    job.expected_root, job._root = folder.resolve(), folder.resolve()


def test_future_original_role_binds_full_inputs_both_weights_schema_and_replays_without_gpu(tmp_path, monkeypatch):
    job, inventory, plan, items, calls = stage_fixture(tmp_path, monkeypatch)
    prompts = m.coverage.REVIEW, m.coverage.VERIFY
    result = m.coverage.review_batch(job, inventory, plan, items, plan["batches"][6], 6)
    assert all(s["resolved"] for s in result.values())
    assert [model for model, _ in calls] == ["course-defiant:q4", "qwen3.5:9b"]
    assert calls[1][1]["candidate_derivation"]["program_unassessed_clauses"] == []
    assert (m.coverage.REVIEW, m.coverage.VERIFY) == prompts
    before = {p: p.read_bytes() for p in job.root.rglob("*") if p.is_file()}
    make_read_only(job)
    assert m.coverage.review_batch(job, inventory, plan, items, plan["batches"][6], 6) == result
    assert len(calls) == 2
    assert before == {p: p.read_bytes() for p in job.root.rglob("*") if p.is_file()}


def failed_fixture(tmp_path, monkeypatch):
    job, inventory, plan, items, calls = stage_fixture(tmp_path, monkeypatch, failed=True)
    with pytest.raises(ValueError, match="stage_attempt_budget_exhausted"):
        m.coverage.review_batch(job, inventory, plan, items, plan["batches"][5], 5)
    inv, _, private = manuscript()
    assert inv == inventory
    parent = {"inventory": inv, "private": private}
    artifact = m.failed_artifact(job, parent, plan)
    return job, inventory, plan, items, calls, parent, artifact


def test_exhausted_original_proposal_is_authenticated_then_only_unused_reviewer_runs_and_unassessed_remains(tmp_path, monkeypatch):
    job, inventory, plan, items, calls, _, artifact = failed_fixture(tmp_path, monkeypatch)
    before = {p: p.read_bytes() for p in (job.root / "attempts").glob(m.FAILED + "-*.json")}
    job.__class__, job.failed = m.BoundaryTailJob, artifact
    result = m.coverage.review_batch(job, inventory, plan, items, plan["batches"][5], 5)
    assert [model for model, _ in calls] == ["course-defiant:q4", "course-defiant:q4", "qwen3.5:9b"]
    assert calls[2][1]["candidate_derivation"]["program_unassessed_clauses"] == list(result)
    assert all(not s["resolved"] and s["kind"] == "missing" for s in result.values())
    assert not (job.root / (m.FAILED + ".json")).exists()
    assert all(p.read_bytes() == data for p, data in before.items())
    make_read_only(job)
    assert m.coverage.review_batch(job, inventory, plan, items, plan["batches"][5], 5) == result
    assert len(calls) == 3


@pytest.mark.parametrize("change", ["schema", "request", "raw", "failure", "accepted"])
def test_original_failed_response_chain_cannot_be_replaced_or_promoted(tmp_path, monkeypatch, change):
    job, _, plan, _, calls, parent, _ = failed_fixture(tmp_path, monkeypatch)
    receipt = job.root / "response-1.json"
    attempt = job.root / "attempts" / (m.FAILED + "-01.json")
    path = receipt if change == "schema" else attempt
    value = m.whole.read(path)
    field = {"schema": "schema_sha256", "request": "request_sha256", "raw": "raw_response",
        "failure": "validation_error", "accepted": "accepted_structure"}[change]
    value[field] = True if change == "accepted" else "changed"
    atomic_json(path, value)
    with pytest.raises((ValueError, m.coverage.jsonschema.ValidationError)):
        m.failed_artifact(job, parent, plan)
    assert len(calls) == 2


@pytest.mark.parametrize("change", ["schema", "derivation", "cache"])
def test_future_cache_tampering_or_missing_output_cannot_trigger_fresh_inference(tmp_path, monkeypatch, change):
    job, inventory, plan, items, calls = stage_fixture(tmp_path, monkeypatch)
    m.coverage.review_batch(job, inventory, plan, items, plan["batches"][6], 6)
    stem = "batch-0006-body-0000-review"
    if change == "cache":
        (job.root / (stem + ".json")).unlink()
    else:
        path = job.root / ("response-1.json" if change == "schema" else stem + m.DERIVATION)
        value = m.whole.read(path)
        value["schema_sha256" if change == "schema" else "compiled_candidate_sha256"] = "f" * 64
        atomic_json(path, value)
    make_read_only(job)
    with pytest.raises(ValueError):
        m.coverage.review_batch(job, inventory, plan, items, plan["batches"][6], 6)
    assert len(calls) == 2


def test_original_future_two_attempts_cannot_be_refreshed(tmp_path, monkeypatch):
    job, inventory, plan, items, calls = stage_fixture(tmp_path, monkeypatch, malformed=True)
    for _ in range(2):
        with pytest.raises(ValueError, match="stage_attempt_budget_exhausted"):
            m.coverage.review_batch(job, inventory, plan, items, plan["batches"][6], 6)
        assert len(calls) == 2


def test_negative_independent_judgment_keeps_all_proposed_coverage_unresolved(tmp_path, monkeypatch):
    job, inventory, plan, items, calls = stage_fixture(tmp_path, monkeypatch, negative=True)
    result = m.coverage.review_batch(job, inventory, plan, items, plan["batches"][6], 6)
    assert all(not s["resolved"] and s["teaching_detected"] for s in result.values())
    assert len(calls) == 2


@pytest.mark.parametrize("name", ["batch-0005-body-0000-groups-0000-verify-01", "batch-0006-body-0000-review-01"])
def test_attempted_remaining_roles_are_rejected_before_any_dispatch(tmp_path, name):
    folder = tmp_path / "attempts"
    folder.mkdir()
    atomic_json(folder / (name + ".json"), {"synthetic_prior_attempt": True})
    with pytest.raises(ValueError, match="unused"):
        m.unused(SimpleNamespace(root=tmp_path), {"batches": [{"article_blocks": [[]]}] * 7})


@pytest.mark.parametrize("change", ["live", "success", "budget", "paused"])
def test_only_actual_exited_original_failed_prefix_is_eligible(monkeypatch, change):
    status = {"state": "stopped", "completed_batches": 5,
        "reason_code": "wiki_stage_attempt_budget_exhausted_" + m.FAILED}
    guard = {"state": "worker_exited", "returncode": 1}
    monkeypatch.setattr(m.coverage.writing, "identity_live", lambda row: change == "live")
    if change == "success":
        guard["returncode"] = 0
    elif change == "budget":
        status["reason_code"] = "different"
    elif change == "paused":
        status["state"] = "paused"
    with pytest.raises(ValueError, match="actual_stopped_exited"):
        m.prior_stopped(status, guard)


def test_original_cli_identity_changes_only_key_and_always_restores(monkeypatch):
    def original():
        return {m.ordered.__name__: "a" * 64, "unchanged": "b" * 64}
    monkeypatch.setattr(m.ordered, "bindings", original)
    with pytest.raises(RuntimeError):
        with m.original_cli_identity():
            assert m.ordered.bindings() == {"__main__": "a" * 64, "unchanged": "b" * 64}
            raise RuntimeError("Synthetic error")
    assert m.ordered.bindings is original
