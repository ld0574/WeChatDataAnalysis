"""Recover complete notebook text around a fragment or an extracted visual.

This projects private context, not a lesson-association or coverage verdict.
Callers must also verify the retained visual-link input-file bindings.
"""

from __future__ import annotations

import copy

from .course_library import canonical
from .course_textbook_evidence import sha, validate_inventory


def require(value, reason):
    if not value:
        raise ValueError("notebook_context_" + reason)


def build(inventory, visual_sources, visual_links, target, *, neighbors=1):
    """Keep literal target IDs while adding whole originals and cell context."""
    validate_inventory(inventory)
    require(type(neighbors) is int and neighbors in (0, 1), "bounded_cell_neighbors")
    sources, originals = inventory["sources"], inventory["originals"]
    groups = {}
    for ref, row in sources.items():
        if row["kind"] == "document":
            groups.setdefault(row["text_origin"]["original"], []).append(ref)
    links = visual_links["links"]
    require(set(links) == set(visual_sources), "complete_visual_mapping")
    mapped = {}
    for ref, row in links.items():
        require(row["unique_direct_link"] is True and len(row["candidates"]) == 1,
                "unique_direct_visual_origin")
        candidate = row["candidates"][0]
        member, cell = candidate["notebook_sha256"], candidate["cell"]
        origin = visual_sources[ref]["origin"]
        require(origin["image_sha256"] == candidate["image_sha256"]
                and origin["document_id"] == candidate["document_id"], "exact_visual_identity")
        require(type(cell) is int and cell >= 0, "real_notebook_cell")
        for text_ref in candidate["direct_text_source_ids"]:
            require(text_ref in sources and sources[text_ref]["kind"] == "document",
                    "real_direct_text_reference")
            text_origin = sources[text_ref]["origin"]
            require(text_origin["member_sha256"] == member and text_origin["cell"] == cell,
                    "direct_text_same_notebook_cell")
        require(any(originals[index]["origin"]["member_sha256"] == member
                    and originals[index]["origin"]["cell"] == cell for index in groups),
                "visual_cell_has_retained_text_record")
        mapped[ref] = (member, cell)
    target_original = None
    if target in sources and sources[target]["kind"] == "document":
        target_original = sources[target]["text_origin"]["original"]
        origin = sources[target]["origin"]
        member, cell = origin["member_sha256"], origin["cell"]
        literal_target = sources[target]
    else:
        require(target in mapped, "real_document_or_visual_target")
        member, cell = mapped[target]
        literal_target = visual_sources[target]
    selected, refs = {}, set()
    for index, parts in sorted(groups.items()):
        original = originals[index]
        origin = original["origin"]
        if origin["member_sha256"] != member or abs(origin["cell"] - cell) > neighbors:
            continue
        parts = sorted(parts, key=lambda key: sources[key]["text_origin"]["start"])
        require("".join(sources[key]["text"] for key in parts) == original["text"],
                "exact_whole_original_reconstruction")
        role = ("target_complete_original" if index == target_original else
                "same_cell_context" if origin["cell"] == cell else "neighbor_cell_context")
        selected[str(index)] = {"record": copy.deepcopy(original), "source_ids": parts,
                                "source_sha256": sha(canonical(original)), "role": role}
        refs.update(parts)
    visual_context = {key: copy.deepcopy(visual_sources[key]) for key, (digest, number) in mapped.items()
                      if digest == member and abs(number - cell) <= neighbors}
    refs.update(visual_context)
    require(target in refs and bool(selected), "literal_target_and_complete_cell_context")
    return {
        "target": target,
        "target_source": copy.deepcopy(literal_target),
        "notebook_sha256": member,
        "cell": cell,
        "cell_neighbor_radius": neighbors,
        "complete_textual_originals": selected,
        "visual_text_context": visual_context,
        "literal_source_ids": sorted(refs),
        "does_not_establish_lesson_affiliation": True,
        "does_not_reclassify_teaching_or_coverage": True,
    }
