import copy
import json
from types import SimpleNamespace

import jsonschema
import pytest

from wechat_decrypt_tool import course_textbook_bounded_paragraph_resume as m
from wechat_decrypt_tool.course_knowledge import atomic_json
from test_course_textbook_fresh_authoring import fake_job
from test_course_textbook_point_mapped_resume import mapped_fixture
from test_course_textbook_visible_context import fixture
from test_course_textbook_whole_unit_repair import verdict


def bounded_fixture():
    inventory, working, packet, context, raw = fixture()
    scope = m.fresh.continuation.scope_for(["U0"], working, inventory, context, packet)
    raw["paragraphs"].append(copy.deepcopy(raw["paragraphs"][0]))
    raw["paragraphs"][1]["text"] = "Synthetic second independent explanation"
    schema = m.bounded_schema(m.writing.section_schema(["U0"], working), scope["expanded"], working["pending_source_cards"])
    return inventory, working, packet, context, raw, schema


def test_schema_prevents_unmapped_points_and_indices_outside_the_actual_array():
    _, _, _, _, raw, schema = bounded_fixture()
    jsonschema.validate(raw, schema)
    for index in [2, 99, -1]:
        changed = copy.deepcopy(raw)
        changed["coverage"]["U0"]["paragraphs"] = [index]
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(changed, schema)


