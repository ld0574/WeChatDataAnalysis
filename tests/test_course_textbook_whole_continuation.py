import copy
import json
from types import SimpleNamespace

import pytest

from wechat_decrypt_tool import course_textbook_whole_continuation as m
from wechat_decrypt_tool.course_knowledge import atomic_json
from test_course_textbook_visible_context import fixture
from test_course_textbook_whole_snapshot import cache_fixture
from test_course_textbook_whole_unit_repair import verdict


def bundle_fixture(tmp_path, *, passed=True):
    inventory, working, packet, context, candidate = fixture()
    candidate["paragraphs"][0]["citations"] = ["O00001", "O00002"]
    scope = m.scope_for(["U0"], working, inventory, context, packet)
    schema = m.writing.review_schema(candidate, ["U0"])
    final, initial = verdict(schema, passed=passed), verdict(schema, passed=False)
    prefix = "section-0007"
    (tmp_path / "attempts").mkdir(exist_ok=True)
    for role, value in [("repair", candidate), ("repair-review", final)]:
        metrics = {"done_reason": "stop", "size_vram": 1, "requested_context": 1024,
            "measured_context": 1024, "expected_content_tokens": 20, "prompt_eval_count": 20, "reserved_output": 16}
        raw = json.dumps(value)
        atomic_json(tmp_path / "attempts" / (prefix + "-" + role + "-01.json"), {"accepted_structure": True,
            "input_sha256": "b" * 64, "raw_response": raw, "request_sha256": "c" * 64, "model_metrics": metrics})
        atomic_json(tmp_path / ("response-" + role + ".json"), {"stage": prefix + "-" + role,
            "request_sha256": "c" * 64, "response": {"message": {"content": raw}}, "metrics": metrics})
        atomic_json(tmp_path / (prefix + "-" + role + ".json"),
            {"input_sha256": "b" * 64, "data": value, "data_sha256": m.digest(value)})
    bundle = SimpleNamespace(key="a" * 32, prefix=prefix, job=SimpleNamespace(root=tmp_path),
        working=working, inventory=inventory, context=context, scope=scope, candidate=candidate,
        initial=initial, final=final, result={"candidate": candidate, "actual_final_review": final},
        proof={"verified_units": 7}, paths=set(), units=[{"id": "section-0008", "point_ids": ["U0"]}])
    contract = {"original_role_mapping": {"write": "repair", "review": "repair-review",
        "repair": "repair", "repair-review": "repair-review"}, "future_units": ["section-0008"]}
    return bundle, contract


def request(bundle, role):
    context = copy.deepcopy(bundle.context)
    if role != "write":
        context["candidate"] = bundle.candidate
    if role == "repair":
        context["review"] = bundle.final
    prompt = m.writing.author.WRITE_PROMPT if role == "write" else (
        m.writing.author.REPAIR_PROMPT if role == "repair" else m.writing.author.REVIEW_PROMPT)
    schema = m.writing.section_schema(["U0"], bundle.working) if role in {"write", "repair"} else (
        m.writing.review_schema(bundle.candidate, ["U0"]))
    validate = (lambda row: m.visible.ORIGINAL_VALIDATE(row, ["U0"], bundle.working, bundle.inventory)) if role in {"write", "repair"} else (
        lambda row: m.writing.author.validate_review(row, schema))
    return bundle.prefix + "-" + role, prompt, schema, context, validate, {"evidence_packet": bundle.scope["original"]}


@pytest.mark.parametrize("passed", [True, False])
def test_derived_orchestration_uses_only_actual_repair_and_final_review_without_changing_judgments(tmp_path, passed):
    bundle, contract = bundle_fixture(tmp_path, passed=passed)
    before = copy.deepcopy((bundle.candidate, bundle.initial, bundle.final))
    original_files = {p: p.read_bytes() for p in tmp_path.glob("*.json")}
    roles = ["write", "review"] if passed else list(m.ROLES)
    for role in roles:
        args = request(bundle, role)
        value, path = m.derived_stage(bundle, contract, *args, read_only=False)
        assert value == (bundle.candidate if role in {"write", "repair"} else bundle.final)
        receipt = json.loads(path.read_bytes())
        assert receipt["actual_model_stage"] == bundle.prefix + "-" + contract["original_role_mapping"][role]
        assert receipt["new_model_calls"] == 0 and receipt["model_write_cache_fabricated"] is False
        assert m.derived_stage(bundle, contract, *args, read_only=True)[0] == value
    assert all(p.read_bytes() == raw for p, raw in original_files.items())
    assert (bundle.candidate, bundle.initial, bundle.final) == before
    assert not (tmp_path / (bundle.prefix + "-write.json")).exists()
    assert all(m.writing.author.verdicts(bundle.final)) == passed


