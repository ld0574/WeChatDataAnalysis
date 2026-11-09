import copy
import json
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import pytest

from wechat_decrypt_tool import course_textbook_one_based_writer as m
from wechat_decrypt_tool.course_knowledge import atomic_json


def sample():
    cards = {key: {"label": key, "teaching": "Synthetic teaching", "category": "concept"} for key in "abcdefghijkl"}
    raw = {"title": "Synthetic title", "groups": [{"heading": "Concept A", "purpose": "Explain prerequisites"},
        {"heading": "Concept B", "purpose": "Explain applications"}],
        "assignments": {key: {"group": 1 if i < 6 else 2, "order": i % 3} for i, key in enumerate(cards)}}
    return cards, raw


def setup_history(tmp_path, monkeypatch):
    (tmp_path / "attempts").mkdir()
    cards, raw = sample()
    stage = "concepts-00-0001"
    context = {"items": cards, "mode": "concepts"}
    schema = m.writing.author.grouping_schema(cards, final=False)
    request = {"prompt": "Synthetic original prompt", "schema": schema, "binding": "original-input-binding"}
    job = SimpleNamespace(root=tmp_path, key="a" * 32, models={"qwen3.5:9b": "model-digest"}, range_notice="")
    monkeypatch.setattr(m.requests, "context_for", lambda *args: (cards, False, context))
    monkeypatch.setattr(m.requests, "transformed_request", lambda *args: request)
    authenticated = []
    monkeypatch.setattr(m.whole.editor.e, "verify_response", lambda folder, name, row: authenticated.append(row["attempt"]))
    for attempt in [1, 2]:
        messages = [{"role": "system", "content": request["prompt"]}, {"role": "user",
            "content": json.dumps({**context, "previous_validation_failure": "" if attempt == 1 else "IndexError"}, ensure_ascii=False)}]
        text = json.dumps(raw)
        metrics = {"model_digest": "model-digest", "eval_count": attempt}
        record = {"stage": stage, "attempt": attempt, "input_sha256": request["binding"],
            "model_digest": "model-digest", "error_type": "IndexError", "validation_error": "IndexError",
            "request_sha256": m.digest(messages), "raw_response": text, "model_metrics": metrics}
        atomic_json(tmp_path / "attempts" / f"{stage}-{attempt:02d}.json", record)
        atomic_json(tmp_path / f"response-{attempt}.json", {"stage": stage,
            "request_sha256": record["request_sha256"], "schema_sha256": m.digest(schema),
            "response": {"message": {"content": text}}, "metrics": metrics})
    return job, raw, stage, authenticated


def test_history_uses_earliest_complete_original_without_inference_or_file_changes(tmp_path, monkeypatch):
    job, raw, stage, authenticated = setup_history(tmp_path, monkeypatch)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    result = m.history(job, {}, stage)
    assert authenticated == [1, 2]
    assert result["receipt"]["selected_attempt"] == 1
    assert result["receipt"]["model_calls"] == 0
    candidate = result["candidate"]
    assert candidate["title"] == raw["title"] and candidate["groups"] == raw["groups"]
    for key, row in candidate["assignments"].items():
        assert row["group"] == raw["assignments"][key]["group"] - 1
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    assert not (tmp_path / (stage + ".json")).exists()


@pytest.mark.parametrize("bad", ["request", "schema", "raw_response", "metrics", "accepted", "wrong_error", "third_attempt", "forged_cache"])
def test_history_rejects_unbound_or_unbudgeted_originals(tmp_path, monkeypatch, bad):
    job, _, stage, _ = setup_history(tmp_path, monkeypatch)
    attempt_path = tmp_path / "attempts" / (stage + "-01.json")
    receipt_path = tmp_path / "response-1.json"
    attempt, receipt = m.whole.read(attempt_path), m.whole.read(receipt_path)
    if bad == "request":
        attempt["request_sha256"] = "different"
    elif bad == "schema":
        receipt["schema_sha256"] = "different"
    elif bad == "raw_response":
        receipt["response"]["message"]["content"] = "different"
    elif bad == "metrics":
        receipt["metrics"]["eval_count"] = 99
    elif bad == "accepted":
        attempt["accepted_structure"] = True
    elif bad == "wrong_error":
        attempt["error_type"] = "ValueError"
    elif bad == "third_attempt":
        atomic_json(tmp_path / "attempts" / (stage + "-03.json"), attempt)
    else:
        atomic_json(tmp_path / (stage + ".json"), {"synthetic": True})
    atomic_json(attempt_path, attempt)
    atomic_json(receipt_path, receipt)
    with pytest.raises(ValueError):
        m.history(job, {}, stage)


