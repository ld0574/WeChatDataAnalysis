"""Retrieve candidates using complete notebook cells, preserving literal targets.

This changes the available context, never an affiliation or coverage verdict.
The caller must verify the retained context package and its input-file hashes.
"""

from __future__ import annotations

from . import course_textbook_affiliation_scope as retrieval


def require(value, reason):
    if not value:
        raise ValueError("contextual_affiliation_" + reason)


def scope(sources, target, notebook_context, *, candidates=12):
    """Use full cells for retrieval, but ask about the unchanged original item."""
    require(target in sources and sources[target]["kind"] not in {"speech", "frame"},
            "actual_courseware_target")
    require(notebook_context["target"] == target, "same_context_target")
    literal = notebook_context["target_source"]
    require(all(literal[key] == sources[target][key] for key in ("kind", "text")),
            "unchanged_literal_target")
    require(notebook_context["does_not_establish_lesson_affiliation"] is True
            and notebook_context["does_not_reclassify_teaching_or_coverage"] is True,
            "context_is_not_a_verdict")
    records = notebook_context["complete_textual_originals"]
    require(bool(records), "complete_cell_text_required")
    texts, refs = [], set()
    for row in records.values():
        parts = row["source_ids"]
        require(bool(parts) and not refs.intersection(parts), "unique_complete_text_fragments")
        require(all(key in sources and sources[key]["kind"] == "document" for key in parts),
                "actual_text_fragments")
        require("".join(sources[key]["text"] for key in parts) == row["record"]["text"],
                "exact_complete_original_text")
        texts.append(row["record"]["text"])
        refs.update(parts)
    for key, row in notebook_context["visual_text_context"].items():
        require(key in sources and all(row[field] == sources[key][field]
                                     for field in ("kind", "text")), "exact_visual_text")
        require(key not in refs, "distinct_visual_reference")
        texts.append(row["text"])
        refs.add(key)
    require(refs == set(notebook_context["literal_source_ids"]) and target in refs,
            "complete_declared_context_sources")
    # The synthetic query stays inside candidate retrieval. Neither the item
    # under review nor any of the supplied classroom originals is rewritten.
    query_sources = {**sources, target: {**sources[target], "text": "\n".join(texts)}}
    expanded = retrieval.scope(query_sources, target, candidates=candidates)
    literal_scope = retrieval.scope(sources, target, candidates=4)
    selected = set(expanded["lesson_sources"]) | set(literal_scope["lesson_sources"])
    return {
        "target": target,
        "courseware_source": sources[target],
        "complete_notebook_context": notebook_context,
        "lesson_sources": {key: sources[key] for key in sorted(selected)},
        "retrieval": {
            "policy": "complete-cell-and-literal-target-candidates-v1",
            "searched_lesson_sources": expanded["retrieval"]["searched_lesson_sources"],
            "complete_cell_seed_ids": expanded["retrieval"]["seed_ids"],
            "literal_target_seed_ids": literal_scope["retrieval"]["seed_ids"],
            "candidate_count": len(selected),
            "ranking_is_affiliation_evidence": False,
            "unretrieved_sources_are_excluded": False,
        },
    }
