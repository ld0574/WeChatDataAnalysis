import copy
import json
from contextlib import contextmanager

import jsonschema
import pytest

from wechat_decrypt_tool import course_textbook_resolution as resolution
from wechat_decrypt_tool.course_knowledge import atomic_json
from wechat_decrypt_tool.course_library import canonical
from wechat_decrypt_tool.course_textbook_evidence import inventory, sha


@pytest.fixture
def archive(tmp_path):
    (tmp_path / "knowledge.sqlite3").touch()
    value = inventory(
        {
            "id": "a" * 24,
            "source_export_sha256": "b" * 64,
            "evidence": {
                str(i): {
                    "kind": "speech",
                    "text": f"Original fixture source {i}: a condition determines its corresponding action.",
                    "start": i,
                    "end": i + 1,
                }
                for i in range(5)
            },
        },
        lesson_artifact="c" * 64,
    )
    invkey = sha(canonical(value))
    dest = tmp_path / "wiki/textbook-evidence" / invkey
    dest.mkdir(parents=True)
    atomic_json(dest / "manifest.json", value)
    refs = list(value["sources"])
    points = {
        key: {
            "label": "Fixture fact " + key,
            "teaching": "The fixture condition controls the fixture action.",
            "category": "concept",
            "citations": citations,
        }
        for key, citations in {
            "P0000_000": refs[:2],
            "P0000_001": refs[1:3],
            "P0000_002": [refs[3]],
        }.items()
    }
    review = {
        "reviews": [
            {
                "point": i,
                "verdict": "unsupported" if i == 0 else "supported",
                "reason": "Fixture review requires original support.",
            }
            for i in range(3)
        ],
        "excluded_sources": [
            {
                "reference": refs[4],
                "valid": False,
                "reason": "Fixture exclusion disputed.",
            }
        ],
        "missing_points": [],
    }
    parent = {
        "job": "d" * 32,
        "lesson": value["lesson"],
        "inventory_sha256": invkey,
        "points": points,
        "pages": [{"page": 0, "points": list(points), "local_review_passed": False}],
        "source_dispositions": {
            ref: {
                "points": [
                    key for key, point in points.items() if ref in point["citations"]
                ],
                "non_teaching_reason": "Fixture exclusion." if i == 4 else "",
            }
            for i, ref in enumerate(refs)
        },
        "issues": [{"page": 0, "review": review}],
        "automatic_local_evidence_review_passed": False,
        "whole_lesson_knowledge_coverage_verified": False,
        "semantic_accuracy_verified": False,
        "reader_article_published": False,
        "source_deletion_allowed": False,
    }
    folder = tmp_path / "wiki/textbook-keyed-jobs" / parent["job"]
    folder.mkdir(parents=True)
    atomic_json(
        folder / "ledger.json", {"data": parent, "data_sha256": sha(canonical(parent))}
    )
    (folder / "attempts").mkdir()
    (folder / "attempts/original-failed.json").write_bytes(
        b'{"state":"prior_failure_budget_spent"}'
    )
    return tmp_path, value, parent, refs


def teaching(source):
    return {
        "kind": "teaching",
        "points": [
            {
                "label": "Fixture condition",
                "teaching": "The fixture condition determines an action.",
                "category": "concept",
                "quotations": [source["text"]],
            }
        ],
    }


def audit(passed=True):
    return {
        "verdict": "supported" if passed else "needs_changes",
        "reason": "The complete fixture original was checked.",
        "missing_information": []
        if passed
        else ["An original fixture condition remains unexplained."],
    }


def test_scope_closes_over_shared_originals_and_keeps_unrelated_sources(archive):
    _, _, parent, refs = archive
    assert set(resolution.affected_sources(parent)) == set(refs[:3] + refs[4:])
    changed = copy.deepcopy(parent)
    changed["issues"][0]["review"]["missing_points"] = [{"citations": [refs[3]]}]
    assert set(resolution.affected_sources(changed)) == set(refs)


def test_quotations_must_be_literal_substantial_and_not_rewritten(archive):
    _, value, _, refs = archive
    source = value["sources"][refs[0]]
    good = teaching(source)
    resolution.validate_decision(good, source)
    for mutation in ("invented", "too_short", "duplicate", "missing"):
        bad = copy.deepcopy(good)
        if mutation == "invented":
            bad["points"][0]["quotations"] = [
                "This quotation is not present in the original."
            ]
        elif mutation == "too_short":
            bad["points"][0]["quotations"] = [source["text"][:2]]
        elif mutation == "duplicate":
            bad["points"].append(copy.deepcopy(bad["points"][0]))
        else:
            bad["points"][0].pop("quotations")
        with pytest.raises((ValueError, jsonschema.ValidationError)):
            resolution.validate_decision(bad, source)
    bad = audit()
    bad["missing_information"] = ["Still missing an explanation."]
    with pytest.raises(ValueError, match="consistent_audit"):
        resolution.validate_audit(bad)


