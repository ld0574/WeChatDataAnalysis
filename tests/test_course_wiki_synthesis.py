import copy
import json

import pytest
from fastapi.testclient import TestClient

from test_course_wiki import archive as source_archive, published as source_published
from test_course_wiki_documents import courseware as source_courseware
from wechat_decrypt_tool import course_wiki_synthesis as synthesis
from wechat_decrypt_tool import course_wiki_writer as writer
from wechat_decrypt_tool.course_knowledge import GpuBusyError
from wechat_decrypt_tool.course_library import create_app
from wechat_decrypt_tool.course_lesson_exports import digest
from wechat_decrypt_tool.course_video_visual_audit import ReadOnlyArchive
from wechat_decrypt_tool.course_wiki import Wiki, prepare, validate_article

archive = source_archive
published = source_published
courseware = source_courseware


@pytest.fixture
def long_plan(courseware):
    store = courseware[0]
    with store.connect() as db:
        row = dict(db.execute("SELECT * FROM documents LIMIT 1").fetchone())
    chunks = []
    for n in range(4):
        text = (
            f"Complete original evidence for synthetic chapter {n}. " * 18
            + f"\ndef retained_{n}(value):\n    return value + {n}\n"
        )
        chunks.append((f"archive lesson.pdf page {n + 1} chars 0", text))
    store.save_document_versions(
        [
            {
                "path": row["path"],
                "source_key": "archive lesson.pdf",
                "raw_digest": row["raw_digest"],
                "extractor": "synthetic-four-page-native-v1",
                "chunks": chunks,
                "metadata": {},
            }
        ]
    )
    with store.connect() as db:
        rows = db.execute(
            "SELECT * FROM documents WHERE source_key='archive lesson.pdf' ORDER BY id"
        ).fetchall()
    selections = [
        {
            "document": r["id"],
            "revision": r["digest"],
            "text_sha256": digest(r["text"].encode()),
        }
        for r in rows
    ]
    code_packet = prepare(store.root, "Synthetic long topic", [selections[1]])
    ref, source = next(iter(code_packet["sources"].items()))
    plan = {
        "title": "Synthetic long topic",
        "introduction": [selections[0]],
        "chapters": [
            {
                "heading": f"Part {n}",
                "focus": f"Explain synthetic responsibility {n}",
                "selections": [s],
            }
            for n, s in enumerate(selections)
        ],
        "exercises": [
            {"chapter": n, "focus": f"Apply synthetic responsibility {n}"}
            for n in range(4)
        ],
    }
    plan["chapters"][1]["code_excerpt"] = {
        "source": ref,
        "start": source["text"].index("def retained_1"),
        "end": len(source["text"]),
    }
    return store, plan


def model(monkeypatch, intercept=None):
    from wechat_decrypt_tool import course_pdf_ocr

    monkeypatch.setattr(
        course_pdf_ocr, "local_model_digest", lambda _: "frozen-local-weights"
    )
    monkeypatch.setattr(
        synthesis, "local_model_digest", lambda _: "frozen-local-weights"
    )
    calls = []

    def chat(name, messages, schema):
        context = json.loads(messages[1]["content"])
        ref = next(iter(context["sources"]))
        assert sum(len(s["text"]) for s in context["sources"].values()) < 3200
        stage = context.get("current_section", {}).get("heading") or (
            "quiz-" + str(len(context["previous_questions"]))
            if "previous_questions" in context
            else "intro"
        )
        calls.append({"stage": stage, "context": context, "schema": schema})
        if intercept:
            response = intercept(stage, context)
            if response is not None:
                return response
        claim = {"text": "Synthetic bounded claim.", "citations": [ref]}
        if stage == "intro":
            return json.dumps(
                {
                    "overview": claim,
                    "objectives": ["Explain", "Apply"],
                    "prerequisites": [],
                    "gaps": [],
                }
            )
        if stage.startswith("quiz-"):
            return json.dumps(
                {
                    "quiz": {
                        "question": context["focus"],
                        "answer": "Synthetic answer",
                        "explanation": "Synthetic explanation",
                        "criteria": ["State the explicit condition"],
                        "citations": [ref],
                    },
                    "pitfalls": [claim],
                    "gaps": [],
                }
            )
        section = {
            "heading": stage,
            "paragraphs": [
                {
                    "text": f"{stage} original explanation {n}. "
                    + f"Chapter {stage} requires its own retained original conditions and evidence. "
                    * 3,
                    "citations": [ref],
                }
                for n in range(2)
            ],
            "image": "",
            "image_caption": "",
        }
        if not context["selected_code"]:
            section.update(code="", code_citations=[])
        else:
            assert "code" not in schema["properties"]
        return json.dumps(section)

    monkeypatch.setattr(writer, "local_chat", chat)
    return calls


