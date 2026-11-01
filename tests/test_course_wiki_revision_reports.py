"""Repairs bind immutable model history and preserve disagreement flags."""

import json

import pytest

from test_course_wiki_revision import archive as source_archive
from test_course_wiki_revision import published as source_published
from test_course_wiki_revision import setup
from wechat_decrypt_tool import course_pdf_ocr
from wechat_decrypt_tool import course_wiki_review as review
from wechat_decrypt_tool import course_wiki_revision as revision
from wechat_decrypt_tool import course_wiki_writer as writer
from wechat_decrypt_tool.course_knowledge import GpuBusyError
from wechat_decrypt_tool.course_library import canonical
from wechat_decrypt_tool.course_video_visual_audit import ReadOnlyArchive
from wechat_decrypt_tool.course_wiki import Wiki

archive = source_archive
published = source_published


def paired(published, monkeypatch, intercept=None, second_flags=("quiz-0",)):
    store, article_id, calls = setup(
        published, monkeypatch, flagged=("section-0-p-0",), intercept=intercept
    )
    first = json.loads(
        (store.root / "wiki/reviews" / (article_id + ".json")).read_bytes()
    )["job"]

    def judge(model, messages, *, schema):
        context = json.loads(messages[1]["content"])
        return json.dumps(
            {
                "reviews": [
                    {
                        "target": t["target"],
                        "verdict": "partial"
                        if t["target"] in second_flags
                        else "supported",
                        "reason": "Synthetic second model finding.",
                        "evidence": [
                            {
                                "witness": next(
                                    k
                                    for k, w in context["witnesses"].items()
                                    if w["source"] == t["citations"][0]
                                )
                            }
                        ],
                    }
                    for t in context["targets"]
                ]
            }
        )

    monkeypatch.setattr(review, "local_chat", judge)
    monkeypatch.setattr(
        course_pdf_ocr, "local_model_digest", lambda _: "second-weights"
    )
    second = review.review(ReadOnlyArchive(store.root), article_id)["job"]
    assert first != second
    return store, article_id, calls, [first, second]


def test_repair_uses_union_and_keeps_all_conflicting_opinions(published, monkeypatch):
    store, article_id, calls, jobs = paired(published, monkeypatch)
    before = canonical(Wiki(store.root).article(article_id))
    result = revision.revise(ReadOnlyArchive(store.root), article_id, review_jobs=jobs)
    assert {c["original"]["target"] for c in calls} == {"section-0-p-0", "quiz-0"}
    for context in calls:
        findings = context["model_crosscheck_finding"]["model_findings"]
        assert {f["job"] for f in findings} == set(jobs)
        assert {f["verdict"] for f in findings} == {"supported", "partial"}
    child = Wiki(store.root).article(result["id"])
    assert {b["job"] for b in child["packet"]["revision"]["review_reports"]} == set(
        jobs
    )
    assert canonical(Wiki(store.root).article(article_id)) == before
    assert not child["checks"]["semantic_accuracy_verified"]
    again = revision.revise(
        ReadOnlyArchive(store.root), article_id, review_jobs=list(reversed(jobs))
    )
    assert again["id"] == result["id"] and again["state"] == "already_published"
    assert len(calls) == 2


def test_preparation_keeps_old_flag_when_latest_report_has_none(published, monkeypatch):
    store, article_id, calls, jobs = paired(published, monkeypatch, second_flags=())

    def forbidden(*args, **kwargs):
        raise AssertionError("Preparation must not call models or resolve weights")

    monkeypatch.setattr(course_pdf_ocr, "local_model_digest", forbidden)
    monkeypatch.setattr(writer, "local_chat", forbidden)
    result = revision.revise(
        ReadOnlyArchive(store.root), article_id, review_jobs=jobs, prepare_only=True
    )
    assert result["targets"] == ["section-0-p-0"]
    assert result["model_calls"] == 0 and not calls
    prepared = json.loads(
        (store.root / "wiki/prepared-revisions" / (result["id"] + ".json")).read_bytes()
    )
    assert len(prepared["packet"]["revision"]["review_reports"]) == 2
    # Explicit frozen inputs are independent of the mutable latest pointer.
    (store.root / "wiki/reviews" / (article_id + ".json")).write_text("{}")
    assert (
        revision.revise(
            ReadOnlyArchive(store.root),
            article_id,
            review_jobs=list(reversed(jobs)),
            prepare_only=True,
        )
        == result
    )


def test_changed_historical_report_prevents_publication(published, monkeypatch):
    changed = []

    def tamper(context, calls):
        if changed:
            return
        changed.append(True)
        path = store.root / "wiki/review-jobs" / jobs[0] / "receipt.json"
        value = json.loads(path.read_bytes())
        value["summary"]["partial"] += 1
        path.write_bytes(canonical(value))

    store, article_id, _, jobs = paired(published, monkeypatch, intercept=tamper)
    with pytest.raises(ValueError, match="review_"):
        revision.revise(ReadOnlyArchive(store.root), article_id, review_jobs=jobs)
    assert len(Wiki(store.root).catalog()["articles"]) == 1


def test_paired_repair_reuses_checkpoint_after_gpu_wait(published, monkeypatch):
    blocked = []

    def interrupt(context, calls):
        if len(calls) == 2 and not blocked:
            blocked.append(True)
            raise GpuBusyError("Synthetic GPU wait")

    store, article_id, calls, jobs = paired(published, monkeypatch, intercept=interrupt)
    with pytest.raises(GpuBusyError):
        revision.revise(ReadOnlyArchive(store.root), article_id, review_jobs=jobs)
    result = revision.revise(
        ReadOnlyArchive(store.root), article_id, review_jobs=list(reversed(jobs))
    )
    assert result["state"] == "draft_published"
    assert len(calls) == 3
    assert (
        sum(c["original"]["target"] == calls[0]["original"]["target"] for c in calls)
        == 1
    )


def test_bound_forward_reports_and_omission_report_work_together(
    published, monkeypatch
):
    from test_course_wiki_revision_omissions import report as omission_report

    store, article_id, calls, jobs = paired(published, monkeypatch)
    omission = omission_report(store, article_id, monkeypatch)
    result = revision.revise(
        ReadOnlyArchive(store.root),
        article_id,
        review_jobs=jobs,
        omission_jobs=[omission],
    )
    binding = Wiki(store.root).article(result["id"])["packet"]["revision"]
    assert len(binding["review_reports"]) == 2
    assert binding["omission_reports"][0]["job"] == omission
    assert any(
        c.get("source_omission_findings")
        and c["model_crosscheck_finding"]["model_findings"]
        for c in calls
    )


@pytest.mark.parametrize(
    "jobs", ["not-a-list", ["same", "same"], [None], list("abcde")]
)
def test_invalid_report_sets_rejected_before_loading(jobs):
    with pytest.raises(ValueError, match="revision_invalid_review_jobs"):
        revision.source_crosschecks(None, "unused", jobs)
