import copy
import json
from types import SimpleNamespace

import pytest

from wechat_decrypt_tool import course_textbook_reference_sets as module


def fixture():
    inventory = {"sources": {"O0": {"kind": "speech", "text": "Synthetic complete original"}}}
    working = {"points": {"P0": {"citations": ["O0"]}}, "pending_source_cards": {}}
    cited = {"text": "Synthetic prose", "citations": ["O0", "O0"]}
    candidate = {"heading": "Synthetic heading", "objective": "Synthetic objective",
        "overview": copy.deepcopy(cited), "takeaway": copy.deepcopy(cited), "paragraphs": [copy.deepcopy(cited)],
        "coverage": {"P0": {"paragraphs": [0], "reason": "Synthetic mapping"}}, "diagrams": [],
        "quiz": {"question": "Synthetic question", "answer": "Synthetic answer", "explanation": "Synthetic explanation",
            "criteria": ["Synthetic criterion"], "citations": ["O0", "O0"]}}
    return inventory, working, candidate


def test_reference_sets_preserve_prose_mappings_first_order_and_original():
    inventory, working, candidate = fixture()
    before = copy.deepcopy(candidate)
    proposed, changes = module.reference_sets(candidate, ["P0"], working, inventory)
    assert candidate == before and len(changes) == 4
    assert proposed["coverage"] == candidate["coverage"]
    assert proposed["paragraphs"][0]["text"] == candidate["paragraphs"][0]["text"]
    assert proposed["paragraphs"][0]["citations"] == ["O0"]
    result, additions, quarantined = module.normalize(candidate, ["P0"], working, inventory)
    assert result == proposed and not additions and not quarantined
    module.writing.validate_section(result, ["P0"], working, inventory)


@pytest.mark.parametrize("change", ["outside", "empty", "mapping", "blank"])
def test_dedup_does_not_hide_other_invalid_content(change):
    inventory, working, candidate = fixture()
    if change == "outside":
        candidate["paragraphs"][0]["citations"].append("unknown")
    elif change == "empty":
        candidate["quiz"]["citations"] = []
    elif change == "mapping":
        candidate["coverage"]["P0"]["paragraphs"] = [99]
    else:
        candidate["paragraphs"][0]["text"] = " "
    with pytest.raises((ValueError, module.jsonschema.ValidationError)):
        module.normalize(candidate, ["P0"], working, inventory)


def test_unique_reference_order_is_preserved():
    inventory, working, candidate = fixture()
    inventory["sources"]["O1"] = {"kind": "speech", "text": "Synthetic second complete original"}
    working["points"]["P0"]["citations"].append("O1")
    candidate["paragraphs"][0]["citations"] = ["O1", "O0", "O1", "O0"]
    value, _ = module.reference_sets(candidate, ["P0"], working, inventory)
    assert value["paragraphs"][0]["citations"] == ["O1", "O0"]


def test_old_recovery_receipt_is_reconstructed_without_new_fields(tmp_path, monkeypatch):
    job = SimpleNamespace(root=tmp_path)
    path = module.draft.artifact_path(job, "section-0000-write")
    path.parent.mkdir()
    path.write_text(json.dumps({"receipt": {"policy": "original"}}), "utf-8")
    original = {"receipt": {"policy": "original"}, "data": {"untouched": True}}
    monkeypatch.setattr(module, "ORIGINAL_CANDIDATE", lambda *args: copy.deepcopy(original))
    assert module.candidate_from_history(job, "section-0000-write", {}, {}, {}, {}, {}) == original


def test_readonly_replay_never_generates_a_missing_cache(tmp_path):
    job = SimpleNamespace(root=tmp_path)
    with pytest.raises(ValueError, match="prior_stage_must_have_actual_cache"):
        module.readonly_stage(job, "section-0001-review", "synthetic", {}, {}, lambda value: None)


def test_new_receipt_records_dedup_adapter_and_restores_hook(tmp_path, monkeypatch):
    inventory, working, candidate = fixture()
    job = SimpleNamespace(root=tmp_path)
    attempts = tmp_path / "attempts"
    attempts.mkdir()
    (attempts / "section-0002-write-01.json").write_text(json.dumps({"raw_response": json.dumps(candidate)}), "utf-8")
    original_normalizer = module.draft.normalize
    calls = []

    def authenticated_history(*args):
        calls.append(True)
        if len(calls) == 1:
            raise ValueError("requires_whole_draft_resume_complete_scope_safe_original_candidate")
        assert module.draft.normalize is module.normalize
        return {"receipt": {"selected_attempt": 1}, "data": {}}

    monkeypatch.setattr(module, "ORIGINAL_CANDIDATE", authenticated_history)
    result = module.candidate_from_history(job, "section-0002-write", {}, {"assigned_points": working["points"]}, {}, working, inventory)
    receipt = result["receipt"]["reference_set_normalization"]
    assert len(receipt["changes"]) == 4 and receipt["model_calls"] == 0
    assert not receipt["semantic_acceptance"]
    assert receipt["adapter_sha256"] == module.writing.whole.sha(module.Path(module.__file__).read_bytes())
    assert module.draft.normalize is original_normalizer
    assert not (tmp_path / "section-0002-write.json").exists()
