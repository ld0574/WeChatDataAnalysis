import copy
import json
from types import SimpleNamespace

import pytest

from test_course_textbook_gap_additions import packet
from wechat_decrypt_tool import course_textbook_edition as module
from wechat_decrypt_tool.course_library import canonical
from wechat_decrypt_tool.course_textbook_evidence import sha


def test_merge_preserves_factual_edits_and_invalidates_changed_support():
    value, targets, candidate, review = packet()
    original = value["book"]
    corrected = copy.deepcopy(original)
    corrected["chapters"][0]["sections"][0]["paragraphs"][0] = "A corrected and independently accepted explanation."
    old_coverage = {"other": {"kind": "covered", "reason": "Old proof", "paragraph_ids": ["p000"]},
                    **{k: {"kind": "missing", "reason": "Old missing teaching"} for k in targets}}
    actual, coverage, selected = module.merge(original, corrected, candidate, review, old_coverage)
    paragraphs = actual["chapters"][0]["sections"][0]["paragraphs"]
    assert paragraphs[0] == corrected["chapters"][0]["sections"][0]["paragraphs"][0]
    assert paragraphs[-1] == candidate["additions"][0]["text"]
    assert coverage["other"]["kind"] == "missing"
    assert all(coverage[k]["kind"] == "covered" for k in targets)
    assert len(selected) == 1
    assert original != corrected and corrected != actual
    assert old_coverage["gap1"]["kind"] == "missing"


def test_appending_to_an_earlier_section_translates_later_paragraph_ids():
    value, targets, candidate, review = packet()
    original = value["book"]
    section = copy.deepcopy(original["chapters"][0]["sections"][0])
    section["id"] = "later"
    section["paragraphs"] = ["A distinct later explanation"]
    original["chapters"][0]["sections"].append(section)
    coverage = {"stable": {"kind": "covered", "reason": "Existing exact proof", "paragraph_ids": ["p001"]}}
    actual, inherited, _ = module.merge(original, original, candidate, review, coverage)
    assert inherited["stable"]["paragraph_ids"] == ["p002"]
    assert module.paragraph_positions(actual)[("later", 0)] == ("p002", "A distinct later explanation")


def test_rejected_additions_never_enter_the_reading_book_or_resolve_sources():
    value, targets, candidate, review = packet()
    review["paragraphs"]["add000"]["supported"] = False
    coverage = {k: {"kind": "missing", "reason": "Old issue"} for k in targets}
    actual, inherited, selected = module.merge(value["book"], value["book"], candidate, review, coverage)
    assert actual == value["book"] and selected == []
    assert all(inherited[k]["kind"] == "missing" for k in targets)


def test_cache_without_actual_model_response_is_rejected(tmp_path):
    attempts = tmp_path / "attempts"
    attempts.mkdir()
    data = {"supported": True}
    module.atomic_json(tmp_path / "check.json", {"input_sha256": "frozen", "data_sha256": sha(canonical(data)), "data": data})
    module.atomic_json(attempts / "check-01.json", {"accepted_structure": True, "input_sha256": "frozen",
        "raw_response": json.dumps(data), "request_sha256": "request", "model_metrics": {"done_reason": "stop"}})
    with pytest.raises(ValueError, match="actual_model_receipt"):
        module.checked_cache(tmp_path, "check")


def test_export_is_clean_and_keeps_unfinished_status(tmp_path):
    value, _, _, _ = packet()
    module.export_draft(tmp_path, value["book"], {"missing_source_inputs": 2, "remaining_prior_issues": 1, "reading_issues": 3})
    html = (tmp_path / "working-reading.html").read_text("utf-8")
    assert "尚未验收" in html and "2 条来源覆盖疑点" in html
    assert "source_keys" not in html and "raw_response" not in html and "D:\\" not in html
    assert "<aside role=\"status\"" in html


def test_dispatch_never_restarts_a_failed_parent_or_launches_a_review(tmp_path, monkeypatch):
    key = "a" * 32
    folder = tmp_path / "wiki/textbook-gap-addition-jobs" / key
    folder.mkdir(parents=True)
    module.atomic_json(folder / "unmapped-review-progress.json", {"stage": "additions-review-1", "state": "stopped"})
    monkeypatch.setattr(module, "TextbookStore", lambda _: SimpleNamespace(root=tmp_path, checkpoint=lambda: None))
    monkeypatch.setattr(module.additions.final.writing.planmod, "identity_live", lambda _: False)
    monkeypatch.setattr(module, "inputs_for", lambda *args: pytest.fail("Failed parent must not be re-reviewed"))
    with pytest.raises(ValueError, match="parent_review_stopped_no_restart"):
        module.dispatch(tmp_path, key, "b" * 32, tmp_path / "tokenizer")
    state = json.loads((tmp_path / "wiki/textbook-edition-dispatch.json").read_bytes())
    assert state["state"] == "stopped" and state["reason_code"].endswith("parent_review_stopped_no_restart")


def test_contradictory_targets_remain_unknown_and_paragraph_verdicts_do_not_change(tmp_path, monkeypatch):
    value, targets, candidate, review = packet()
    for ref in targets:
        candidate["targets"][ref].update(kind="non_teaching", addition_ids=[])
        review["targets"][ref].update(teaching=False, covered=True, addition_ids=[])
    attempts = tmp_path / "attempts"
    attempts.mkdir()
    for number in [1, 2]:
        module.atomic_json(attempts / f"additions-review-1-{number:02}.json", {
            "input_sha256": "frozen", "validation_error": "requires_gap_additions_consistent_teaching_coverage",
            "raw_response": json.dumps(review)})
    before = {p.name: p.read_bytes() for p in attempts.iterdir()}
    monkeypatch.setattr(module, "verify_response", lambda *args: None)
    schema = module.additions.review_schema(candidate, targets, value["sources"])
    context = {"targets": targets, "complete_sources_for_this_check": value["sources"]}
    result, receipt = module.conservative_review(tmp_path, candidate, context, schema, "frozen")
    assert result["paragraphs"] == review["paragraphs"]
    assert all(r["teaching"] and not r["covered"] for r in result["targets"].values())
    assert module.additions.resolved(candidate, result) == set()
    assert receipt["additional_model_calls"] == 0 and set(receipt["unknown_target_ids"]) == set(targets)
    assert {p.name: p.read_bytes() for p in attempts.iterdir()} == before
    with pytest.raises(ValueError, match="only_known_failed_final_review"):
        module.conservative_review(tmp_path, candidate, context, schema, "different-input")
