import copy
import json
from types import SimpleNamespace

import jsonschema
import pytest

from wechat_decrypt_tool import course_textbook_fresh_authoring as m
from wechat_decrypt_tool.course_knowledge import atomic_json
from test_course_textbook_visible_context import fixture
from test_course_textbook_whole_snapshot import cache_fixture
from test_course_textbook_whole_unit_repair import verdict


def test_plan_transform_preserves_every_semantic_membership_and_resolves_only_unused_groups_and_ties():
    cards = {key: {} for key in ("a", "b", "c")}
    value = {"title": "Synthetic title", "groups": [
        {"heading": "Unused", "purpose": "Unused"},
        {"heading": "Concept A", "purpose": "Synthetic A"},
        {"heading": "Concept B", "purpose": "Synthetic B"}],
        "assignments": {"a": {"group": 1, "order": 7}, "b": {"group": 1, "order": 7},
            "c": {"group": 2, "order": 8}}}
    before = copy.deepcopy(value)
    result = m.plan_candidate(value, cards, final=True)
    assert result["groups"] == value["groups"][1:]
    assert result["assignments"] == {"a": {"group": 0, "order": 0}, "b": {"group": 0, "order": 1},
        "c": {"group": 1, "order": 8}}
    assert value == before
    assert m.plan_candidate(result, cards, final=True) == result


@pytest.mark.parametrize("change", ["missing", "unknown", "duplicate_heading", "blank"])
def test_plan_transform_rejects_substantive_or_incomplete_errors(change):
    cards = {"a": {}, "b": {}}
    value = {"title": "Synthetic", "groups": [{"heading": "A", "purpose": "A"}, {"heading": "B", "purpose": "B"}],
        "assignments": {"a": {"group": 0, "order": 0}, "b": {"group": 1, "order": 1}}}
    if change == "missing":
        del value["assignments"]["a"]
    elif change == "unknown":
        value["assignments"]["a"]["group"] = 9
    elif change == "duplicate_heading":
        value["groups"][1]["heading"] = "A"
    else:
        value["groups"][0]["purpose"] = " "
    with pytest.raises((ValueError, jsonschema.ValidationError)):
        m.plan_candidate(value, cards, final=True)


@pytest.mark.parametrize("change", ["duplicate", "unknown", "false_with_paragraph", "true_without_paragraph"])
def test_decoding_schema_prevents_known_reference_and_disposition_conflicts_in_oneof(change):
    inventory, working, packet, context, candidate = fixture()
    scope = m.continuation.scope_for(["U0"], working, inventory, context, packet)
    original = m.writing.section_schema(["U0"], working)
    schema = m.section_schema(original, scope["expanded"], working["pending_source_cards"])
    assert schema["oneOf"][0]["properties"]["overview"]["properties"]["citations"]["items"]["enum"] == [
        "O00000", "O00001", "O00002"]
    jsonschema.validate(candidate, schema)
    assert original == m.writing.section_schema(["U0"], working)
    if change == "duplicate":
        candidate["quiz"]["citations"] *= 2
    elif change == "unknown":
        candidate["paragraphs"][0]["citations"] = ["O00004"]
    elif change == "false_with_paragraph":
        candidate["coverage"]["U0"]["teaching"] = False
    else:
        candidate["coverage"]["U0"]["paragraphs"] = []
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(candidate, schema)


def test_metadata_proposal_changes_no_prose_disposition_or_coverage_and_keeps_the_raw_candidate():
    inventory, working, packet, context, raw = fixture()
    before = copy.deepcopy(raw)
    scope = m.continuation.scope_for(["U0"], working, inventory, context, packet)
    result, changes = m.citation_candidate(raw, ["U0"], working, inventory, scope)
    assert changes and result["paragraphs"][0]["citations"] == ["O00001", "O00002"]
    assert result["coverage"] == raw["coverage"]
    assert result["paragraphs"][0]["text"] == raw["paragraphs"][0]["text"]
    assert raw == before
    assert m.citation_candidate(result, ["U0"], working, inventory, scope) == (result, {})


@pytest.mark.parametrize("change", ["unknown", "mapping", "disposition", "blank", "graph"])
def test_metadata_proposal_never_defers_other_defects(change):
    inventory, working, packet, context, raw = fixture()
    scope = m.continuation.scope_for(["U0"], working, inventory, context, packet)
    if change == "unknown":
        raw["quiz"]["citations"] = ["O00004"]
    elif change == "mapping":
        raw["coverage"]["U0"]["paragraphs"] = [88]
    elif change == "disposition":
        raw["coverage"]["U0"]["teaching"] = False
    elif change == "blank":
        raw["paragraphs"][0]["text"] = " "
    else:
        raw["diagrams"] = [{"kind": "flow", "title": "Synthetic", "caption": "Synthetic",
            "nodes": [{"id": "a", "label": "A"}], "edges": [{"from": "a", "to": "missing", "label": "Bad"}],
            "citations": ["O00002"]}]
    with pytest.raises((ValueError, jsonschema.ValidationError)):
        m.citation_candidate(raw, ["U0"], working, inventory, scope)


def fake_job(tmp_path, monkeypatch, *, read_only=False):
    inventory, working, packet, context, raw = fixture()
    monkeypatch.setattr(m.BASE_JOB, "__init__", lambda *args: None)
    monkeypatch.setattr(m.BASE_JOB, "root", tmp_path, raising=False)
    monkeypatch.setattr(m.BASE_JOB, "packet", packet, raising=False)
    monkeypatch.setattr(m, "execution", lambda *args: {"synthetic_contract": True})
    job = m.job_type({"synthetic_contract": True}, read_only=read_only)(None, inventory, working, None)
    return job, inventory, working, packet, context, raw