@pytest.mark.parametrize("changed", ["context", "candidate", "review", "schema", "packet", "prompt", "actual_cache"])
def test_derived_mapping_rejects_changed_input_or_real_role_cache(tmp_path, changed):
    bundle, contract = bundle_fixture(tmp_path, passed=False)
    name, prompt, schema, context, validate, kwargs = request(bundle, "repair")
    if changed == "context":
        context["sources"] = {}
    elif changed == "candidate":
        context["candidate"] = {}
    elif changed == "review":
        context["review"] = {"passed": True}
    elif changed == "schema":
        schema = {}
    elif changed == "packet":
        kwargs = {"evidence_packet": bundle.scope["expanded"]}
    elif changed == "prompt":
        prompt = m.writing.author.WRITE_PROMPT
    else:
        atomic_json(tmp_path / (bundle.prefix + "-repair.json"),
            {"input_sha256": "b" * 64, "data": {}, "data_sha256": m.digest({})})
    with pytest.raises(ValueError):
        m.derived_stage(bundle, contract, name, prompt, schema, context, validate, kwargs, read_only=False)
    assert not list(tmp_path.glob("*-continuation-request.json"))


def test_read_only_mapping_never_creates_a_missing_contract(tmp_path):
    bundle, contract = bundle_fixture(tmp_path)
    with pytest.raises(ValueError, match="required_existing_stage_mapping"):
        m.derived_stage(bundle, contract, *request(bundle, "write"), read_only=True)
    assert not list(tmp_path.glob("*-continuation-request.json"))


def test_failed_repair_proposal_is_explicitly_derived_without_a_successful_repair_cache(tmp_path):
    bundle, contract = bundle_fixture(tmp_path)
    bundle.uses_proposal = True
    bundle.raw_candidate = copy.deepcopy(bundle.candidate)
    bundle.raw_candidate["paragraphs"][0]["citations"] = ["O00002"]
    bundle.repair_binding = "b" * 64
    bundle.result["actual_repair_candidate_sha256"] = m.digest(bundle.raw_candidate)
    contract["original_role_mapping"].update(write="repair-proposal", repair="repair-proposal")
    (tmp_path / (bundle.prefix + "-repair.json")).unlink()
    value, path = m.derived_stage(bundle, contract, *request(bundle, "write"), read_only=False)
    receipt = json.loads(path.read_bytes())
    assert value == bundle.candidate and receipt["explicit_citation_proposal_derivation"] is True
    assert receipt["actual_data_sha256"] == m.digest(bundle.raw_candidate)
    assert receipt["derived_data_sha256"] == m.digest(bundle.candidate)
    assert not (tmp_path / (bundle.prefix + "-repair.json")).exists()


@pytest.mark.parametrize("changed", ["text", "extra", "missing", "point", "packet"])
def test_scope_rejects_any_changed_or_unseen_original(tmp_path, changed):
    inventory, working, packet, context, _ = fixture()
    if changed == "text":
        context["context_sources"]["O00002"]["text"] = "Changed synthetic original"
    elif changed == "extra":
        context["context_sources"]["O00004"] = inventory["sources"]["O00004"]
    elif changed == "missing":
        context["context_sources"] = {}
    elif changed == "point":
        context["assigned_points"] = {}
    else:
        packet["sources"]["O00004"] = inventory["sources"]["O00004"]
    with pytest.raises(ValueError):
        m.scope_for(["U0"], working, inventory, context, packet)


