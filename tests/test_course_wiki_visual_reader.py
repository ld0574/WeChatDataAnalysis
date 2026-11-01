import json
import os
from contextlib import contextmanager

import psutil
import pytest
from fastapi.testclient import TestClient

from test_course_wiki_snapshot import publication as source_publication
from test_course_wiki_snapshot import archive, courseware, published  # noqa: F401
from test_course_wiki_visual_review import judgment
from wechat_decrypt_tool import course_library as library
from wechat_decrypt_tool import course_wiki_snapshot as snapshot
from wechat_decrypt_tool import course_wiki_visual_review as visual
from wechat_decrypt_tool.course_knowledge import atomic_json
from wechat_decrypt_tool.course_video_visual_audit import ReadOnlyArchive

publication = source_publication


@pytest.fixture
def reports(publication, monkeypatch):
    @contextmanager
    def slot(*args):
        yield

    monkeypatch.setattr(visual, "slot", slot)
    monkeypatch.setattr(
        visual, "local_model_digest", lambda model: "synthetic-" + model
    )

    def chat(model, messages, *, schema):
        target = json.loads(messages[1]["content"])["target"]
        return json.dumps(judgment(target)), {
            "prompt_eval_count": 1000,
            "context_length": 16384,
            "size_vram": 8 * 1024**2,
        }

    monkeypatch.setattr(visual, "vision_chat", chat)
    results = [
        visual.review(ReadOnlyArchive(publication), "e" * 32, model)
        for model in ("fixture-one", "fixture-two")
    ]

    def forbidden(*args, **kwargs):
        raise AssertionError("reader_must_not_call_model")

    monkeypatch.setattr(visual, "vision_chat", forbidden)
    monkeypatch.setattr(visual, "local_model_digest", forbidden)
    return publication, results


def test_visual_history_api_retains_both_reports_and_restores_without_model(
    reports, tmp_path
):
    root, results = reports
    client = TestClient(library.create_app(ReadOnlyArchive(root)))
    response = client.get("/wiki/api/visual/" + "e" * 32)
    assert response.status_code == 200
    history = response.json()
    assert len(history["jobs"]) == 2
    assert {r["report"]["job"] for r in history["jobs"]} == {r["job"] for r in results}
    assert all(r["state"] == "complete" for r in history["jobs"])
    assert (
        not history["image_relevance_verified"]
        and not history["semantic_accuracy_verified"]
    )
    selected = client.get(
        "/wiki/api/visual/" + "e" * 32, params={"job": results[0]["job"]}
    ).json()
    assert len(selected["jobs"]) == 1
    backup = snapshot.create(root, tmp_path / "backup", reserve_gib=0)
    restored = snapshot.restore(backup, tmp_path / "restored", reserve_gib=0)
    reader = TestClient(library.create_app(snapshot.PublishedStore(restored)))
    assert reader.get("/wiki/api/visual/" + "e" * 32).json() == history
    atomic_json(
        tmp_path / "reader-fixture.json", {"root": str(restored), "article": "e" * 32}
    )


def test_corrupt_history_does_not_hide_valid_report_or_block_article(reports):
    root, results = reports
    first = results[0]
    (root / "wiki/visual-review-jobs" / first["job"] / "section-0.json").unlink()
    client = TestClient(library.create_app(ReadOnlyArchive(root)))
    history = client.get("/wiki/api/visual/" + "e" * 32).json()
    assert sorted(r["state"] for r in history["jobs"]) == ["complete", "invalid"]
    assert client.get("/wiki/api/article/" + "e" * 32).status_code == 200
    assert not history["source_deletion_allowed"]


@pytest.mark.parametrize("live", [True, False])
def test_progress_requires_matching_live_process_identity(reports, live):
    root, results = reports
    key = results[0]["job"]
    (root / "wiki/visual-review-jobs" / key / "receipt.json").unlink()
    folder = root / "wiki/visual-reviews"
    folder.mkdir(exist_ok=True)
    atomic_json(
        folder / ("e" * 32 + ".progress.json"),
        {
            "article": "e" * 32,
            "job": key,
            "state": "waiting_gpu",
            "pid": os.getpid(),
            "created": psutil.Process().create_time() + (0 if live else 10),
        },
    )
    result = visual.published_reports(root, "e" * 32, job=key)
    assert result["jobs"][0]["state"] == ("waiting_gpu" if live else "incomplete")


def test_unknown_article_and_traversal_are_safe(reports):
    root, _ = reports
    client = TestClient(library.create_app(ReadOnlyArchive(root)))
    assert client.get("/wiki/api/visual/" + "a" * 32).status_code == 404
    assert (
        client.get(
            "/wiki/api/visual/" + "e" * 32, params={"job": "../../outside"}
        ).status_code
        == 503
    )
