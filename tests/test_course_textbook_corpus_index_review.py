import copy
import json
from types import SimpleNamespace

import jsonschema
import pytest

from wechat_decrypt_tool import course_textbook_corpus_index_review as m
from wechat_decrypt_tool.course_knowledge import atomic_json
from test_course_textbook_corpus_structural_resume import sample
from test_course_textbook_fresh_authoring import fake_job
from test_course_textbook_whole_unit_repair import verdict


def bundle_sample(tmp_path):
    inv, working, packet, context, scope, raw, _ = sample()
    return SimpleNamespace(root=tmp_path, job=SimpleNamespace(root=tmp_path, models={"qwen3.5:9b": "b" * 64}),
        inventory=inv, working=working, scope=scope, context=context, candidate=copy.deepcopy(raw),
        prefix="section-0001", original={"synthetic": True}, paths=set(), defects={"synthetic": True}), raw


@pytest.mark.parametrize("count", [2, 7, 12])
def test_count_branch_uses_same_actual_array_length_and_mapping_bound(count):
    inv, working, _, _, scope, raw, _ = sample()
    schema = m.tagged_schema(m.writing.section_schema(["U0"], working), scope["expanded"], working["pending_source_cards"])
    raw["paragraph_count"] = count
    raw["paragraphs"] = [dict(raw["paragraphs"][0], text=f"Synthetic complete explanation {i}") for i in range(count)]
    raw["coverage"]["U0"]["paragraphs"] = [count - 1]
    jsonschema.validate(raw, schema)
    before = copy.deepcopy(raw)
    candidate, metadata = m.tagged_candidate(raw, ["U0"], working, inv, scope)
    assert "paragraph_count" not in candidate and candidate["coverage"] == raw["coverage"]
    assert [p["text"] for p in candidate["paragraphs"]] == [p["text"] for p in raw["paragraphs"]]
    assert metadata["prose_and_teaching_unchanged"] and raw == before
    raw["coverage"]["U0"]["paragraphs"] = [count]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(raw, schema)


@pytest.mark.parametrize("defect", ["wrong_count", "blank", "duplicate", "missing_task", "empty_mapping", "no_tag"])
def test_decoding_rejects_padding_and_missing_obligations(defect):
    _, working, _, _, scope, raw, _ = sample()
    schema = m.tagged_schema(m.writing.section_schema(["U0"], working), scope["expanded"], working["pending_source_cards"])
    raw["paragraph_count"] = 2
    if defect == "wrong_count":
        raw["paragraph_count"] = 7
    elif defect == "blank":
        raw["paragraphs"][0]["text"] = " \n\t"
    elif defect == "duplicate":
        raw["paragraphs"][1] = copy.deepcopy(raw["paragraphs"][0])
    elif defect == "missing_task":
        raw["coverage"] = {}
    elif defect == "empty_mapping":
        raw["coverage"]["U0"]["paragraphs"] = []
    else:
        raw.pop("paragraph_count")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(raw, schema)


def test_explicit_nonteaching_branch_is_preserved():
    _, working, _, _, scope, _, _ = sample()
    schema = m.tagged_schema(m.writing.section_schema(["U0"], working), scope["expanded"], working["pending_source_cards"])
    raw = {"kind": "non_teaching", "coverage": {"U0": {"teaching": False, "paragraphs": [], "reason": "Synthetic administration"}}}
    jsonschema.validate(raw, schema)


def test_proposal_keeps_actual_prose_and_uses_all_body_only_for_invalid_indices(tmp_path):
    bundle, raw = bundle_sample(tmp_path)
    raw["coverage"]["U0"]["paragraphs"] = [1, 7]
    before = copy.deepcopy(raw)
    candidate, metadata = m.index_proposal(raw, bundle)
    assert raw == before and candidate["coverage"]["U0"]["paragraphs"] == [0, 1]
    assert metadata["invalid_mappings"]["U0"]["original_indices"] == [1, 7]
    assert metadata["mapping_proposals_are_factual_evidence"] is False
    assert metadata["whole_original_coverage_still_required"]
    assert candidate["overview"]["text"] == raw["overview"]["text"]
    assert candidate["quiz"] == raw["quiz"] and candidate["diagrams"] == raw["diagrams"]
    assert [p["text"] for p in candidate["paragraphs"]] == [p["text"] for p in raw["paragraphs"]]
    with pytest.raises(ValueError, match="actual_out_of_bounds_mapping"):
        m.index_proposal(candidate, bundle)


