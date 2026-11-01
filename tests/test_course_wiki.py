import copy
import json
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from test_course_lesson_exports import archive as source_archive
from wechat_decrypt_tool import course_library as library
from wechat_decrypt_tool import course_wiki as wiki
from wechat_decrypt_tool.course_knowledge import GpuBusyError, atomic_json
from wechat_decrypt_tool.course_video_visual_audit import ReadOnlyArchive


archive = source_archive


@pytest.fixture
def published(request):
    store, key, item = request.getfixturevalue("archive")
    item.update(relative_path=r"Collection\Lesson 2.mp4", title="Synthetic lesson")
    atomic_json(
        store.root / "inventory-unique.json",
        {"videos": [item], "unique_readable_videos": 1},
    )
    payload = json.loads(
        (store.root / "lessons" / key / "evidence.json").read_text("utf-8")
    )
    for f in payload["frames"]:
        path = store.root / f["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic frame bytes " + str(f["id"]).encode())
    result = library.publish(ReadOnlyArchive(store.root))
    assert result["requested"] == result["published"] == 1
    return store, key


def plan(key):
    return {
        "title": "Synthetic topic",
        "selections": [{"video": key, "sections": ["section-0"]}],
    }


def article(packet):
    speech = next(k for k, v in packet["sources"].items() if v["kind"] == "speech")
    frame = next(k for k, v in packet["sources"].items() if v["kind"] == "frame")
    c = {"text": "Synthetic explanation. " * 10, "citations": [speech]}
    return {
        "title": "Synthetic topic",
        "overview": copy.deepcopy(c),
        "objectives": ["Explain the conditions", "Apply the rule"],
        "prerequisites": [],
        "sections": [
            {
                "heading": "Section " + str(i),
                "paragraphs": [copy.deepcopy(c)],
                "code": "",
                "code_citations": [],
                "image": frame if i == 0 else "",
                "image_caption": "Synthetic diagram" if i == 0 else "",
            }
            for i in range(4)
        ],
        "pitfalls": [copy.deepcopy(c)],
        "quiz": [
            {
                "question": "Explain the condition " + str(i),
                "answer": "Synthetic answer",
                "explanation": "Synthetic rationale",
                "criteria": ["Identify the condition"],
                "citations": [speech],
            }
            for i in range(3)
        ],
        "gaps": ["Synthetic missing source condition"],
    }


def test_frozen_sources_do_not_merge_away_explanations(published):
    store, key = published
    reader = library.Library(store.root)
    lesson = reader.lesson(key)
    assert len(lesson["sections"]) == 2
    assert len(reader.terms()[0]["occurrences"]) == 2
    assert reader.search("shared concept")
    packet = wiki.prepare(store.root, **plan(key))
    assert len(packet["sources"]) == 7
    assert not packet["full_topic_coverage_verified"]
    assert any(v.get("image_sha256") for v in packet["sources"].values())


@pytest.mark.parametrize(
    "mutation",
    [
        lambda a: a["overview"].update(citations=["invented"]),
        lambda a: a["sections"][0].update(image=a["overview"]["citations"][0]),
        lambda a: a["sections"][0].update(paragraphs=[]),
        lambda a: a.update(sections=a["sections"][:1]),
        lambda a: a.update(quiz=a["quiz"][:2]),
        lambda a: a["quiz"][0].update(answer=""),
        lambda a: a["quiz"][0].update(criteria=[]),
        lambda a: a["sections"][0].update(
            code="invented executable", code_citations=a["overview"]["citations"]
        ),
    ],
)
def test_rejects_untraceable_or_outline_only_drafts(published, mutation):
    store, key = published
    packet = wiki.prepare(store.root, **plan(key))
    value = article(packet)
    mutation(value)
    with pytest.raises(ValueError):
        wiki.validate_article(value, packet)


def test_checks_are_never_semantic_acceptance(published):
    store, key = published
    packet = wiki.prepare(store.root, **plan(key))
    checks = wiki.validate_article(article(packet), packet)
    assert checks["structure_verified"] and checks["citation_ids_verified"]
    assert not any(
        checks[k]
        for k in (
            "semantic_accuracy_verified",
            "image_relevance_verified",
            "full_topic_coverage_verified",
            "source_deletion_allowed",
        )
    )


def mock_model(monkeypatch, store, key):
    from wechat_decrypt_tool import course_pdf_ocr

    packet = wiki.prepare(store.root, **plan(key))
    monkeypatch.setattr(
        course_pdf_ocr, "local_model_digest", lambda _: "local-weights-digest"
    )
    calls = []

    def chat(model, messages, schema):
        assert schema == wiki.ARTICLE
        assert "sources" in json.loads(messages[1]["content"])
        calls.append(model)
        return json.dumps(article(packet))

    monkeypatch.setattr(wiki, "local_chat", chat)
    return calls


def test_generation_resumes_and_rejects_tampered_publication(published, monkeypatch):
    store, key = published
    calls = mock_model(monkeypatch, store, key)
    result = wiki.generate(ReadOnlyArchive(store.root), plan(key))
    assert result["state"] == "draft_published"
    value = wiki.Wiki(store.root).article(result["id"])
    assert value["state"] == "model_draft"
    assert (
        wiki.generate(ReadOnlyArchive(store.root), plan(key))["state"]
        == "already_published"
    )
    assert len(calls) == 1
    row = wiki.Wiki(store.root).catalog()["articles"][0]
    (store.root / "wiki" / "objects" / (row["object"] + ".json")).write_text("{}")
    with pytest.raises(ValueError, match="checksum"):
        wiki.Wiki(store.root).article(result["id"])