def test_compilation_replaces_only_closed_scope_and_retains_negative_audits(archive):
    _, value, parent, refs = archive
    before = copy.deepcopy(parent)
    targets = resolution.affected_sources(parent)
    decisions = {ref: teaching(value["sources"][ref]) for ref in targets}
    decisions[refs[4]] = {
        "kind": "non_teaching",
        "reason": "Fixture administrative navigation only.",
    }
    audits = {ref: audit() for ref in targets}
    result = resolution.compile_ledger(value, parent, decisions, audits, "e" * 32)
    assert parent == before
    assert result["points"]["P0000_002"] == parent["points"]["P0000_002"]
    assert "P0000_000" not in result["points"] and "P0000_001" not in result["points"]
    assert not result["issues"]
    assert set(result["source_dispositions"]) == set(refs)
    assert all("quotations" not in point for point in result["points"].values())
    assert not result["whole_lesson_knowledge_coverage_verified"]
    assert not result["semantic_accuracy_verified"]
    assert not result["source_deletion_allowed"]
    audits[refs[0]] = audit(False)
    failed = resolution.compile_ledger(value, parent, decisions, audits, "e" * 32)
    assert failed["state"] == "ledger_requires_semantic_repair"
    assert not failed["automatic_local_evidence_review_passed"]
    assert len(failed["issues"]) == 1
    decisions.pop(refs[0])
    with pytest.raises(ValueError, match="exact_disputed_source_scope"):
        resolution.compile_ledger(value, parent, decisions, audits, "e" * 32)


def inference(monkeypatch, *, unsupported=False, invalid=False):
    calls = []
    monkeypatch.setattr(
        "wechat_decrypt_tool.course_pdf_ocr.local_model_digest", lambda model: "f" * 64
    )

    @contextmanager
    def slot(self, stage):
        yield

    def response(self, messages, *, schema):
        ctx = json.loads(messages[1]["content"])
        prompt = messages[0]["content"]
        calls.append((prompt, ctx))
        result = (
            teaching(ctx["source"])
            if prompt == resolution.PROMPT
            else audit(not unsupported)
        )
        return "{}" if invalid else json.dumps(result), None

    monkeypatch.setattr(resolution.ResolutionJob, "slot", slot)
    monkeypatch.setattr(resolution.ResolutionJob, "generate_response", response)
    return calls


def test_resume_reuses_calls_and_preserves_original_failures_and_budget(
    archive, monkeypatch
):
    root, _, parent, _ = archive
    old = (
        root
        / "wiki/textbook-keyed-jobs"
        / parent["job"]
        / "attempts/original-failed.json"
    )
    before = old.read_bytes()
    calls = inference(monkeypatch)
    result = resolution.run(root, parent["job"])
    assert result["state"] == "ledger_ready_for_conceptual_reorganization"
    assert len(calls) == 2 * len(resolution.affected_sources(parent))
    size = len(calls)
    assert resolution.run(root, parent["job"]) == result
    assert len(calls) == size
    assert old.read_bytes() == before
    folder = root / "wiki/textbook-keyed-jobs" / result["job"]
    saved = json.loads((folder / "ledger.json").read_bytes())
    assert saved["data_sha256"] == sha(canonical(saved["data"]))
    assert not (folder / "reading.json").exists()
    from wechat_decrypt_tool.course_textbook_authoring import load_inputs

    _, writing_input = load_inputs(root, parent["inventory_sha256"], result["job"])
    assert writing_input == saved["data"]


def test_failed_semantic_audit_does_not_start_another_repair_loop(archive, monkeypatch):
    root, _, parent, _ = archive
    calls = inference(monkeypatch, unsupported=True)
    result = resolution.run(root, parent["job"])
    assert result["state"] == "ledger_requires_semantic_repair"
    assert result["unresolved_sources"] == result["resolved_sources"]
    assert len(calls) == result["resolved_sources"] * 2
    previous = len(calls)
    resolution.run(root, parent["job"])
    assert len(calls) == previous


def test_structural_failure_cannot_refresh_attempts_on_restart(archive, monkeypatch):
    root, _, parent, _ = archive
    calls = inference(monkeypatch, invalid=True)
    for _ in range(2):
        with pytest.raises(ValueError, match="stage_attempt_budget_exhausted"):
            resolution.run(root, parent["job"])
        assert len(calls) == 2
