import copy
import json
from types import SimpleNamespace

import pytest

from wechat_decrypt_tool import course_textbook_whole_repair_resume as m
from wechat_decrypt_tool.course_knowledge import atomic_json
from test_course_textbook_visible_context import fixture


def failed_history(tmp_path, monkeypatch):
    inventory, working, packet, context, original = fixture()
    scope = m.visible.visible_scope(["U0"], working, inventory, context, packet)
    schema = m.unit.grammar.bind(m.writing.section_schema(["U0"], working), scope["expanded"])
    job = SimpleNamespace(root=tmp_path, models={"qwen3.5:9b": "a" * 64})
    request = {"prompt": "Synthetic exact repair prompt", "schema": schema, "binding": "b" * 64}
    monkeypatch.setattr(m.draft, "capture_request", lambda *args: copy.deepcopy(request))
    (tmp_path / "attempts").mkdir()
    previous, prefix = "", "section-0007"
    for number in [1, 2]:
        candidate = copy.deepcopy(original)
        candidate["paragraphs"][0]["text"] += " " + str(number)
        messages = [{"role": "system", "content": request["prompt"]}, {"role": "user", "content": json.dumps(
            {**context, "previous_validation_failure": previous}, ensure_ascii=False)}]
        metrics = {"model_digest": "a" * 64, "done_reason": "stop", "size_vram": 1,
            "requested_context": 1024, "measured_context": 1024, "expected_content_tokens": 20,
            "prompt_eval_count": 20, "reserved_output": 16}
        row = {"stage": prefix + "-repair", "attempt": number, "input_sha256": request["binding"],
            "model_digest": "a" * 64, "request_sha256": m.digest(messages), "raw_response": json.dumps(candidate),
            "model_metrics": metrics, "error_type": "ValueError", "validation_error": m.ERROR}
        atomic_json(tmp_path / "attempts" / f"{prefix}-repair-{number:02}.json", row)
        atomic_json(tmp_path / f"response-{number}.json", {"stage": row["stage"], "request_sha256": row["request_sha256"],
            "response": {"message": {"content": row["raw_response"]}}, "metrics": metrics})
        previous = m.ERROR
    return job, schema, context, scope["expanded"], working, inventory, scope, prefix


def test_original_repair_text_disposition_and_coverage_remain_unchanged_and_no_cache_is_forged(tmp_path, monkeypatch):
    args = failed_history(tmp_path, monkeypatch)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    raw, proposed, proof = m.failed_repair(*args)
    assert proof["selected_repair_attempt"] == 1 and len(proof["citation_proposals"]) == 1
    assert proposed["paragraphs"][0]["citations"] == ["O00001", "O00002"]
    assert proposed["paragraphs"][0]["text"] == raw["paragraphs"][0]["text"]
    assert proposed["coverage"] == raw["coverage"] and proposed["kind"] == raw["kind"]
    assert proof["citation_proposals_are_factual_evidence"] is False
    assert all(p.read_bytes() == data for p, data in before.items())
    assert not (tmp_path / "section-0007-repair.json").exists()


@pytest.mark.parametrize("changed", ["request", "input", "model", "accepted", "failure", "missing", "response", "cache"])
def test_original_budget_request_response_and_defect_must_all_authenticate(tmp_path, monkeypatch, changed):
    args = failed_history(tmp_path, monkeypatch)
    path = tmp_path / "attempts/section-0007-repair-01.json"
    row = json.loads(path.read_bytes())
    if changed == "missing":
        path.unlink()
    elif changed == "cache":
        atomic_json(tmp_path / "section-0007-repair.json", {})
    elif changed == "response":
        response = json.loads((tmp_path / "response-1.json").read_bytes())
        response["response"]["message"]["content"] = "Changed synthetic response"
        atomic_json(tmp_path / "response-1.json", response)
    else:
        key = {"request": "request_sha256", "input": "input_sha256", "model": "model_digest",
            "accepted": "accepted_structure", "failure": "validation_error"}[changed]
        row[key] = True if changed == "accepted" else "Changed synthetic identity"
        atomic_json(path, row)
    with pytest.raises(ValueError):
        m.failed_repair(*args)


def test_proposal_cannot_hide_unseen_sources_or_bad_coverage_or_change_teaching_disposition():
    inventory, working, packet, context, original = fixture()
    scope = m.visible.visible_scope(["U0"], working, inventory, context, packet)
    for changed in ["source", "mapping", "disposition"]:
        value = copy.deepcopy(original)
        if changed == "source":
            value["quiz"]["citations"] = ["O00004"]
        elif changed == "mapping":
            value["coverage"]["U0"]["paragraphs"] = [99]
        else:
            value["kind"] = "non_teaching"
        with pytest.raises(ValueError):
            m.propose(value, ["U0"], working, inventory, scope)


def test_negative_final_judgments_are_retained_in_result(tmp_path, monkeypatch):
    args = failed_history(tmp_path, monkeypatch)
    raw, candidate, history = m.failed_repair(*args)
    bundle = SimpleNamespace(key="a" * 32, prefix="section-0007", candidate=candidate, raw_candidate=raw,
        repair_history=history, initial={"passed": False, "reason": "Synthetic initial negative judgment"},
        job=SimpleNamespace(root=tmp_path))
    final = {"passed": False, "reason": "Synthetic independently unresolved source support"}
    result = m.result(bundle, final, {"synthetic_contract": True})
    assert result["actual_final_review"] == final and result["factual_checks_passed"] is False
    assert result["original_initial_review"] == bundle.initial
    assert result["repair_model_cache_fabricated"] is False and result["reader_publication_allowed"] is False