@pytest.mark.parametrize("bad", ["missing_group", "zero_and_one_mixed", "outside", "duplicate_heading", "blank_purpose", "missing_assignment"])
def test_no_guessing_ambiguous_group_meanings(tmp_path, monkeypatch, bad):
    job, raw, stage, _ = setup_history(tmp_path, monkeypatch)
    if bad == "missing_group":
        for row in raw["assignments"].values():
            row["group"] = 1
    elif bad == "zero_and_one_mixed":
        raw["assignments"]["a"]["group"] = 0
    elif bad == "outside":
        raw["assignments"]["a"]["group"] = 3
    elif bad == "duplicate_heading":
        raw["groups"][1]["heading"] = raw["groups"][0]["heading"]
    elif bad == "blank_purpose":
        raw["groups"][1]["purpose"] = " "
    else:
        raw["assignments"].pop("a")
    for i in [1, 2]:
        a = tmp_path / "attempts" / f"{stage}-{i:02d}.json"
        r = tmp_path / f"response-{i}.json"
        record, receipt = m.whole.read(a), m.whole.read(r)
        text = json.dumps(raw)
        record["raw_response"] = receipt["response"]["message"]["content"] = text
        atomic_json(a, record)
        atomic_json(r, receipt)
    with pytest.raises(ValueError, match="complete_unambiguous"):
        m.history(job, {}, stage)


@pytest.mark.parametrize("final", [True, False])
def test_new_count_grammar_binds_indices_to_actual_group_array(final):
    cards, raw = sample()
    raw["group_count"] = 2
    for row in raw["assignments"].values():
        row["group"] -= 1
    before = copy.deepcopy(raw)
    candidate = m.compile_counted(raw, cards, final=final)
    assert raw == before and candidate["groups"] == raw["groups"]
    raw["assignments"]["a"]["group"] = 2
    with pytest.raises(jsonschema.ValidationError):
        m.compile_counted(raw, cards, final=final)
    raw["assignments"]["a"]["group"] = 0
    raw["group_count"] = 3
    with pytest.raises(jsonschema.ValidationError):
        m.compile_counted(raw, cards, final=final)


def test_continuation_requires_original_status_and_is_single_use(tmp_path, monkeypatch):
    status = {"state": "stopped"}
    job_root = tmp_path / "job"
    job_root.mkdir()
    atomic_json(job_root / "progress.json", status)
    b = SimpleNamespace(root=tmp_path, folder=tmp_path / "continuation", job=SimpleNamespace(root=job_root),
        execution={"original_status": status}, inventory="inventory", ledger="ledger", tokenizer="tokenizer")
    monkeypatch.setattr(m, "load", lambda *args: b)
    monkeypatch.setattr(m.guard, "checkpoint", lambda root: None)
    monkeypatch.setattr(m, "job_type", lambda *args, **kwargs: m.writing.AuthoringJob)
    calls = []
    monkeypatch.setattr(m.writing, "run", lambda *args: calls.append(args))
    m.worker(tmp_path, "a" * 32)
    assert len(calls) == 1
    with pytest.raises(ValueError, match="once"):
        m.worker(tmp_path, "a" * 32)


def test_binding_names_survive_cli_import_identity():
    assert Path(m.__file__).stem in m.bindings()["modules"]
    assert "__main__" not in m.bindings()["modules"]