@pytest.mark.parametrize("change", ["short", "long", "missing_map", "empty_map", "unknown_ref", "duplicate_ref", "false_with_map", "old_format"])
def test_exact_count_never_relaxes_other_structural_and_disposition_checks(change):
    _, _, _, _, raw, schema = bounded_fixture()
    if change == "short":
        raw["paragraphs"].pop()
    elif change == "long":
        raw["paragraphs"].append(copy.deepcopy(raw["paragraphs"][0]))
    elif change == "missing_map":
        del raw["coverage"]["U0"]
    elif change == "empty_map":
        raw["coverage"]["U0"]["paragraphs"] = []
    elif change == "unknown_ref":
        raw["overview"]["citations"] = ["O00004"]
    elif change == "duplicate_ref":
        raw["overview"]["citations"] *= 2
    elif change == "false_with_map":
        raw["coverage"]["U0"]["teaching"] = False
    else:
        raw["paragraphs"][0]["point_ids"] = ["U0"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(raw, schema)


def test_42_confirmed_points_all_remain_required_and_have_real_bounded_destinations():
    inventory, working, packet, context, raw, _ = bounded_fixture()
    working["pending_source_cards"] = {}
    ids = [f"P{i:03}" for i in range(42)]
    schema = m.bounded_schema(m.writing.section_schema(ids, working), packet, {})
    assert set(schema["properties"]["coverage"]["required"]) == set(ids)
    assert schema["properties"]["paragraphs"]["minItems"] == 11
    assert schema["properties"]["paragraphs"]["maxItems"] == 11
    for row in schema["properties"]["coverage"]["properties"].values():
        assert row["properties"]["paragraphs"]["minItems"] == 1
        assert row["properties"]["paragraphs"]["items"]["maximum"] == 10


def test_explicit_non_teaching_branch_is_preserved_without_fabricated_paragraphs():
    _, _, _, _, raw, schema = bounded_fixture()
    raw = {"kind": "non_teaching", "coverage": {"U0": {"teaching": False, "paragraphs": [], "reason": "Synthetic exclusion"}}}
    jsonschema.validate(raw, schema)


def test_format_hook_restores_prior_adapters_and_prompts_on_failure():
    before = m.fresh.section_schema, m.fresh.citation_candidate, copy.deepcopy(m.writing.composition.PROMPTS)
    with pytest.raises(RuntimeError):
        with m.bounded_format():
            assert m.fresh.section_schema is m.bounded_schema
            assert m.NOTICE in m.writing.composition.PROMPTS[m.writing.author.REPAIR_PROMPT]
            raise RuntimeError("Synthetic failure")
    assert (m.fresh.section_schema, m.fresh.citation_candidate, m.writing.composition.PROMPTS) == before


def test_actual_fresh_role_receives_fixed_count_decoding_schema_then_keeps_raw_response(tmp_path, monkeypatch):
    job, inventory, working, packet, context, raw = fake_job(tmp_path, monkeypatch)
    raw["paragraphs"].append(copy.deepcopy(raw["paragraphs"][0]))
    raw["paragraphs"][1]["text"] = "Synthetic second independent explanation"
    generated, values = [], {}

    def generate(self, name, prompt, schema, supplied, validate, **kwargs):
        generated.append(name)
        body = schema["oneOf"][0]["properties"]
        assert body["paragraphs"]["minItems"] == body["paragraphs"]["maxItems"] == 2
        assert "point_ids" not in body["paragraphs"]["items"]["properties"]
        assert kwargs["evidence_packet"] == m.fresh.continuation.scope_for(["U0"], working, inventory, context, packet)["expanded"]
        assert m.NOTICE in m.writing.composition.PROMPTS[prompt]
        validate(raw)
        values[name] = copy.deepcopy(raw)
        atomic_json(tmp_path / (name + ".json"), {"synthetic_raw": raw})
        return raw

    monkeypatch.setattr(m.fresh.BASE_JOB, "stage", generate)
    monkeypatch.setattr(job.reader, "stage", lambda name, *args, **kwargs: copy.deepcopy(values[name]))
    with m.bounded_format():
        candidate = job.stage("section-0008-repair", m.writing.author.REPAIR_PROMPT,
            m.writing.section_schema(["U0"], working), context, lambda row: None, evidence_packet=packet)
    assert generated == ["section-0008-repair"]
    assert candidate["coverage"] == raw["coverage"]
    assert [p["text"] for p in candidate["paragraphs"]] == [p["text"] for p in raw["paragraphs"]]
    assert json.loads((tmp_path / "section-0008-repair.json").read_bytes())["synthetic_raw"] == raw


def test_original_repair_runs_even_after_positive_initial_audit_and_negative_final_is_retained(tmp_path, monkeypatch):
    inventory, working, packet, context, raw, _ = bounded_fixture()
    scope = m.fresh.continuation.scope_for(["U0"], working, inventory, context, packet)
    candidate, _ = m.mapped.ORIGINAL_CANDIDATE(raw, ["U0"], working, inventory, scope)
    bundle = SimpleNamespace(prior=SimpleNamespace(original={}), root=tmp_path, folder=tmp_path / "resume",
        count=8, units=[None] * 14, inventory=inventory, working=working, context=context,
        prefix="section-0008", scope=scope, candidate=candidate, metadata={"omitted_point_ids": ["U0"]}, paths=set())
    bundle.folder.mkdir()
    atomic_json(bundle.folder / "execution.json", {"synthetic": True})
    atomic_json(bundle.folder / "proposal.json", {"synthetic": True})
    calls = []

    class SyntheticJob:
        root = tmp_path
        reader = SimpleNamespace(paths=set())

        def stage(self, name, prompt, schema, supplied, validate, **kwargs):
            calls.append(name)
            assert supplied["failed_write_is_untrusted"] is True
            assert supplied["original_repair_is_mandatory"] is True
            if name.endswith("-repair"):
                assert all(m.writing.author.verdicts(supplied["review"]))
                assert m.fresh.section_schema is m.bounded_schema
                value = candidate
            else:
                value = verdict(schema, passed=not name.endswith("-repair-review"))
            with m.mapped.visible.packet_scope(scope):
                validate(value)
            return value

    monkeypatch.setattr(m.mapped, "ORIGINAL_FACTORY", lambda *args, **kwargs: lambda *args: SyntheticJob())
    monkeypatch.setattr(m.writing, "TextbookStore", lambda root: None)
    result = m.original_roles(bundle, None, read_only=False)
    assert calls == ["section-0008-review", "section-0008-repair", "section-0008-repair-review"]
    assert result["final_checks_passed"] is False
    assert result["initial_review"] != result["final_review"]
    assert not (tmp_path / "section-0008-write.json").exists()


def test_remaining_role_guard_rejects_any_existing_attempt_or_prospective_request(tmp_path):
    bundle = SimpleNamespace(job=SimpleNamespace(root=tmp_path), prefix="section-0008", count=8, units=[None] * 9)
    m.require_unused(bundle)
    (tmp_path / "attempts").mkdir()
    (tmp_path / "attempts/section-0008-review-01.json").write_text('{}')
    with pytest.raises(ValueError, match="all_remaining_original_roles_unused"):
        m.require_unused(bundle)


def test_remaining_role_guard_rejects_used_future_writer(tmp_path):
    bundle = SimpleNamespace(job=SimpleNamespace(root=tmp_path), prefix="section-0008", count=8,
        units=[None] * 9 + [{"id": "section-0009"}])
    (tmp_path / "section-0009-write-prospective-request.json").write_text('{}')
    with pytest.raises(ValueError, match="all_remaining_original_roles_unused"):
        m.require_unused(bundle)


def test_future_dispatch_bypasses_old_point_id_adapter_but_uses_the_actual_base_class(tmp_path, monkeypatch):
    calls = []

    class FreshJob:
        def __init__(self, *args):
            self.reader = SimpleNamespace(paths=set())

        def stage(self, *args, **kwargs):
            calls.append("original_fresh_role")
            assert m.fresh.section_schema is m.bounded_schema
            return {"synthetic": True}

    class OldResumed(FreshJob):
        def stage(self, *args, **kwargs):
            raise AssertionError("Old point-ID adapter must not intercept future writing")

    monkeypatch.setattr(m.reviewed, "resumed_type", lambda *args, **kwargs: OldResumed)
    bundle = SimpleNamespace(prior=SimpleNamespace(checked_result={}), root=tmp_path, folder=tmp_path,
        prefix="section-0008", contract={"future_units": ["section-0009"]})
    job = m.resumed_type(bundle, {"files": {}}, read_only=False)()
    assert job.stage("section-0009-write", None, None, None, None) == {"synthetic": True}
    assert calls == ["original_fresh_role"]


def test_both_old_and_new_coverage_obligations_and_prior_negative_reviews_survive(monkeypatch):
    inventory = {"synthetic": True}
    prior = SimpleNamespace(contract={"inventory": m.digest(inventory)}, prefix="section-0007",
        metadata={"omitted_point_ids": ["P7"]}, candidate={"synthetic": "old"})
    bundle = SimpleNamespace(prior=prior, inventory=inventory, prefix="section-0008",
        metadata={"omitted_point_ids": ["P8"]}, candidate={"synthetic": "new"})
    def original(*args):
        return [{"kind": "retained_negative_review"}]
    monkeypatch.setattr(m.writing, "final_issues", original)
    backup = m.writing.backup_job
    with m.runtime_policies(bundle):
        issues = m.writing.final_issues(inventory, {}, [])
        assert [v["kind"] for v in issues] == ["retained_negative_review",
            "untrusted_failed_write_mapping_requires_whole_original_coverage",
            "omitted_point_mapping_proposal_requires_whole_original_coverage"]
    assert m.writing.final_issues is original
    assert m.writing.backup_job is backup


def test_failed_input_verification_keeps_both_original_raw_attempts_and_selects_last_without_merging(tmp_path, monkeypatch):
    inventory, working, scope, raw = mapped_fixture()
    raw["paragraphs"][0]["point_ids"] = []
    context = {"assigned_points": working["points"]}
    job = SimpleNamespace(root=tmp_path, models={"qwen3.5:9b": "a" * 64})
    bundle = SimpleNamespace(context=context, scope=scope, working=working, inventory=inventory,
        job=job, prior=SimpleNamespace(original={}), prefix="section-0008", paths=set())
    schema = m.writing.section_schema(["U0"], working)
    actual = m.mapped.point_schema(schema, scope["expanded"], working["pending_source_cards"])
    request = {"prompt": "Synthetic actual prompt", "schema": actual, "binding": "b" * 64}
    atomic_json(tmp_path / "section-0008-write-prospective-request.json", {
        "policy": m.fresh.POLICY, "execution_sha256": m.digest({}), "stage": "section-0008-write",
        "original_schema_sha256": m.digest(schema), "actual_schema_sha256": m.digest(actual),
        "original_context_sha256": m.digest(context), "actual_context_sha256": m.digest(context),
        "original_packet_sha256": m.digest(scope["original"]), "actual_packet_sha256": m.digest(scope["expanded"]),
        "original_budgets_reset": False, "reader_publication_allowed": False})
    error = ""
    (tmp_path / "attempts").mkdir()
    for number in [1, 2]:
        value = copy.deepcopy(raw)
        value["paragraphs"][0]["text"] = f"Synthetic original attempt {number}"
        messages = [{"role": "system", "content": request["prompt"]}, {"role": "user", "content": json.dumps(
            {**context, "previous_validation_failure": error}, ensure_ascii=False)}]
        row = {"stage": "section-0008-write", "attempt": number, "input_sha256": request["binding"],
            "model_digest": "a" * 64, "request_sha256": m.digest(messages), "error_type": "ValueError",
            "validation_error": "requires_point_mapped_missing_teaching_ids_U0", "raw_response": json.dumps(value),
            "model_metrics": {"done_reason": "stop"}}
        atomic_json(tmp_path / "attempts" / f"section-0008-write-{number:02}.json", row)
        atomic_json(tmp_path / f"response-{number}.json", {"stage": row["stage"], "request_sha256": row["request_sha256"],
            "schema_sha256": m.digest(actual), "response": {"message": {"content": row["raw_response"]}}, "metrics": row["model_metrics"]})
        error = row["validation_error"]
    before = {p: p.read_bytes() for p in tmp_path.rglob('*.json')}
    monkeypatch.setattr(m.fresh.continuation.draft, "capture_request", lambda *args: request)
    monkeypatch.setattr(m.whole.editor.e, "verify_response", lambda *args: None)
    m.failed_writes(bundle, job)
    assert bundle.selected == "section-0008-write-02.json"
    assert bundle.candidate["paragraphs"][0]["text"] == "Synthetic original attempt 2"
    assert len(bundle.history) == 2
    assert len(bundle.paths) == 5
    assert {p: p.read_bytes() for p in before} == before
    assert not (tmp_path / "section-0008-write.json").exists()


def test_bindings_have_stable_file_stems_even_when_cli_module_name_is_main():
    assert "course_textbook_bounded_paragraph_resume" in m.bindings()
    assert "__main__" not in m.bindings()
