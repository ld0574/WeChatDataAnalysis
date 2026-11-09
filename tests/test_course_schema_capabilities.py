import copy
import json

import pytest

from wechat_decrypt_tool import course_schema_capabilities as m


def fixture(case, content):
    plan = {"model_digest": "a" * 64}
    response = {"done_reason": "stop", "message": {"content": json.dumps(content)},
        "prompt_eval_count": 32, "eval_count": 12}
    loaded = {"models": [{"digest": plan["model_digest"], "context_length": m.OPTIONS["num_ctx"], "size_vram": 100}]}
    return case, response, loaded, plan


def test_probes_use_only_fixed_synthetic_inputs_and_exactly_three_calls():
    cases = m.cases()
    assert len(cases) == 3 and len({case["id"] for case in cases}) == 3
    for case in cases:
        packet = m.request(case)
        assert packet["format"] == case["schema"]
        assert packet["model"] == "course-defiant:q4" and packet["think"] is False
        assert packet["options"]["num_predict"] == 256
        assert packet["messages"] == [{"role": "user", "content": case["prompt"]}]


@pytest.mark.parametrize(("index", "content", "keywords"), [
    (0, {}, ["minProperties"]),
    (0, {"1": [101]}, []),
    (1, {}, ["required", "required"]),
    (1, {"paragraph": 2, "sources": [101]}, []),
    (1, {"paragraph": 2, "sources": []}, ["minItems"]),
    (2, {"1": [101], "2": [101], "3": [101]}, ["maxProperties"]),
])
def test_independent_validator_detects_actual_provider_keyword_violations(index, content, keywords):
    result = m.outcome(*fixture(m.cases()[index], content))
    assert result["violated_keywords"] == keywords
    assert result["schema_valid"] == (not keywords)


@pytest.mark.parametrize("change", ["truncated", "invalid_json", "wrong_digest", "wrong_context", "no_vram"])
def test_incomplete_generation_and_unproven_gpu_are_not_silent_success(change):
    case, response, loaded, plan = fixture(m.cases()[0], {"1": [101]})
    if change == "truncated":
        response["done_reason"] = "length"
    elif change == "invalid_json":
        response["message"]["content"] = "{"
        assert m.outcome(case, response, loaded, plan)["violated_keywords"] == ["invalid_json"]
        return
    elif change == "wrong_digest":
        loaded["models"][0]["digest"] = "b" * 64
    elif change == "wrong_context":
        loaded["models"][0]["context_length"] = 1024
    else:
        loaded["models"][0]["size_vram"] = 0
    with pytest.raises(ValueError):
        m.outcome(case, response, loaded, plan)


def test_readonly_verifier_reconstructs_all_fixed_requests_and_actual_outcomes(tmp_path, monkeypatch):
    root, folder, plan = tmp_path, tmp_path / "job", {"model_digest": "a" * 64, "cases": m.cases()}
    folder.mkdir()
    rows = []
    for case in plan["cases"]:
        _, response, loaded, _ = fixture(case, {})
        m.atomic_json(folder / (case["id"] + "-request-sent.json"), m.request(case))
        (folder / (case["id"] + "-wire-response.bin")).write_text(json.dumps(response))
        m.atomic_json(folder / (case["id"] + "-loaded.json"), loaded)
        row = m.outcome(case, response, loaded, plan)
        rows.append(row)
        m.atomic_json(folder / (case["id"] + "-outcome.json"), row)
    result = m.summary("c" * 64, rows)
    m.atomic_json(folder / "result.json", result)
    monkeypatch.setattr(m, "load", lambda *_: (root, folder, plan))
    monkeypatch.setattr(m, "local_model_digest", lambda *_: pytest.fail("readonly called provider"))
    monkeypatch.setattr(m.guard, "checkpoint", lambda *_: pytest.fail("readonly checkpoint"))
    before = {path: path.read_bytes() for path in folder.iterdir()}
    assert m.verify(root, "c" * 64) == result
    assert all(path.read_bytes() == raw for path, raw in before.items())
    bad = copy.deepcopy(rows[0])
    bad["schema_valid"] = True
    m.atomic_json(folder / "min_properties-outcome.json", bad)
    with pytest.raises(ValueError, match="actual_schema_outcome"):
        m.verify(root, "c" * 64)


def test_summary_never_claims_course_progress_or_universal_decoder_support():
    result = m.summary("a" * 64, [])
    assert result["course_inputs"] == result["course_roles_used"] == 0
    assert not result["course_budgets_reset"]
    assert not result["universal_keyword_support_proven"]
    assert not result["reader_publication_allowed"]
