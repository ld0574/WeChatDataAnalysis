import copy
import json
from contextlib import contextmanager

import jsonschema
import pytest

from wechat_decrypt_tool import course_textbook_authoring as author
from wechat_decrypt_tool import course_textbook_composition as module
from wechat_decrypt_tool.course_knowledge import atomic_json
from test_course_textbook_authoring import archive as archive
from test_course_textbook_authoring import (
    fixture_plan,
    group_value,
    review_value,
    section_value,
)


@pytest.fixture
def disputed_archive(request):
    root, key, inventory, parent = request.getfixturevalue("archive")
    ref = next(iter(inventory["sources"]))
    target = next(k for k, v in parent["points"].items() if ref in v["citations"])
    parent["points"][target]["teaching"] = "UNSUPPORTED_OLD_CLAIM_MUST_BE_QUARANTINED"
    parent.update(
        policy=module.REVISION_POLICY,
        source_revision_rounds=1,
        state="ledger_requires_semantic_repair",
        automatic_local_evidence_review_passed=False,
        issues=[
            {
                "page": 0,
                "reference": ref,
                "source_revision_audit": {
                    "verdict": "needs_changes",
                    "reason": "Fixture unresolved source claim.",
                    "missing_information": ["Fixture condition needs checking."],
                },
            }
        ],
    )
    folder = root / "wiki/textbook-keyed-jobs" / parent["job"]
    author.save(folder, "ledger", parent)
    atomic_json(
        folder / "progress.json",
        {
            "stage": "source_revision_complete",
            "pid": 9999999,
            "created": 1,
        },
    )
    atomic_json(
        folder / "exhausted-source-attempt.json", {"attempt": 2, "state": "failed"}
    )
    atomic_json(folder / "authoring-execution.json", {"old_contract": "preserve"})
    return root, key, inventory, parent, target, folder


def inference(monkeypatch, working, *, semantic_failure=False, malformed=False):
    calls = []
    monkeypatch.setattr(
        "wechat_decrypt_tool.course_pdf_ocr.local_model_digest", lambda _: "e" * 64
    )

    @contextmanager
    def slot(self, stage):
        yield

    def respond(self, messages, *, schema):
        prompt = messages[0]["content"]
        context = json.loads(messages[1]["content"])
        calls.append((prompt, context))
        if malformed:
            return "{}", None
        if prompt == module.PROMPTS[author.GROUP_PROMPT]:
            value = group_value(context["items"], min(2, len(context["items"])))
        elif prompt in [
            module.PROMPTS[p] for p in (author.WRITE_PROMPT, author.REPAIR_PROMPT)
        ]:
            ids = list(context["assigned_points"])
            value = section_value(ids, working)
            for k in set(ids).intersection(working["pending_source_cards"]):
                value["coverage"][k]["teaching"] = True
        else:
            value = review_value(schema)
            if semantic_failure and prompt == module.PROMPTS[author.REVIEW_PROMPT]:
                value["quiz"]["passed"] = False
                value["quiz"]["reason"] = "Fixture answer is not supported."
        return json.dumps(value), None

    monkeypatch.setattr(module.AuthoringJob, "slot", slot)
    monkeypatch.setattr(module.AuthoringJob, "generate_response", respond)
    return calls


def test_disputed_claims_are_quarantined_and_complete_originals_are_preserved(
    disputed_archive,
):
    root, key, inventory, parent, target, folder = disputed_archive
    before = {p: p.read_bytes() for p in folder.iterdir()}
    actual, working = module.load_inputs(root, key, parent["job"])
    assert actual == inventory and target not in working["points"]
    assert working["quarantined_point_keys"] == [target]
    assert working["inherited_issues"] == parent["issues"]
    assert not working["source_revision_budget_refreshed"]
    assert not working["reader_publication_allowed"]
    for k, point in parent["points"].items():
        if k != target:
            assert working["points"][k] == point
    card = next(iter(working["pending_source_cards"]))
    ref = working["pending_source_cards"][card]["reference"]
    assert working["points"][card]["teaching"] == inventory["sources"][ref]["text"]
    context = module.writing_context([card], working, inventory)
    assert set(context["sources"]) == {
        ref,
        *working["pending_source_cards"][card]["neighbors"],
    }
    assert all(
        row["text"] == inventory["sources"][r]["text"]
        for r, row in context["sources"].items()
    )
    assert all(p.read_bytes() == raw for p, raw in before.items())


