import copy
import json
from types import SimpleNamespace

import jsonschema
import pytest

from wechat_decrypt_tool import course_textbook_corpus_structural_resume as m
from wechat_decrypt_tool.course_knowledge import atomic_json
from test_course_textbook_fresh_authoring import fake_job
from test_course_textbook_visible_context import fixture
from test_course_textbook_whole_unit_repair import verdict


def sample():
    inventory, working, packet, context, raw = fixture()
    scope = m.fresh.continuation.scope_for(["U0"], working, inventory, context, packet)
    raw["paragraphs"] += [dict(raw["paragraphs"][0], text="Synthetic second full explanation")]
    schema = m.section_schema(m.writing.section_schema(["U0"], working), scope["expanded"], working["pending_source_cards"])
    return inventory, working, packet, context, scope, raw, schema


def test_variable_format_allows_fewer_than_twelve_real_paragraphs_without_losing_a_task():
    _, _, _, _, _, raw, schema = sample()
    for count in (2, 3, 9, 12):
        raw["paragraphs"] = [dict(raw["paragraphs"][0], text=f"Synthetic complete paragraph {i}") for i in range(count)]
        jsonschema.validate(raw, schema)
    for count in (1, 13):
        raw["paragraphs"] = [dict(raw["paragraphs"][0], text=f"Synthetic complete paragraph {i}") for i in range(count)]
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(raw, schema)


@pytest.mark.parametrize("change", ["blank", "duplicate", "missing_task", "empty_mapping", "unknown_reference", "false_mapping", "large_index"])
def test_decoding_rules_reject_padding_and_keep_original_scope_and_teaching_checks(change):
    _, _, _, _, _, raw, schema = sample()
    if change == "blank":
        raw["paragraphs"][1]["text"] = " \n\t"
    elif change == "duplicate":
        raw["paragraphs"][1] = copy.deepcopy(raw["paragraphs"][0])
    elif change == "missing_task":
        raw["coverage"] = {}
    elif change == "empty_mapping":
        raw["coverage"]["U0"]["paragraphs"] = []
    elif change == "unknown_reference":
        raw["paragraphs"][0]["citations"] = ["O00004"]
    elif change == "false_mapping":
        raw["coverage"]["U0"]["teaching"] = False
    else:
        raw["coverage"]["U0"]["paragraphs"] = [12]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(raw, schema)


@pytest.mark.parametrize("change,code", [("blank", "nonempty_actual_paragraphs"),
    ("duplicate", "distinct_actual_paragraphs"), ("mapping", "mapping_indices_within_actual_array_length_2")])
def test_actual_validation_returns_the_real_structural_error_instead_of_citation_mask(change, code):
    inv, working, _, _, scope, raw, _ = sample()
    if change == "blank":
        raw["paragraphs"][1]["text"] = " "
    elif change == "duplicate":
        raw["paragraphs"][1]["text"] = raw["paragraphs"][0]["text"]
        raw["paragraphs"][1]["citations"] = ["O00000"]
    else:
        raw["coverage"]["U0"]["paragraphs"] = [2]
    before = copy.deepcopy(raw)
    with pytest.raises(ValueError, match=code):
        m.citation_candidate(raw, ["U0"], working, inv, scope)
    assert raw == before


def test_only_citation_metadata_can_be_proposed_and_original_raw_remains_unchanged():
    inv, working, _, _, scope, raw, _ = sample()
    before = copy.deepcopy(raw)
    candidate, changes = m.citation_candidate(raw, ["U0"], working, inv, scope)
    assert changes and candidate["coverage"] == raw["coverage"]
    assert candidate["paragraphs"][0]["text"] == raw["paragraphs"][0]["text"]
    assert raw == before
    raw["quiz"]["answer"] = " "
    with pytest.raises(ValueError, match="missing_textbook_authored_text"):
        m.citation_candidate(raw, ["U0"], working, inv, scope)


def test_format_is_restored_after_error_and_explicit_nonteaching_branch_remains_possible():
    _, _, _, _, _, _, schema = sample()
    excluded = {"kind": "non_teaching", "coverage": {"U0": {"teaching": False, "paragraphs": [], "reason": "Synthetic administrative source"}}}
    jsonschema.validate(excluded, schema)
    before = m.fresh.section_schema, m.fresh.citation_candidate, copy.deepcopy(m.writing.composition.PROMPTS)
    with pytest.raises(RuntimeError):
        with m.variable_format():
            assert m.NOTICE in m.writing.composition.PROMPTS[m.writing.author.REPAIR_PROMPT]
            raise RuntimeError("Synthetic failure")
    assert (m.fresh.section_schema, m.fresh.citation_candidate, m.writing.composition.PROMPTS) == before