def failed_fixture(tmp_path, monkeypatch):
    bundle, raw = bundle_sample(tmp_path)
    (tmp_path / "attempts").mkdir()
    raw["coverage"]["U0"]["paragraphs"] = [7]
    initial = {"synthetic_review": True}
    context = {**m.common(bundle), "candidate": bundle.candidate, "review": initial}
    schema = m.prior.section_schema(m.writing.section_schema(["U0"], bundle.working),
        bundle.scope["expanded"], bundle.working["pending_source_cards"])
    request = {"schema": schema, "prompt": "Synthetic real prompt", "binding": "a" * 64}
    monkeypatch.setattr(m.fresh.continuation.draft, "capture_request", lambda *args: request)
    monkeypatch.setattr(m.whole.editor.e, "verify_response", lambda *args: None)
    name = bundle.prefix + "-repair"
    atomic_json(tmp_path / (name + "-prospective-request.json"), {"policy": m.fresh.POLICY,
        "execution_sha256": m.digest(bundle.original), "stage": name,
        "original_schema_sha256": m.digest(m.writing.section_schema(["U0"], bundle.working)),
        "actual_schema_sha256": m.digest(schema), "original_context_sha256": m.digest(context),
        "actual_context_sha256": m.digest(context), "original_packet_sha256": m.digest(bundle.scope["original"]),
        "actual_packet_sha256": m.digest(bundle.scope["expanded"]), "original_budgets_reset": False, "reader_publication_allowed": False})
    previous = ""
    for number in (1, 2):
        messages = [{"role": "system", "content": request["prompt"]}, {"role": "user", "content": json.dumps(
            {**context, "previous_validation_failure": previous}, ensure_ascii=False)}]
        row = {"stage": name, "attempt": number, "accepted_structure": False, "error_type": "ValueError",
            "input_sha256": request["binding"], "request_sha256": m.digest(messages), "model_digest": "b" * 64,
            "raw_response": json.dumps(raw), "model_metrics": {"done_reason": "stop"},
            "validation_error": "requires_corpus_structural_mapping_indices_within_actual_array_length_2"}
        atomic_json(tmp_path / "attempts" / f"{name}-{number:02d}.json", row)
        atomic_json(tmp_path / f"response-{number}.json", {"stage": name, "request_sha256": row["request_sha256"],
            "schema_sha256": m.digest(schema), "response": {"message": {"content": row["raw_response"]}}, "metrics": row["model_metrics"]})
        previous = row["validation_error"]
    return bundle, initial


def test_both_exhausted_repairs_require_real_complete_requests_and_wire_receipts(tmp_path, monkeypatch):
    bundle, initial = failed_fixture(tmp_path, monkeypatch)
    original = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    m.failed_repairs(bundle, bundle.job, initial)
    assert bundle.selected == "section-0001-repair-02.json" and len(bundle.repair_history) == 2
    assert original == {p: p.read_bytes() for p in original}
    assert not (tmp_path / "section-0001-repair.json").exists()


@pytest.mark.parametrize("defect", ["accepted", "request", "schema", "response", "metrics", "masked_error", "model"])
def test_changed_or_misidentified_failure_is_rejected(tmp_path, monkeypatch, defect):
    bundle, initial = failed_fixture(tmp_path, monkeypatch)
    path = tmp_path / ("attempts/section-0001-repair-02.json" if defect in {"accepted", "request", "masked_error", "model"} else "response-2.json")
    row = m.whole.read(path)
    if defect == "accepted":
        row["accepted_structure"] = True
    elif defect == "request":
        row["request_sha256"] = "c" * 64
    elif defect == "masked_error":
        row["validation_error"] = "requires_different_failure"
    elif defect == "model":
        row["model_digest"] = "c" * 64
    elif defect == "schema":
        row["schema_sha256"] = "c" * 64
    elif defect == "response":
        row["response"]["message"]["content"] = "{}"
    else:
        row["metrics"]["done_reason"] = "length"
    atomic_json(path, row)
    with pytest.raises(ValueError):
        m.failed_repairs(bundle, bundle.job, initial)


def test_final_review_only_uses_original_unused_role_and_preserves_negative(tmp_path, monkeypatch):
    bundle, raw = bundle_sample(tmp_path)
    raw["coverage"]["U0"]["paragraphs"] = [7]
    bundle.proposed, bundle.metadata = m.index_proposal(raw, bundle)
    bundle.raw, bundle.initial = raw, {"synthetic": True}
    bundle.folder, bundle.count, bundle.units = tmp_path, 1, [{}, {}, {}]
    calls = []
    for name in ("execution.json", "proposal.json"):
        atomic_json(tmp_path / name, {"synthetic": True})

    class FreshJob:
        root = tmp_path
        proposals = {}
        reader = SimpleNamespace(paths=set())

        def stage(self, name, prompt, schema, context, validate, **kwargs):
            calls.append(name)
            assert context["candidate_is_untrusted"]
            assert context["full_body_indices_are_search_scope_not_proof"]
            value = verdict(schema, passed=False)
            validate(value)
            return value

    monkeypatch.setattr(m.prior, "make_job", lambda *args, **kwargs: FreshJob())
    result = m.final_review(bundle, read_only=False)
    assert calls == ["section-0001-repair-review"] and not result["final_checks_passed"]
    assert result["whole_original_coverage_required"] and not result["reader_publication_allowed"]
    assert not (tmp_path / "section-0001-repair.json").exists()