def test_actual_complete_context_with_no_neighbor_original_is_supported():
    inventory, working, _, _, _ = fixture()
    working["points"]["U0"]["citations"] = list(inventory["sources"])
    packet = m.visible.ORIGINAL_PACKET(["U0"], working, inventory)
    context = {**m.writing.whole.context_for(inventory, list(packet["sources"])), "assigned_points": working["points"]}
    scope = m.scope_for(["U0"], working, inventory, context, packet)
    assert not context["context_sources"] and scope["original"] == scope["expanded"]


def future_job(tmp_path, monkeypatch, *, read_only=False):
    bundle, contract = bundle_fixture(tmp_path)
    monkeypatch.setattr(m.visible.ORIGINAL_JOB, "__init__", lambda *args: None)
    monkeypatch.setattr(m.visible.ORIGINAL_JOB, "key", bundle.key, raising=False)
    monkeypatch.setattr(m.visible.ORIGINAL_JOB, "root", tmp_path, raising=False)
    cls = m.job_type(bundle, contract, m.visible.Recovery([]), read_only=read_only)
    job = cls(None, bundle.inventory, bundle.working, None)
    return job, bundle


def test_future_whole_role_binds_oneof_and_preserves_complete_inputs_and_budget(tmp_path, monkeypatch):
    job, bundle = future_job(tmp_path, monkeypatch)
    calls = []

    def stage(self, name, prompt, schema, context, validate, **kwargs):
        calls.append((name, copy.deepcopy(context), copy.deepcopy(schema), copy.deepcopy(kwargs)))
        assert schema["oneOf"][0]["properties"]["overview"]["properties"]["citations"]["items"]["enum"] == ["O00000", "O00001", "O00002"]
        assert kwargs["evidence_packet"] == bundle.scope["expanded"]
        validate(bundle.candidate)
        return bundle.candidate

    monkeypatch.setattr(m.visible.ORIGINAL_JOB, "stage", stage)
    monkeypatch.setattr(job.reader, "stage", lambda *args, **kwargs: copy.deepcopy(bundle.candidate))
    name, prompt, schema, context, validate, kwargs = request(bundle, "write")
    assert job.stage(name.replace("0007", "0008"), prompt, schema, context, validate, **kwargs) == bundle.candidate
    assert calls[0][1] == context and schema == m.writing.section_schema(["U0"], bundle.working)
    receipt = json.loads((tmp_path / "section-0008-write-continuation-request.json").read_bytes())
    assert receipt["original_budgets_reset"] is False and receipt["actual_visible_sources"] == 3
    assert not (tmp_path / "section-0008-write.json").exists()


def test_future_metadata_defect_does_not_consume_another_call_and_actual_review_sees_unverified_proposals(tmp_path, monkeypatch):
    job, bundle = future_job(tmp_path, monkeypatch)
    raw = copy.deepcopy(bundle.candidate)
    raw["paragraphs"][0]["citations"] = ["O00002"]
    calls = []

    def stage(self, name, prompt, schema, context, validate, **kwargs):
        calls.append(name)
        if name.endswith("-write"):
            value = raw
            atomic_json(tmp_path / (name + ".json"), {"input_sha256": "b" * 64,
                "data_sha256": m.digest(value), "data": value})
        else:
            assert context["candidate"] == bundle.candidate
            assert context["citation_proposals_are_factual_evidence"] is False
            assert context["unverified_citation_proposals"]
            value = verdict(schema, passed=False)
        validate(value)
        return value

    monkeypatch.setattr(m.visible.ORIGINAL_JOB, "stage", stage)
    monkeypatch.setattr(job.reader, "stage", lambda name, prompt, schema, *args, **kwargs:
        copy.deepcopy(raw) if name.endswith("-write") else verdict(schema, passed=False))
    monkeypatch.setattr(m, "checked_cache", lambda *args: (copy.deepcopy(raw), "b" * 64))
    name, prompt, schema, context, validate, kwargs = request(bundle, "write")
    candidate = job.stage(name.replace("0007", "0008"), prompt, schema, context, validate, **kwargs)
    assert candidate == bundle.candidate
    assert json.loads((tmp_path / "section-0008-write.json").read_bytes())["data"] == raw
    assert candidate["paragraphs"][0]["text"] == raw["paragraphs"][0]["text"]
    assert candidate["coverage"] == raw["coverage"]
    name, prompt, schema, context, validate, kwargs = request(bundle, "review")
    audit = job.stage(name.replace("0007", "0008"), prompt, schema, context, validate, **kwargs)
    assert not all(m.writing.author.verdicts(audit))
    assert calls == ["section-0008-write", "section-0008-review"]


