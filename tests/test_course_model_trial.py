import json
import sqlite3
from contextlib import contextmanager

import pytest

from wechat_decrypt_tool import course_model_trial as trial


def test_gold_schema_does_not_supply_expected_answers():
    for case in trial.gold_cases():
        schema = trial.gold_schema(case["expected"])
        assert set(schema["required"]) == set(case["expected"])
        assert "enum" not in json.dumps(schema)
        assert "const" not in json.dumps(schema)
        assert "default" not in json.dumps(schema)
        assert all(
            schema["properties"][k]["type"] != "null"
            for k, v in case["expected"].items()
            if v is None
        )


def test_prepare_reads_snapshot_and_keeps_production_unchanged(tmp_path):
    path = tmp_path / "knowledge.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE videos(id TEXT, title TEXT, status TEXT);
            CREATE TABLE notes(video TEXT, window INTEGER, data TEXT, status TEXT);
            CREATE TABLE segments(id INTEGER, video TEXT, n INTEGER, start REAL, end REAL, text TEXT);
            CREATE TABLE frames(id INTEGER, video TEXT, time REAL, description TEXT);
        """)
        for n in range(4):
            key = str(n)
            db.execute("INSERT INTO videos VALUES(?,?,?)", (key, key, "transcribed"))
            db.execute(
                "INSERT INTO notes VALUES(?,?,?,?)",
                (
                    key,
                    0,
                    json.dumps(
                        {"summary": "python", "relations": [{"relation": "无直接关系"}]}
                    ),
                    "ready",
                ),
            )
            for i in range(6):
                db.execute(
                    "INSERT INTO segments VALUES(?,?,?,?,?,?)",
                    (n * 10 + i + 1, key, i, i * 25, (i + 1) * 25, "synthetic"),
                )
            db.execute(
                "INSERT INTO frames VALUES(?,?,?,?)", (n + 1, key, 0, "synthetic")
            )
    before = path.read_bytes()
    plan = trial.prepare(tmp_path)
    assert path.read_bytes() == before
    assert len(plan["samples"]) == 4
    assert len({s["video"] for s in plan["samples"]}) == 4
    for sample in plan["samples"]:
        assert sample["visuals"][0]["id"] == 7
        assert sample["original_ids"][-1] < 0
        assert [e["id"] for e in sample["evidence"]] == list(range(1, 7))


def test_gold_requires_exact_types_and_records_real_prompt(monkeypatch):
    case = trial.gold_cases()[0]
    output = {**case["expected"], "retry_429": 0}
    monkeypatch.setattr(
        trial.knowledge, "local_chat", lambda *a, **k: json.dumps(output)
    )
    result = trial.run_one("synthetic:model", case, "gold")
    assert result["status"] == "ok"
    assert not result["field_matches"]["retry_429"]
    assert not result["exact_pass"]
    assert result["attempts"][0]["request_sha256"]


def test_failed_generation_is_recorded_without_overwriting(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic inference failure")

    monkeypatch.setattr(trial.knowledge, "local_chat", fail)
    result = trial.run_one("synthetic:model", trial.gold_cases()[0], "gold")
    assert result["status"] == "error"
    assert "output" not in result
    assert result["attempts"]


def test_review_escapes_evidence_and_does_not_declare_winner(tmp_path):
    case = {**trial.gold_cases()[0], "source": "<script>alert(1)</script>"}
    plan = {"samples": [], "gold": [case], "limitations": ["synthetic"]}
    trial.write_report(tmp_path, plan, {})
    assert "<script>" not in (tmp_path / "review.html").read_text("utf-8")
    assert "&lt;script&gt;" in (tmp_path / "review.html").read_text("utf-8")
    summary = json.loads((tmp_path / "summary.json").read_text("utf-8"))
    assert summary["semantic_winner"] is None
    assert summary["production_changed"] is False


def test_trial_unloads_both_models_before_releasing_gpu(tmp_path, monkeypatch):
    folder = tmp_path / "model-trials" / "demo"
    folder.mkdir(parents=True)
    trial.knowledge.atomic_json(
        folder / "plan.json",
        {"gold": [trial.gold_cases()[0]], "samples": [], "limitations": []},
    )
    events = []

    @contextmanager
    def lease(store):
        events.append("acquired")
        yield
        events.append("released")

    def run(model, case, kind):
        assert events.count("acquired") > events.count("released")
        return {
            "model": model,
            "case": case["id"],
            "kind": kind,
            "attempts": [],
            "status": "error",
            "seconds": 0,
        }

    monkeypatch.setattr(trial.knowledge, "gpu_lease", lease)
    monkeypatch.setattr(
        trial.knowledge, "unload_model", lambda m: events.append("unload:" + m)
    )
    monkeypatch.setattr(trial, "local_model_digest", lambda m: m)
    monkeypatch.setattr(trial, "run_one", run)
    monkeypatch.setattr(trial.time, "sleep", lambda _: None)
    trial.main(
        ["--root", str(tmp_path), "--name", "demo", "--models", "test:a", "test:b"]
    )
    assert events[-3:] == ["unload:test:a", "unload:test:b", "released"]
    assert (
        json.loads((folder / "status.json").read_text("utf-8"))["stage"]
        == "complete_with_errors"
    )
    # Completed attempts are resumed from disk without acquiring another GPU lease.
    events.clear()
    trial.main(
        ["--root", str(tmp_path), "--name", "demo", "--models", "test:a", "test:b"]
    )
    assert not events


@pytest.mark.parametrize("name", ["../escape", "one/two", "D:\\outside"])
def test_rejects_trial_path_traversal(tmp_path, name):
    with pytest.raises(SystemExit):
        trial.main(["--root", str(tmp_path), "--name", name, "--prepare-only"])
