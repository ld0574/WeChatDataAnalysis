import copy
import json

import pytest

from test_course_wiki import archive as source_archive
from test_course_wiki import published as source_published
from test_course_wiki_review import setup as review_setup
from wechat_decrypt_tool import course_wiki_revision as revision
from wechat_decrypt_tool import course_wiki_review as review
from wechat_decrypt_tool import course_wiki_writer as writer
from wechat_decrypt_tool.course_knowledge import GpuBusyError
from wechat_decrypt_tool.course_library import canonical
from wechat_decrypt_tool.course_video_visual_audit import ReadOnlyArchive
from wechat_decrypt_tool.course_wiki import Wiki, versioned_articles

archive = source_archive
published = source_published


def setup(published, monkeypatch, flagged=("section-0-p-0", "quiz-0"), intercept=None):
    def judgments(context, calls):
        return json.dumps(
            {
                "reviews": [
                    {
                        "target": t["target"],
                        "verdict": "partial" if t["target"] in flagged else "supported",
                        "reason": "Synthetic missing condition."
                        if t["target"] in flagged
                        else "Synthetic support.",
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

    store, parent_id, _ = review_setup(published, monkeypatch, judgments)
    review.review(ReadOnlyArchive(store.root), parent_id)
    calls = []

    def repair(model_name, messages, *, schema):
        context = json.loads(messages[1]["content"])
        calls.append(context)
        if intercept:
            custom = intercept(context, calls)
            if custom is not None:
                return custom
        original = context["original"]
        refs = original["citations"]
        replacement = schema["oneOf"][0]
        assert set(replacement["properties"]["references"]["items"]["enum"]) == set(
            context["sources"]
        )
        if original["target"].startswith("quiz-"):
            value = {
                "action": "replace",
                "question": original["question"],
                "answer": "Corrected synthetic answer.",
                "explanation": "A synthetic rationale bounded to the evidence.",
                "criteria": ["State the condition."],
                "references": refs,
                "gap": "",
            }
        else:
            value = {
                "action": "replace",
                "text": original["text"] + " Corrected synthetic condition.",
                "references": refs,
                "gap": "",
            }
        return json.dumps(value)

    monkeypatch.setattr(writer, "local_chat", repair)
    return store, parent_id, calls


def run(store, parent_id):
    return revision.revise(ReadOnlyArchive(store.root), parent_id)


def test_revision_preserves_original_and_only_replaces_flagged_targets(
    published, monkeypatch
):
    store, parent_id, calls = setup(published, monkeypatch)
    wiki = Wiki(store.root)
    parent = wiki.article(parent_id)
    before = canonical(parent)
    result = run(store, parent_id)
    child = wiki.article(result["id"])
    assert canonical(wiki.article(parent_id)) == before
    assert child["packet"]["sources"] == parent["packet"]["sources"]
    assert child["packet"]["revision"]["parent_article"] == parent_id
    assert child["pipeline"] == revision.POLICY
    assert child["article"]["sections"][1:] == parent["article"]["sections"][1:]
    assert (
        child["article"]["sections"][0]["paragraphs"][1]
        == parent["article"]["sections"][0]["paragraphs"][1]
    )
    assert child["article"]["quiz"][1:] == parent["article"]["quiz"][1:]
    assert not child["checks"]["semantic_accuracy_verified"]
    n = len(calls)
    assert run(store, parent_id)["state"] == "already_published"
    assert len(calls) == n == 2
    rows = wiki.catalog()["articles"]
    assert (
        next(r for r in rows if r["id"] == parent_id)["superseded_by"] == result["id"]
    )
    assert next(r for r in rows if r["id"] == result["id"])["revision_of"] == parent_id
    assert not next(r for r in rows if r["id"] == result["id"]).get("superseded_by")
    assert len(wiki.search("synthetic condition")) == 1
    assert (
        review.published_review(store.root, parent_id)["state"]
        == "model_crosscheck_complete"
    )


def test_invalid_fix_budget_survives_restart_and_does_not_publish(
    published, monkeypatch
):
    def invalid(context, calls):
        return json.dumps(
            {
                "action": "replace",
                "text": "Invented references cannot pass.",
                "references": ["invented"],
                "gap": "",
            }
        )

    store, parent_id, calls = setup(
        published, monkeypatch, flagged=("overview",), intercept=invalid
    )
    with pytest.raises(ValueError, match="budget_exhausted"):
        run(store, parent_id)
    assert len(calls) == 3
    with pytest.raises(ValueError, match="budget_exhausted"):
        run(store, parent_id)
    assert len(calls) == 3
    assert len(Wiki(store.root).catalog()["articles"]) == 1
    assert (
        calls[1]["previous_validation_failure"]
        == "revision_missing_or_invented_reference"
    )


def test_omissions_cannot_publish_an_empty_or_shrunken_article(published, monkeypatch):
    def omit(context, calls):
        return json.dumps(
            {
                "action": "omit",
                "text": "",
                "references": [],
                "gap": "Synthetic missing source.",
            }
        )

    store, parent_id, _ = setup(
        published,
        monkeypatch,
        flagged=("section-0-p-0", "section-0-p-1"),
        intercept=omit,
    )
    with pytest.raises(ValueError, match="sources_cannot_support_complete_article"):
        run(store, parent_id)
    assert len(Wiki(store.root).catalog()["articles"]) == 1
    state = json.loads((store.root / "wiki/last-build.json").read_bytes())
    assert state["state"] == "needs_more_sources"
    saved = json.loads(
        (
            store.root / "wiki/jobs" / state["id"] / "assembled-revision.json"
        ).read_bytes()
    )
    assert saved["article"]["sections"][0]["paragraphs"] == []
    assert len(saved["article"]["sections"]) == 4  # Never hide the empty chapter.


def test_resume_reuses_successful_fix_after_gpu_interruption(published, monkeypatch):
    interrupted = []

    def stop(context, calls):
        if context["original"]["target"] == "quiz-0" and not interrupted:
            interrupted.append(True)
            raise GpuBusyError("Synthetic GPU interruption")

    store, parent_id, calls = setup(published, monkeypatch, intercept=stop)
    with pytest.raises(GpuBusyError):
        run(store, parent_id)
    assert len(Wiki(store.root).catalog()["articles"]) == 1
    assert run(store, parent_id)["state"] == "draft_published"
    assert sum(c["original"]["target"] == "section-0-p-0" for c in calls) == 1


def test_no_report_or_no_flagged_findings_does_not_start_repair(published, monkeypatch):
    store, parent_id, calls = setup(published, monkeypatch, flagged=())
    with pytest.raises(ValueError, match="no_flagged_targets"):
        run(store, parent_id)
    assert not calls
    (store.root / "wiki/reviews" / (parent_id + ".json")).unlink()
    with pytest.raises(ValueError, match="requires_complete_source_crosscheck"):
        run(store, parent_id)
    assert not calls


def test_changed_source_review_is_rejected_before_publication(published, monkeypatch):
    changed = []

    def alter(context, calls):
        if not changed:
            changed.append(True)
            path = store.root / "wiki/reviews" / (parent_id + ".json")
            value = json.loads(path.read_bytes())
            value["summary"]["supported"] = 999
            path.write_bytes(canonical(value))

    store, parent_id, _ = setup(published, monkeypatch, intercept=alter)
    with pytest.raises(ValueError, match="review_receipt_changed"):
        run(store, parent_id)
    assert len(Wiki(store.root).catalog()["articles"]) == 1


def test_revision_code_must_remain_a_literal_source_excerpt():
    packet = {"sources": {"one": {"text": "original_code()"}}}
    value = {
        "action": "replace",
        "text": "invented_code()",
        "references": ["one"],
        "gap": "",
    }
    with pytest.raises(ValueError, match="code_not_literal"):
        revision.validate_fix(value, packet, code=True)


def test_omissions_use_stable_original_indices():
    article = {
        "overview": {"text": "Overview"},
        "gaps": [],
        "sections": [{"paragraphs": [{"text": str(i)} for i in range(5)]}],
        "quiz": [],
    }
    changes = {
        f"section-0-p-{i}": {
            "action": "omit",
            "text": "",
            "references": [],
            "gap": "Missing source.",
        }
        for i in (1, 3)
    }
    result = revision.apply_fixes(article, changes)
    assert [p["text"] for p in result["sections"][0]["paragraphs"]] == ["0", "2", "4"]
    assert len(article["sections"][0]["paragraphs"]) == 5


def test_version_families_choose_one_latest_branch_and_reject_cycles():
    rows = [
        {"id": "a", "updated": 1},
        {"id": "b", "updated": 2, "revision_of": "a"},
        {"id": "c", "updated": 3, "revision_of": "a"},
        {"id": "d", "updated": 4, "revision_of": "b"},
    ]
    before = copy.deepcopy(rows)
    result = versioned_articles(rows)
    assert rows == before
    assert [r["id"] for r in result if not r.get("superseded_by")] == ["d"]
    assert {r["superseded_by"] for r in result[:-1]} == {"d"}
    skewed = versioned_articles(
        [{"id": "a", "updated": 10}, {"id": "b", "updated": 1, "revision_of": "a"}]
    )
    assert skewed[0]["superseded_by"] == "b"  # Clock skew cannot resurrect an ancestor.
    with pytest.raises(ValueError, match="ancestry"):
        versioned_articles(
            [
                {"id": "a", "updated": 1, "revision_of": "b"},
                {"id": "b", "updated": 2, "revision_of": "a"},
            ]
        )
    with pytest.raises(ValueError, match="ancestry"):
        versioned_articles([{"id": "a", "updated": 1, "revision_of": "missing"}])


def test_coverage_counts_current_topics_and_all_versions_separately(
    published, monkeypatch
):
    from wechat_decrypt_tool.course_wiki_coverage import build

    store, parent_id, _ = setup(published, monkeypatch)
    child_id = run(store, parent_id)["id"]
    receipt = build(ReadOnlyArchive(store.root))
    assert receipt["summary"]["draft_articles"] == 1
    assert receipt["summary"]["draft_article_versions"] == 2
    assert receipt["courses"][0]["draft_articles"] == [child_id]


def test_duplicate_rewritten_questions_are_rejected_without_blaming_sources(
    published, monkeypatch
):
    def duplicate(context, calls):
        original = Wiki(store.root).article(parent_id)["article"]["quiz"][1]
        return json.dumps(
            {
                "action": "replace",
                **{
                    k: original[k]
                    for k in ("question", "answer", "explanation", "criteria")
                },
                "references": original["citations"],
                "gap": "",
            }
        )

    store, parent_id, _ = setup(
        published, monkeypatch, flagged=("quiz-0",), intercept=duplicate
    )
    with pytest.raises(ValueError, match="assembled_draft_rejected"):
        run(store, parent_id)
    assert len(Wiki(store.root).catalog()["articles"]) == 1
    assert (
        json.loads((store.root / "wiki/last-build.json").read_bytes())["state"]
        == "failed"
    )


def test_missing_overview_cannot_be_hidden_by_other_usable_sections(
    published, monkeypatch
):
    def omit(context, calls):
        return json.dumps(
            {
                "action": "omit",
                "text": "",
                "references": [],
                "gap": "The overview lacks evidence.",
            }
        )

    store, parent_id, _ = setup(
        published, monkeypatch, flagged=("overview",), intercept=omit
    )
    with pytest.raises(ValueError, match="overview_needs_more_sources"):
        run(store, parent_id)
    assert len(Wiki(store.root).catalog()["articles"]) == 1
    assert (
        json.loads((store.root / "wiki/last-build.json").read_bytes())["state"]
        == "needs_more_sources"
    )