def test_complete_private_manuscript_is_written_without_publishing_or_resetting_source_budget(
    disputed_archive, monkeypatch
):
    root, key, inventory, parent, _, folder = disputed_archive
    _, working = module.load_inputs(root, key, parent["job"])
    before = {p: p.read_bytes() for p in folder.iterdir()}
    calls = inference(monkeypatch, working)
    result = author.run(root, key, parent["job"], protocol=module)
    assert result["policy"] == module.POLICY
    assert result["state"] == "article_requires_editorial_repair"
    assert result["quality_issues"] == 1
    job = root / "wiki/textbook-composition-jobs" / result["job"]
    private = json.loads((job / "private-article.json").read_bytes())["data"]
    assert set(private["private_coverage"]) == set(working["points"])
    assert private["inherited_issues"] == parent["issues"]
    assert not private["reader_publication_allowed"]
    assert not (job / "reading.json").exists() and not (job / "reading.html").exists()
    assert all(p.read_bytes() == raw for p, raw in before.items())
    for prompt, context in calls:
        assert "UNSUPPORTED_OLD_CLAIM_MUST_BE_QUARANTINED" not in json.dumps(context)
    writing = [ctx for p, ctx in calls if p == module.PROMPTS[author.WRITE_PROMPT]]
    assert writing
    refs = {r for ctx in writing for r in ctx["sources"]}
    assert set(inventory["sources"]) <= refs
    old = len(calls)
    assert author.run(root, key, parent["job"], protocol=module) == result
    assert len(calls) == old


@pytest.mark.parametrize("failure", ["malformed", "semantic"])
def test_article_budgets_survive_restart_and_cannot_be_refreshed(
    disputed_archive, monkeypatch, failure
):
    root, key, _, parent, _, _ = disputed_archive
    _, working = module.load_inputs(root, key, parent["job"])
    calls = inference(
        monkeypatch,
        working,
        malformed=failure == "malformed",
        semantic_failure=failure == "semantic",
    )
    if failure == "malformed":
        for _ in range(2):
            with pytest.raises(ValueError, match="stage_attempt_budget_exhausted"):
                author.run(root, key, parent["job"], protocol=module)
        assert len(calls) == 2
    else:
        result = author.run(root, key, parent["job"], protocol=module)
        assert result["quality_issues"] == result["sections"] + 1
        assert (
            sum(p == module.PROMPTS[author.REPAIR_PROMPT] for p, _ in calls)
            == result["sections"]
        )
        count = len(calls)
        author.run(root, key, parent["job"], protocol=module)
        assert len(calls) == count
    count = len(calls)
    monkeypatch.setattr(
        "wechat_decrypt_tool.course_pdf_ocr.local_model_digest", lambda _: "f" * 64
    )
    with pytest.raises(ValueError, match="preserve_bound_authoring_budget"):
        author.run(root, key, parent["job"], protocol=module)
    assert len(calls) == count


def test_non_teaching_disposition_is_explicit_and_teaching_requires_primary_support(
    disputed_archive,
):
    root, key, inventory, parent, _, _ = disputed_archive
    _, working = module.load_inputs(root, key, parent["job"])
    ids = list(working["points"])
    raw = next(iter(working["pending_source_cards"]))
    good = section_value(ids, working)
    good["coverage"][raw].update(teaching=False, paragraphs=[])
    module.validate_section(good, ids, working, inventory)
    for change in [
        "missing-decision",
        "teaching-without-body",
        "non-teaching-with-body",
        "foreign",
    ]:
        value = copy.deepcopy(good)
        row = value["coverage"][raw]
        if change == "missing-decision":
            del row["teaching"]
        elif change == "teaching-without-body":
            row["teaching"] = True
        elif change == "non-teaching-with-body":
            row["paragraphs"] = [0]
        else:
            value["paragraphs"][0]["citations"] = ["invented"]
        with pytest.raises((ValueError, jsonschema.ValidationError)):
            module.validate_section(value, ids, working, inventory)


def test_oversized_original_context_is_never_truncated(disputed_archive):
    root, key, inventory, parent, _, _ = disputed_archive
    _, working = module.load_inputs(root, key, parent["job"])
    value = copy.deepcopy(inventory)
    raw = next(iter(working["pending_source_cards"]))
    neighbor = working["pending_source_cards"][raw]["neighbors"][0]
    value["sources"][neighbor]["text"] = "full fixture context " * 2000
    with pytest.raises(ValueError, match="complete_point_context_too_large"):
        module.writing_units(fixture_plan(working), working, value)


def test_unfinished_parent_and_cross_source_quarantine_are_rejected(disputed_archive):
    root, key, _, parent, target, folder = disputed_archive
    atomic_json(folder / "progress.json", {"stage": "generating"})
    with pytest.raises(ValueError, match="completed_parent"):
        module.load_inputs(root, key, parent["job"])
    atomic_json(
        folder / "progress.json",
        {"stage": "source_revision_complete", "pid": 9999999, "created": 1},
    )
    other = parent["points"][next(k for k in parent["points"] if k != target)][
        "citations"
    ][0]
    parent["points"][target]["citations"].append(other)
    parent["source_dispositions"][other]["points"] = [
        k for k, p in parent["points"].items() if other in p["citations"]
    ]
    author.save(folder, "ledger", parent)
    with pytest.raises(ValueError, match="shared_source_closure"):
        module.load_inputs(root, key, parent["job"])