@pytest.mark.parametrize("changed", ["scope", "mapping", "graph"])
def test_future_metadata_deferral_still_rejects_other_structural_defects(tmp_path, monkeypatch, changed):
    job, bundle = future_job(tmp_path, monkeypatch)
    value = copy.deepcopy(bundle.candidate)
    value["paragraphs"][0]["citations"] = ["O00002"]
    if changed == "scope":
        value["quiz"]["citations"] = ["O00004"]
    elif changed == "mapping":
        value["coverage"]["U0"]["paragraphs"] = [99]
    else:
        value["diagrams"] = [{"kind": "flow", "title": "Synthetic graph", "caption": "Synthetic caption",
            "nodes": [{"id": "a", "label": "A"}], "edges": [{"from": "a", "to": "missing", "label": "Bad edge"}],
            "citations": ["O00002"]}]

    def stage(self, name, prompt, schema, context, validate, **kwargs):
        validate(value)
        pytest.fail("A genuine structure defect must not be deferred")

    monkeypatch.setattr(m.visible.ORIGINAL_JOB, "stage", stage)
    name, prompt, schema, context, validate, kwargs = request(bundle, "write")
    with pytest.raises((ValueError, m.jsonschema.ValidationError)):
        job.stage(name.replace("0007", "0008"), prompt, schema, context, validate, **kwargs)
    assert not list(tmp_path.glob("*-whole-future-citation-proposal.json"))


@pytest.mark.parametrize("changed", ["scope", "role", "future_unit"])
def test_future_orchestration_cannot_bypass_declared_roles_or_scope(tmp_path, monkeypatch, changed):
    job, bundle = future_job(tmp_path, monkeypatch)
    monkeypatch.setattr(m.visible.ORIGINAL_JOB, "stage", lambda *args, **kwargs: pytest.fail("unexpected inference"))
    name, prompt, schema, context, validate, kwargs = request(bundle, "write")
    name = name.replace("0007", "0008")
    if changed == "scope":
        context["context_sources"] = {}
    elif changed == "role":
        prompt = m.writing.author.REPAIR_PROMPT
    else:
        name = name.replace("0008", "0009")
    with pytest.raises(ValueError):
        job.stage(name, prompt, schema, context, validate, **kwargs)
    assert not list(tmp_path.glob("*-continuation-request.json"))


@pytest.mark.parametrize("changed", ["attempt", "cache", "proof", "request"])
def test_future_preparation_rejects_any_already_started_role(tmp_path, changed):
    (tmp_path / "attempts").mkdir()
    target = {"attempt": tmp_path / "attempts/section-0008-write-01.json", "cache": tmp_path / "section-0008-write.json",
        "proof": tmp_path / "section-0008-write.proof.json", "request": tmp_path / "section-0008-write-continuation-request.json"}[changed]
    assert m.unused_future(tmp_path, ["section-0008"])
    atomic_json(target, {})
    assert not m.unused_future(tmp_path, ["section-0008"])


def test_strict_read_only_authentication_rejects_cache_different_from_actual_accepted_response(tmp_path, monkeypatch):
    old_reader, name, prompt, schema, context, data = cache_fixture(tmp_path, monkeypatch)
    reader = m.StrictReader(old_reader.job, {}, {}, m.visible.Recovery([]))
    assert reader.stage(name, prompt, schema, context, lambda row: None) == data
    changed = {"changed_synthetic_cache": True}
    path = tmp_path / (name + ".json")
    row = json.loads(path.read_bytes())
    row.update(data=changed, data_sha256=m.digest(changed))
    atomic_json(path, row)
    path = tmp_path / (name + ".proof.json")
    row = json.loads(path.read_bytes())
    row["data_sha256"] = m.digest(changed)
    atomic_json(path, row)
    with pytest.raises(ValueError, match="actual_accepted_response"):
        reader.stage(name, prompt, schema, context, lambda row: None)