def test_actual_unused_role_receives_variable_schema_and_keeps_raw_prose(tmp_path, monkeypatch):
    job, inv, working, packet, context, raw = fake_job(tmp_path, monkeypatch)
    raw["paragraphs"] = [dict(raw["paragraphs"][0], text=f"Synthetic complete explanation {i}") for i in range(3)]
    values, calls = {}, []

    def generate(self, name, prompt, schema, supplied, validate, **kwargs):
        calls.append(name)
        body = schema["oneOf"][0]["properties"]
        assert body["paragraphs"]["minItems"] == 2 and body["paragraphs"]["maxItems"] == 12
        assert m.NOTICE in m.writing.composition.PROMPTS[prompt]
        validate(raw)
        values[name] = copy.deepcopy(raw)
        atomic_json(tmp_path / (name + ".json"), {"synthetic_raw": raw})
        return raw

    monkeypatch.setattr(m.fresh.BASE_JOB, "stage", generate)
    monkeypatch.setattr(job.reader, "stage", lambda name, *args, **kwargs: copy.deepcopy(values[name]))
    with m.variable_format():
        candidate = job.stage("section-0001-repair", m.writing.author.REPAIR_PROMPT,
            m.writing.section_schema(["U0"], working), context, lambda row: None, evidence_packet=packet)
    assert calls == ["section-0001-repair"]
    assert [p["text"] for p in candidate["paragraphs"]] == [p["text"] for p in raw["paragraphs"]]
    assert json.loads((tmp_path / "section-0001-repair.json").read_bytes())["synthetic_raw"] == raw


def failed_fixture(tmp_path, monkeypatch):
    (tmp_path / "attempts").mkdir()
    inv, working, packet, context, scope, raw, _ = sample()
    raw["paragraphs"][1] = copy.deepcopy(raw["paragraphs"][0])
    job = SimpleNamespace(root=tmp_path, models={"qwen3.5:9b": "b" * 64})
    bundle = SimpleNamespace(job=job, prefix="section-0001", context=context, scope=scope,
        inventory=inv, working=working, original={"synthetic": True}, paths=set())
    schema = m.original.bounded.bounded_schema(m.writing.section_schema(["U0"], working), scope["expanded"], working["pending_source_cards"])
    request = {"prompt": "Synthetic actual prompt", "schema": schema, "binding": "a" * 64}
    monkeypatch.setattr(m.fresh.continuation.draft, "capture_request", lambda *args: request)
    monkeypatch.setattr(m.whole.editor.e, "verify_response", lambda *args: None)
    stage = bundle.prefix + "-write"
    atomic_json(tmp_path / (stage + "-prospective-request.json"), {"policy": m.fresh.POLICY,
        "execution_sha256": m.digest(bundle.original), "stage": stage,
        "original_schema_sha256": m.digest(m.writing.section_schema(["U0"], working)),
        "actual_schema_sha256": m.digest(schema), "original_context_sha256": m.digest(context),
        "actual_context_sha256": m.digest(context), "original_packet_sha256": m.digest(packet),
        "actual_packet_sha256": m.digest(scope["expanded"]), "original_budgets_reset": False, "reader_publication_allowed": False})
    previous = ""
    for number in (1, 2):
        messages = [{"role": "system", "content": request["prompt"]}, {"role": "user", "content": json.dumps(
            {**context, "previous_validation_failure": previous}, ensure_ascii=False)}]
        row = {"stage": stage, "attempt": number, "input_sha256": request["binding"], "request_sha256": m.digest(messages),
            "model_digest": "b" * 64, "error_type": "ValueError", "accepted_structure": False,
            "validation_error": "requires_fresh_authoring_only_primary_citation_metadata_proposed",
            "raw_response": json.dumps(raw), "model_metrics": {"done_reason": "stop"}}
        atomic_json(tmp_path / "attempts" / f"{stage}-{number:02d}.json", row)
        atomic_json(tmp_path / f"response-{number}.json", {"stage": stage, "request_sha256": row["request_sha256"],
            "schema_sha256": m.digest(schema), "response": {"message": {"content": row["raw_response"]}}, "metrics": row["model_metrics"]})
        previous = row["validation_error"]
    return bundle


