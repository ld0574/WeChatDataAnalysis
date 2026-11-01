import json
from types import SimpleNamespace

import pytest

from wechat_decrypt_tool import course_wiki_batch as batch
from wechat_decrypt_tool.course_knowledge import GpuBusyError, atomic_json
from wechat_decrypt_tool.course_library import canonical
from wechat_decrypt_tool.course_lesson_exports import digest


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    lessons = {f"video-{i}": {"revision": "original", "id": i} for i in range(7)}
    catalog = {
        "lessons": [
            {"id": key, "state": "draft_available" if i < 6 else "not_published"}
            for i, key in enumerate(lessons)
        ]
    }
    cited = []
    calls = []
    (tmp_path / "wiki/theme-planning-sources").mkdir(parents=True)
    store = SimpleNamespace(root=tmp_path, checkpoint=lambda: None)
    monkeypatch.setattr(
        batch,
        "Library",
        lambda root: SimpleNamespace(
            catalog=lambda: catalog, lesson=lambda key: lessons[key]
        ),
    )
    monkeypatch.setattr(
        batch,
        "Wiki",
        lambda root: SimpleNamespace(
            catalog=lambda: {"articles": [{"id": v} for v in cited]},
            article=lambda key: {"packet": {"sources": {"s": {"video": key}}}},
        ),
    )
    monkeypatch.setattr(batch, "local_model_digest", lambda name: "fixed-weights")

    def propose(store, video, model, **kwargs):
        calls.append((video, "propose"))
        job = digest(video.encode())[:32]
        plan = {"video": video}
        (store.root / "wiki/planning-jobs" / job).mkdir(parents=True, exist_ok=True)
        atomic_json(store.root / f"wiki/planning-jobs/{job}/plan.json", plan)
        return {
            "job": job,
            "state": "prepared_for_writing",
            "plan_sha256": digest(canonical(plan)),
            "selected_sections": ["s1", "s2", "s3"],
            "unselected_sections": ["s4"],
        }

    def generate(store, plan, model, **kwargs):
        assert kwargs["fixed_assessments"] is True
        calls.append((plan["video"], "generate"))
        return {"id": digest(plan["video"].encode())[:32]}

    def review(store, article, model, **kwargs):
        assert kwargs.get("single_targets") or kwargs.get("per_source")
        calls.append(
            (article, "review" if kwargs.get("single_targets") else "omissions")
        )
        return {"job": "a" * 32, "summary": {"partial": 1}}

    monkeypatch.setattr(batch, "propose", propose)
    monkeypatch.setattr(batch, "generate", generate)
    monkeypatch.setattr(batch, "review", review)
    monkeypatch.setattr(batch, "omissions", review)
    monkeypatch.setattr(batch, "published_review", lambda *a, **k: {"valid": True})
    monkeypatch.setattr(batch, "published_report", lambda *a, **k: {"valid": True})
    return SimpleNamespace(store=store, lessons=lessons, cited=cited, calls=calls)


def test_preparation_excludes_unpublished_cited_attempted_and_reserved_sources(
    pipeline,
):
    p = pipeline
    p.cited.append("video-0")
    atomic_json(
        p.store.root / "wiki/theme-planning-sources/old.json", {"video": "video-1"}
    )
    first = batch.prepare(p.store, limit=2)
    _, spec = batch.manifest(p.store.root, first["batch"])
    assert [r["video"] for r in spec["courses"]] == ["video-2", "video-3"]
    second = batch.prepare(p.store, limit=340)
    _, spec = batch.manifest(p.store.root, second["batch"])
    assert [r["video"] for r in spec["courses"]] == ["video-4", "video-5"]
    assert batch.prepare(p.store)["state"] == "no_unattempted_published_courses"
    assert not p.calls


def test_finished_batch_does_not_regenerate_or_claim_semantic_acceptance(pipeline):
    p = pipeline
    key = batch.prepare(p.store, limit=2)["batch"]
    result = batch.run(p.store, key)
    before = list(p.calls)
    result = batch.run(p.store, key)
    assert p.calls == before
    assert len(before) == 8
    assert all(
        r["state"] == "draft_checks_complete" for r in result["courses"].values()
    )
    assert all(r["unselected_sections"] == ["s4"] for r in result["courses"].values())
    assert not result["semantic_accuracy_verified"]
    assert not result["full_course_coverage_verified"]
    assert not result["source_deletion_allowed"]
    assert not result["automatic_revision_loop_allowed"]


def test_failed_course_is_retained_without_blocking_or_retrying_others(
    pipeline, monkeypatch
):
    p = pipeline
    original = batch.propose

    def fail(store, video, *args, **kwargs):
        if video == "video-0":
            p.calls.append((video, "failed_plan"))
            raise ValueError("theme_attempt_budget_exhausted")
        return original(store, video, *args, **kwargs)

    monkeypatch.setattr(batch, "propose", fail)
    key = batch.prepare(p.store, limit=2)["batch"]
    result = batch.run(p.store, key)
    assert result["courses"]["video-0"]["state"] == "needs_review"
    assert result["courses"]["video-1"]["state"] == "draft_checks_complete"
    batch.run(p.store, key)
    assert p.calls.count(("video-0", "failed_plan")) == 1


