"""Bound request size without shortening originals, claims or omission scope."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from test_course_wiki_keyed_omissions import (
    archive as source_archive,
    published as source_published,
    courseware as source_courseware,
    long_plan as source_long_plan,
    drafted as source_drafted,
)

from test_course_wiki_keyed_omissions import (
    mock_model,
    check,
)
from test_course_wiki_synthesis import model, run
from test_course_wiki_snapshot_writers import retain_frames
from wechat_decrypt_tool import course_wiki_completeness as omissions
from wechat_decrypt_tool import course_wiki_check_constraints as constraints
from wechat_decrypt_tool import course_wiki_snapshot as snapshot
from wechat_decrypt_tool.course_knowledge import GpuBusyError, atomic_json
from wechat_decrypt_tool.course_library import canonical
from wechat_decrypt_tool.course_wiki import Wiki

archive = source_archive
published = source_published
courseware = source_courseware
long_plan = source_long_plan
drafted = source_drafted


def oversized_context():
    return {
        "sources": {"a": {"text": "Full retained original.", "origin": {"id": 1}}},
        "article_targets": {
            "p": {"text": "Synthetic claim. " * 860, "citations": ["a"]}
        },
        "windows": {
            f"a@{i}": {"source": "a", "quote": "range " * 80} for i in range(5)
        },
    }


def test_window_partition_preserves_complete_nonwindow_context_and_exact_order():
    context = oversized_context()
    frozen = canonical(context)
    assert len(frozen.decode()) > 16000
    groups = list(omissions.window_groups(context))
    assert len(groups) > 1
    assert all(len(canonical(g).decode()) <= 16000 for g in groups)
    assert [k for g in groups for k in g["windows"]] == list(context["windows"])
    for g in groups:
        assert {k: v for k, v in g.items() if k != "windows"} == {
            k: v for k, v in context.items() if k != "windows"
        }
        assert all(w == context["windows"][k] for k, w in g["windows"].items())
    assert canonical(context) == frozen


def test_single_window_too_large_remains_explicit_without_truncation():
    context = oversized_context()
    context["article_targets"]["p"]["text"] *= 2
    with pytest.raises(ValueError, match="single_window_context_exceeds_bound"):
        list(omissions.window_groups(context))


@pytest.fixture
def large_drafted(long_plan, monkeypatch, request):
    def introduction(stage, context):
        if stage != "intro":
            return None
        ref = next(iter(context["sources"]))
        return json.dumps(
            {
                "overview": {
                    "text": "Synthetic long overview. "
                    + "x" * getattr(request, "param", 12500),
                    "citations": [ref],
                },
                "objectives": ["Explain", "Apply"],
                "prerequisites": [],
                "gaps": [],
            }
        )

    model(monkeypatch, intercept=introduction)
    article = run(long_plan)["id"]
    monkeypatch.setattr(
        omissions, "local_model_digest", lambda _: "frozen-local-weights"
    )
    return long_plan[0], article


@pytest.mark.parametrize("large_drafted", [30000], indirect=True)
def test_unsplittable_actual_article_fails_before_any_job_or_model_call(
    large_drafted, monkeypatch
):
    calls = mock_model(monkeypatch)
    with pytest.raises(ValueError, match="single_window_context_exceeds_bound"):
        check(large_drafted, keyed_windows=True, window_batches=True)
    assert not calls
    assert not list(
        large_drafted[0].root.glob("wiki/completeness-jobs/*/manifest.json")
    )


def test_real_oversized_article_batches_resume_and_restore_without_originals(
    large_drafted, monkeypatch, published
):
    store, article = large_drafted
    retain_frames(store, published[1])
    value = Wiki(store.root).article(article)
    frozen = canonical(value)
    with pytest.raises(ValueError, match="chapter_context_exceeds_bound"):
        list(omissions.stages(value, per_source=True))
    calls = mock_model(monkeypatch)
    result = check(large_drafted, keyed_windows=True, window_batches=True)
    assert result["generation_constraints"] == constraints.BOUNDED_KEYED_REVERSE_POLICY
    assert result["window_batching"] == omissions.WINDOW_BATCHING
    # The writer's repair envelope is separate from the frozen stage context.
    assert all(
        len(
            canonical(
                {k: v for k, v in c.items() if k != "previous_validation_failure"}
            ).decode()
        )
        <= 16000
        for c in calls
    )
    chapter_zero = [
        c for c in calls if c["chapter"] == value["article"]["sections"][0]["heading"]
    ]
    assert len(chapter_zero) > 1
    assert all(c["sources"] == chapter_zero[0]["sources"] for c in chapter_zero)
    assert all(
        c["article_targets"] == chapter_zero[0]["article_targets"] for c in chapter_zero
    )
    assert all(
        c["article_targets"]["overview"]["text"] == value["article"]["overview"]["text"]
        for c in chapter_zero
    )
    count = len(calls)
    restored_report = omissions.published_report(store.root, article)
    assert (
        check(large_drafted, keyed_windows=True, window_batches=True)["job"]
        == result["job"]
    )
    assert (
        len(calls) == count and canonical(Wiki(store.root).article(article)) == frozen
    )
    backup = snapshot.create(
        store.root, store.root.parent / "window-batches-backup", reserve_gib=0
    )
    restored = snapshot.restore(
        backup, store.root.parent / "window-batches-restored", reserve_gib=0
    )
    assert omissions.published_report(restored, article) == restored_report
    checked = subprocess.run(
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
        check=True,
        timeout=60,
    )
    assert json.loads(checked.stdout)["original_root_access_attempts"] == 0


@pytest.mark.parametrize("bounded_first", [False, True])
def test_old_and_batched_keyed_cannot_switch_or_refresh_exhausted_budget(
    drafted, monkeypatch, bounded_first
):
    calls = mock_model(monkeypatch, intercept=lambda *a: '{"reviews":{}}')
    options = {"keyed_windows": True, "window_batches": bounded_first}
    with pytest.raises(ValueError, match="attempt_budget_exhausted"):
        check(drafted, **options)
    store, _ = drafted
    before = {
        p: p.read_bytes()
        for p in store.root.glob("wiki/completeness-jobs/*/attempts/*.json")
    }
    assert len(before) == len(calls) == 3
    with pytest.raises(ValueError, match="constraint_change_requires_new_article"):
        check(drafted, keyed_windows=True, window_batches=not bounded_first)
    with pytest.raises(ValueError, match="attempt_budget_exhausted"):
        check(drafted, **options)
    assert len(calls) == 3
    assert all(p.read_bytes() == raw for p, raw in before.items())


def test_batched_gpu_wait_and_pause_resume_same_completed_stage(drafted, monkeypatch):
    state = ["gpu"]

    def interrupt(context, calls):
        if len(calls) == 2 and state:
            state[0] = "pause"
            raise GpuBusyError("synthetic occupied GPU")
        if state and state[0] == "pause":
            state.clear()
            raise InterruptedError("synthetic pause")

    calls = mock_model(monkeypatch, intercept=interrupt)
    with pytest.raises(GpuBusyError):
        check(drafted, keyed_windows=True, window_batches=True)
    store, _ = drafted
    before = {
        p: p.read_bytes()
        for p in store.root.glob("wiki/completeness-jobs/*/chapter-*.json")
    }
    assert len(before) == 1
    with pytest.raises(InterruptedError):
        check(drafted, keyed_windows=True, window_batches=True)
    result = check(drafted, keyed_windows=True, window_batches=True)
    assert result["state"] == "model_omissions_complete"
    assert all(p.read_bytes() == raw for p, raw in before.items())
    assert calls.count(calls[0]) == 1


@pytest.mark.parametrize("mutation", ["receipt", "recipe"])
def test_partition_binding_cannot_be_changed_in_reports(drafted, monkeypatch, mutation):
    mock_model(monkeypatch)
    result = check(drafted, keyed_windows=True, window_batches=True)
    store, article = drafted
    folder = store.root / "wiki/completeness-jobs" / result["job"]
    if mutation == "receipt":
        result["window_batching"] = "changed"
        atomic_json(folder / "receipt.json", result)
        atomic_json(store.root / "wiki/completeness" / (article + ".json"), result)
    else:
        manifest = json.loads((folder / "manifest.json").read_bytes())
        manifest["recipe"]["extra"]["window_batching"] = "changed"
        atomic_json(folder / "manifest.json", manifest)
    with pytest.raises(
        ValueError, match="window_batching_changed|recipe_or_article_changed"
    ):
        omissions.published_report(store.root, article)


@pytest.mark.parametrize(
    "options",
    [
        {"window_batches": True},
        {"keyed_windows": True, "window_batches": True},
        {"per_source": True, "window_batches": True},
        {"window_batches": 1},
    ],
)
def test_batched_protocol_requires_explicit_keyed_per_source(drafted, options):
    store, article = drafted
    with pytest.raises(ValueError, match="window_batches_require_keyed_per_source"):
        omissions.review(store, article, **options)


def test_batched_policy_is_only_supported_for_reverse():
    assert constraints.generation_policy(
        constraints.BOUNDED_KEYED_REVERSE_POLICY, reverse=True
    )
    with pytest.raises(ValueError, match="unknown_generation_constraints"):
        constraints.generation_policy(constraints.BOUNDED_KEYED_REVERSE_POLICY)