def test_two_actual_failed_responses_are_authenticated_without_combining_or_editing(tmp_path, monkeypatch):
    bundle = failed_fixture(tmp_path, monkeypatch)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    m.failed_writes(bundle)
    assert len(bundle.history) == 2 and bundle.defects["duplicate_paragraph_groups"] == [[0, 1]]
    assert {p: p.read_bytes() for p in before} == before
    assert not (tmp_path / "section-0001-write.json").exists()


@pytest.mark.parametrize("change", ["accepted_cache", "request", "schema", "response", "metrics", "attempt"])
def test_invalid_history_or_wire_receipt_is_rejected(tmp_path, monkeypatch, change):
    bundle = failed_fixture(tmp_path, monkeypatch)
    if change == "accepted_cache":
        atomic_json(tmp_path / "section-0001-write.json", {})
    else:
        path = tmp_path / ("attempts/section-0001-write-02.json" if change in {"request", "attempt"} else "response-2.json")
        row = m.whole.read(path)
        if change == "request":
            row["request_sha256"] = "c" * 64
        elif change == "attempt":
            row["accepted_structure"] = True
        elif change == "schema":
            row["schema_sha256"] = "c" * 64
        elif change == "response":
            row["response"]["message"]["content"] = "{}"
        else:
            row["metrics"]["done_reason"] = "length"
        atomic_json(path, row)
    with pytest.raises(ValueError):
        m.failed_writes(bundle)


def test_repair_is_mandatory_and_negative_final_review_is_preserved(tmp_path, monkeypatch):
    inv, working, packet, context, scope, raw, _ = sample()
    repaired, _ = m.citation_candidate(raw, ["U0"], working, inv, scope)
    bundle = SimpleNamespace(root=tmp_path, folder=tmp_path / "resume", count=1, units=[{}, {}, {}],
        context=context, scope=scope, inventory=inv, working=working, prefix="section-0001",
        candidate=raw, defects={"blank_paragraph_indices": [1]})
    calls = []

    class FreshJob:
        root = tmp_path
        reader = SimpleNamespace(paths=set())

        def stage(self, name, prompt, schema, supplied, validate, **kwargs):
            calls.append(name)
            assert supplied["candidate_is_untrusted"] and supplied["original_repair_is_mandatory"]
            if name.endswith("-repair"):
                assert m.fresh.section_schema is m.section_schema
                value = repaired
            else:
                value = verdict(schema, passed=not name.endswith("-repair-review"))
            with m.visible.packet_scope(scope):
                validate(value)
            return value

    monkeypatch.setattr(m, "make_job", lambda *args, **kwargs: FreshJob())
    result = m.original_roles(bundle, read_only=False)
    assert calls == ["section-0001-review", "section-0001-repair", "section-0001-repair-review"]
    assert result["final_checks_passed"] is False
    assert not (tmp_path / "section-0001-write.json").exists()


def test_any_used_remaining_original_role_blocks_registration(tmp_path):
    bundle = SimpleNamespace(job=SimpleNamespace(root=tmp_path), prefix="section-0001", count=1,
        units=[{}, {}, {"id": "section-0002"}])
    m.require_unused(bundle)
    (tmp_path / "attempts").mkdir()
    atomic_json(tmp_path / "attempts/section-0002-write-01.json", {})
    with pytest.raises(ValueError, match="remaining_original_roles_unused"):
        m.require_unused(bundle)


def test_full_original_coverage_obligation_is_retained_along_with_existing_negatives(monkeypatch):
    inv = {"synthetic": True}
    bundle = SimpleNamespace(inventory=inv, prefix="section-0001", candidate={"synthetic": "untrusted"})
    old = [{"kind": "section_semantic_review"}, {"kind": "complete_original_source_to_actual_article_review_required"}]
    monkeypatch.setattr(m.writing, "final_issues", lambda *args: copy.deepcopy(old))
    with m.runtime_issues(bundle):
        issues = m.writing.final_issues(inv, {}, [])
    assert issues[:2] == old and issues[2]["kind"] == "untrusted_padded_write_requires_complete_original_coverage"
