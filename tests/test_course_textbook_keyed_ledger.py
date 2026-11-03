import copy
import json
import os

import jsonschema
import psutil
import pytest

from wechat_decrypt_tool.course_knowledge import atomic_json
from wechat_decrypt_tool.course_library import canonical
from wechat_decrypt_tool.course_textbook_evidence import inventory, sha
from wechat_decrypt_tool import course_textbook_keyed_ledger as keyed
from wechat_decrypt_tool import course_textbook_ledger as old


def body(label="Fixture concept"):
    return {
        "label": label,
        "teaching": "The fixture explicitly distinguishes a condition and its result.",
        "category": "concept",
    }


def point(refs):
    return {**body(), "citations": list(refs)}


def test_each_source_key_is_mandatory_and_its_disposition_is_exclusive():
    sources = {"a": {}, "b": {}}
    good = {
        "a": {"kind": "teaching", "points": [body()]},
        "b": {"kind": "non_teaching", "reason": "Fixture greeting."},
    }
    assert keyed.normalize_sources(good, sources) == {
        "points": [point(["a"])],
        "excluded_sources": [{"reference": "b", "reason": "Fixture greeting."}],
    }
    for mutation in ("missing", "both", "empty", "unknown", "whitespace"):
        value = copy.deepcopy(good)
        if mutation == "missing":
            value.pop("b")
        elif mutation == "both":
            value["a"]["reason"] = "Competing exclusion"
        elif mutation == "empty":
            value["a"]["points"] = []
        elif mutation == "unknown":
            value["c"] = value["b"]
        else:
            value["b"]["reason"] = " "
        with pytest.raises((ValueError, jsonschema.ValidationError)):
            keyed.normalize_sources(value, sources)


def test_identical_teaching_is_merged_with_all_sources_and_stable_order():
    sources = {"a": {}, "b": {}}
    value = {
        "b": {"kind": "teaching", "points": [body(), body()]},
        "a": {"kind": "teaching", "points": [body()]},
    }
    normalized = keyed.normalize_sources(value, sources)
    assert normalized["points"] == [point(["a", "b"])]
    assert not normalized["excluded_sources"]


def test_review_schema_requires_every_point_and_exclusion_without_extra_keys():
    candidate = {
        "points": [point(["a"])],
        "excluded_sources": [{"reference": "b", "reason": "Fixture greeting."}],
    }
    good = {
        "reviews": {
            "0": {"verdict": "supported", "reason": "Fixture support checked."}
        },
        "excluded_sources": {
            "b": {"valid": True, "reason": "Fixture greeting checked."}
        },
        "missing_points": [],
    }
    audit = keyed.normalize_review(good, candidate, {"a": {}, "b": {}})
    assert old.audit_passed(audit)
    for key, field in (("0", "reviews"), ("b", "excluded_sources")):
        value = copy.deepcopy(good)
        value[field].pop(key)
        with pytest.raises(jsonschema.ValidationError):
            keyed.normalize_review(value, candidate, {"a": {}, "b": {}})
    value = copy.deepcopy(good)
    value["reviews"]["1"] = good["reviews"]["0"]
    with pytest.raises(jsonschema.ValidationError):
        keyed.normalize_review(value, candidate, {"a": {}, "b": {}})


def test_partitions_preserve_every_whole_source_once():
    sources = {
        "a": {"text": "A" * 1800},
        "b": {"text": "B" * 1400},
        "c": {"text": ""},
        "d": {"text": "D" * 1700},
    }
    parts = keyed.partitions(list(sources), sources)
    assert [ref for group in parts for ref in group] == list(sources)
    assert all(
        sum(len(sources[ref]["text"]) for ref in group) <= keyed.PART_CHARACTERS
        for group in parts
    )


def test_semantic_repair_scope_closes_over_shared_claims():
    candidate = {
        "points": [point(["a", "b"]), point(["b", "c"]), point(["d"])],
        "excluded_sources": [{"reference": "e", "reason": "Fixture greeting."}],
    }
    audit = {
        "reviews": [
            {"point": i, "verdict": "partial" if i == 0 else "supported"}
            for i in range(3)
        ],
        "missing_points": [],
        "excluded_sources": [{"reference": "e", "valid": False}],
    }
    assert keyed.repair_references(candidate, audit) == {"a", "b", "c", "e"}


@pytest.fixture
def predecessor(tmp_path, monkeypatch):
    (tmp_path / "knowledge.sqlite3").touch()
    value = inventory(
        {
            "id": "a" * 24,
            "source_export_sha256": "b" * 64,
            "evidence": {
                str(i): {
                    "kind": "speech",
                    "text": str(i) * 4000,
                    "start": i,
                    "end": i + 1,
                }
                for i in range(3)
            },
        },
        lesson_artifact="c" * 64,
    )
    assert len(value["pages"]) == 3
    key = sha(canonical(value))
    folder = tmp_path / "wiki" / "textbook-evidence" / key
    folder.mkdir(parents=True)
    atomic_json(folder / "manifest.json", value)
    monkeypatch.setattr(
        "wechat_decrypt_tool.course_pdf_ocr.local_model_digest", lambda model: "d" * 64
    )
    calls, failed_calls = [], []

    def old_inference(self, messages, *, schema):
        calls.append(messages)
        context = json.loads(messages[-1]["content"])
        refs = list(context["sources"])
        if "candidate" in context:
            result = {
                "reviews": [
                    {
                        "point": 0,
                        "verdict": "supported",
                        "reason": "Fixture support checked.",
                    }
                ],
                "missing_points": [],
                "excluded_sources": [],
            }
        elif refs == value["pages"][0]:
            result = {"points": [point(refs)], "excluded_sources": []}
        else:
            failed_calls.append(messages)
            if len(failed_calls) == 1:
                result = {
                    "points": [point([refs[0]])],
                    "excluded_sources": [
                        {
                            "reference": refs[0],
                            "reason": "Conflicting fixture exclusion.",
                        }
                    ],
                }
            else:
                result = {
                    "points": [point([refs[0]])],
                    "excluded_sources": [
                        {"reference": refs[1], "reason": "Fixture navigation."}
                    ],
                }
        return json.dumps(result), {
            "model_digest": "d" * 64,
            "done_reason": "stop",
            "prompt_eval_count": 100,
            "eval_count": 100,
            "context_length": 16384,
            "size_vram": 1024,
        }

    monkeypatch.setattr(old.LedgerJob, "generate_response", old_inference)
    with pytest.raises(ValueError, match="attempt_budget_exhausted"):
        old.run(tmp_path, key, wait_seconds=0)
    assert len(calls) == 4
    parent_folder = next((tmp_path / "wiki/textbook-ledger-jobs").iterdir())
    status = json.loads((parent_folder / "progress.json").read_bytes())
    status.update(pid=999999999, created=1)
    atomic_json(parent_folder / "progress.json", status)
    return tmp_path, key, value, parent_folder, calls


