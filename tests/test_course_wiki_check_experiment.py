import importlib.util
import json
from pathlib import Path

import pytest

from test_course_wiki_review import archive as source_archive
from test_course_wiki_review import published as source_published
from test_course_wiki_review import setup

archive = source_archive
published = source_published


def tool():
    path = Path(__file__).resolve().parents[1] / "tools/course_wiki_check_experiment.py"
    spec = importlib.util.spec_from_file_location("check_experiment", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("valid", [True, False])
def test_one_diagnostic_call_preserves_production_files_and_budget(
    published, monkeypatch, valid
):
    store, article_id, _ = setup(published, monkeypatch)
    experiment = tool()
    monkeypatch.setattr(
        experiment, "local_model_digest", lambda _: "local-fixed-weights"
    )
    before = {
        p.relative_to(store.root): p.read_bytes()
        for p in (store.root / "wiki").rglob("*.json")
    }
    calls = []

    def chat(model, messages, *, schema):
        calls.append(schema)
        context = json.loads(messages[1]["content"])
        target = context["targets"][0]
        witness = next(
            k
            for k, w in context["witnesses"].items()
            if w["source"] in target["citations"]
        )
        return json.dumps(
            {
                "reviews": [
                    {
                        "target": target["target"],
                        "verdict": "partial",
                        "reason": "Synthetic reason",
                        "evidence": [{"witness": witness}] if valid else [],
                    }
                ]
            }
        )

    monkeypatch.setattr(experiment, "local_chat", chat)
    result = experiment.run(store.root, "forward", article_id, "quiz-0", "local")
    assert result["state"] == (
        "bounded_grammar_experiment_passed_structure_only" if valid else "failed"
    )
    assert len(calls) == 1
    if valid:
        assert result["semantic_accuracy_verified"] is False
    with pytest.raises(ValueError, match="existing_attempt_preserved"):
        experiment.run(store.root, "forward", article_id, "quiz-0", "local")
    assert len(calls) == 1
    assert all((store.root / path).read_bytes() == raw for path, raw in before.items())
    assert not (store.root / "wiki/reviews" / (article_id + ".json")).exists()
    attempt = next((store.root / "wiki/check-experiments").glob("*/attempt.json"))
    assert json.loads(attempt.read_bytes())["raw"]


@pytest.mark.parametrize("single_targets", [False, True])
def test_baseline_requires_exact_failed_request_and_preserves_all_three_attempts(
    published, monkeypatch, single_targets
):
    store, article_id, _ = setup(published, monkeypatch)
    experiment = tool()
    monkeypatch.setattr(
        experiment, "local_model_digest", lambda _: "local-fixed-weights"
    )
    calls = []
    stage = "section-0-p-0" if single_targets else "overview"

    def response(model, messages, *, schema):
        calls.append(messages)
        context = json.loads(messages[1]["content"])
        target = context["targets"][0]
        witness = next(
            k
            for k, w in context["witnesses"].items()
            if w["source"] in target["citations"]
        )
        return json.dumps(
            {
                "reviews": [
                    {
                        "target": target["target"],
                        "verdict": "partial",
                        "reason": "Synthetic reason",
                        "evidence": []
                        if target["target"] == stage
                        else [{"witness": witness}],
                    }
                ]
            }
        )

    monkeypatch.setattr(experiment.review, "local_chat", response)
    with pytest.raises(ValueError, match="budget_exhausted"):
        experiment.review.review(
            experiment.ReadOnlyArchive(store.root),
            article_id,
            single_targets=single_targets,
        )
    manifest = next((store.root / "wiki/review-jobs").glob("*/manifest.json"))
    baseline = manifest.parent.name
    saved = {p: p.read_bytes() for p in manifest.parent.glob("*.json")}
    count = len(calls)
    assert (
        sum(json.loads(m[1]["content"])["targets"][0]["target"] == stage for m in calls)
        == 3
    )
    baseline_messages = next(
        m for m in calls if json.loads(m[1]["content"])["targets"][0]["target"] == stage
    )
    monkeypatch.setattr(experiment, "local_chat", response)
    result = experiment.run(
        store.root, "forward", article_id, stage, "local", baseline_job=baseline
    )
    assert result["state"] == "failed"  # Still refuses empty evidence.
    recipe = next((store.root / "wiki/check-experiments").glob("*/recipe.json"))
    frozen = json.loads(recipe.read_bytes())
    assert frozen["baseline"]["job"] == baseline
    assert frozen["messages"] == baseline_messages
    assert len(calls) == count + 1
    assert all(p.read_bytes() == raw for p, raw in saved.items())
    monkeypatch.setattr(experiment, "local_model_digest", lambda _: "different-weights")
    with pytest.raises(ValueError, match="baseline_request_or_identity_changed"):
        experiment.run(
            store.root,
            "forward",
            article_id,
            stage,
            "local",
            baseline_job=baseline,
        )
    assert len(calls) == count + 1


def test_pause_before_gpu_does_not_consume_diagnostic_attempt(published, monkeypatch):
    store, article_id, _ = setup(published, monkeypatch)
    experiment = tool()
    monkeypatch.setattr(
        experiment, "local_model_digest", lambda _: "local-fixed-weights"
    )
    calls = []
    monkeypatch.setattr(experiment, "local_chat", lambda *a, **k: calls.append(True))
    flag = store.root / "pause.flag"
    flag.write_text("user_pause")
    result = experiment.run(store.root, "forward", article_id, "quiz-0", "local")
    assert result["state"] == "deferred"
    assert flag.read_text() == "user_pause"
    assert calls == []
    assert not list((store.root / "wiki/check-experiments").glob("*/attempt.json"))


def test_reason_bound_rejects_overlong_reply_and_retains_its_single_attempt(
    published, monkeypatch
):
    store, article_id, _ = setup(published, monkeypatch)
    experiment = tool()
    monkeypatch.setattr(
        experiment, "local_model_digest", lambda _: "local-fixed-weights"
    )
    calls = []

    def chat(model, messages, *, schema):
        calls.append(schema)
        assert all(
            branch["properties"]["reason"]["maxLength"] == 700
            for branch in schema["properties"]["reviews"]["items"]["oneOf"]
        )
        context = json.loads(messages[1]["content"])
        target = context["targets"][0]
        witness = next(
            k
            for k, w in context["witnesses"].items()
            if w["source"] in target["citations"]
        )
        return json.dumps(
            {
                "reviews": [
                    {
                        "target": target["target"],
                        "verdict": "partial",
                        "reason": "x" * 701,
                        "evidence": [{"witness": witness}],
                    }
                ]
            }
        )

    monkeypatch.setattr(experiment, "local_chat", chat)
    result = experiment.run(
        store.root,
        "forward",
        article_id,
        "quiz-0",
        "local",
        schema_variant="bounded-reason-v2",
    )
    assert result["state"] == "failed" and result["error_type"] == "ValidationError"
    folder = store.root / "wiki/check-experiments" / result["job"]
    assert (
        json.loads((folder / "recipe.json").read_bytes())["schema_variant"]
        == "bounded-reason-v2"
    )
    assert (
        len(
            json.loads(json.loads((folder / "attempt.json").read_bytes())["raw"])[
                "reviews"
            ][0]["reason"]
        )
        == 701
    )
    with pytest.raises(ValueError, match="existing_attempt_preserved"):
        experiment.run(
            store.root,
            "forward",
            article_id,
            "quiz-0",
            "local",
            schema_variant="bounded-reason-v2",
        )
    assert len(calls) == 1


def test_unknown_or_reverse_reason_variant_never_starts_a_model(published, monkeypatch):
    store, article_id, _ = setup(published, monkeypatch)
    experiment = tool()
    for kind, variant, message in [
        ("forward", "unknown", "unknown_schema_variant"),
        ("reverse", "bounded-reason-v2", "requires_forward"),
    ]:
        with pytest.raises(ValueError, match=message):
            experiment.run(
                store.root, kind, article_id, "quiz-0", "local", schema_variant=variant
            )
    assert not (store.root / "wiki/check-experiments").exists()