def test_gpu_deferral_can_resume_same_batch_without_marking_failure(
    pipeline, monkeypatch
):
    p = pipeline
    original = batch.propose
    busy = [True]

    def defer(*args, **kwargs):
        if busy.pop() if busy else False:
            raise GpuBusyError("Synthetic shared GPU is occupied")
        return original(*args, **kwargs)

    monkeypatch.setattr(batch, "propose", defer)
    key = batch.prepare(p.store, limit=1)["batch"]
    assert batch.run(p.store, key)["state"] == "deferred_gpu"
    assert not p.calls
    result = batch.run(p.store, key)
    assert result["courses"]["video-0"]["state"] == "draft_checks_complete"
    assert len(p.calls) == 4


def test_pause_preserves_inflight_batch_and_does_not_start_next_course(
    pipeline, monkeypatch
):
    p = pipeline
    monkeypatch.setattr(
        batch,
        "generate",
        lambda *a, **kw: (_ for _ in ()).throw(InterruptedError("Paused")),
    )
    key = batch.prepare(p.store, limit=2)["batch"]
    with pytest.raises(InterruptedError):
        batch.run(p.store, key)
    progress = json.loads(
        (p.store.root / f"wiki/theme-batches/{key}/progress.json").read_bytes()
    )
    assert progress["state"] == "paused"
    assert progress["courses"]["video-0"]["state"] == "paused"
    assert progress["courses"]["video-1"]["state"] == "pending"
    assert p.calls == [("video-0", "propose")]


@pytest.mark.parametrize("change", ["source", "model"])
def test_changed_frozen_inputs_fail_before_any_model_call(
    pipeline, monkeypatch, change
):
    p = pipeline
    key = batch.prepare(p.store, limit=1)["batch"]
    if change == "source":
        p.lessons["video-0"]["revision"] = "new"
    else:
        monkeypatch.setattr(batch, "local_model_digest", lambda name: "changed-weights")
    result = batch.run(p.store, key)
    assert result["courses"]["video-0"]["state"] == "needs_review"
    assert not p.calls


def test_changed_plan_cannot_be_passed_to_writer(pipeline, monkeypatch):
    p = pipeline
    original = batch.propose

    def changed(*args, **kwargs):
        value = original(*args, **kwargs)
        atomic_json(
            p.store.root / f"wiki/planning-jobs/{value['job']}/plan.json",
            {"video": "other"},
        )
        return value

    monkeypatch.setattr(batch, "propose", changed)
    key = batch.prepare(p.store, limit=1)["batch"]
    result = batch.run(p.store, key)
    assert result["courses"]["video-0"]["state"] == "needs_review"
    assert p.calls == [("video-0", "propose")]


def test_changed_manifest_and_foreign_progress_scope_are_rejected(pipeline):
    p = pipeline
    key = batch.prepare(p.store, limit=1)["batch"]
    folder, _ = batch.manifest(p.store.root, key)
    atomic_json(
        folder / "progress.json",
        {"batch": key, "courses": {"other": {"state": "pending"}}},
    )
    with pytest.raises(ValueError, match="progress_scope_changed"):
        batch.run(p.store, key)
    value = json.loads((folder / "manifest.json").read_bytes())
    value["recipe"]["model"] = "changed-model"
    atomic_json(folder / "manifest.json", value)
    with pytest.raises(ValueError, match="manifest_changed"):
        batch.run(p.store, key)
    assert not p.calls


def test_explicit_no_topic_does_not_generate_filler_or_retry(pipeline, monkeypatch):
    p = pipeline
    calls = []

    def skip(*args, **kwargs):
        calls.append(True)
        return {"job": "a" * 32, "state": "no_supported_topic_proposed"}

    monkeypatch.setattr(batch, "propose", skip)
    key = batch.prepare(p.store, limit=1)["batch"]
    result = batch.run(p.store, key)
    assert result["courses"]["video-0"]["state"] == "no_supported_topic"
    batch.run(p.store, key)
    assert calls == [True]
    assert not p.calls


@pytest.mark.parametrize("change", ["article", "missing_report"])
def test_completed_rows_revalidate_publications_instead_of_trusting_status(
    pipeline, monkeypatch, change
):
    p = pipeline
    key = batch.prepare(p.store, limit=1)["batch"]
    batch.run(p.store, key)
    before = list(p.calls)
    if change == "article":
        monkeypatch.setattr(
            batch,
            "Wiki",
            lambda root: SimpleNamespace(article=lambda key: {"changed": True}),
        )
    else:

        def missing(*args, **kwargs):
            raise FileNotFoundError("Synthetic removed immutable report")

        monkeypatch.setattr(batch, "published_report", missing)
    result = batch.run(p.store, key)
    assert result["courses"]["video-0"]["state"] == "needs_review"
    assert p.calls == before
