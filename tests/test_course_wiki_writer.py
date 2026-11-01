import json
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from test_course_wiki import archive as source_archive
from test_course_wiki import article, plan
from test_course_wiki import published as source_published
from wechat_decrypt_tool import course_wiki_writer as writer
from wechat_decrypt_tool.course_knowledge import GpuBusyError, ModelOutputTruncatedError
from wechat_decrypt_tool.course_video_visual_audit import ReadOnlyArchive
from wechat_decrypt_tool.course_wiki import Wiki, prepare
from wechat_decrypt_tool.course_library import create_app

archive = source_archive
published = source_published


def model(monkeypatch, store, key, intercept=None):
    from wechat_decrypt_tool import course_pdf_ocr

    monkeypatch.setattr(
        course_pdf_ocr, "local_model_digest", lambda _: "local-fixed-weights"
    )
    packet = prepare(store.root, **plan(key))
    full = article(packet)
    citation = full["overview"]["citations"]
    headings = ["Definition", "Conditions", "Procedure", "Limitations"]
    outline = {
        k: full[k] for k in ("title", "overview", "objectives", "prerequisites", "gaps")
    }
    outline["sections"] = [
        {"heading": h, "purpose": "Develop " + h, "citations": citation}
        for h in headings
    ]
    calls = []

    def chat(model_name, messages, schema):
        context = json.loads(messages[1]["content"])
        stage = (
            "outline"
            if schema == writer.OUTLINE
            else "exercises"
            if schema == writer.EXERCISES
            else context["current_section"]["heading"]
        )
        calls.append(stage)
        if intercept:
            custom = intercept(stage, calls, context)
            if custom is not None:
                return custom
        if schema == writer.OUTLINE:
            return json.dumps(outline)
        if schema == writer.EXERCISES:
            return json.dumps({k: full[k] for k in ("quiz", "pitfalls", "gaps")})
        assert schema == writer.SECTION
        number = headings.index(stage)
        result = dict(full["sections"][number])
        result["heading"] = stage
        result["paragraphs"] = [
            {
                "text": f"{stage} paragraph {i}. "
                + (
                    f"The synthetic rule in section {number} needs its stated conditions; "
                    "an unspecified condition must remain an explicit evidence gap. "
                )
                * 2,
                "citations": citation,
            }
            for i in range(2)
        ]
        return json.dumps(result)

    monkeypatch.setattr(writer, "local_chat", chat)
    return calls, packet


def run(store, key):
    return writer.generate(ReadOnlyArchive(store.root), plan(key))


def job_folder(store):
    return next((store.root / "wiki" / "jobs").iterdir())


def test_staged_article_acquires_and_releases_gpu_for_every_call(
    published, monkeypatch
):
    store, key = published
    calls, _ = model(monkeypatch, store, key)
    real = writer.gpu_lease
    events = []

    @contextmanager
    def observed(source):
        with real(source):
            events.append("enter")
            yield
            events.append("exit")

    monkeypatch.setattr(writer, "gpu_lease", observed)
    result = run(store, key)
    assert calls == [
        "outline",
        "Definition",
        "Conditions",
        "Procedure",
        "Limitations",
        "exercises",
    ]
    assert events == ["enter", "exit"] * 6
    value = Wiki(store.root).article(result["id"])
    assert value["pipeline"] == writer.POLICY
    assert not value["checks"]["semantic_accuracy_verified"]
    assert len(value["article"]["quiz"]) == 3
    assert (job_folder(store) / "assembled.json").is_file()
    assert run(store, key)["state"] == "already_published"
    assert len(calls) == 6


def test_resume_keeps_prior_sections_and_never_publishes_partial_article(
    published, monkeypatch
):
    store, key = published
    interrupted = []

    def intercept(stage, calls, context):
        if stage == "Conditions" and not interrupted:
            interrupted.append(True)
            raise GpuBusyError("Synthetic interruption")

    calls, _ = model(monkeypatch, store, key, intercept)
    with pytest.raises(GpuBusyError):
        run(store, key)
    folder = job_folder(store)
    retained = (folder / "section-0.json").read_bytes()
    assert Wiki(store.root).catalog()["articles"] == []
    result = run(store, key)
    assert result["state"] == "draft_published"
    assert calls.count("outline") == calls.count("Definition") == 1
    assert (folder / "section-0.json").read_bytes() == retained


def short_response(context):
    return json.dumps(
        {
            "heading": context["current_section"]["heading"],
            "paragraphs": [
                {
                    "text": "Too short",
                    "citations": context["current_section"]["citations"],
                }
            ],
            "code": "",
            "code_citations": [],
            "image": "",
            "image_caption": "",
        }
    )


def test_short_section_is_repaired_and_raw_attempt_is_retained_locally(
    published, monkeypatch
):
    store, key = published
    repairs = []

    def intercept(stage, calls, context):
        if stage == "Definition":
            repairs.append(context["previous_validation_failure"])
            if calls.count(stage) == 1:
                return short_response(context)

    model(monkeypatch, store, key, intercept)
    run(store, key)
    attempts = job_folder(store) / "attempts"
    failed = json.loads((attempts / "section-0-01.json").read_text())
    assert "Too short" in failed["raw_response"]
    assert (
        failed["validation_error"]
        == "section_requires_at_least_two_developed_paragraphs"
    )
    assert repairs == ["", failed["validation_error"]]
    assert json.loads((attempts / "section-0-02.json").read_text())[
        "accepted_structure"
    ]


