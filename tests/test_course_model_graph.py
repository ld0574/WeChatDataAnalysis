import copy
import json

import pytest

from wechat_decrypt_tool import course_model_graph as graph
from wechat_decrypt_tool.course_model_trial import digest


@pytest.fixture
def inputs():
    sample = {
        "id": "lesson-0",
        "video": "lesson",
        "title": "synthetic",
        "window": 0,
        "category": "synthetic",
        "original_ids": [10, -20],
        "evidence": [
            {"id": 1, "time": "00:00:00", "text": "</script><script>alert(1)</script>"}
        ],
        "visuals": [{"id": 2, "time": "00:00:00", "text": "synthetic OCR"}],
    }
    plan = {"samples": [sample]}
    note = {
        "summary": "synthetic",
        "concepts": [
            {"name": "A", "explanation": "synthetic", "evidence": [1]},
            {"name": "B", "explanation": "synthetic", "evidence": [2]},
        ],
        "relations": [
            {"from": "A", "to": "A", "relation": "self", "evidence": [1]},
            {"from": "A", "to": "B", "relation": "无直接关系", "evidence": [2]},
        ],
        "questions": [],
    }
    results = {
        m: {
            "course:lesson-0": {
                "model": m,
                "status": "ok",
                "input_sha256": digest(sample),
                "attempts": [{"request_sha256": "same-request"}],
                "output": copy.deepcopy(note),
            }
        }
        for m in ["original", "candidate"]
    }
    binding = {
        "plan_sha256": digest(plan),
        "models": {"original": "a", "candidate": "b"},
    }
    return plan, results, binding


def test_preserves_all_nodes_edges_and_exact_evidence_bindings(inputs):
    data = graph.build_data(*inputs)
    sample = data["samples"][0]
    assert sample["evidence"]["1"]["original_id"] == 10
    assert sample["evidence"]["2"]["original_id"] == -20
    assert sample["evidence"]["2"]["kind"] == "ocr"
    for note in sample["models"].values():
        assert len(note["nodes"]) == len(note["edges"]) == 2
        assert note["edges"][0]["source"] == note["edges"][0]["target"] == ["node-0"]
        assert note["edges"][0]["flags"] == ["self_edge"]
        assert note["edges"][1]["flags"] == ["negative_label_candidate"]
    assert data["semantic_accuracy_verified"] is False
    assert data["source_deletion_allowed"] is False


def test_escapes_script_breakout_and_prohibits_background_connections(inputs):
    data = graph.build_data(*inputs)
    output = graph.render(data)
    assert "</script><script>alert(1)</script>" not in output
    assert "\\u003c/script\\u003e" in output
    assert "connect-src 'none'" in output
    payload = output.split('<script id="trial-data" type="application/json">')[1].split(
        "</script>"
    )[0]
    assert json.loads(payload) == data
    assert "fetch(" not in output


@pytest.mark.parametrize(
    "change,match",
    [
        ("plan", "plan changed"),
        ("input", "frozen evidence"),
        ("request", "prompts differ"),
        ("model", "model set changed"),
        ("original_ids", "source binding"),
        ("citation", "nonexistent evidence"),
    ],
)
def test_rejects_unbound_or_invalid_trial_results(inputs, change, match):
    plan, results, binding = inputs
    if change == "plan":
        binding["plan_sha256"] = "wrong"
    elif change == "input":
        results["candidate"]["course:lesson-0"]["input_sha256"] = "wrong"
    elif change == "request":
        results["candidate"]["course:lesson-0"]["attempts"][0]["request_sha256"] = (
            "different"
        )
    elif change == "model":
        del results["candidate"]
    elif change == "original_ids":
        plan["samples"][0]["original_ids"].pop()
        binding["plan_sha256"] = digest(plan)
    elif change == "citation":
        results["candidate"]["course:lesson-0"]["output"]["concepts"][0]["evidence"] = [
            999
        ]
    with pytest.raises(ValueError, match=match):
        graph.build_data(plan, results, binding)


def test_duplicate_names_remain_visible_with_endpoint_warning(inputs):
    plan, results, binding = inputs
    for group in results.values():
        group["course:lesson-0"]["output"]["concepts"].append(
            {"name": "A", "explanation": "second A", "evidence": [1]}
        )
    data = graph.build_data(plan, results, binding)
    for note in data["samples"][0]["models"].values():
        assert len(note["nodes"]) == 3
        assert note["duplicate_names"]
        assert "ambiguous_endpoint" in note["edges"][0]["flags"]


def test_cli_writes_separate_artifacts_without_changing_trial_inputs(inputs, tmp_path):
    originals = {}
    for name, value in zip(("plan", "results", "binding"), inputs, strict=True):
        path = tmp_path / (name + ".json")
        path.write_text(json.dumps(value), encoding="utf-8")
        originals[path] = path.read_bytes()
    graph.main(["--trial", str(tmp_path)])
    assert (tmp_path / "graph-comparison.html").is_file()
    receipt = json.loads(
        (tmp_path / "graph-comparison-validation.json").read_text("utf-8")
    )
    assert receipt["semantic_accuracy_verified"] is False
    assert receipt["production_changed"] is False
    assert all(path.read_bytes() == raw for path, raw in originals.items())
