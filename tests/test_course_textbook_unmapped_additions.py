import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_course_textbook_gap_additions import packet
from wechat_decrypt_tool import course_textbook_unmapped_additions as module


def failed_candidate():
    value, targets, candidate, review = packet()
    for ref in targets:
        candidate["targets"][ref].update(kind="non_teaching", addition_ids=[])
        review["targets"][ref].update(teaching=False, covered=False, addition_ids=[])
    return value, targets, candidate, review


def save_failed_repairs(folder, first, second):
    attempts = folder / "attempts"
    attempts.mkdir(parents=True)
    for number, candidate in enumerate([first, second], 1):
        module.atomic_json(attempts / f"additions-repair-{number:02}.json", {
            "validation_error": "requires_gap_additions_no_unassigned_prose", "raw_response": json.dumps(candidate)})


def test_earliest_candidate_and_failed_mapping_are_retained_exactly(tmp_path):
    value, targets, first, _ = failed_candidate()
    second = copy.deepcopy(first)
    second["additions"][0]["text"] += " A later different paragraph."
    save_failed_repairs(tmp_path, first, second)
    before = {p.name: p.read_bytes() for p in (tmp_path / "attempts").iterdir()}
    candidate, unassigned, _ = module.select_candidate(tmp_path, value["book"], targets, value["sources"])
    assert candidate == first and unassigned == ["add000"]
    assert all(not row["addition_ids"] for row in candidate["targets"].values())
    assert {p.name: p.read_bytes() for p in (tmp_path / "attempts").iterdir()} == before


def test_other_structural_failures_cannot_be_waived(tmp_path):
    value, targets, candidate, _ = failed_candidate()
    candidate["targets"]["gap1"]["source_quote"] = "Fabricated original quotation."
    save_failed_repairs(tmp_path, candidate, candidate)
    with pytest.raises(ValueError, match="actual_original_quote"):
        module.select_candidate(tmp_path, value["book"], targets, value["sources"])


def test_scoped_check_retains_complete_cited_target_and_neighbour_sources():
    sources = {f"s{i}": {"kind": "speech", "text": f"Complete original {i}" * 50} for i in range(10)}
    candidate = {"additions": [{"source_keys": ["s8"]}]}
    scope = module.source_scope(sources, {"s2": {}}, candidate)
    assert set(scope) == {"s0", "s1", "s2", "s3", "s4", "s6", "s7", "s8", "s9"}
    assert all(scope[k] == sources[k] for k in scope)


def test_only_unused_review_is_called_and_mapping_failure_stays_negative(tmp_path, monkeypatch):
    value, targets, candidate, review = failed_candidate()
    key = "a" * 32
    folder = tmp_path / "wiki/textbook-gap-addition-jobs" / key
    folder.mkdir(parents=True)
    module.atomic_json(folder / "manifest.json", {"recipe": {"extra": {"writer": "b" * 32, "writer_bindings": {}}}})
    schema = module.original.review_schema(candidate, targets, value["sources"])
    context = {"targets": targets, "complete_sources_for_this_check": value["sources"]}
    binding = {"unassigned_addition_ids": ["add000"]}
    monkeypatch.setattr(module, "prepare", lambda *args: (value, candidate, schema, context, binding))
    monkeypatch.setattr(module, "TextbookStore", lambda _: SimpleNamespace(root=tmp_path, checkpoint=lambda: None))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    calls = []

    class Job:
        def __init__(self, *args):
            self.root, self.key = folder, key

        def stage(self, name, prompt, schema, context, validate, **kwargs):
            calls.append(name)
            validate(review)
            return review

        def progress(self, *args, **kwargs):
            pass

    monkeypatch.setattr(module, "UnmappedJob", Job)
    result = module.run(tmp_path, key, tmp_path / "unused-tokenizer")
    assert calls == ["additions-review-1"]
    assert result["original_mapping_failure_retained"] is True
    assert result["semantic_acceptance"] is result["reader_publication_allowed"] is False
    saved = json.loads((folder / "private-unmapped-additions.json").read_bytes())
    assert saved["candidate"] == candidate
