import copy
import json

import pytest

from wechat_decrypt_tool.course_knowledge import atomic_json
from wechat_decrypt_tool.course_library import canonical
from wechat_decrypt_tool.course_textbook_evidence import inventory, sha
from wechat_decrypt_tool import course_textbook_ledger as ledger


def point(ref="E00000_000"):
    return {
        "label": "Fixture principle",
        "teaching": "The fixture explicitly distinguishes a condition from a result.",
        "category": "principle",
        "citations": [ref],
    }


def candidate():
    return {
        "points": [point()],
        "excluded_sources": [
            {
                "reference": "E00001_000",
                "reason": "Greeting only, no instructional statement.",
            }
        ],
    }


def audit():
    return {
        "reviews": [
            {
                "point": 0,
                "verdict": "supported",
                "reason": "The cited fixture states the condition and result.",
            }
        ],
        "missing_points": [],
        "excluded_sources": [
            {
                "reference": "E00001_000",
                "valid": True,
                "reason": "The fixture is a greeting.",
            }
        ],
    }


def test_every_source_requires_a_teaching_or_non_teaching_disposition():
    sources = {"E00000_000": {}, "E00001_000": {}}
    ledger.validate_ledger(candidate(), sources)
    for mutation in ("missing", "invented", "both", "duplicate", "empty_reason"):
        value = candidate()
        if mutation == "missing":
            value["excluded_sources"] = []
        elif mutation == "invented":
            value["points"][0]["citations"] = ["unknown"]
        elif mutation == "both":
            value["points"][0]["citations"].append("E00001_000")
        elif mutation == "duplicate":
            value["excluded_sources"] *= 2
        else:
            value["excluded_sources"][0]["reason"] = " "
        with pytest.raises(ValueError):
            ledger.validate_ledger(value, sources)


def test_review_checks_every_knowledge_point_and_every_exclusion():
    sources = {"E00000_000": {}, "E00001_000": {}}
    ledger.validate_audit(audit(), candidate(), sources)
    assert ledger.audit_passed(audit())
    for field in ("reviews", "excluded_sources"):
        value = audit()
        value[field] = []
        with pytest.raises(ValueError):
            ledger.validate_audit(value, candidate(), sources)
    value = audit()
    value["reviews"] *= 2
    with pytest.raises(ValueError):
        ledger.validate_audit(value, candidate(), sources)
    for mutation in ("partial", "missing_knowledge", "bad_exclusion"):
        value = audit()
        if mutation == "partial":
            value["reviews"][0]["verdict"] = "partial"
        elif mutation == "missing_knowledge":
            value["missing_points"] = [point("E00001_000")]
        else:
            value["excluded_sources"][0]["valid"] = False
        assert not ledger.audit_passed(value)


@pytest.fixture
def setup_job(tmp_path, monkeypatch):
    (tmp_path / "knowledge.sqlite3").touch()
    value = inventory(
        {
            "id": "a" * 24,
            "source_export_sha256": "b" * 64,
            "evidence": {
                "0": {
                    "kind": "speech",
                    "start": 0,
                    "end": 1,
                    "text": "Fixture instructional condition and result.",
                },
                "1": {
                    "kind": "speech",
                    "start": 1,
                    "end": 2,
                    "text": "Fixture greeting only.",
                },
            },
        },
        lesson_artifact="c" * 64,
    )
    key = sha(canonical(value))
    folder = tmp_path / "wiki" / "textbook-evidence" / key
    folder.mkdir(parents=True)
    atomic_json(folder / "manifest.json", value)
    monkeypatch.setattr(
        "wechat_decrypt_tool.course_pdf_ocr.local_model_digest", lambda model: "d" * 64
    )
    calls = []

    def inference(self, messages, *, schema):
        calls.append(copy.deepcopy(messages))
        context = json.loads(messages[-1]["content"])
        response = (
            audit()
            if "candidate" in context and "review" not in context
            else candidate()
        )
        return json.dumps(response), {"synthetic_fixture": True}

    monkeypatch.setattr(ledger.LedgerJob, "generate_response", inference)
    return tmp_path, key, value, calls


def test_complete_ledger_is_reviewed_private_and_resumes_without_model_replay(
    setup_job,
):
    root, key, value, calls = setup_job
    result = ledger.run(root, key, wait_seconds=0)
    assert len(calls) == 2
    assert result["automatic_local_evidence_review_passed"] is True
    assert result["state"] == "ledger_ready_for_conceptual_reorganization"
    assert set(result["source_dispositions"]) == set(value["sources"])
    assert result["whole_lesson_knowledge_coverage_verified"] is False
    assert result["semantic_accuracy_verified"] is False
    assert result["reader_article_published"] is False
    assert ledger.run(root, key, wait_seconds=0) == result
    assert len(calls) == 2
    local = root / "wiki" / "textbook-ledger-jobs" / result["job"]
    saved = json.loads((local / "ledger.json").read_bytes())
    saved["data"]["points"]["P0000_000"]["teaching"] = "Changed after acceptance"
    atomic_json(local / "ledger.json", saved)
    with pytest.raises(ValueError, match="checksum_changed"):
        ledger.run(root, key, wait_seconds=0)
    assert len(calls) == 2


def test_changed_execution_recipe_cannot_silently_refresh_attempt_budget(
    setup_job, monkeypatch
):
    root, key, _, calls = setup_job
    ledger.run(root, key, wait_seconds=0)
    monkeypatch.setattr(ledger, "REVIEW", ledger.REVIEW + "\nChanged recipe.")
    with pytest.raises(ValueError, match="bound_ledger_recipe_changed"):
        ledger.run(root, key, wait_seconds=0)
    assert len(calls) == 2


def test_failed_structure_attempts_remain_exhausted_across_restarts(
    setup_job, monkeypatch
):
    root, key, _, calls = setup_job

    def invalid(self, messages, *, schema):
        calls.append(messages)
        return json.dumps({"points": [], "excluded_sources": []}), None

    monkeypatch.setattr(ledger.LedgerJob, "generate_response", invalid)
    for _ in range(2):
        with pytest.raises(ValueError, match="attempt_budget_exhausted"):
            ledger.run(root, key, wait_seconds=0)
    assert len(calls) == 2
    stages = list(
        (root / "wiki" / "textbook-ledger-jobs").glob(
            "*/attempts/page-0000-extract-*.json"
        )
    )
    assert len(stages) == 2


def test_unresolved_semantics_survive_one_bounded_repair_without_false_publication(
    setup_job, monkeypatch
):
    root, key, _, calls = setup_job

    def unresolved(self, messages, *, schema):
        calls.append(messages)
        context = json.loads(messages[-1]["content"])
        response = candidate()
        if "candidate" in context and "review" not in context:
            response = audit()
            response["reviews"][0]["verdict"] = "partial"
        return json.dumps(response), None

    monkeypatch.setattr(ledger.LedgerJob, "generate_response", unresolved)
    result = ledger.run(root, key, wait_seconds=0)
    assert len(calls) == 4
    assert len(result["issues"]) == 1
    assert result["automatic_local_evidence_review_passed"] is False
    assert result["reader_article_published"] is False
    assert result["state"] == "ledger_requires_semantic_repair"
    ledger.run(root, key, wait_seconds=0)
    assert len(calls) == 4


def test_pause_preserves_inputs_and_avoids_model_calls(setup_job):
    root, key, _, calls = setup_job
    (root / "pause.flag").write_text("user pause")
    with pytest.raises(InterruptedError, match="textbook_paused"):
        ledger.run(root, key, wait_seconds=0)
    assert not calls
    assert (root / "pause.flag").read_text() == "user pause"
