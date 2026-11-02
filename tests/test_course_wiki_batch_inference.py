"""New scope inference intents survive resume; old work keeps its budget."""

import json

import pytest

from test_course_wiki_batch import pipeline as source_pipeline
from wechat_decrypt_tool import course_wiki_batch as batch
from wechat_decrypt_tool.course_knowledge import GpuBusyError, atomic_json
from wechat_decrypt_tool.course_wiki_text_inference import profile

pipeline = source_pipeline


def test_new_inference_bound_before_planning_and_resumed_without_flag(
    pipeline, monkeypatch
):
    p = pipeline
    key = batch.prepare(p.store, limit=1)["batch"]
    old_propose = batch.propose
    old_reverse = batch.omissions
    seen = []

    def propose(*args, **kwargs):
        paths = list(
            (
                p.store.root / "wiki/theme-batches" / key / "check-inference-intents"
            ).glob("*.json")
        )
        assert (
            len(paths) == 1
            and json.loads(paths[0].read_bytes())["inference"] == profile()
        )
        return old_propose(*args, **kwargs)

    def reverse(*args, **kwargs):
        assert (
            kwargs["keyed_windows"]
            and kwargs["window_batches"]
            and kwargs["large_context"]
        )
        assert kwargs["constrained_evidence"]
        seen.append(kwargs)
        if len(seen) == 1:
            raise GpuBusyError("synthetic GPU occupied")
        return old_reverse(*args, **kwargs)

    monkeypatch.setattr(batch, "propose", propose)
    monkeypatch.setattr(batch, "omissions", reverse)
    first = batch.run(p.store, key, max_items=1, large_context_new_checks=True)
    assert first["state"] == "deferred_gpu"
    row = next(iter(first["courses"].values()))
    binding = row["check_inference_sha256"]
    second = batch.run(p.store, key, max_items=1)
    assert second["state"] == "finite_batch_finished"
    assert next(iter(second["courses"].values()))["check_inference_sha256"] == binding
    assert seen[0] == seen[1]


@pytest.mark.parametrize("change", ["switch", "delete", "tamper"])
def test_inference_intent_cannot_change_after_work_started(
    pipeline, monkeypatch, change
):
    p = pipeline
    key = batch.prepare(p.store, limit=1)["batch"]
    monkeypatch.setattr(
        batch, "propose", lambda *a, **kw: (_ for _ in ()).throw(GpuBusyError("busy"))
    )
    batch.run(p.store, key, large_context_new_checks=True)
    folder = p.store.root / "wiki/theme-batches" / key
    path = next((folder / "check-inference-intents").glob("*.json"))
    if change == "delete":
        path.unlink()
    if change == "tamper":
        value = json.loads(path.read_bytes())
        value["inference"]["options"]["num_ctx"] = 8192
        atomic_json(path, value)
    result = batch.run(
        p.store, key, large_context_new_checks=False if change == "switch" else None
    )
    assert next(iter(result["courses"].values()))["state"] == "needs_review"
    assert not p.calls


def test_old_started_item_cannot_gain_new_inference_budget(pipeline, monkeypatch):
    p = pipeline
    key = batch.prepare(p.store, limit=1)["batch"]
    monkeypatch.setattr(
        batch, "propose", lambda *a, **kw: (_ for _ in ()).throw(GpuBusyError("busy"))
    )
    batch.run(p.store, key)
    result = batch.run(p.store, key, large_context_new_checks=True)
    row = next(iter(result["courses"].values()))
    assert row["state"] == "needs_review"
    assert not list(
        (p.store.root / "wiki/theme-batches" / key / "check-inference-intents").glob(
            "*.json"
        )
    )
    assert not p.calls


def test_orphan_pending_intent_recovers_before_progress_write(tmp_path):
    spec = {"video": "source"}
    row = {"state": "pending"}
    assert batch.check_inference(tmp_path, "batch", spec, row, True)
    recovered = {"state": "pending"}
    assert batch.check_inference(tmp_path, "batch", spec, recovered, None)
    assert recovered == row


@pytest.mark.parametrize("value", [1, "yes"])
def test_invalid_inference_option_fails_before_work(pipeline, value):
    key = batch.prepare(pipeline.store, limit=1)["batch"]
    with pytest.raises(ValueError, match="invalid_check_inference_option"):
        batch.run(pipeline.store, key, large_context_new_checks=value)
