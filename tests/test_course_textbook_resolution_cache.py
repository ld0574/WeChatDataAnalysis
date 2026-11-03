import copy
import json
from contextlib import contextmanager

import pytest

from test_course_textbook_resolution import archive as archive
from test_course_textbook_resolution import inference
from wechat_decrypt_tool import course_textbook_resolution as resolution
from wechat_decrypt_tool import course_textbook_resolution_cache as recovery
from wechat_decrypt_tool.course_knowledge import atomic_json


def point(quotation):
    return {
        "kind": "teaching",
        "points": [
            {
                "label": "Fixture fact",
                "teaching": "The original fixture condition controls the operation.",
                "category": "concept",
                "quotations": [quotation],
            }
        ],
    }


def test_whitespace_recovery_preserves_every_nonwhitespace_character_and_exact_offsets():
    source = {
        "text": "Before. The original\n  fixture condition controls the operation. After."
    }
    raw = point("The original fixture condition controls the operation.")
    before = copy.deepcopy(raw)
    restored, spans = recovery.literal_quotations(raw, source)
    assert raw == before
    assert restored["points"][0]["teaching"] == raw["points"][0]["teaching"]
    span = spans[0]
    assert (
        restored["points"][0]["quotations"][0]
        == source["text"][span["start"] : span["end"]]
    )
    assert len(spans) == 1


def test_changes_to_real_text_or_ambiguous_normalized_passages_are_rejected():
    for quotation, source in [
        ("The original fact.", "The altered fact."),
        ("Theoriginalfact.", "The original fact. The original fact."),
        (" ", "The original fact."),
    ]:
        with pytest.raises(ValueError):
            recovery.literal_quotations(point(quotation), {"text": source})
    with pytest.raises(ValueError, match="actual_whitespace"):
        recovery.literal_quotations(
            point("The original fact."), {"text": "The original fact."}
        )


def test_real_checkpoint_recovery_spends_no_model_calls_and_keeps_failed_attempts(
    request, monkeypatch
):
    root, _, parent, _ = request.getfixturevalue("archive")
    calls = inference(monkeypatch)
    base = resolution.ResolutionJob.generate_response

    @contextmanager
    def observed_slot(self, stage):
        self.progress("generating", stage)
        yield

    monkeypatch.setattr(resolution.ResolutionJob, "slot", observed_slot)

    def wrong_whitespace(self, messages, *, schema):
        raw, _ = base(self, messages, schema=schema)
        value = json.loads(raw)
        if len(calls) <= 2:
            value["points"][0]["quotations"] = [
                "".join(
                    character
                    for character in value["points"][0]["quotations"][0]
                    if not character.isspace()
                )
            ]
        metrics = {
            "model_digest": self.model_digest,
            "done_reason": "stop",
            "prompt_eval_count": 500,
            "eval_count": 100,
            "context_length": 16384,
            "size_vram": 6000000000,
        }
        return json.dumps(value), metrics

    monkeypatch.setattr(resolution.ResolutionJob, "generate_response", wrong_whitespace)
    with pytest.raises(ValueError, match="attempt_budget_exhausted"):
        resolution.run(root, parent["job"])
    progress = json.loads(
        (root / "wiki/textbook-resolution-last-build.json").read_bytes()
    )
    folder = root / "wiki/textbook-keyed-jobs" / progress["job"]
    before = {p: p.read_bytes() for p in (folder / "attempts").glob("*.json")}
    with pytest.raises(ValueError, match="worker_confirmed_exited"):
        recovery.recover(root, progress["job"])
    # This fixture executes inline: simulate the old process identity having ended.
    progress["created"] = 0
    atomic_json(folder / "progress.json", progress)
    result = recovery.recover(root, progress["job"])
    assert result["model_calls"] == 0
    assert len(calls) == 2
    assert result["restored_spans"] == 1
    assert all(p.read_bytes() == value for p, value in before.items())
    completed = resolution.run(root, parent["job"])
    assert completed["state"] == "ledger_ready_for_conceptual_reorganization"
    assert len(calls) == 2 + 1 + (len(resolution.affected_sources(parent)) - 1) * 2
    assert all(p.read_bytes() == value for p, value in before.items())