def test_attempt_budget_survives_restart_without_gpu_loop(published, monkeypatch):
    store, key = published
    calls, _ = model(
        monkeypatch,
        store,
        key,
        lambda stage, calls, context: (
            short_response(context) if stage == "Definition" else None
        ),
    )
    with pytest.raises(ValueError, match="budget_exhausted"):
        run(store, key)
    before = list(calls)
    with pytest.raises(ValueError, match="budget_exhausted"):
        run(store, key)
    assert calls == before and calls.count("Definition") == 3
    assert Wiki(store.root).catalog()["articles"] == []


def test_truncated_response_retained_without_accepting_it(published, monkeypatch):
    store, key = published

    def intercept(stage, calls, context):
        if stage == "Definition" and calls.count(stage) == 1:
            raise ModelOutputTruncatedError(
                {
                    "done_reason": "length",
                    "message": {"content": "synthetic partial JSON"},
                }
            )

    model(monkeypatch, store, key, intercept)
    run(store, key)
    failed = json.loads(
        (job_folder(store) / "attempts" / "section-0-01.json").read_text()
    )
    assert failed["raw_response"] == "synthetic partial JSON"
    assert failed["model_metrics"]["done_reason"] == "length"


def test_tampered_stage_is_rejected_before_model_resume(published, monkeypatch):
    store, key = published

    def interrupt(stage, calls, context):
        if stage == "Conditions":
            raise GpuBusyError("Synthetic interruption")

    calls, _ = model(monkeypatch, store, key, interrupt)
    with pytest.raises(GpuBusyError):
        run(store, key)
    path = job_folder(store) / "outline.json"
    value = json.loads(path.read_text())
    value["data"]["title"] = "tampered"
    path.write_text(json.dumps(value))
    before = list(calls)
    with pytest.raises(ValueError, match="checkpoint_changed"):
        run(store, key)
    assert calls == before


def test_copied_prior_paragraph_is_not_accepted_as_a_new_section(
    published, monkeypatch
):
    store, key = published
    calls, packet = model(monkeypatch, store, key)
    result = run(store, key)
    sections = Wiki(store.root).article(result["id"])["article"]["sections"]
    candidate = dict(sections[1])
    candidate["paragraphs"] = sections[0]["paragraphs"]
    with pytest.raises(ValueError, match="previous_section"):
        writer.validate_section(
            candidate,
            packet,
            {"heading": candidate["heading"]},
            [p["text"].strip() for p in sections[0]["paragraphs"]],
        )
    assert len(calls) == 6


def test_search_finds_body_and_answer_with_no_model_call(published, monkeypatch):
    store, key = published
    calls, _ = model(monkeypatch, store, key)
    result = run(store, key)
    wiki = Wiki(store.root)
    rows = wiki.search("unspecified condition")
    assert rows[0]["id"] == result["id"] and rows[0]["section"].startswith("section-")
    assert "unspecified" not in rows[0]["title"].casefold()
    assert wiki.search("synthetic answer")[0]["section"] == "quiz"
    assert wiki.search("a unique nonexistent phrase") == []
    client = TestClient(create_app(ReadOnlyArchive(store.root)))
    response = client.get("/wiki/api/search", params={"q": "unspecified condition"})
    assert response.status_code == 200
    assert response.json()["method"] == "local_article_keyword_search"
    assert response.json()["items"][0]["id"] == result["id"]
    assert len(calls) == 6


def test_chinese_question_search_keeps_topic_keywords(published, monkeypatch):
    store, key = published
    calls, _ = model(monkeypatch, store, key)
    delegate = writer.local_chat

    def chinese(model_name, messages, schema):
        value = json.loads(delegate(model_name, messages, schema))
        if schema == writer.SECTION and value["heading"] == "Definition":
            value["paragraphs"][0]["text"] += " 索引更新必须核对已有记录。"
        return json.dumps(value)

    monkeypatch.setattr(writer, "local_chat", chinese)
    run(store, key)
    rows = Wiki(store.root).search("如何更新索引？")
    assert rows and rows[0]["heading"] == "Definition"
    assert Wiki(store.root).search("如何计算损失函数？") == []
    assert len(calls) == 6


def test_model_rejection_updates_status_before_any_content_call(published, monkeypatch):
    from wechat_decrypt_tool import course_pdf_ocr

    store, key = published
    calls, _ = model(monkeypatch, store, key)

    def reject(_):
        raise ValueError("Model is not installed local weights")

    monkeypatch.setattr(course_pdf_ocr, "local_model_digest", reject)
    with pytest.raises(ValueError):
        run(store, key)
    assert calls == [] and Wiki(store.root).catalog()["articles"] == []
    assert Wiki(store.root).catalog()["build"]["state"] == "failed"
    assert Wiki(store.root).catalog()["build"]["stage"] == "preparing"