def run(fixture):
    store, plan = fixture
    return synthesis.generate(ReadOnlyArchive(store.root), plan)


def test_fixed_assessments_preserve_body_checkpoints_and_failed_question_budget(
    long_plan, monkeypatch
):
    def answers(stage, context):
        if not stage.startswith("quiz-"):
            return None
        if "fixed_question" not in context and stage != "quiz-1":
            return None
        ref = next(iter(context["sources"]))
        quiz = {
            "answer": "Synthetic answer from current full chapter.",
            "explanation": "Synthetic explanation of the explicit condition.",
            "criteria": ["Identify the chapter condition."],
            "citations": [ref],
        }
        if "fixed_question" not in context:
            quiz["question"] = context["previous_questions"][0]
        return json.dumps(
            {
                "quiz": quiz,
                "pitfalls": [{"text": "Synthetic condition.", "citations": [ref]}],
                "gaps": [],
            }
        )

    calls = model(monkeypatch, answers)
    store, plan = long_plan
    with pytest.raises(ValueError, match="budget_exhausted_self-check-1"):
        run(long_plan)
    legacy = json.loads((store.root / "wiki/last-build.json").read_bytes())["id"]
    attempts = store.root / "wiki/jobs" / legacy / "attempts"
    before = {p.name: p.read_bytes() for p in attempts.glob("*.json")}
    assert len(calls) == 9
    result = synthesis.generate(
        ReadOnlyArchive(store.root), plan, fixed_assessments=True
    )
    child = Wiki(store.root).article(result["id"])
    assert result["id"] != legacy
    assert [q["question"] for q in child["article"]["quiz"]] == [
        "请根据本章原文完成以下任务：" + e["focus"] for e in plan["exercises"]
    ]
    assert {p.name: p.read_bytes() for p in attempts.glob("*.json")} == before
    assert (
        sum(
            call["stage"] in {"intro", "Part 0", "Part 1", "Part 2", "Part 3"}
            for call in calls
        )
        == 5
    )
    assert len(calls) == 13
    assert (
        synthesis.generate(ReadOnlyArchive(store.root), plan, fixed_assessments=True)[
            "state"
        ]
        == "already_published"
    )
    with pytest.raises(ValueError, match="budget_exhausted_self-check-1"):
        run(long_plan)
    assert len(calls) == 13


def test_fixed_assessments_reject_repeated_learning_goals_before_generation(
    long_plan, monkeypatch
):
    calls = model(monkeypatch)
    store, plan = long_plan
    plan["exercises"][1]["focus"] = plan["exercises"][0]["focus"]
    with pytest.raises(ValueError, match="need_distinct_goals"):
        synthesis.generate(ReadOnlyArchive(store.root), plan, fixed_assessments=True)
    assert not calls


def test_fixed_assessments_do_not_accept_model_generated_questions():
    with pytest.raises(ValueError, match="must_not_be_generated"):
        synthesis.fixed_exercise(
            {"quiz": {"question": "Model copied previous task"}},
            "Frozen complete chapter task",
        )