def test_actual_future_stage_uses_tagged_grammar_without_changing_original_raw(tmp_path, monkeypatch):
    job, inv, working, packet, context, raw = fake_job(tmp_path, monkeypatch)
    raw["paragraph_count"] = 2
    raw["paragraphs"] = [dict(raw["paragraphs"][0], text=f"Synthetic complete paragraph {i}") for i in range(2)]
    calls, values = [], {}

    def generate(self, name, prompt, schema, supplied, validate, **kwargs):
        calls.append(name)
        jsonschema.validate(raw, schema)
        assert m.NOTICE in m.writing.composition.PROMPTS[prompt]
        invalid = copy.deepcopy(raw)
        invalid["coverage"]["U0"]["paragraphs"] = [7]
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(invalid, schema)
        validate(raw)
        values[name] = copy.deepcopy(raw)
        atomic_json(tmp_path / (name + ".json"), {"synthetic_raw": raw})
        return raw

    monkeypatch.setattr(m.fresh.BASE_JOB, "stage", generate)
    monkeypatch.setattr(job.reader, "stage", lambda name, *args, **kwargs: copy.deepcopy(values[name]))
    before = m.fresh.section_schema, m.fresh.citation_candidate, copy.deepcopy(m.writing.composition.PROMPTS)
    with m.future_format():
        candidate = job.stage("section-0002-write", m.writing.author.WRITE_PROMPT,
            m.writing.section_schema(["U0"], working), context, lambda row: None, evidence_packet=packet)
    assert calls == ["section-0002-write"] and "paragraph_count" not in candidate
    assert m.whole.read(tmp_path / "section-0002-write.json")["synthetic_raw"]["paragraph_count"] == 2
    assert (m.fresh.section_schema, m.fresh.citation_candidate, m.writing.composition.PROMPTS) == before


def test_full_original_and_padding_obligations_survive_alongside_old_negatives(monkeypatch, tmp_path):
    bundle, raw = bundle_sample(tmp_path)
    bundle.proposed, bundle.metadata = raw, {"invalid_mappings": {"U0": {}}}
    old = [{"kind": "section_semantic_review"}, {"kind": "complete_original_source_to_actual_article_review_required"}]
    monkeypatch.setattr(m.writing, "final_issues", lambda *args: copy.deepcopy(old))
    with m.runtime_issues(bundle):
        issues = m.writing.final_issues(bundle.inventory, {}, [])
    assert issues[:2] == old
    assert [row["kind"] for row in issues[2:]] == ["untrusted_padded_write_requires_complete_original_coverage",
        "invalid_index_search_scope_requires_complete_original_coverage"]


def test_original_orchestration_replays_reviewed_proposal_without_retrying_write_or_repair(tmp_path, monkeypatch):
    bundle, raw = bundle_sample(tmp_path)
    bundle.folder = tmp_path / "review"
    bundle.job.key = "a" * 32
    bundle.extra, bundle.parent = {}, {}
    bundle.contract = {"future_units": ["section-0002"]}
    calls = []

    class FreshJob:
        def __init__(self, *args):
            self.key = bundle.job.key
            self.reader = SimpleNamespace(paths=set())

        def stage(self, name, prompt, schema, context, validate, **kwargs):
            calls.append(name)
            assert m.fresh.section_schema is m.tagged_schema
            return "Synthetic unused future role"

    monkeypatch.setattr(m.prior.original, "first_type", lambda *args, **kwargs: FreshJob)
    audit = verdict(m.writing.review_schema(raw, ["U0"]), passed=False)
    result = {"candidate": raw, "final_review": audit, "files": {}}
    bundle.selected = "section-0001-repair-02.json"
    job = m.resumed_type(bundle, result, read_only=False)()
    for role in ("write", "review", "repair", "repair-review"):
        context = dict(bundle.context)
        if role != "write":
            context["candidate"] = raw
        if role == "repair":
            context["review"] = audit
        prompt = m.writing.author.WRITE_PROMPT if role == "write" else (
            m.writing.author.REPAIR_PROMPT if role == "repair" else m.writing.author.REVIEW_PROMPT)
        schema = m.writing.section_schema(["U0"], bundle.working) if role in {"write", "repair"} else m.writing.review_schema(raw, ["U0"])
        value = job.stage("section-0001-" + role, prompt, schema, context, lambda row: None,
            evidence_packet=bundle.scope["original"])
        assert value == (raw if role in {"write", "repair"} else audit)
    assert calls == [] and not (tmp_path / "section-0001-repair.json").exists()
    assert job.stage("section-0002-write", None, {}, {}, lambda row: None) == "Synthetic unused future role"
    assert calls == ["section-0002-write"]
    receipt = m.whole.read(bundle.folder / "derived/section-0001-repair.json")
    assert not receipt["accepted_failed_repair_cache_created"] and receipt["new_model_calls"] == 0
