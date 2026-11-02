"""Finite source-bound revisions also preserve flexible chapter assignments."""

import json

import pytest

from test_course_wiki_flexible_structure import (
    archive as source_archive,
    fixed_model,
    long_evidence as source_long_evidence,
    planned,
    published as source_published,
)
from wechat_decrypt_tool import course_wiki_review as review
from wechat_decrypt_tool import course_wiki_revision as revision
from wechat_decrypt_tool import course_wiki_synthesis as synthesis
from wechat_decrypt_tool import course_wiki_writer as writer
from wechat_decrypt_tool.course_library import canonical
from wechat_decrypt_tool.course_wiki import Wiki

archive = source_archive
published = source_published
long_evidence = source_long_evidence


@pytest.mark.parametrize("omit_paragraph", [False, True])
def test_three_chapter_revision_retains_sources_and_rejects_underdeveloped_result(
    long_evidence, monkeypatch, omit_paragraph
):
    store, _, _, _, _, _, plan = planned(long_evidence, monkeypatch, count=3)
    fixed_model(monkeypatch)
    parent_id = synthesis.generate(store, plan, fixed_assessments=True)["id"]
    wiki = Wiki(store.root)
    parent = wiki.article(parent_id)
    before = canonical(parent)

    def judge(name, messages, *, schema):
        context = json.loads(messages[1]["content"])
        return json.dumps(
            {
                "reviews": [
                    {
                        "target": t["target"],
                        "verdict": "partial"
                        if t["target"] in {"section-0-p-0", "quiz-0"}
                        else "supported",
                        "reason": "Synthetic condition requires correction.",
                        "evidence": [
                            {
                                "witness": next(
                                    k
                                    for k, v in context["witnesses"].items()
                                    if v["source"] == t["citations"][0]
                                )
                            }
                        ],
                    }
                    for t in context["targets"]
                ]
            }
        )

    monkeypatch.setattr(review, "local_chat", judge)
    checked = review.review(
        store, parent_id, single_targets=True, constrained_evidence=True
    )
    calls = []

    def repair(name, messages, *, schema):
        context = json.loads(messages[1]["content"])
        original = context["original"]
        calls.append(original["target"])
        if original["target"].startswith("quiz-"):
            return json.dumps(
                {
                    "action": "replace",
                    "question": original["question"],
                    "answer": "Synthetic corrected answer",
                    "explanation": "Synthetic retained condition",
                    "criteria": ["State the retained condition"],
                    "references": original["citations"],
                    "gap": "",
                }
            )
        return json.dumps(
            {
                "action": "omit" if omit_paragraph else "replace",
                "text": ""
                if omit_paragraph
                else original["text"] + " Synthetic corrected condition.",
                "references": [] if omit_paragraph else original["citations"],
                "gap": "Synthetic unsupported paragraph omitted"
                if omit_paragraph
                else "",
            }
        )

    monkeypatch.setattr(writer, "local_chat", repair)
    kwargs = {"review_jobs": [checked["job"]]}
    if omit_paragraph:
        with pytest.raises(ValueError, match="sources_cannot_support_complete_article"):
            revision.revise(store, parent_id, **kwargs)
        assert len(wiki.catalog()["articles"]) == 1
        count = len(calls)
        with pytest.raises(ValueError, match="sources_cannot_support_complete_article"):
            revision.revise(store, parent_id, **kwargs)
        assert len(calls) == count
    else:
        revised = revision.revise(store, parent_id, **kwargs)
        child = wiki.article(revised["id"])
        assert len(child["article"]["sections"]) == len(child["article"]["quiz"]) == 3
        for field in [
            "sources",
            "chapter_plan",
            "exercise_plan",
            "chapter_scopes",
            "quiz_scopes",
            "structure_contract",
        ]:
            assert child["packet"][field] == parent["packet"][field]
        assert not child["checks"]["semantic_accuracy_verified"]
        assert (
            revision.revise(store, parent_id, **kwargs)["state"] == "already_published"
        )
    assert canonical(wiki.article(parent_id)) == before
    assert sorted(calls) == ["quiz-0", "section-0-p-0"]
