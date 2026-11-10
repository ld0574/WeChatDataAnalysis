"""Assemble a private, explicitly unfinished reading draft without new inference.

Only independently reviewed additions with resolved covered groups and an exact
existing anchor enter the body. Everything else remains in the private review
packet. This neither rewrites prose nor publishes or clears inherited issues.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from . import course_textbook_corpus_gap_remaining as remaining

original = remaining.original
POLICY = "private-exact-anchor-gap-assembly-with-all-obligations-retained-v1"


def require(ok, code):
    if not ok:
        raise ValueError("requires_gap_assembly_" + code)


def parent_items(private):
    """Reproduce original A identifiers and retain native paths, without a DB."""
    items, paths = {}, {}

    def add(kind, content, citations, path, chapter="", section=""):
        key = f"A{len(items):06d}"
        items[key] = {"kind": kind, "content": content, "chapter": chapter,
            "section": section, "citations": list(citations)}
        paths[key] = path

    for name in ("introduction", "prerequisites"):
        for index, row in enumerate(private[name]):
            add(name, row["text"], row["citations"], [name, index])
    for ci, chapter in enumerate(private["chapters"]):
        for si, section in enumerate(chapter["sections"]):
            base = ["chapters", ci, "sections", si]
            for index, row in enumerate(section["paragraphs"]):
                add("paragraph", row["text"], row["citations"], [*base, "paragraphs", index],
                    chapter["heading"], section["heading"])
            for index, graph in enumerate(section["diagrams"]):
                add("diagram", {k: v for k, v in graph.items() if k != "citations"}, graph["citations"],
                    [*base, "diagrams", index], chapter["heading"], section["heading"])
    for index, row in enumerate(private["takeaways"]):
        add("takeaway", row["text"], row["citations"], ["takeaways", index])
    return items, paths


def compose(private, expected_items, records, status, inherited_issues):
    items, paths = parent_items(private)
    require(items == expected_items and bool(items), "same_entire_parent_article_and_original_anchor_order")
    additions, placed, retained, canonical = [], {}, [], {}
    parent_texts = {row["content"] for row in items.values() if isinstance(row["content"], str)}
    numbers = [row["batch"] for row in records]
    require(len(set(numbers)) == len(numbers) == status["reviewed_batches"], "all_actual_reviewed_batches_once")
    for record in sorted(records, key=lambda row: row["batch"]):
        candidate, audit, context = (record[k] for k in ("candidate", "audit", "context"))
        require(context["article"] == items, "same_actual_parent_in_every_batch")
        actual = original.assess(candidate, audit, context)
        require({**actual, "candidate_sha256": remaining.digest(candidate), "audit_sha256": remaining.digest(audit)}
            == record["outcome"], "same_independently_checked_batch_disposition")
        resolved = set(actual["resolved_target_clauses"])
        for local, paragraph in candidate["additions"].items():
            key = f"B{record['batch']:04d}:{local}"
            linked = [(i, group) for i, group in enumerate(candidate["groups"]) if local in group["article_items"]]
            covered = [i for i, group in linked if group["kind"] == "covered" and group["clauses"]
                and set(group["clauses"]) <= resolved]
            anchor, reasons = paragraph["anchor"], []
            require(anchor in items or anchor == "new_section", "known_exact_original_anchor")
            if not linked:
                reasons.append("unmapped_addition")
            if not audit["additions"][local]["passed"]:
                reasons.append("independent_addition_review_failed")
            if not covered:
                reasons.append("no_independently_resolved_covered_group")
            if anchor == "new_section":
                reasons.append("new_section_requires_editorial_placement")
            if paragraph["text"] in parent_texts:
                reasons.append("exact_parent_text_duplicate")
            row = {"id": key, "batch": record["batch"], "local_id": local, "paragraph": copy.deepcopy(paragraph),
                "linked_groups": [i for i, _ in linked], "independently_resolved_groups": covered,
                "reason_codes": reasons, "body_placement_allowed": not reasons}
            additions.append(row)
            if reasons:
                retained.append(key)
                continue
            fingerprint = remaining.digest(paragraph)
            if fingerprint in canonical:
                row.update(body_placement_allowed=False, exact_duplicate_of=canonical[fingerprint])
                row["reason_codes"].append("exact_reviewed_addition_duplicate")
                retained.append(key)
                continue
            canonical[fingerprint] = key
            placed.setdefault(anchor, []).append(key)
    by_id = {row["id"]: row for row in additions}
    blocks = []
    for key, value in items.items():
        blocks.append({"id": key, "origin": "parent", "native_path": paths[key], "item": copy.deepcopy(value)})
        for new in placed.get(key, []):
            blocks.append({"id": new, "origin": "reviewed_addition", "after": key,
                "paragraph": copy.deepcopy(by_id[new]["paragraph"])})
    summary = {"state": "private_anchored_gap_draft_requires_full_editorial_and_reading_acceptance",
        "parent_items_preserved": len(items), "reviewed_batches": len(records),
        "retained_unresolved_batches": status["retained_unresolved_batches"],
        "candidate_paragraphs": len(additions), "placed_paragraphs": sum(map(len, placed.values())),
        "retained_paragraphs": len(retained),
        "unmapped_paragraphs": sum("unmapped_addition" in row["reason_codes"] for row in additions),
        "pending_new_sections": sum("new_section_requires_editorial_placement" in row["reason_codes"] for row in additions),
        "remaining_original_unresolved": status["remaining_original_unresolved"],
        "remaining_source_conflicts": status["remaining_source_conflicts"],
        "inherited_parent_issues": len(inherited_issues), "new_model_calls": 0,
        "semantic_acceptance": False, "reader_publication_allowed": False, "source_deletion_allowed": False}
    require(summary["candidate_paragraphs"] == summary["placed_paragraphs"] + summary["retained_paragraphs"],
        "every_actual_addition_retained_exactly_once")
    return {"policy": POLICY, "summary": summary, "parent_private": copy.deepcopy(private),
        "ordered_body_blocks": blocks, "all_reviewed_additions": additions, "retained_addition_ids": retained,
        "original_gap_status": copy.deepcopy(status), "inherited_parent_issues": copy.deepcopy(inherited_issues),
        "whole_article_reading_diagrams_courseware_and_choice_quiz_acceptance_pending": True}


def reconstruct(root, key):
    root = Path(root).resolve()
    status = remaining.verify(root, key)
    _, folder, prior, old, recipe, data, _ = remaining.load(root, key)
    restored = remaining.whole.read(folder / "independent-result-restore.json")
    require(restored["state"] == "corpus_gap_remaining_results_independently_reconstructed"
        and restored["job"] == key and restored["result_sha256"] == remaining.digest(status)
        and restored["original_root_reads"] == restored["new_model_calls"] == 0,
        "actual_independent_complete_gap_result_restore_before_assembly")
    path = root / "wiki/textbook-whole-authoring-jobs" / recipe["parent"] / "private-article.json"
    require(any((root / name).resolve() == path and remaining.whole.sha(path.read_bytes()) == checksum
        for name, checksum in data["parent_proof"]["files"].items()), "authenticated_original_parent_private_bytes")
    saved = remaining.whole.read(path)
    private = saved["data"]
    require(saved["data_sha256"] == remaining.digest(private) == data["parent_proof"]["private_article_sha256"],
        "same_complete_original_parent_private_article")
    records, parked = [], []
    for number, context in enumerate(data["contexts"]):
        prefix = f"batch-{number:04d}"
        source = prior if number < remaining.PREFIX else folder
        if (source / (prefix + "-unresolved.json")).exists():
            parked.append(number)
            continue
        outcome = remaining.whole.read(source / (prefix + "-result.json"))
        if (old / (prefix + "-edit.json")).exists():
            raw, _ = remaining.whole.editor.e.checked_cache(old, prefix + "-edit")
            candidate = original.compile_candidate(raw, context, data["units"])
        else:
            candidate, _, _ = remaining.previous.failed_candidate(old, recipe, data, number)
        audit, _ = remaining.whole.editor.e.checked_cache(old, prefix + "-review")
        records.append({"batch": number, "candidate": candidate, "audit": audit, "context": context, "outcome": outcome})
    require(len(parked) == status["retained_unresolved_batches"]
        and len(records) + len(parked) == status["original_batches"], "entire_original_batch_partition_preserved")
    result = compose(private, data["contexts"][0]["article"], records, status, data["inherited_parent_issues"])
    result.update(job=key, parked_original_batches=parked, result_restore_sha256=remaining.digest(restored),
        code_sha256=remaining.whole.sha(Path(__file__).read_bytes()))
    return result


def run(root, key, *, read_only=False):
    result = reconstruct(root, key)
    path = Path(root).resolve() / "wiki/corpus-gap-assemblies" / key / "private-draft.json"
    if not read_only:
        remaining.guard.checkpoint(Path(root).resolve())
        require(not path.exists(), "single_immutable_private_assembly_no_model_retry")
    remaining.retained(path, result, read_only=read_only)
    return {**result["summary"], "job": key, "draft_sha256": remaining.digest(result), "path": str(path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("command", choices=["build", "verify"])
    args = parser.parse_args()
    try:
        result = run(args.root, args.job, read_only=args.command == "verify")
        print(json.dumps(result))
    except BaseException as exc:
        print(json.dumps(remaining.whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
