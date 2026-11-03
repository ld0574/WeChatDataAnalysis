import copy
import json

import pytest

from wechat_decrypt_tool import course_textbook_authoring as author
from wechat_decrypt_tool import course_textbook_manuscript_review as coverage
from wechat_decrypt_tool import course_textbook_post_repair_review as post
from wechat_decrypt_tool.course_knowledge import atomic_json
from wechat_decrypt_tool.course_library import canonical
from wechat_decrypt_tool.course_textbook_evidence import sha
from test_course_textbook_authoring import archive as archive
from test_course_textbook_composition import disputed_archive as disputed_archive
from test_course_textbook_manuscript_review import fake_inference
from test_course_textbook_post_repair_review import (
    failed_manuscript,
    passing_write_manuscript,
)


def post_exited(folder):
    path = folder / "post-repair-review-progress.json"
    value = json.loads(path.read_bytes())
    value.update(pid=9999999, created=1)
    atomic_json(path, value)


@pytest.mark.parametrize("source", [failed_manuscript, passing_write_manuscript])
def test_coverage_selects_factual_reviewed_derivative_without_writes_or_model_calls(
    request, monkeypatch, source
):
    root, key, calls, folder, reply = source(request, monkeypatch)
    result = post.run(root, folder.name)
    post_exited(folder)
    originals = {p: p.read_bytes() for p in folder.rglob("*.json")}
    count = len(calls)
    inventory, private = coverage.inputs(root, folder.name)
    assert private == post.saved(folder / post.OUTPUT)["data"]
    assert sha(canonical(private)) == result["private_article_sha256"]
    assert set(private["unaccepted_sections"]) == {"section-0000"}
    assert private["post_repair_lineage"]["whole_lesson_coverage_verified"] is False
    assert len(calls) == count
    assert all(p.read_bytes() == raw for p, raw in originals.items())
    coverage_calls = fake_inference(monkeypatch)
    reviewed = coverage.run(root, folder.name)
    assert coverage_calls
    assert reviewed["article_sha256"] == result["private_article_sha256"]
    assert not reviewed["reader_publication_allowed"]
    total = len(coverage_calls)
    assert coverage.run(root, folder.name) == reviewed
    assert len(coverage_calls) == total


def test_rehashing_modified_derived_body_and_result_cannot_authenticate_it(
    request, monkeypatch
):
    root, key, calls, folder, reply = failed_manuscript(request, monkeypatch)
    post.run(root, folder.name)
    post_exited(folder)
    private = copy.deepcopy(post.saved(folder / post.OUTPUT)["data"])
    private["chapters"][0]["sections"][0]["paragraphs"][0]["text"] += " Forged fact."
    author.save(folder, post.OUTPUT.removesuffix(".json"), private)
    result = post.saved(folder / "post-repair-review-result.json")["data"]
    result["private_article_sha256"] = sha(canonical(private))
    author.save(folder, "post-repair-review-result", result)
    count = len(calls)
    with pytest.raises(ValueError, match="exact_authenticated_derived_article"):
        coverage.inputs(root, folder.name)
    assert len(calls) == count
    assert not (folder / coverage.CONTRACT).exists()


@pytest.mark.parametrize("tamper", ["cache", "request", "metrics"])
def test_real_post_review_cache_request_and_gpu_metrics_are_required(
    request, monkeypatch, tamper
):
    root, key, calls, folder, reply = failed_manuscript(request, monkeypatch)
    post.run(root, folder.name)
    post_exited(folder)
    if tamper == "cache":
        path = folder / "section-0001-repair-review.json"
        value = json.loads(path.read_bytes())
        value["data"]["quiz"]["reason"] += " Modified."
        value["data_sha256"] = sha(canonical(value["data"]))
    else:
        path = folder / "attempts/section-0001-repair-review-01.json"
        value = json.loads(path.read_bytes())
        if tamper == "request":
            value["request_sha256"] = "0" * 64
        else:
            value["model_metrics"]["size_vram"] = 0
    atomic_json(path, value)
    count = len(calls)
    with pytest.raises(ValueError):
        coverage.inputs(root, folder.name)
    assert len(calls) == count
    assert not (folder / coverage.CONTRACT).exists()


def test_incomplete_post_review_blocks_coverage_instead_of_falling_back(
    request, monkeypatch
):
    root, key, calls, folder, reply = failed_manuscript(request, monkeypatch)
    atomic_json(folder / post.CONTRACT, {"state": "pending"})
    count = len(calls)
    with pytest.raises(FileNotFoundError):
        coverage.inputs(root, folder.name)
    assert len(calls) == count
    assert not (folder / coverage.CONTRACT).exists()


def test_completed_but_live_post_review_worker_blocks_selection(request, monkeypatch):
    root, key, calls, folder, reply = failed_manuscript(request, monkeypatch)
    post.run(root, folder.name)
    with pytest.raises(ValueError, match="post_review_worker_exited"):
        coverage.inputs(root, folder.name)


def test_later_derivative_does_not_refresh_existing_original_coverage_budget(
    request, monkeypatch
):
    root, key, calls, folder, reply = failed_manuscript(request, monkeypatch)
    original = post.saved(folder / "private-article.json")["data"]
    coverage_calls = fake_inference(monkeypatch)
    first = coverage.run(root, folder.name)
    assert first["article_sha256"] == sha(canonical(original))
    before = (folder / coverage.CONTRACT).read_bytes()
    total = len(coverage_calls)
    post.run(root, folder.name)
    post_exited(folder)
    assert coverage.inputs(root, folder.name)[1] == original
    assert coverage.run(root, folder.name) == first
    assert (folder / coverage.CONTRACT).read_bytes() == before
    assert len(coverage_calls) == total


def test_derivative_cannot_rebind_another_coverage_execution(request, monkeypatch):
    root, key, calls, folder, reply = failed_manuscript(request, monkeypatch)
    post.run(root, folder.name)
    post_exited(folder)
    atomic_json(folder / coverage.CONTRACT, {"article_sha256": "0" * 64})
    with pytest.raises(ValueError, match="preserve_bound_review_budget"):
        coverage.inputs(root, folder.name)