def test_complete_source_binding_never_edits_candidate_facts_or_claims_support():
    source = {"text": "The original fixture condition controls the operation."}
    raw = point("This proposed quotation was paraphrased.")
    before = copy.deepcopy(raw)
    bound, spans = recovery.complete_source_quotations(raw, source)
    assert raw == before
    assert len(spans) == 1
    assert bound["points"][0]["quotations"] == [source["text"]]
    assert {
        key: value for key, value in bound["points"][0].items() if key != "quotations"
    } == {key: value for key, value in raw["points"][0].items() if key != "quotations"}
    with pytest.raises(ValueError, match="short_complete_original"):
        recovery.complete_source_quotations(raw, {"text": "x" * 2049})
    with pytest.raises(ValueError, match="actual_nonliteral"):
        recovery.complete_source_quotations(point(source["text"]), source)


def test_full_source_binding_preserves_failures_and_negative_independent_audit(
    request, monkeypatch
):
    root, _, parent, _ = request.getfixturevalue("archive")
    calls = inference(monkeypatch, unsupported=True)
    base = resolution.ResolutionJob.generate_response

    @contextmanager
    def observed_slot(self, stage):
        self.progress("generating", stage)
        yield

    monkeypatch.setattr(resolution.ResolutionJob, "slot", observed_slot)

    def paraphrased_quote(self, messages, *, schema):
        raw, _ = base(self, messages, schema=schema)
        value = json.loads(raw)
        if len(calls) <= 2:
            value["points"][0]["quotations"] = ["A nonliteral proposed quotation."]
            if len(calls) == 2:
                value["points"][0]["teaching"] = (
                    "A different second original candidate."
                )
        return json.dumps(value), {
            "model_digest": self.model_digest,
            "done_reason": "stop",
            "prompt_eval_count": 500,
            "eval_count": 100,
            "context_length": 16384,
            "size_vram": 6000000000,
        }

    monkeypatch.setattr(
        resolution.ResolutionJob, "generate_response", paraphrased_quote
    )
    with pytest.raises(ValueError, match="attempt_budget_exhausted"):
        resolution.run(root, parent["job"])
    progress = json.loads(
        (root / "wiki/textbook-resolution-last-build.json").read_bytes()
    )
    folder = root / "wiki/textbook-keyed-jobs" / progress["job"]
    before = {path: path.read_bytes() for path in (folder / "attempts").glob("*.json")}
    progress["created"] = 0
    atomic_json(folder / "progress.json", progress)
    recovered = recovery.bind_complete_source(root, progress["job"])
    assert recovered["model_calls"] == 0 and len(calls) == 2
    assert recovered["independent_source_audit_still_required"]
    receipt = json.loads(
        (folder / (progress["stage"] + "-complete-source-binding.json")).read_bytes()
    )
    assert receipt["original_candidate_sha256"]
    assert receipt["original_candidates_differ"]
    assert receipt["selection_rule"] == "earliest_original_failed_attempt_no_fact_edits"
    selected = json.loads((folder / (progress["stage"] + ".json")).read_bytes())["data"]
    first = json.loads(json.loads(next(iter(before.values())))["raw_response"])
    assert selected["points"][0]["teaching"] == first["points"][0]["teaching"]
    assert not receipt["candidate_fact_fields_changed"]
    assert not receipt["model_quotations_claimed_literal"]
    assert not receipt["semantic_support_claimed"]
    finished = resolution.run(root, parent["job"])
    assert finished["state"] == "ledger_requires_semantic_repair"
    assert finished["unresolved_sources"] == len(resolution.affected_sources(parent))
    assert len(calls) == 3 + (len(resolution.affected_sources(parent)) - 1) * 2
    assert all(path.read_bytes() == raw for path, raw in before.items())
    completed = resolution.run(root, parent["job"])
    assert completed == finished
    assert len(calls) == 2 + 1 + (len(resolution.affected_sources(parent)) - 1) * 2
    assert all(p.read_bytes() == value for p, value in before.items())
