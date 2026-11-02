"""Real recipe/attempt/restore paths, with local inference transport mocked."""

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_course_wiki_keyed_omissions import (
    archive as source_archive,
    published as source_published,
    courseware as source_courseware,
    long_plan as source_long_plan,
    drafted as source_drafted,
    mock_model,
    check,
    as_keyed,
)
from test_course_wiki_window_batches import large_drafted as source_large_drafted
from test_course_wiki_completeness import response
from test_course_wiki_snapshot_writers import retain_frames
from wechat_decrypt_tool import course_wiki_text_inference as inference
from wechat_decrypt_tool import course_wiki_completeness as omissions
from wechat_decrypt_tool import course_wiki_check_constraints as constraints
from wechat_decrypt_tool import course_wiki_snapshot as snapshot
from wechat_decrypt_tool.course_knowledge import ModelOutputTruncatedError, atomic_json

archive = source_archive
published = source_published
courseware = source_courseware
long_plan = source_long_plan
drafted = source_drafted
large_drafted = source_large_drafted


def metrics(digest="frozen-local-weights"):
    return {
        "done_reason": "stop",
        "prompt_eval_count": 7909,
        "eval_count": 2100,
        "eval_duration": 2000,
        "context_length": 16384,
        "size_vram": 6 * 1024**3,
        "model_digest": digest,
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("done_reason", "length"),
        ("prompt_eval_count", 12384),
        ("prompt_eval_count", 0),
        ("prompt_eval_count", True),
        ("eval_count", 4001),
        ("eval_count", 0),
        ("context_length", 8192),
        ("size_vram", 0),
        ("size_vram", True),
        ("model_digest", "changed"),
    ],
)
def test_invalid_gpu_context_and_output_headroom_never_pass(field, value):
    data = metrics()
    data[field] = value
    with pytest.raises(ValueError, match="budget_or_gpu_unproven"):
        inference.validate_metrics(data, "frozen-local-weights")


@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "truncated",
        "wrong_context",
        "cpu",
        "changed_weights",
        "duplicate_owner",
    ],
)
def test_transport_pins_local_weights_options_gpu_and_token_margin(monkeypatch, case):
    import httpx

    calls = []

    def weights(model):
        calls.append(model)
        return "changed" if case == "changed_weights" and len(calls) > 1 else "weights"

    monkeypatch.setattr(inference, "local_model_digest", weights)

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, *, json):
            assert url == "http://127.0.0.1:11434/api/chat"
            assert calls == ["local-model"]
            assert json["options"] == inference.profile()["options"]
            assert json["format"] == {"synthetic": "schema"}
            assert json["messages"] == [{"role": "user", "content": "synthetic"}]
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {
                    "done_reason": "length" if case == "truncated" else "stop",
                    "prompt_eval_count": 7909,
                    "eval_count": 2100,
                    "message": {"content": "synthetic"},
                },
            )

        def get(self, url):
            assert url == "http://127.0.0.1:11434/api/ps"
            owner = {
                "digest": "weights",
                "context_length": 8192 if case == "wrong_context" else 16384,
                "size_vram": 0 if case == "cpu" else 6 * 1024**3,
            }
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {
                    "models": [owner, owner] if case == "duplicate_owner" else [owner]
                },
            )

    monkeypatch.setattr(httpx, "Client", Client)

    def run():
        return inference.chat(
            "local-model",
            [{"role": "user", "content": "synthetic"}],
            schema={"synthetic": "schema"},
        )

    if case == "valid":
        raw, data = run()
        assert raw == "synthetic"
        inference.validate_metrics(data, "weights")
    elif case == "truncated":
        with pytest.raises(ModelOutputTruncatedError):
            run()
    else:
        with pytest.raises(ValueError):
            run()


def mock_large(monkeypatch, *, invalid=False):
    calls = []

    def chat(name, messages, *, schema):
        c = json.loads(messages[1]["content"])
        calls.append(c)
        assert schema == omissions.selection_schema(c, keyed=True)
        return (
            '{"reviews":{}}' if invalid else json.dumps(as_keyed(response(c)))
        ), metrics()

    monkeypatch.setattr(inference, "chat", chat)
    return calls


def large_check(fixture):
    return check(fixture, keyed_windows=True, window_batches=True, large_context=True)


def test_large_context_attempt_metrics_bind_report_and_independent_restore(
    large_drafted, published, monkeypatch
):
    store, article = large_drafted
    retain_frames(store, published[1])
    calls = mock_large(monkeypatch)
    result = large_check(large_drafted)
    assert result["inference"] == inference.profile()
    assert result["generation_constraints"] == constraints.LARGE_CONTEXT_REVERSE_POLICY
    expected = omissions.published_report(store.root, article)
    assert large_check(large_drafted)["job"] == result["job"]
    assert len(calls) == len(result["chapters"])
    backup = snapshot.create(
        store.root, store.root.parent / "large-backup", reserve_gib=0
    )
    restored = snapshot.restore(
        backup, store.root.parent / "large-restored", reserve_gib=0
    )
    assert omissions.published_report(restored, article) == expected
    run = subprocess.run(
        [
            sys.executable,
            "-I",
            str(Path(__file__).with_name("wiki_snapshot_isolated_reader.py")),
            str(restored),
            str(store.root),
            str(Path(snapshot.__file__).parent),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    assert json.loads(run.stdout)["original_root_access_attempts"] == 0


@pytest.mark.parametrize(
    "mutation",
    ["gpu_metrics", "missing_attempt", "inference_receipt", "request_binding"],
)
def test_large_context_proof_is_required_when_rereading_report(
    drafted, monkeypatch, mutation
):
    store, article = drafted
    mock_large(monkeypatch)
    result = large_check(drafted)
    folder = store.root / "wiki/completeness-jobs" / result["job"]
    attempt = next((folder / "attempts").glob("*.json"))
    if mutation == "gpu_metrics":
        value = json.loads(attempt.read_bytes())
        value["model_metrics"]["context_length"] = 8192
        atomic_json(attempt, value)
    elif mutation == "request_binding":
        value = json.loads(attempt.read_bytes())
        value["request_sha256"] = "changed"
        atomic_json(attempt, value)
    elif mutation == "missing_attempt":
        attempt.unlink()
    else:
        result["inference"]["options"]["num_ctx"] = 8192
        atomic_json(folder / "receipt.json", result)
        atomic_json(store.root / "wiki/completeness" / (article + ".json"), result)
    with pytest.raises(ValueError, match="inference_"):
        omissions.published_report(store.root, article)


@pytest.mark.parametrize("large_first", [False, True])
def test_inference_switch_never_refreshes_exhausted_attempts(
    drafted, monkeypatch, large_first
):
    small_calls = mock_model(monkeypatch, intercept=lambda *a: '{"reviews":{}}')
    large_calls = mock_large(monkeypatch, invalid=True)

    def run(large):
        return check(
            drafted, keyed_windows=True, window_batches=True, large_context=large
        )

    with pytest.raises(ValueError, match="attempt_budget_exhausted"):
        run(large_first)
    assert len(small_calls) + len(large_calls) == 3
    with pytest.raises(ValueError, match="constraint_change_requires_new_article"):
        run(not large_first)
    with pytest.raises(ValueError, match="attempt_budget_exhausted"):
        run(large_first)
    assert len(small_calls) + len(large_calls) == 3


def test_large_context_cannot_omit_keyed_window_batch_policy(drafted):
    with pytest.raises(ValueError, match="large_context_requires_window_batches"):
        check(drafted, large_context=True)