def test_new_role_uses_scoped_grammar_once_then_review_sees_unverified_metadata_and_keeps_negative_verdicts(tmp_path, monkeypatch):
    job, inventory, working, packet, context, raw = fake_job(tmp_path, monkeypatch)
    generated, values = [], {}

    def generate(self, name, prompt, schema, supplied, validate, **kwargs):
        generated.append(name)
        assert kwargs["evidence_packet"] == m.continuation.scope_for(["U0"], working, inventory, context, packet)["expanded"]
        if name.endswith("-write"):
            assert supplied == context
            value = raw
        else:
            assert supplied["candidate"]["paragraphs"][0]["citations"] == ["O00001", "O00002"]
            assert supplied["candidate"]["coverage"] == raw["coverage"]
            assert supplied["unverified_citation_proposals"]
            assert supplied["citation_proposals_are_factual_evidence"] is False
            value = verdict(schema, passed=False)
        validate(value)
        values[name] = copy.deepcopy(value)
        atomic_json(tmp_path / (name + ".json"), {"synthetic_raw": value})
        return value

    monkeypatch.setattr(m.BASE_JOB, "stage", generate)
    monkeypatch.setattr(job.reader, "stage", lambda name, *args, **kwargs: copy.deepcopy(values[name]))
    def original_validate(value):
        m.writing.validate_section(value, ["U0"], working, inventory)
    candidate = job.stage("section-0000-write", m.writing.author.WRITE_PROMPT,
        m.writing.section_schema(["U0"], working), context, original_validate, evidence_packet=packet)
    assert json.loads((tmp_path / "section-0000-write.json").read_bytes())["synthetic_raw"] == raw
    audit_schema = m.writing.review_schema(candidate, ["U0"])
    audit = job.stage("section-0000-review", m.writing.author.REVIEW_PROMPT, audit_schema,
        {**context, "candidate": candidate}, lambda row: m.writing.author.validate_review(row, audit_schema), evidence_packet=packet)
    assert not all(m.writing.author.verdicts(audit))
    assert generated == ["section-0000-write", "section-0000-review"]
    assert (tmp_path / "section-0000-write-prospective-derived.json").is_file()
    generated.clear()
    assert job.stage("section-0000-write", m.writing.author.WRITE_PROMPT,
        m.writing.section_schema(["U0"], working), context, original_validate, evidence_packet=packet) == candidate
    assert not generated


def test_read_only_role_never_creates_missing_receipts_or_attempts_inference(tmp_path, monkeypatch):
    job, _, working, packet, context, _ = fake_job(tmp_path, monkeypatch, read_only=True)
    monkeypatch.setattr(m.BASE_JOB, "stage", lambda *args, **kwargs: pytest.fail("Unexpected model attempt"))
    with pytest.raises(ValueError, match="existing_artifact_without_inference"):
        job.stage("section-0000-write", m.writing.author.WRITE_PROMPT,
            m.writing.section_schema(["U0"], working), context, lambda value: None, evidence_packet=packet)
    assert not list(tmp_path.glob("*.json"))
    with pytest.raises(ValueError, match="inference_forbidden"):
        job.generate_response()
    with pytest.raises(ValueError, match="gpu_acquisition_forbidden"):
        job.slot()


@pytest.mark.parametrize("filename", ["progress.json", "attempts/section-0000-write-01.json",
    "section-0000-write.json", "section-0000-write.proof.json", "response-synthetic.json"])
def test_prepare_refuses_any_already_started_writer_and_preserves_existing_failure(tmp_path, monkeypatch, filename):
    inventory_key = "a" * 64
    (tmp_path / "wiki/textbook-whole-record-evidence" / inventory_key).mkdir(parents=True)
    job = SimpleNamespace(root=tmp_path / "job", key="b" * 32)
    job.root.mkdir()
    target = job.root / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('{"original_failure":true}', encoding="utf-8")
    before = target.read_bytes()
    monkeypatch.setattr(m, "inputs", lambda *args: (tmp_path, {}, {}, job))
    monkeypatch.setattr(m, "execution", lambda *args: {})
    with pytest.raises(ValueError, match="fresh_writer_before_any_model_role"):
        m.prepare(tmp_path, inventory_key, "c" * 32, tmp_path / "tokenizer.json")
    assert target.read_bytes() == before
    assert not (job.root / m.CONTRACT).exists()


@pytest.mark.parametrize("change", ["schema", "metrics", "response"])
def test_read_only_authentication_requires_actual_schema_and_unchanged_gpu_response(tmp_path, monkeypatch, change):
    old_reader, name, prompt, schema, context, data = cache_fixture(tmp_path, monkeypatch)
    reader = m.StrictReader(old_reader.job, {}, {}, m.visible.Recovery([]))
    receipt_path = tmp_path / "response-1.json"
    receipt = json.loads(receipt_path.read_bytes())
    captured = m.continuation.draft.capture_request(old_reader.job, name, prompt, schema, context, old_reader.job.packet)
    receipt["schema_sha256"] = m.digest(captured["schema"])
    atomic_json(receipt_path, receipt)
    assert reader.stage(name, prompt, schema, context, lambda row: None) == data
    if change == "schema":
        receipt["schema_sha256"] = "changed"
    elif change == "metrics":
        receipt["metrics"] = {**receipt["metrics"], "different_synthetic_metric": True}
    else:
        receipt["response"]["message"]["content"] = "{}"
    atomic_json(receipt_path, receipt)
    with pytest.raises(ValueError):
        reader.stage(name, prompt, schema, context, lambda row: None)
