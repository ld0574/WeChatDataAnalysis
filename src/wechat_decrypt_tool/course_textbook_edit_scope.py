"""Preserve source dependencies when reviewing replacement paragraphs."""

from __future__ import annotations

import re


def require(value, code):
    if not value:
        raise ValueError("requires_edit_scope_" + code)


def replacement_sources(sources, coverage, candidate, additional=()):
    """Include old supporting material, not just the replacement's new citations.

    This is a conservative dependency set for paragraph replacements. It does
    not establish semantic support or replace independent coverage checks.
    """
    require(set(coverage) == set(sources), "complete_original_coverage_ledger")
    required = set(additional)
    replaced = set()
    for row in candidate["paragraph_changes"]:
        require(row["kind"] in {"append", "replace"}, "actual_paragraph_operation")
        required.update(row["source_keys"])
        if row["kind"] == "replace":
            replaced.add(row["paragraph_id"])
    for ref, row in coverage.items():
        require(row["kind"] in {"covered", "missing", "non_teaching"}, "known_source_disposition")
        if row["kind"] == "covered" and replaced.intersection(row["paragraph_ids"]):
            required.add(ref)
    require(required <= set(sources), "only_frozen_original_sources")
    # A source split into several bounded records must be reconstructed, not
    # silently limited to the single record named by an earlier citation.
    originals = {match.group(1) for ref in required
                 if (match := re.fullmatch(r"(E[0-9]{5})_[0-9]{3}", ref))}
    required.update(ref for ref in sources
                    if (match := re.fullmatch(r"(E[0-9]{5})_[0-9]{3}", ref))
                    and match.group(1) in originals)
    return {ref: value for ref, value in sources.items() if ref in required}


def validate_replacement_scope(sources, coverage, candidate, provided, additional=()):
    require(set(provided) <= set(sources)
            and all(sources[ref] == value for ref, value in provided.items()), "unchanged_scoped_source_bytes")
    required = replacement_sources(sources, coverage, candidate, additional)
    require(set(required) <= set(provided), "all_replaced_paragraph_dependencies_present")
    return required