def test_long_originals_are_not_silently_summarized_to_old_packet_limit(
    long_plan, monkeypatch
):
    store, plan = long_plan
    packet, intro, chapters = synthesis.prepare_plan(store.root, plan)
    assert sum(len(s["text"]) for s in packet["sources"].values()) > 3200
    assert len(packet["sources"]) == 4
    calls = model(monkeypatch)
    result = run(long_plan)
    value = Wiki(store.root).article(result["id"])
    assert len(calls) == 9 and len(value["article"]["quiz"]) == 4
    assert value["pipeline"] == synthesis.POLICY
    assert not value["checks"]["semantic_accuracy_verified"]
    assert packet["scope_note"] in value["article"]["gaps"]
    for n, call in enumerate(calls[1:5]):
        assert call["context"]["sources"] == chapters[n]["sources"]
        allowed = call["schema"]["properties"]["paragraphs"]["items"]["properties"][
            "citations"
        ]["items"]["enum"]
        assert allowed == sorted(chapters[n]["sources"])
    selected = synthesis.code_excerpt(plan["chapters"][1]["code_excerpt"], chapters[1])
    assert value["article"]["sections"][1]["code"] == selected["text"]
    assert run(long_plan)["state"] == "already_published" and len(calls) == 9
    client = TestClient(create_app(ReadOnlyArchive(store.root)))
    assert client.get("/wiki/api/article/" + result["id"]).status_code == 200
    assert any(
        row["id"] == result["id"]
        for row in Wiki(store.root).search("Synthetic long topic")
    )


@pytest.mark.parametrize("target", ["paragraph", "overview", "quiz", "code", "heading"])
def test_cross_chapter_citations_and_selected_code_cannot_drift_on_read_or_revision(
    long_plan, monkeypatch, target
):
    store, _ = long_plan
    model(monkeypatch)
    result = run(long_plan)
    value = Wiki(store.root).article(result["id"])
    a, p = value["article"], value["packet"]
    foreign = p["chapter_scopes"][3][0]
    if target == "paragraph":
        a["sections"][0]["paragraphs"][0]["citations"] = [foreign]
    elif target == "overview":
        a["overview"]["citations"] = [foreign]
    elif target == "quiz":
        a["quiz"][0]["citations"] = [foreign]
    elif target == "heading":
        a["sections"][0]["heading"] = "Unplanned"
    else:
        a["sections"][1]["code"] = a["sections"][1]["code"].rstrip()
    with pytest.raises(ValueError, match="synthesis_"):
        validate_article(a, p)


def test_resumes_complete_chapters_without_partial_publication(long_plan, monkeypatch):
    store, _ = long_plan
    interrupted = []

    def intercept(stage, context):
        if stage == "Part 2" and not interrupted:
            interrupted.append(True)
            raise GpuBusyError("Synthetic handoff")

    calls = model(monkeypatch, intercept)
    with pytest.raises(GpuBusyError):
        run(long_plan)
    assert Wiki(store.root).catalog()["articles"] == []
    folder = next((store.root / "wiki/jobs").iterdir())
    retained = (folder / "chapter-1.json").read_bytes()
    result = run(long_plan)
    assert (
        result["state"] == "draft_published"
        and (folder / "chapter-1.json").read_bytes() == retained
    )
    assert sum(c["stage"] == "intro" for c in calls) == 1
    assert sum(c["stage"] == "Part 1" for c in calls) == 1


def test_failed_stage_budget_persists_across_restarts_and_keeps_prior_work(
    long_plan, monkeypatch
):
    store, _ = long_plan
    calls = model(
        monkeypatch, lambda stage, context: "{}" if stage == "Part 2" else None
    )
    for _ in range(2):
        with pytest.raises(ValueError, match="budget_exhausted"):
            run(long_plan)
    assert sum(c["stage"] == "Part 2" for c in calls) == 3
    assert Wiki(store.root).catalog()["articles"] == []


def test_scoped_target_repairs_have_only_assigned_originals(long_plan):
    store, plan = long_plan
    p, _, _ = synthesis.prepare_plan(store.root, plan)
    for target, allowed in [
        ("section-1-p-0", p["chapter_scopes"][1]),
        ("section-1-code", p["chapter_scopes"][1]),
        ("overview", p["overview_scope"]),
        ("quiz-2", p["quiz_scopes"][2]),
    ]:
        scoped = synthesis.target_packet(p, target, {})
        assert set(scoped["sources"]) == set(allowed)
    ref = p["chapter_scopes"][2][0]
    assert list(
        synthesis.target_packet(p, "pitfalls-0", {"citations": [ref]})["sources"]
    ) == [ref]


