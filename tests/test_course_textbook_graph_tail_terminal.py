import copy
import json
from types import SimpleNamespace

import pytest

from wechat_decrypt_tool import course_textbook_graph_tail_terminal as m
from wechat_decrypt_tool.course_knowledge import atomic_json
from test_course_textbook_graph_write_resume import sample


def history(tmp_path, monkeypatch):
    (tmp_path / "attempts").mkdir()
    inventory, working, packet, context, raw = sample()
    scope = m.fresh.continuation.scope_for(["U0"], working, inventory, context, packet)
    schema = m.prior.indexed.tagged_schema(m.writing.section_schema(["U0"], working), scope["expanded"], working["pending_source_cards"])
    candidate = copy.deepcopy(raw)
    candidate.pop("paragraph_count")
    bundle = SimpleNamespace(base=SimpleNamespace(contract={"synthetic": True}), prefix="section-0012",
        context=context, candidate=candidate, inventory=inventory, working=working, scope=scope,
        paths=set(), defects=m.prior.graph_defects(candidate))
    job = SimpleNamespace(root=tmp_path, models={"qwen3.5:9b": "synthetic_model"})
    monkeypatch.setattr(m.fresh.continuation.draft, "capture_request", lambda *args: {
        "schema": schema, "prompt": "Synthetic repair", "binding": "original_repair_input"})
    monkeypatch.setattr(m.whole.editor.e, "verify_response", lambda *args: None)
    initial = {"synthetic": "actual_accepted_initial_review"}
    actual_context = {**context, "candidate_is_untrusted": True, "original_repair_is_mandatory": True,
        "observed_structural_defects": bundle.defects, "adapter_graph_changes": 0, "candidate": candidate, "review": initial}
    name = bundle.prefix + "-repair"
    atomic_json(tmp_path / (name + "-prospective-request.json"), {"policy": m.fresh.POLICY,
        "execution_sha256": m.digest(bundle.base.contract), "stage": name,
        "original_schema_sha256": m.digest(m.writing.section_schema(["U0"], working)),
        "actual_schema_sha256": m.digest(schema), "original_context_sha256": m.digest(actual_context),
        "actual_context_sha256": m.digest(actual_context), "original_packet_sha256": m.digest(packet),
        "actual_packet_sha256": m.digest(scope["expanded"]), "original_budgets_reset": False,
        "reader_publication_allowed": False})
    for number in (1, 2):
        text = json.dumps(raw)
        messages = [{"role": "system", "content": "Synthetic repair"}, {"role": "user", "content": json.dumps(
            {**actual_context, "previous_validation_failure": "" if number == 1 else "ValueError"}, ensure_ascii=False)}]
        row = {"stage": name, "attempt": number, "input_sha256": "original_repair_input", "model_digest": "synthetic_model",
            "request_sha256": m.digest(messages), "error_type": "ValueError", "validation_error": "ValueError",
            "raw_response": text, "model_metrics": {"done_reason": "stop"}}
        atomic_json(tmp_path / "attempts" / f"{name}-{number:02d}.json", row)
        atomic_json(tmp_path / f"response-{number}.json", {"stage": name, "request_sha256": row["request_sha256"],
            "schema_sha256": m.digest(schema), "response": {"message": {"content": text}}, "metrics": row["model_metrics"]})
    return bundle, job, initial


def test_authenticate_both_original_failed_repairs_without_accepting_or_changing_them(tmp_path, monkeypatch):
    bundle, job, initial = history(tmp_path, monkeypatch)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    actual = m.repair_failures(bundle, job, initial)
    assert len(actual) == 2 and actual[0]["raw_sha256"] == actual[1]["raw_sha256"]
    assert all(row["observed_graph_defects"][0]["unknown_endpoints"] == 1 for row in actual)
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    assert not (tmp_path / "section-0012-repair.json").exists()


@pytest.mark.parametrize("bad", ["request", "schema", "raw", "length", "accepted", "third", "prospective"])
def test_unbound_incomplete_or_unbudgeted_repair_is_not_authenticated(tmp_path, monkeypatch, bad):
    bundle, job, initial = history(tmp_path, monkeypatch)
    p, q = tmp_path / "attempts/section-0012-repair-01.json", tmp_path / "response-1.json"
    row, receipt = m.whole.read(p), m.whole.read(q)
    if bad == "request":
        row["request_sha256"] = "different_request"
    elif bad == "schema":
        receipt["schema_sha256"] = "different_schema"
    elif bad == "raw":
        receipt["response"]["message"]["content"] = "different_response"
    elif bad == "length":
        row["model_metrics"]["done_reason"] = "length"
        receipt["metrics"]["done_reason"] = "length"
    elif bad == "accepted":
        atomic_json(tmp_path / "section-0012-repair.json", {"synthetic": True})
    elif bad == "third":
        atomic_json(tmp_path / "attempts/section-0012-repair-03.json", row)
    else:
        atomic_json(tmp_path / "section-0012-repair-prospective-request.json", {"different": True})
    atomic_json(p, row)
    atomic_json(q, receipt)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    with pytest.raises(ValueError):
        m.repair_failures(bundle, job, initial)
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*.json")}


@pytest.mark.parametrize("bad", ["successful_guard", "live_worker", "paused", "other_stage", "other_prefix"])
def test_only_a_real_exhausted_terminal_repair_can_be_declared_incomplete(tmp_path, monkeypatch, bad):
    root, job = tmp_path / "tail", tmp_path / "job"
    (root / "guard").mkdir(parents=True)
    job.mkdir()
    protected = {"pid": 1, "created": 2, "state": "worker_exited", "returncode": 1}
    status = {"pid": 3, "created": 4, "state": "stopped", "completed_sections": 12, "total_sections": 16,
        "reason_code": "wiki_stage_attempt_budget_exhausted_section-0012-repair"}
    if bad == "successful_guard":
        protected["returncode"] = 0
    elif bad == "paused":
        status["state"] = "paused"
    elif bad == "other_stage":
        status["reason_code"] = "wiki_stage_attempt_budget_exhausted_section-0012-write"
    elif bad == "other_prefix":
        status["completed_sections"] = 13
    atomic_json(root / "guard/guard-progress.json", protected)
    atomic_json(job / "progress.json", status)
    bundle = SimpleNamespace(folder=root, job=SimpleNamespace(root=job), prefix="section-0012", count=12, units=[None]*16)
    monkeypatch.setattr(m.tail, "load", lambda *args: bundle)
    monkeypatch.setattr(m.writing, "identity_live", lambda row: bad == "live_worker" and row["pid"] == 3)
    with pytest.raises(ValueError, match="actual_terminal_original_repair_and_guard_exited"):
        m.inspect(tmp_path, "a"*32)
