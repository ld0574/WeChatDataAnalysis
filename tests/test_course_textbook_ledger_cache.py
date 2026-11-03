import copy
import json

import pytest

from wechat_decrypt_tool.course_textbook_ledger_cache import compose
from wechat_decrypt_tool import course_textbook_ledger as ledger
from wechat_decrypt_tool.course_textbook_ledger_cache import recover
from wechat_decrypt_tool.course_textbook_evidence import inventory, sha
from wechat_decrypt_tool.course_knowledge import atomic_json
from wechat_decrypt_tool.course_library import canonical


def claim(ref):
    return {
        "label": "Fixture knowledge",
        "teaching": "A specific fixture condition.",
        "category": "concept",
        "citations": [ref],
    }


def test_only_an_explicit_existing_exclusion_can_fill_the_missing_disposition():
    candidates = [
        {
            "points": [claim("c")],
            "excluded_sources": [{"reference": "a", "reason": "Fixture greeting."}],
        },
        {
            "points": [],
            "excluded_sources": [
                {"reference": "a", "reason": "Fixture greeting."},
                {"reference": "b", "reason": "Fixture navigation."},
            ],
        },
    ]
    before = copy.deepcopy(candidates)
    result, provenance = compose(candidates, {"a": {}, "b": {}, "c": {}})
    assert result["points"] == candidates[0]["points"]
    assert result["excluded_sources"] == [
        *candidates[0]["excluded_sources"],
        candidates[1]["excluded_sources"][1],
    ]
    assert provenance == {
        "base_candidate": 0,
        "borrowed": [{"reference": "b", "candidate": 1}],
    }
    assert candidates == before


def test_missing_source_cannot_be_invented_as_non_teaching():
    candidates = [{"points": [claim("a")], "excluded_sources": []} for _ in range(2)]
    with pytest.raises(ValueError, match="no_unambiguous_explicit_exclusion"):
        compose(candidates, {"a": {}, "b": {}})


def test_conflicting_teaching_classification_requires_new_semantic_work():
    candidates = [
        {"points": [claim("a")], "excluded_sources": []},
        {
            "points": [claim("b")],
            "excluded_sources": [
                {"reference": "a", "reason": "Competing classification."}
            ],
        },
    ]
    with pytest.raises(ValueError, match="no_unambiguous_explicit_exclusion"):
        compose(candidates, {"a": {}, "b": {}, "c": {}})


@pytest.mark.parametrize(
    "mutation", ["unknown", "duplicate", "empty", "complete", "only_one"]
)
def test_malformed_or_already_complete_candidates_are_not_rewritten(mutation):
    candidates = [
        {"points": [claim("a")], "excluded_sources": []},
        {
            "points": [claim("a")],
            "excluded_sources": [{"reference": "b", "reason": "Fixture greeting."}],
        },
    ]
    sources = {"a": {}, "b": {}, "c": {}}
    if mutation == "unknown":
        candidates[0]["points"][0]["citations"] = ["invented"]
    elif mutation == "duplicate":
        candidates[1]["excluded_sources"] *= 2
    elif mutation == "empty":
        candidates[1]["excluded_sources"][0]["reason"] = " "
    elif mutation == "complete":
        sources = {"a": {}, "b": {}}
    else:
        candidates.pop()
    with pytest.raises(ValueError):
        compose(candidates, sources)


@pytest.mark.parametrize("tamper_metrics", [False, True])
def test_cache_recovery_preserves_attempts_and_still_runs_the_full_review(
    tmp_path, monkeypatch, tamper_metrics
):
    (tmp_path / "knowledge.sqlite3").touch()
    value = inventory(
        {
            "id": "a" * 24,
            "source_export_sha256": "b" * 64,
            "evidence": {
                str(i): {
                    "kind": "speech",
                    "text": f"Fixture source {i}",
                    "start": i,
                    "end": i + 1,
                }
                for i in range(3)
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
    candidates = [
        {
            "points": [claim("E00002_000")],
            "excluded_sources": [
                {"reference": "E00000_000", "reason": "Fixture greeting."}
            ],
        },
        {
            "points": [],
            "excluded_sources": [
                {"reference": "E00000_000", "reason": "Fixture greeting."},
                {"reference": "E00001_000", "reason": "Fixture navigation."},
            ],
        },
    ]
    calls = []

    def incomplete(self, messages, *, schema):
        response = candidates[len(calls)]
        calls.append(messages)
        return json.dumps(response), {
            "model_digest": "d" * 64,
            "done_reason": "stop",
            "prompt_eval_count": 100,
            "eval_count": 100,
            "context_length": 16384,
            "size_vram": 1024,
        }

    monkeypatch.setattr(ledger.LedgerJob, "generate_response", incomplete)
    with pytest.raises(ValueError, match="attempt_budget_exhausted"):
        ledger.run(tmp_path, key, wait_seconds=0)
    assert len(calls) == 2
    job_folder = next((tmp_path / "wiki" / "textbook-ledger-jobs").iterdir())
    status = json.loads((job_folder / "progress.json").read_bytes())
    status.update(pid=999999999, created=1)
    atomic_json(job_folder / "progress.json", status)
    attempts = sorted((job_folder / "attempts").glob("*.json"))
    if tamper_metrics:
        record = json.loads(attempts[0].read_bytes())
        record["model_metrics"]["done_reason"] = "length"
        atomic_json(attempts[0], record)
    before = {p.name: sha(p.read_bytes()) for p in attempts}
    if tamper_metrics:
        with pytest.raises(ValueError, match="budget_or_gpu_unproven"):
            recover(tmp_path, key, job_folder.name, 0)
        assert not (job_folder / "page-0000-extract.json").exists()
    else:
        receipt_key, receipt = recover(tmp_path, key, job_folder.name, 0)
        assert receipt["model_calls"] == 0
        assert receipt["full_source_review_still_required"] is True
        saved = json.loads((job_folder / "page-0000-extract.json").read_bytes())
        assert saved["recovery"]["sha256"] == receipt_key

        def review_only(self, messages, *, schema):
            calls.append(messages)
            context = json.loads(messages[-1]["content"])
            assert "candidate" in context
            response = {
                "reviews": [
                    {
                        "point": 0,
                        "verdict": "supported",
                        "reason": "Fixture support checked.",
                    }
                ],
                "missing_points": [],
                "excluded_sources": [
                    {
                        "reference": ref,
                        "valid": True,
                        "reason": "Fixture exclusion checked.",
                    }
                    for ref in ("E00000_000", "E00001_000")
                ],
            }
            return json.dumps(response), None

        monkeypatch.setattr(ledger.LedgerJob, "generate_response", review_only)
        result = ledger.run(tmp_path, key, wait_seconds=0)
        assert result["automatic_local_evidence_review_passed"] is True
        assert result["reader_article_published"] is False
        assert len(calls) == 3
    assert {p.name: sha(p.read_bytes()) for p in attempts} == before
