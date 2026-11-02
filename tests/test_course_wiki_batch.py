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
        plan = {"video": video}
        if "directory" in kwargs:
            plan.update(directory=kwargs["directory"], page=kwargs["page_number"])
        if "allocation" in kwargs:
            plan.update(
                allocation=kwargs["allocation"],
                allocation_scope=kwargs["allocation_scope"],
            )
        job = digest(canonical(plan))[:32]
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
        return {"id": digest(canonical(plan))[:32]}

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


def test_same_course_pages_are_distinct_resumable_items_and_reservation_is_idempotent(
    pipeline, monkeypatch
):
    from wechat_decrypt_tool import course_wiki_directory as directory

    p = pipeline
    monkeypatch.setattr(
        directory,
        "create",
        lambda store, video: {
            "directory": "d" * 32,
            "pages": 2,
            "lesson_sha256": digest(canonical(p.lessons[video])),
        },
    )
    first = batch.prepare_directories(p.store, ["video-0"])
    assert first["pages"] == 2 and first["courses"] == 1
    again = batch.prepare_directories(p.store, ["video-0"])
    assert again["batch"] == first["batch"] and again["state"] == "already_prepared"
    result = batch.run(p.store, first["batch"])
    rows = list(result["courses"].values())
    assert len(rows) == 2
    assert [r["page"] for r in rows] == [0, 1]
    assert len({r["article"] for r in rows}) == 2
    assert all(r["state"] == "draft_checks_complete" for r in rows)
    before = list(p.calls)
    batch.run(p.store, first["batch"])
    assert p.calls == before


def test_directory_pages_cannot_get_fresh_budget_by_joining_another_batch(
    pipeline, monkeypatch
):
    from wechat_decrypt_tool import course_wiki_directory as directory

    p = pipeline
    monkeypatch.setattr(
        directory,
        "create",
        lambda store, video: {
            "directory": digest(video.encode())[:32],
            "pages": 2,
            "lesson_sha256": digest(canonical(p.lessons[video])),
        },
    )
    batch.prepare_directories(p.store, ["video-0"])
    with pytest.raises(ValueError, match="page_already_reserved"):
        batch.prepare_directories(p.store, ["video-0", "video-1"])
    assert not p.calls


def test_allocated_original_scopes_are_resumable_and_keep_unselected_ranges(
    pipeline, monkeypatch
):
    from wechat_decrypt_tool import course_wiki_allocation as allocation

    p = pipeline
    monkeypatch.setattr(
        allocation,
        "create",
        lambda store, partition: {
            "allocation": "c" * 32,
            "video": "video-0",
            "lesson_sha256": digest(canonical(p.lessons["video-0"])),
            "scopes": 2,
        },
    )
    original = batch.propose

    def proposed(store, video, model, **options):
        assert options["allocation"] == "c" * 32
        result = original(store, video, model, **options)
        result.update(
            evidence_partition="e" * 32,
            scope_evidence_pages=[options["allocation_scope"]],
            selected_evidence_pages=[options["allocation_scope"]],
            unselected_evidence_pages=[1 - options["allocation_scope"]],
            partially_selected_sections=["s1"],
            complete_sections_selected=[],
        )
        return result

    monkeypatch.setattr(batch, "propose", proposed)
    first = batch.prepare_allocations(p.store, ["e" * 32])
    assert first["scopes"] == 2 and not p.calls
    assert batch.prepare_allocations(p.store, ["e" * 32])["batch"] == first["batch"]
    result = batch.run(p.store, first["batch"])
    rows = list(result["courses"].values())
    assert len({r["article"] for r in rows}) == 2
    assert [r["allocation_scope"] for r in rows] == [0, 1]
    assert [r["unselected_evidence_pages"] for r in rows] == [[1], [0]]
    assert all(r["complete_sections_selected"] == [] for r in rows)
    before = list(p.calls)
    batch.run(p.store, first["batch"])
    assert p.calls == before


def test_recomposing_allocation_batch_cannot_reset_original_scope_budget(
    pipeline, monkeypatch
):
    from wechat_decrypt_tool import course_wiki_allocation as allocation

    p = pipeline
    monkeypatch.setattr(
        allocation,
        "create",
        lambda store, partition: {
            "allocation": partition,
            "video": "video-0",
            "lesson_sha256": digest(canonical(p.lessons["video-0"])),
            "scopes": 2,
        },
    )
    batch.prepare_allocations(p.store, ["a" * 32])
    with pytest.raises(ValueError, match="allocation_scope_already_reserved"):
        batch.prepare_allocations(p.store, ["a" * 32, "b" * 32])
    assert not p.calls


def test_bounded_slice_retains_all_pending_items_and_resumes_same_reservation(pipeline):
    p = pipeline
    key = batch.prepare(p.store, limit=3)["batch"]
    result = batch.run(p.store, key, max_items=1)
    assert result["state"] == "slice_finished" and result["remaining_items"] == 2
    assert [r["state"] for r in result["courses"].values()] == [
        "draft_checks_complete",
        "pending",
        "pending",
    ]
    before = list(p.calls)
    result = batch.run(p.store, key, max_items=1)
    assert result["remaining_items"] == 1
    assert p.calls[: len(before)] == before and len(p.calls) == 8
    assert batch.run(p.store, key, max_items=1)["state"] == "finite_batch_finished"
    assert len(p.calls) == 12


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, 10001])
def test_invalid_slice_limit_never_starts_models(pipeline, limit):
    key = batch.prepare(pipeline.store, limit=2)["batch"]
    with pytest.raises(ValueError, match="invalid_item_limit"):
        batch.run(pipeline.store, key, max_items=limit)
    assert not pipeline.calls


def test_slice_counts_failed_item_without_retry_or_starting_next(pipeline, monkeypatch):
    key = batch.prepare(pipeline.store, limit=2)["batch"]

    def failed(*args, **kwargs):
        pipeline.calls.append("failed")
        raise ValueError("Synthetic exhausted attempt budget")

    monkeypatch.setattr(batch, "propose", failed)
    result = batch.run(pipeline.store, key, max_items=1)
    assert result["state"] == "slice_finished"
    assert [r["state"] for r in result["courses"].values()] == [
        "needs_review",
        "pending",
    ]
    assert pipeline.calls == ["failed"]