def test_busy_gpu_never_calls_model_or_publishes(published, monkeypatch):
    store, key = published
    calls = mock_model(monkeypatch, store, key)

    def busy(_):
        raise GpuBusyError("busy")

    monkeypatch.setattr(wiki, "gpu_lease", busy)
    with pytest.raises(GpuBusyError):
        wiki.generate(ReadOnlyArchive(store.root), plan(key))
    assert calls == [] and wiki.Wiki(store.root).catalog()["articles"] == []
    status = json.loads((store.root / "wiki" / "last-build.json").read_text())
    assert status["state"] == "deferred_gpu"


def test_waiting_pilot_uses_same_lease_and_generates_only_after_release(
    published, monkeypatch
):
    store, key = published
    calls = mock_model(monkeypatch, store, key)
    real_lease = wiki.gpu_lease
    attempts = []

    @contextmanager
    def temporarily_busy(source):
        attempts.append(True)
        if len(attempts) == 1:
            raise GpuBusyError("busy")
        with real_lease(source):
            yield

    monkeypatch.setattr(wiki, "gpu_lease", temporarily_busy)
    monkeypatch.setattr(wiki.time, "sleep", lambda _: None)
    result = wiki.generate(ReadOnlyArchive(store.root), plan(key), wait_seconds=10)
    assert result["state"] == "draft_published"
    assert len(attempts) == 2 and len(calls) == 1


def test_failed_answer_does_not_publish_partial_article(published, monkeypatch):
    store, key = published
    mock_model(monkeypatch, store, key)
    monkeypatch.setattr(wiki, "local_chat", lambda *args, **kwargs: "{}")
    with pytest.raises(KeyError):
        wiki.generate(ReadOnlyArchive(store.root), plan(key))
    assert wiki.Wiki(store.root).catalog()["articles"] == []
    status = json.loads((store.root / "wiki" / "last-build.json").read_text())
    assert status["state"] == "failed"


def test_missing_or_nonlocal_model_records_failure_before_content_call(
    published, monkeypatch
):
    from wechat_decrypt_tool import course_pdf_ocr

    store, key = published
    calls = mock_model(monkeypatch, store, key)

    def reject_model(_):
        raise ValueError("Only installed local weights are allowed")

    monkeypatch.setattr(course_pdf_ocr, "local_model_digest", reject_model)
    with pytest.raises(ValueError):
        wiki.generate(ReadOnlyArchive(store.root), plan(key))
    assert calls == []
    assert wiki.Wiki(store.root).catalog()["build"]["state"] == "failed"


def test_failed_source_republication_keeps_old_readable_catalog(published):
    store, key = published
    before = (store.root / "library" / "catalog.json").read_bytes()
    payload = json.loads(
        (store.root / "lessons" / key / "evidence.json").read_text("utf-8")
    )
    (store.root / payload["frames"][0]["path"]).unlink()
    with pytest.raises(OSError):
        library.publish(ReadOnlyArchive(store.root))
    assert (store.root / "library" / "catalog.json").read_bytes() == before
    assert library.Library(store.root).lesson(key)


def test_full_source_catalog_exposes_unpublished_courses(published):
    store, key = published
    path = store.root / "inventory-unique.json"
    inv = json.loads(path.read_text("utf-8"))
    pending = {
        **inv["videos"][0],
        "path": str(store.root / "pending.mp4"),
        "relative_path": r"Collection\Lesson 10.mp4",
        "title": "Pending source",
    }
    inv["videos"].append(pending)
    inv["unique_readable_videos"] = 2
    atomic_json(path, inv)
    result = library.publish(ReadOnlyArchive(store.root))
    assert result["requested"] == 2 and result["published"] == 1
    assert result["lessons"][0]["id"] == key  # Natural order: 2 before 10.
    assert result["lessons"][1]["state"] == "not_published"
    assert result["unpublished"][0]["code"] == "unregistered_lesson"


def test_unavailable_unknown_oversized_sources_fail_explicitly(published):
    store, key = published
    with pytest.raises(ValueError, match="Unknown"):
        wiki.prepare(store.root, "Topic", [{"video": key, "sections": ["section-99"]}])
    with pytest.raises(KeyError):
        wiki.prepare(
            store.root, "Topic", [{"video": "unknown", "sections": ["section-0"]}]
        )
    with pytest.raises(ValueError):
        library.source_folders({"relative_path": r"..\outside.mp4"})


def test_serving_is_read_only_images_are_frozen_and_missing_is_explicit(
    published, monkeypatch
):
    store, key = published
    calls = mock_model(monkeypatch, store, key)
    result = wiki.generate(ReadOnlyArchive(store.root), plan(key))
    ro = ReadOnlyArchive(store.root)
    client = TestClient(library.create_app(ro))
    assert client.get("/").status_code == 200
    assert client.get("/wiki/api/catalog").json()["articles"][0]["id"] == result["id"]
    assert client.get("/wiki/api/article/" + result["id"]).status_code == 200
    assert client.get("/library/api/lesson/unknown").status_code == 404
    assert client.get("/wiki/api/article/unknown").status_code == 404
    assert client.get("/library-assets/other.js").status_code == 404
    lesson = library.Library(store.root).lesson(key)
    sha = next(
        e["image_sha256"] for e in lesson["evidence"].values() if e["kind"] == "frame"
    )
    assert client.get("/library/frame/" + sha).status_code == 200
    media = store.root / "library" / "media" / (sha + ".jpg")
    media.write_bytes(b"tampered")
    assert client.get("/library/frame/" + sha).status_code == 503
    assert len(calls) == 1  # No model or archive writes during reads.
