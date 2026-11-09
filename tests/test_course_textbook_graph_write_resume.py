import copy
import json
from types import SimpleNamespace

import pytest

from wechat_decrypt_tool import course_textbook_graph_write_resume as m
from wechat_decrypt_tool.course_knowledge import atomic_json
from test_course_textbook_fresh_authoring import fake_job
from test_course_textbook_visible_context import fixture
from test_course_textbook_whole_unit_repair import verdict


def sample():
    inventory, working, packet, context, raw = fixture()
    raw["paragraphs"].append(dict(raw["paragraphs"][0], text="Synthetic second explanation"))
    raw["paragraph_count"] = 2
    raw["diagrams"] = [{"kind": "flow", "title": "Synthetic", "caption": "Synthetic",
        "nodes": [{"id": "a", "label": "Synthetic A"}, {"id": "b", "label": "Synthetic B"}],
        "edges": [{"from": "a", "to": "missing", "label": "Synthetic relation"}],
        "citations": ["O00001"]}]
    return inventory, working, packet, context, raw


def history(tmp_path, monkeypatch):
    (tmp_path / "attempts").mkdir()
    inventory, working, packet, context, raw = sample()
    scope = m.fresh.continuation.scope_for(["U0"], working, inventory, context, packet)
    schema = m.indexed.tagged_schema(m.writing.section_schema(["U0"], working), scope["expanded"], working["pending_source_cards"])
    job = SimpleNamespace(root=tmp_path, models={"qwen3.5:9b": "synthetic_model"})
    bundle = SimpleNamespace(job=job, base=SimpleNamespace(contract={"synthetic": True}), prefix="section-0008",
        context=context, inventory=inventory, working=working, scope=scope, paths=set())
    monkeypatch.setattr(m.fresh.continuation.draft, "capture_request", lambda *args: {
        "schema": schema, "binding": "synthetic_binding", "prompt": "Synthetic prompt"})
    authenticated = []
    monkeypatch.setattr(m.whole.editor.e, "verify_response", lambda root, stage, row: authenticated.append(row["attempt"]))
    name = bundle.prefix + "-write"
    atomic_json(tmp_path / (name + "-prospective-request.json"), {"policy": m.fresh.POLICY,
        "execution_sha256": m.digest(bundle.base.contract), "stage": name,
        "original_schema_sha256": m.digest(m.writing.section_schema(["U0"], working)),
        "actual_schema_sha256": m.digest(schema), "original_context_sha256": m.digest(context),
        "actual_context_sha256": m.digest(context), "original_packet_sha256": m.digest(packet),
        "actual_packet_sha256": m.digest(scope["expanded"]),
        "original_budgets_reset": False, "reader_publication_allowed": False})
    for number in (1, 2):
        body = copy.deepcopy(raw)
        body["paragraphs"][0]["text"] += f" {number}"
        text = json.dumps(body)
        messages = [{"role": "system", "content": "Synthetic prompt"}, {"role": "user", "content": json.dumps(
            {**context, "previous_validation_failure": "" if number == 1 else "ValueError"}, ensure_ascii=False)}]
        metrics = {"done_reason": "stop", "eval_count": number}
        row = {"stage": name, "attempt": number, "input_sha256": "synthetic_binding", "model_digest": "synthetic_model",
            "request_sha256": m.digest(messages), "error_type": "ValueError", "validation_error": "ValueError",
            "raw_response": text, "model_metrics": metrics}
        atomic_json(tmp_path / "attempts" / f"{name}-{number:02d}.json", row)
        atomic_json(tmp_path / f"response-{number}.json", {"stage": name, "request_sha256": row["request_sha256"],
            "schema_sha256": m.digest(schema), "response": {"message": {"content": text}}, "metrics": metrics})
    return bundle, authenticated


def test_authenticate_complete_failure_without_graph_edit_or_accepted_write(tmp_path, monkeypatch):
    bundle, authenticated = history(tmp_path, monkeypatch)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*.json")}
    m.failed_writes(bundle)
    assert authenticated == [1, 2]
    assert bundle.candidate["diagrams"][0]["edges"][0]["to"] == "missing"
    assert bundle.candidate["paragraphs"][0]["text"].endswith(" 2")
    assert bundle.defects[0]["unknown_endpoints"] == 1
    assert bundle.defects[0]["unconnected_nodes"] == 1
    assert "paragraph_count" not in bundle.candidate
    assert before == {path: path.read_bytes() for path in tmp_path.rglob("*.json")}
    assert not (tmp_path / "section-0008-write.json").exists()


@pytest.mark.parametrize("bad", ["request", "schema", "raw", "length_stop", "cache", "third", "prospective"])
def test_reject_unbound_or_unbudgeted_history(tmp_path, monkeypatch, bad):
    bundle, _ = history(tmp_path, monkeypatch)
    p = tmp_path / "attempts/section-0008-write-01.json"
    q = tmp_path / "response-1.json"
    row, receipt = m.whole.read(p), m.whole.read(q)
    if bad == "request":
        row["request_sha256"] = "forged"
    elif bad == "schema":
        receipt["schema_sha256"] = "forged"
    elif bad == "raw":
        receipt["response"]["message"]["content"] = "forged"
    elif bad == "length_stop":
        row["model_metrics"]["done_reason"] = "length"
        receipt["metrics"]["done_reason"] = "length"
    elif bad == "cache":
        atomic_json(tmp_path / "section-0008-write.json", {"synthetic": True})
    elif bad == "third":
        atomic_json(tmp_path / "attempts/section-0008-write-03.json", row)
    else:
        path = tmp_path / "section-0008-write-prospective-request.json"
        data = m.whole.read(path)
        data["actual_context_sha256"] = "forged"
        atomic_json(path, data)
    atomic_json(p, row)
    atomic_json(q, receipt)
    with pytest.raises(ValueError):
        m.failed_writes(bundle)


