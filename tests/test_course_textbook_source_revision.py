import copy
import json
from contextlib import contextmanager

import pytest

from test_course_textbook_resolution import archive as archive
from test_course_textbook_resolution import audit, inference, teaching
from wechat_decrypt_tool import course_textbook_authoring as authoring
from wechat_decrypt_tool import course_textbook_resolution as original
from wechat_decrypt_tool import course_textbook_source_revision as revision
from wechat_decrypt_tool.course_knowledge import atomic_json


@pytest.fixture
def completed_archive(request, monkeypatch):
    root, inventory, parent, _ = request.getfixturevalue("archive")
    inference(monkeypatch, unsupported=True)
    completed = original.run(root, parent["job"])
    assert completed["unresolved_sources"] > 0
    return root, inventory, completed["job"]


def revise_inference(monkeypatch, *, supported=True, malformed=False):
    calls = []

    @contextmanager
    def slot(self, stage):
        self.progress("generating", stage)
        yield

    def response(self, messages, *, schema):
        context = json.loads(messages[1]["content"])
        calls.append(context)
        if messages[0]["content"] == revision.PROMPT:
            value = teaching(context["source"])
            for point in value["points"]:
                del point["quotations"]
        else:
            value = audit(supported)
        return "{}" if malformed else json.dumps(value), None

    monkeypatch.setattr(revision.SourceRevisionJob, "slot", slot)
    monkeypatch.setattr(revision.SourceRevisionJob, "generate_response", response)
    return calls


def test_one_revision_uses_full_originals_keeps_unrelated_points_and_old_attempts(
    completed_archive, monkeypatch
):
    root, inventory, parent = completed_archive
    _, old, _, _ = revision.inputs(root, parent)
    folder = root / "wiki/textbook-keyed-jobs" / parent
    before = {path: path.read_bytes() for path in folder.rglob("*.json")}
    calls = revise_inference(monkeypatch)
    result = revision.run(root, parent)
    assert result["state"] == "ledger_ready_for_conceptual_reorganization"
    assert result["unresolved_sources"] == 0
    assert len(calls) == 2 * len(old["issues"])
    for context in calls:
        assert context["source"]["text"] in [
            source["text"] for source in inventory["sources"].values()
        ]
    ledger = json.loads(
        (root / "wiki/textbook-keyed-jobs" / result["job"] / "ledger.json").read_bytes()
    )["data"]
    revised = {issue["reference"] for issue in old["issues"]}
    for key, point in old["points"].items():
        if not revised.intersection(point["citations"]):
            assert ledger["points"][key] == point
    assert ledger["source_revision_rounds"] == 1
    assert all(path.read_bytes() == raw for path, raw in before.items())
    assert revision.run(root, parent) == result
    assert len(calls) == 2 * len(old["issues"])


def test_negative_revision_cannot_write_articles_or_start_another_revision(
    completed_archive, monkeypatch
):
    root, inventory, parent = completed_archive
    calls = revise_inference(monkeypatch, supported=False)
    result = revision.run(root, parent)
    count = len(calls)
    assert result["state"] == "ledger_requires_semantic_repair"
    assert result["unresolved_sources"] == result["revised_sources"]
    with pytest.raises(ValueError, match="resolved_ledger_semantic_issues"):
        authoring.load_inputs(
            root, original.sha(original.canonical(inventory)), result["job"]
        )
    with pytest.raises(ValueError, match="single_revision_original_resolution_parent"):
        revision.run(root, result["job"])
    assert revision.run(root, parent) == result and len(calls) == count


def test_exhausted_structure_budget_and_changed_recipe_do_not_add_model_calls(
    completed_archive, monkeypatch
):
    root, _, parent = completed_archive
    calls = revise_inference(monkeypatch, malformed=True)
    for _ in range(2):
        with pytest.raises(ValueError, match="attempt_budget_exhausted"):
            revision.run(root, parent)
    assert len(calls) == 2
    monkeypatch.setattr(revision, "POLICY", revision.POLICY + "-changed")
    with pytest.raises(ValueError, match="preserved_source_revision_budget"):
        revision.run(root, parent)
    assert len(calls) == 2


def test_changed_original_source_audit_is_rejected_before_revision_inference(
    completed_archive, monkeypatch
):
    root, _, parent = completed_archive
    folder = root / "wiki/textbook-keyed-jobs" / parent
    path = next(folder.glob("source-????-audit.json"))
    record = json.loads(path.read_bytes())
    record["data"]["reason"] = "Tampered fixture review."
    atomic_json(path, record)
    calls = revise_inference(monkeypatch)
    with pytest.raises(ValueError, match="unchanged_original_source_audit_inputs"):
        revision.run(root, parent)
    assert not calls


def test_short_source_citation_is_bound_by_program_and_body_is_unchanged():
    source = {
        "text": "The complete original fixture teaches a condition and its action."
    }
    body = teaching(source)
    del body["points"][0]["quotations"]
    before = copy.deepcopy(body)
    decision = revision.source_decision(body, source)
    assert body == before
    assert decision["points"][0]["quotations"] == [source["text"]]
    decision["points"][0].pop("quotations")
    assert decision == before
