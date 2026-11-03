import copy
from pathlib import Path

import jsonschema
import pytest

from wechat_decrypt_tool import course_textbook_gap_additions as module
from test_course_textbook_gap_plan import inputs


def packet():
    value = inputs()
    targets = {k: {"kind": "missing", "reason": "Synthetic original omission"} for k in ("gap1", "gap2")}
    paragraph = {"id": "add000", "section": "s", "text": "A complete synthetic explanation. " * 5,
                 "source_keys": list(targets)}
    candidate = {"additions": [paragraph], "targets": {k: {
        "kind": "added", "addition_ids": ["add000"], "source_quote": value["sources"][k]["text"][:70],
        "reason": "Supplies the actual missing teaching"} for k in targets}}
    review = {"paragraphs": {"add000": {"supported": True, "fully_explained": True,
              "fits_section": True, "reason": "Synthetic review", "source_keys": list(targets)}},
              "targets": {k: {"teaching": True, "covered": True, "addition_ids": ["add000"],
                          "source_quote": candidate["targets"][k]["source_quote"], "reason": "Synthetic coverage"}
                          for k in targets}}
    return value, targets, candidate, review


def test_original_teaching_can_be_grouped_without_replacing_factual_edits():
    value, targets, candidate, review = packet()
    original = copy.deepcopy(value["book"])
    module.check_candidate(candidate, original, targets, value["sources"])
    module.check_review(review, candidate, targets, value["sources"])
    assert module.resolved(candidate, review) == set(targets)
    current = copy.deepcopy(original)
    current["chapters"][0]["sections"][0]["paragraphs"][0] = "An already corrected factual explanation"
    combined = module.assemble(current, candidate)
    assert combined["chapters"][0]["sections"][0]["paragraphs"][0] == "An already corrected factual explanation"
    assert len(combined["chapters"][0]["sections"][0]["paragraphs"]) == 2
    assert combined["quiz"] == current["quiz"]
    assert current != combined and value["book"] == original
    assert "source_keys" not in str(combined)


def test_invented_sources_wrong_mapping_and_fabricated_quotes_are_rejected():
    value, targets, candidate, _ = packet()
    changed = copy.deepcopy(candidate)
    changed["additions"][0]["source_keys"] = ["invented"]
    with pytest.raises(jsonschema.ValidationError):
        module.check_candidate(changed, value["book"], targets, value["sources"])
    changed = copy.deepcopy(candidate)
    changed["additions"][0]["source_keys"] = ["gap1"]
    with pytest.raises(ValueError, match="actual_target"):
        module.check_candidate(changed, value["book"], targets, value["sources"])
    changed = copy.deepcopy(candidate)
    changed["targets"]["gap1"]["source_quote"] = "This quotation is not in the original source"
    with pytest.raises(ValueError, match="actual_original_quote"):
        module.check_candidate(changed, value["book"], targets, value["sources"])


def test_a_partial_or_unsupported_explanation_does_not_resolve_its_sources():
    value, targets, candidate, review = packet()
    review["paragraphs"]["add000"]["fully_explained"] = False
    module.check_review(review, candidate, targets, value["sources"])
    assert module.resolved(candidate, review) == set()
    review["paragraphs"]["add000"].update(supported=True, source_keys=[])
    with pytest.raises(ValueError, match="positive_paragraph_has_evidence"):
        module.check_review(review, candidate, targets, value["sources"])


def test_non_teaching_requires_both_judgments_and_actual_source_quotes():
    value, targets, candidate, _ = packet()
    candidate["additions"] = []
    for row in candidate["targets"].values():
        row.update(kind="non_teaching", addition_ids=[])
    review = {"paragraphs": {}, "targets": {k: {
        "teaching": False, "covered": False, "addition_ids": [], "source_quote": row["source_quote"],
        "reason": "Synthetic non-teaching context"} for k, row in candidate["targets"].items()}}
    jsonschema.Draft202012Validator.check_schema(module.review_schema(candidate, targets, value["sources"]))
    module.check_candidate(candidate, value["book"], targets, value["sources"])
    module.check_review(review, candidate, targets, value["sources"])
    assert module.resolved(candidate, review) == set(targets)
    candidate["targets"]["gap1"]["kind"] = "unresolved"
    assert module.resolved(candidate, review) == {"gap2"}


@pytest.mark.parametrize("second_pass", [True, False])
def test_only_one_semantic_repair_and_no_publication(tmp_path, monkeypatch, second_pass):
    value, targets, candidate, review = packet()
    calls = []
    writer = "a" * 32
    source_folder = tmp_path / "wiki/textbook-gap-write-jobs" / writer
    source_folder.mkdir(parents=True)
    (tmp_path / "knowledge.sqlite3").touch()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setattr(module, "local_model_digest", lambda _: value["model_digest"])
    monkeypatch.setattr(module.final, "inputs_for", lambda *args: (
        value, {}, value["book"], {"source_coverage": targets, "reading": {"coherence": {"passed": False}}}, {}, {}))

    class Job:
        def __init__(self, store, *args):
            self.key = "b" * 32
            self.root = store.root / "wiki/test-addition-job"
            self.root.mkdir(parents=True)
            module.atomic_json(self.root / "manifest.json", {"job": self.key})

        def stage(self, stage, prompt, schema, context, validate):
            calls.append(stage)
            if stage in {"additions-write", "additions-repair"}:
                result = copy.deepcopy(candidate)
            else:
                assert "article" not in context
                result = copy.deepcopy(review)
                if stage.endswith("-0") or not second_pass:
                    result["paragraphs"]["add000"]["fully_explained"] = False
            validate(result)
            return result

        def progress(self, *args, **kwargs):
            pass

    monkeypatch.setattr(module, "AdditionJob", Job)
    result = module.run(tmp_path, writer, tmp_path / "tokenizer-unused")
    assert calls == ["additions-write", "additions-review-0", "additions-repair", "additions-review-1"]
    assert result["resolved_targets"] == (2 if second_pass else 0)
    assert result["whole_article_reading_issues_retained"] == 1
    assert result["integration_with_factual_edits_pending"] is True
    assert result["reader_publication_allowed"] is result["semantic_acceptance"] is False
    assert result["goal_complete"] is result["source_deletion_allowed"] is False
    assert not (tmp_path / "reading.html").exists()