@pytest.mark.parametrize("flagged", ["section-2-p-0", "quiz-3"])
def test_long_article_revision_uses_assigned_sources_and_keeps_original_code(
    long_plan, monkeypatch, flagged
):
    from wechat_decrypt_tool import course_wiki_review as review
    from wechat_decrypt_tool import course_wiki_revision as revision

    store, _ = long_plan
    model(monkeypatch)
    parent_id = run(long_plan)["id"]
    parent = Wiki(store.root).article(parent_id)

    def judgments(name, messages, *, schema):
        context = json.loads(messages[1]["content"])
        return json.dumps(
            {
                "reviews": [
                    {
                        "target": t["target"],
                        "verdict": "partial" if t["target"] == flagged else "supported",
                        "reason": "Synthetic evidence check.",
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

    monkeypatch.setattr(review, "local_chat", judgments)
    review.review(ReadOnlyArchive(store.root), parent_id, single_targets=True)
    calls = []

    def repair(name, messages, *, schema):
        context = json.loads(messages[1]["content"])
        calls.append(context)
        assert len(context["sources"]) == 1
        assert sum(len(s["text"]) for s in context["sources"].values()) < 3200
        original = context["original"]
        result = {
            "action": "replace",
            "references": original["citations"],
            "gap": "",
        }
        if flagged.startswith("quiz-"):
            question = parent["article"]["quiz"][int(flagged.split("-")[1])]
            result.update(
                {
                    k: question[k]
                    for k in ("question", "answer", "explanation", "criteria")
                }
            )
            result["explanation"] += " Synthetic corrected condition."
        else:
            result["text"] = original["text"] + " Synthetic corrected condition."
        return json.dumps(result)

    monkeypatch.setattr(writer, "local_chat", repair)
    child_id = revision.revise(ReadOnlyArchive(store.root), parent_id)["id"]
    child = Wiki(store.root).article(child_id)
    assert len(calls) == 1
    assert child["packet"]["chapter_scopes"] == parent["packet"]["chapter_scopes"]
    assert [s["code"] for s in child["article"]["sections"]] == [
        s["code"] for s in parent["article"]["sections"]
    ]
    assert Wiki(store.root).article(parent_id) == parent
    assert child["packet"]["revision"]["parent_article"] == parent_id


@pytest.mark.parametrize(
    "mutation",
    [
        "outside_code",
        "bad_code_range",
        "duplicate_heading",
        "bad_quiz_chapter",
        "oversized_focus",
    ],
)
def test_invalid_plans_fail_before_any_model_call(long_plan, monkeypatch, mutation):
    store, plan = long_plan
    plan = copy.deepcopy(plan)
    calls = model(monkeypatch)
    if mutation == "outside_code":
        plan["chapters"][1]["code_excerpt"]["source"] = "invented"
    elif mutation == "bad_code_range":
        plan["chapters"][1]["code_excerpt"]["end"] = 10**8
    elif mutation == "duplicate_heading":
        plan["chapters"][1]["heading"] = plan["chapters"][0]["heading"]
    elif mutation == "bad_quiz_chapter":
        plan["exercises"][0]["chapter"] = True
    else:
        plan["chapters"][0]["focus"] = "a" * 501
    with pytest.raises(ValueError):
        synthesis.generate(ReadOnlyArchive(store.root), plan)
    assert calls == []
    assert (
        json.loads((store.root / "wiki/last-build.json").read_text())["state"]
        == "failed"
    )


def test_stage_rejects_foreign_packet_before_using_gpu(long_plan, monkeypatch):
    store, plan = long_plan
    p, _, _ = synthesis.prepare_plan(store.root, plan)
    calls = model(monkeypatch)
    job = writer.DraftJob(
        ReadOnlyArchive(store.root), p, "fixture", "frozen-local-weights", 0
    )
    with pytest.raises(ValueError, match="outside_frozen_packet"):
        job.stage(
            "foreign",
            "prompt",
            {},
            {},
            lambda _: None,
            evidence_packet={"sources": {"invented": {"text": "foreign"}}},
        )
    assert calls == []