def keyed_inference(calls, *, always_partial=False):
    def infer(self, messages, *, schema):
        calls.append(copy.deepcopy(messages))
        context = json.loads(messages[-1]["content"])
        if "reviews" in schema["properties"]:
            result = {
                "reviews": {
                    i: {
                        "verdict": "partial" if always_partial else "supported",
                        "reason": "Fixture evidence compared.",
                    }
                    for i in schema["properties"]["reviews"]["properties"]
                },
                "excluded_sources": {
                    ref: {
                        "valid": True,
                        "reason": "Fixture non-teaching classification checked.",
                    }
                    for ref in schema["properties"]["excluded_sources"]["properties"]
                },
                "missing_points": [],
            }
        else:
            result = {
                ref: {"kind": "teaching", "points": [body("Fixture concept " + ref)]}
                for ref in context["sources"]
            }
        return json.dumps(result), {"synthetic_fixture": True}

    return infer


def test_successor_reuses_verified_page_and_repairs_only_the_missing_source(
    predecessor, monkeypatch
):
    root, key, inventory, parent, old_calls = predecessor
    before = {
        p.relative_to(parent).as_posix(): sha(p.read_bytes())
        for p in parent.rglob("*")
        if p.is_file()
    }
    plan_key, plan = keyed.prepare(root, key, parent.name)
    assert len(plan["imports"]) == 1
    assert plan["partial"]["missing_references"] == [inventory["pages"][1][-1]]
    assert plan["parent_attempt_budgets_preserved"] is True
    calls = []
    monkeypatch.setattr(keyed.KeyedJob, "generate_response", keyed_inference(calls))
    result = keyed.run(root, plan_key, wait_seconds=0)
    assert len(old_calls) == 4
    assert result["pages"][0]["reused_parent"] is True
    assert len(result["pages"]) == 3
    assert result["automatic_local_evidence_review_passed"] is True
    assert result["reader_article_published"] is False
    assert len(calls) == 5
    first = json.loads(calls[0][-1]["content"])
    assert list(first["sources"]) == plan["partial"]["missing_references"]
    assert set(first["context_sources"]) == set(inventory["pages"][1]) - set(
        first["sources"]
    )
    for messages in calls:
        context = json.loads(messages[-1]["content"])
        assert not set(context["sources"]).intersection(inventory["pages"][0])
    assert keyed.run(root, plan_key, wait_seconds=0) == result
    assert len(calls) == 5
    assert {
        p.relative_to(parent).as_posix(): sha(p.read_bytes())
        for p in parent.rglob("*")
        if p.is_file()
    } == before
    monkeypatch.setattr(
        keyed, "KEYED_EXTRACT", keyed.KEYED_EXTRACT + "\nChanged execution."
    )
    with pytest.raises(ValueError, match="execution_changed_preserve_budget"):
        keyed.run(root, plan_key, wait_seconds=0)
    assert len(calls) == 5


def test_successor_refuses_changed_parent_files_and_live_parent(predecessor):
    root, key, _, parent, _ = predecessor
    progress_path = parent / "progress.json"
    stopped = json.loads(progress_path.read_bytes())
    live = {**stopped, "pid": os.getpid(), "created": psutil.Process().create_time()}
    atomic_json(progress_path, live)
    with pytest.raises(ValueError, match="parent_worker_still_live"):
        keyed.prepare(root, key, parent.name)
    atomic_json(progress_path, stopped)
    plan, _ = keyed.prepare(root, key, parent.name)
    attempt = next((parent / "attempts").glob("page-0001-extract-*.json"))
    value = json.loads(attempt.read_bytes())
    value["validation_error"] = "changed"
    atomic_json(attempt, value)
    with pytest.raises(ValueError, match="parent_bound_file_changed"):
        keyed.load_plan(root, plan)


def test_unresolved_semantics_do_not_turn_structured_completion_into_an_article(
    predecessor, monkeypatch
):
    root, key, _, parent, _ = predecessor
    plan, _ = keyed.prepare(root, key, parent.name)
    calls = []
    monkeypatch.setattr(
        keyed.KeyedJob, "generate_response", keyed_inference(calls, always_partial=True)
    )
    result = keyed.run(root, plan, wait_seconds=0)
    assert len(result["issues"]) == 2
    assert result["automatic_local_evidence_review_passed"] is False
    assert result["state"] == "ledger_requires_semantic_repair"
    assert result["reader_article_published"] is False
    assert result["semantic_accuracy_verified"] is False
    count = len(calls)
    assert keyed.run(root, plan, wait_seconds=0) == result
    assert len(calls) == count