def test_no_automatic_graph_guessing_or_unrelated_error_recovery():
    raw = sample()[-1]
    before = copy.deepcopy(raw)
    assert m.graph_defects(raw)[0]["nodes"] == 2
    assert raw == before
    raw["diagrams"][0]["nodes"][1]["id"] = "a"
    with pytest.raises(ValueError, match="only_observed_endpoint_defect"):
        m.graph_defects(raw)
    raw = sample()[-1]
    raw["diagrams"][0]["edges"][0]["to"] = "b"
    with pytest.raises(ValueError, match="actual_invalid_graph"):
        m.graph_defects(raw)


def test_mandatory_original_repair_even_if_first_review_misses_defect(tmp_path, monkeypatch):
    job = fake_job(tmp_path, monkeypatch)[0]
    inventory, working, packet, context, raw = sample()
    untrusted = copy.deepcopy(raw)
    untrusted.pop("paragraph_count")
    scope = m.fresh.continuation.scope_for(["U0"], working, inventory, context, packet)
    monkeypatch.setattr(m.original, "job_type", lambda *args, **kwargs: type(job))
    monkeypatch.setattr(m.writing, "TextbookStore", lambda root: None)
    values, called = {}, []

    def generate(self, name, prompt, schema, supplied, validate, **kwargs):
        called.append(name)
        assert supplied["candidate_is_untrusted"] is True
        assert supplied["original_repair_is_mandatory"] is True
        assert supplied["observed_structural_defects"][0]["unknown_endpoints"] == 1
        if name.endswith("-repair"):
            assert supplied["candidate"]["diagrams"][0]["edges"][0]["to"] == "missing"
            value = copy.deepcopy(raw)
            value["diagrams"][0]["edges"][0]["to"] = "b"
        else:
            value = verdict(schema, passed=not name.endswith("repair-review"))
        validate(value)
        values[name] = value
        atomic_json(tmp_path / (name + ".json"), {"synthetic_raw": value})
        return value

    monkeypatch.setattr(m.fresh.BASE_JOB, "stage", generate)
    monkeypatch.setattr(m.fresh.StrictReader, "stage", lambda self, name, *args, **kwargs: copy.deepcopy(values[name]))
    bundle = SimpleNamespace(base=None, root=tmp_path, inventory=inventory, working=working, tokenizer=None,
        count=8, units=[None] * 16, prefix="section-0008", context=context, candidate=untrusted,
        scope=scope, defects=m.graph_defects(untrusted), paths=set(), folder=tmp_path / "continuation")
    bundle.folder.mkdir()
    atomic_json(bundle.folder / "execution.json", {"synthetic": True})
    atomic_json(tmp_path / m.fresh.CONTRACT, {"synthetic_contract": True})
    result = m.original_roles(bundle, read_only=False)
    assert called == ["section-0008-review", "section-0008-repair", "section-0008-repair-review"]
    assert result["final_checks_passed"] is False
    assert result["initial_review"] == values[called[0]]
    assert result["candidate"]["diagrams"][0]["edges"][0]["to"] == "b"
    assert bundle.candidate == untrusted
    assert not (tmp_path / "section-0008-write.json").exists()
    assert result["reader_publication_allowed"] is False


@pytest.mark.parametrize("name", ["section-0008-review.json", "section-0008-repair-prospective-request.json",
    "attempts/section-0008-repair-01.json", "attempts/section-0009-write-01.json", "reading-0000.json"])
def test_cannot_refresh_a_used_remaining_role(tmp_path, name):
    bundle = SimpleNamespace(prefix="section-0008", count=8,
        units=[{"id": f"section-{i:04d}"} for i in range(10)], job=SimpleNamespace(root=tmp_path))
    m.require_unused(bundle)
    (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
    atomic_json(tmp_path / name, {"synthetic": True})
    with pytest.raises(ValueError, match="all_remaining_original_roles_unused"):
        m.require_unused(bundle)


def test_future_original_planning_and_writing_dispatch_are_unchanged(tmp_path, monkeypatch):
    called = []

    class Parent:
        def stage(self, *args, **kwargs):
            called.append((args, kwargs))
            return "original"

    monkeypatch.setattr(m.original, "job_type", lambda *args, **kwargs: Parent)
    bundle = SimpleNamespace(base=None, prefix="section-0008")
    cls = m.resumed_type(bundle, {}, read_only=True)
    job = cls.__new__(cls)
    for name in ("concepts-00-0001", "section-0007-write", "section-0009-write", "reading-0000"):
        assert job.stage(name, "original_prompt", {}, {}, None, evidence_packet={}) == "original"
    assert len(called) == 4


def test_inherited_issues_and_negative_state_are_retained_and_restored(monkeypatch):
    inventory = {"synthetic": True}

    def old(*args):
        return [{"kind": "original_negative_review"}]
    monkeypatch.setattr(m.writing, "final_issues", old)
    bundle = SimpleNamespace(inventory=inventory, prefix="section-0008", candidate={"synthetic": True}, defects=[{"unknown_endpoints": 1}])
    with pytest.raises(RuntimeError):
        with m.runtime_issues(bundle):
            rows = m.writing.final_issues(inventory, {}, [])
            assert rows[0]["kind"] == "original_negative_review"
            assert rows[1]["adapter_graph_changes"] == 0
            raise RuntimeError("Synthetic interruption")
    assert m.writing.final_issues is old
