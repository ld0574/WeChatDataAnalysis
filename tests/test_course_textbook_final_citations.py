import copy
import json
from types import SimpleNamespace

import pytest

from wechat_decrypt_tool import course_textbook_final_citations as module
from wechat_decrypt_tool.course_knowledge import atomic_json


def fixture_job(tmp_path, monkeypatch, *, reason=None):
    (tmp_path / "attempts").mkdir()
    original = {
        "paragraphs": [{"text": "虚构测试解释", "citations": ["R0"]}],
        "coverage": {"P0": {"paragraphs": [0], "reason": "测试映射"}},
    }
    records = []
    for n in [1, 2]:
        record = {
            "error_type": "ValueError",
            "validation_error": reason or module.reviewable.repair.ERROR,
            "model_metrics": {},
            "raw_response": json.dumps(original),
        }
        path = tmp_path / "attempts" / f"section-0000-whole-edit-{n:02d}.json"
        atomic_json(path, record)
        records.append((path, record))
    job = SimpleNamespace(
        root=tmp_path,
        model_digest="model",
        inventory={},
        working={},
        stage_input=lambda *a, **k: ({}, "binding"),
    )
    monkeypatch.setattr(module.editor.post, "verify_requests", lambda *a: records)
    monkeypatch.setattr(module.editor, "validate_metrics", lambda *a: None)
    monkeypatch.setattr(module.reviewable, "validate_reviewable", lambda *a: None)

    def proposal(value, *args):
        value = copy.deepcopy(value)
        value["paragraphs"][0]["citations"].append("R1")
        return value, {"0": {"old": ["R0"], "new": ["R0", "R1"]}}

    monkeypatch.setattr(module.reviewable, "propose_citations", proposal)
    return job, original


def test_citation_hypothesis_preserves_failed_calls_and_does_not_fabricate_an_accepted_write(
    tmp_path, monkeypatch
):
    job, original = fixture_job(tmp_path, monkeypatch)
    before = {p: p.read_bytes() for p in (tmp_path / "attempts").iterdir()}
    result = module.proposed(
        job,
        "section-0000-whole-edit",
        "prompt",
        {},
        {"assigned_points": {"P0": {}}},
        lambda value: None,
        {"sources": {}},
        write_receipt=True,
    )
    assert result["paragraphs"][0]["text"] == original["paragraphs"][0]["text"]
    assert result["coverage"] == original["coverage"]
    assert all(p.read_bytes() == content for p, content in before.items())
    assert not (tmp_path / "section-0000-whole-edit.json").exists()
    receipt = json.loads(
        (
            tmp_path / "private-citation-proposals/section-0000-whole-edit.json"
        ).read_bytes()
    )
    assert receipt["factual_acceptance"] is False
    assert receipt["additional_model_calls"] == 0
    assert receipt["original_strict_validation_passed"] is False


def test_non_citation_failure_cannot_be_recovered_as_a_citation_proposal(
    tmp_path, monkeypatch
):
    job, _ = fixture_job(
        tmp_path,
        monkeypatch,
        reason="requires_final_editor_duplicate_prior_body_paragraph",
    )
    with pytest.raises(ValueError, match="complete_citation_only_editorial_hypothesis"):
        module.proposed(
            job,
            "section-0000-whole-edit",
            "prompt",
            {},
            {"assigned_points": {"P0": {}}},
            lambda value: None,
            {"sources": {}},
            write_receipt=True,
        )
    assert not (tmp_path / "private-citation-proposals").exists()


def test_real_structural_validation_still_runs_after_the_citation_proposal(
    tmp_path, monkeypatch
):
    job, _ = fixture_job(tmp_path, monkeypatch)

    def invalid(value):
        raise ValueError("requires_final_editor_duplicate_prior_body_paragraph")

    with pytest.raises(ValueError, match="duplicate_prior_body_paragraph"):
        module.proposed(
            job,
            "section-0000-whole-edit",
            "prompt",
            {},
            {"assigned_points": {"P0": {}}},
            invalid,
            {"sources": {}},
            write_receipt=True,
        )
    assert not (tmp_path / "private-citation-proposals").exists()


def test_factual_review_role_always_uses_original_authentication(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        module, "BASE_AUTHENTICATED", lambda *a: calls.append(a[1]) or {"passed": False}
    )
    result = module.authenticated(
        SimpleNamespace(root=tmp_path),
        "section-0000-actual-body-review",
        "prompt",
        {},
        {},
        lambda v: None,
        {},
    )
    assert result["passed"] is False
    assert calls == ["section-0000-actual-body-review"]


def test_existing_proposal_cannot_change_silently(tmp_path, monkeypatch):
    job, _ = fixture_job(tmp_path, monkeypatch)
    arguments = (
        job,
        "section-0000-whole-edit",
        "prompt",
        {},
        {"assigned_points": {"P0": {}}},
        lambda value: None,
        {"sources": {}},
    )
    module.proposed(*arguments, write_receipt=True)
    path = tmp_path / "private-citation-proposals/section-0000-whole-edit.json"
    value = json.loads(path.read_bytes())
    value["factual_acceptance"] = True
    atomic_json(path, value)
    with pytest.raises(ValueError, match="unchanged_editorial_citation_hypothesis"):
        module.proposed(*arguments, write_receipt=False)
