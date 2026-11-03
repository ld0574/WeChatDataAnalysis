import copy
import json

import pytest

from test_course_textbook_resolution import archive as archive
from test_course_textbook_resolution import audit
from test_course_textbook_source_revision import completed_archive as completed_archive
from test_course_textbook_source_revision import revise_inference
from wechat_decrypt_tool import course_textbook_audit_cache as cache
from wechat_decrypt_tool import course_textbook_authoring as authoring
from wechat_decrypt_tool import course_textbook_source_revision as revision
from wechat_decrypt_tool.course_knowledge import atomic_json


def test_only_verdict_is_downgraded_without_removing_any_information():
    value = audit()
    value["missing_information"] = ["An original fixture condition is missing."]
    before = copy.deepcopy(value)
    result = cache.conservative_verdict(value)
    assert value == before and result["verdict"] == "needs_changes"
    result["verdict"] = "supported"
    assert result == before
    for invalid in (audit(), audit(False), {**value, "missing_information": [" "]}):
        with pytest.raises(ValueError):
            cache.conservative_verdict(invalid)


@pytest.fixture
def stopped_audit(request, monkeypatch):
    root, inventory, parent = request.getfixturevalue("completed_archive")
    calls = revise_inference(monkeypatch)
    response = revision.SourceRevisionJob.generate_response

    def contradictory(self, messages, *, schema):
        raw, _ = response(self, messages, schema=schema)
        value = json.loads(raw)
        if messages[0]["content"] == revision.original.REVIEW and len(calls) <= 3:
            value["missing_information"] = ["An original fixture condition is missing."]
        return json.dumps(value), {
            "model_digest": self.model_digest,
            "done_reason": "stop",
            "prompt_eval_count": 500,
            "eval_count": 100,
            "context_length": 16384,
            "size_vram": 6000000000,
        }

    monkeypatch.setattr(revision.SourceRevisionJob, "generate_response", contradictory)
    with pytest.raises(ValueError, match="attempt_budget_exhausted"):
        revision.run(root, parent)
    progress = json.loads(
        (root / "wiki/textbook-source-revision-last-build.json").read_bytes()
    )
    folder = root / "wiki/textbook-keyed-jobs" / progress["job"]
    return root, inventory, parent, calls, progress, folder


def test_recovery_keeps_failures_budget_and_negative_article_gate(stopped_audit):
    root, inventory, parent, calls, progress, folder = stopped_audit
    before = {path: path.read_bytes() for path in (folder / "attempts").glob("*.json")}
    with pytest.raises(ValueError, match="worker_confirmed_exited"):
        cache.recover(root, progress["job"])
    progress["created"] = 0
    atomic_json(folder / "progress.json", progress)
    result = cache.recover(root, progress["job"])
    assert result["verdict"] == "needs_changes" and result["model_calls"] == 0
    assert result["missing_information_count"] == 1 and len(calls) == 3
    finished = revision.run(root, parent)
    assert finished["state"] == "ledger_requires_semantic_repair"
    assert finished["unresolved_sources"] == 1
    assert len(calls) == 3 + 2 * (finished["revised_sources"] - 1)
    assert all(path.read_bytes() == raw for path, raw in before.items())
    with pytest.raises(ValueError, match="resolved_ledger_semantic_issues"):
        authoring.load_inputs(
            root, revision.sha(revision.canonical(inventory)), finished["job"]
        )
    count = len(calls)
    assert revision.run(root, parent) == finished and len(calls) == count


@pytest.mark.parametrize("mutation", ["input", "error", "candidate"])
def test_tampered_evidence_is_refused_without_more_model_calls(stopped_audit, mutation):
    root, _, _, calls, progress, folder = stopped_audit
    progress["created"] = 0
    atomic_json(folder / "progress.json", progress)
    path = next((folder / "attempts").glob(progress["stage"] + "-*.json"))
    if mutation == "candidate":
        path = folder / "source-0000-revise.json"
    row = json.loads(path.read_bytes())
    if mutation == "input":
        row["input_sha256"] = "0" * 64
    elif mutation == "error":
        row["validation_error"] = "different_failure"
    else:
        row["data"]["points"][0]["teaching"] = "Tampered fixture candidate."
    atomic_json(path, row)
    count = len(calls)
    with pytest.raises(ValueError):
        cache.recover(root, progress["job"])
    assert len(calls) == count
    assert not (folder / (progress["stage"] + ".json")).exists()
