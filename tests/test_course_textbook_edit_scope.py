import copy

import pytest

from wechat_decrypt_tool.course_textbook_edit_scope import (
    replacement_sources,
    validate_replacement_scope,
)


def fixture():
    sources = {key: {"kind": kind, "text": text} for key, kind, text in [
        ("E00157_000", "speech", "New explanation."),
        ("E00407_000", "speech", "An original worked example."),
        ("E00503_000", "document", "Original necessary setup, "),
        ("E00503_001", "document", "including the boundary conditions."),
        ("E00511_000", "document", "A retained implementation detail."),
    ]}
    coverage = {key: {"kind": "non_teaching"} for key in sources}
    coverage.update({"E00407_000": {"kind": "covered", "paragraph_ids": ["p030", "p058"]},
        "E00503_000": {"kind": "covered", "paragraph_ids": ["p018", "p019"]},
        "E00511_000": {"kind": "covered", "paragraph_ids": ["p057", "p058"]}})
    candidate = {"paragraph_changes": [{"kind": "replace", "paragraph_id": ref,
        "source_keys": ["E00157_000"]} for ref in ["p019", "p058"]]}
    return sources, coverage, candidate


def test_a_new_citation_cannot_hide_old_examples_or_conditions_lost_by_replacement():
    sources, coverage, candidate = fixture()
    narrow = {"E00157_000": sources["E00157_000"]}
    with pytest.raises(ValueError, match="all_replaced_paragraph_dependencies_present"):
        validate_replacement_scope(sources, coverage, candidate, narrow)
    required = replacement_sources(sources, coverage, candidate)
    assert set(required) == set(sources)
    assert validate_replacement_scope(sources, coverage, candidate, required) == sources


def test_an_append_preserves_old_paragraphs_without_requiring_unrelated_originals():
    sources, coverage, _ = fixture()
    candidate = {"paragraph_changes": [{"kind": "append", "section_id": "s",
        "source_keys": ["E00157_000"]}]}
    assert set(replacement_sources(sources, coverage, candidate)) == {"E00157_000"}


def test_split_originals_are_complete_and_data_is_never_mutated():
    sources, coverage, candidate = fixture()
    candidate["paragraph_changes"] = candidate["paragraph_changes"][:1]
    before = copy.deepcopy((sources, coverage, candidate))
    scope = replacement_sources(sources, coverage, candidate)
    assert "E00503_001" in scope
    assert scope["E00503_000"]["text"] + scope["E00503_001"]["text"] == "Original necessary setup, including the boundary conditions."
    assert (sources, coverage, candidate) == before


def test_partial_ledgers_and_changed_scoped_sources_fail_closed():
    sources, coverage, candidate = fixture()
    incomplete = copy.deepcopy(coverage)
    del incomplete["E00511_000"]
    with pytest.raises(ValueError, match="complete_original_coverage_ledger"):
        replacement_sources(sources, incomplete, candidate)
    changed = copy.deepcopy(sources)
    changed["E00503_000"]["text"] = "A different original."
    with pytest.raises(ValueError, match="unchanged_scoped_source_bytes"):
        validate_replacement_scope(sources, coverage, candidate, changed)
