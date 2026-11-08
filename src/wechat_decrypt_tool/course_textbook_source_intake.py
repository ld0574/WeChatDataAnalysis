"""Prepare first whole-record inputs for all currently published sources.

Existing inventories, accepted articles and model budgets remain untouched.
This CPU-only intake creates no ledger, prose, acceptance or publication.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

import psutil

from . import course_textbook_evidence as evidence
from . import course_textbook_reader as reader
from . import course_textbook_whole_sources as whole
from .course_knowledge import atomic_json, schema_lease
from .course_library import Library, canonical

POLICY = "first-whole-record-source-intake-all-published-lessons-v1"


def require(value, code):
    if not value:
        raise ValueError("requires_source_intake_" + code)


def bindings():
    from . import course_library
    return {Path(m.__file__).name: whole.sha(Path(m.__file__).read_bytes())
        for m in (sys.modules[__name__], evidence, whole, reader, course_library)}


def checkpoint(root):
    whole.TextbookStore(root).checkpoint()
    require(not (root / "supervisor-stop.flag").exists(), "supervisor_stop_retained")
    require(all(psutil.disk_usage(drive).free >= 32 * 2**30 for drive in ("C:/", "D:/")), "disk_reserve")


def delivered(root):
    surface = reader.Reader(root)
    return {key for key in surface.index()["lessons"] if surface.article(key)["state"] == "reading_ready"}


def original_equivalence(root, parent, artifact):
    require((root / "library/objects" / (evidence.checked_sha(artifact) + ".json")).is_file(),
        "current_published_source_object_exists")
    published = Library(root).object(artifact)
    projected = evidence.inventory(published, lesson_artifact=artifact, attachment_scope=parent["attachment_scope"])
    require(projected["lesson"] == parent["lesson"] and projected["originals"] == parent["originals"],
        "current_all_original_records_text_offsets_and_pixel_digests_equal")
    return {"parent_artifact": parent["lesson_artifact"], "current_artifact": artifact,
        "complete_originals_sha256": whole.sha(canonical(parent["originals"])),
        "original_records": len(parent["originals"]), "original_characters": parent["original_characters"],
        "publication_bytes_equal": parent["lesson_artifact"] == artifact,
        "complete_original_records_equal": True, "raw_export_byte_identity_claimed": False}


def plan(root):
    raw = (root / "library/catalog.json").read_bytes()
    catalog = json.loads(raw)
    require(len(catalog["lessons"]) == 340 and len({r["id"] for r in catalog["lessons"]}) == 340,
        "full_audible_scope")
    available = {r["id"]: r["artifact"] for r in catalog["lessons"] if r["state"] == "draft_available"}
    parents, existing = {}, {}
    for p in (root / "wiki/textbook-evidence").glob("*/manifest.json"):
        value = evidence.load(root, p.parent.name)
        parents.setdefault(value["lesson"], []).append(p.parent.name)
    for p in (root / "wiki/textbook-whole-record-evidence").glob("*/manifest.json"):
        value = whole.load(root, p.parent.name)
        existing.setdefault(value["lesson"], []).append(p.parent.name)
    finished = delivered(root)
    targets, equivalence = {}, {}
    for lesson in sorted(set(available) - set(existing) - finished):
        require(len(parents.get(lesson, [])) == 1, "one_prepared_parent_per_new_lesson")
        key = parents[lesson][0]
        value = evidence.load(root, key)
        equivalence[lesson] = original_equivalence(root, value, available[lesson])
        targets[lesson] = key
    preserved = {}
    for name in ("textbook-evidence", "textbook-whole-record-evidence"):
        for p in (root / "wiki" / name).glob("*/manifest.json"):
            preserved[p.relative_to(root).as_posix()] = whole.sha(p.read_bytes())
    binding = {"policy": POLICY, "catalog_sha256": whole.sha(raw), "available_source_versions": available,
        "targets": targets, "parent_current_original_equivalence": equivalence,
        "preserved_input_files": preserved, "existing_whole_lessons": sorted(existing),
        "already_delivered_lessons": sorted(finished), "code_sha256": bindings(),
        "audible_scope": 340, "silent_scope": 2, "all_recoverable_courseware_required": True,
        "model_calls": 0, "old_budgets_reset": False, "attachments_remain_pending": True,
        "reader_publication_allowed": False, "source_deletion_allowed": False}
    return binding, raw


def same_preserved(root, binding):
    for name, digest in binding["preserved_input_files"].items():
        require(whole.sha((root / name).read_bytes()) == digest, "all_existing_inputs_unchanged")


def row(root, lesson, parent, key):
    value = whole.load(root, key)
    original = evidence.load(root, parent)
    require(value == whole.build(parent, original) and value["lesson"] == lesson,
        "all_complete_originals_exact_no_supplement_association_invented")
    require(not value["semantic_accuracy_verified"] and not value["whole_lesson_knowledge_coverage_verified"]
        and value["reader_payload"] is False and value["source_deletion_allowed"] is False,
        "no_unearned_acceptance")
    return {"lesson": lesson, "parent_inventory": parent, "inventory": key,
        "original_records": len(value["originals"]), "characters": value["original_characters"],
        "whole_records": len(value["sources"]), "scheduling_groups": len(value["pages"])}


def summary(batch, binding, rows):
    return {"state": "first_whole_record_inputs_prepared_not_articles", "batch": batch,
        "available_source_lessons": len(binding["available_source_versions"]),
        "new_whole_inputs": len(rows), "existing_whole_lessons_preserved": len(binding["existing_whole_lessons"]),
        "already_delivered_lessons_preserved": len(binding["already_delivered_lessons"]),
        "original_records": sum(r["original_records"] for r in rows),
        "characters": sum(r["characters"] for r in rows), "audible_scope": 340, "silent_scope": 2,
        "remaining_unpublished_sources": 340 - len(binding["available_source_versions"]),
        "all_recoverable_courseware_required": True, "model_calls": 0, "articles_written": 0,
        "semantic_accuracy_verified": False, "reader_publication_allowed": False,
        "source_deletion_allowed": False, "full_processing_restore": False}


def run(root):
    root = Path(root).resolve()
    with schema_lease(root / "wiki/first-whole-record-source-intake.lock", timeout=0):
        checkpoint(root)
        binding, raw = plan(root)
        require(bool(binding["targets"]), "no_new_whole_record_inputs_to_prepare")
        batch = whole.sha(canonical(binding))
        folder = root / "wiki/first-whole-record-source-intakes" / batch
        require(not folder.exists(), "finite_batch_once_prior_results_preserved")
        folder.mkdir(parents=True)
        atomic_json(folder / "manifest.json", binding)
        (folder / "frozen-source-catalog.json").write_bytes(raw)
        identity = {"pid": psutil.Process().pid, "created": psutil.Process().create_time()}
        rows = []
        try:
            for lesson, parent in binding["targets"].items():
                checkpoint(root)
                same_preserved(root, binding)
                key, _ = whole.prepare(root, parent)
                rows.append(row(root, lesson, parent, key))
                atomic_json(folder / "prepared-inputs.json", rows)
                atomic_json(folder / "progress.json", {**identity, "state": "preparing_first_whole_inputs",
                    "completed": len(rows), "total": len(binding["targets"]), "updated": time.time(), "model_calls": 0})
            same_preserved(root, binding)
            result = summary(batch, binding, rows)
            atomic_json(folder / "result.json", result)
            checked = verify(root, batch)
            atomic_json(folder / "progress.json", {**identity, "state": checked["state"], "completed": len(rows),
                "total": len(rows), "updated": time.time(), "model_calls": 0})
            return checked
        except BaseException as exc:
            atomic_json(folder / "failure.json", whole.failure_status(exc))
            raise


def verify(root, batch):
    root = Path(root).resolve()
    evidence.checked_sha(batch)
    folder = root / "wiki/first-whole-record-source-intakes" / batch
    binding, rows = [whole.read(folder / name) for name in ("manifest.json", "prepared-inputs.json")]
    require(whole.sha(canonical(binding)) == batch and binding["policy"] == POLICY
        and binding["code_sha256"] == bindings(), "same_exact_input_recipe_and_code")
    frozen = (folder / "frozen-source-catalog.json").read_bytes()
    require(whole.sha(frozen) == binding["catalog_sha256"], "frozen_catalog_exact_bytes")
    catalog = json.loads(frozen)
    require(len(catalog["lessons"]) == 340 and {r["id"]: r["artifact"] for r in catalog["lessons"]
        if r["state"] == "draft_available"} == binding["available_source_versions"], "all_ready_source_versions")
    require([r["lesson"] for r in rows] == list(binding["targets"]), "all_targets_prepared_once")
    require(rows == [row(root, lesson, parent, saved["inventory"]) for (lesson, parent), saved in
        zip(binding["targets"].items(), rows, strict=True)], "actual_whole_inputs_reconstructed")
    require(binding["parent_current_original_equivalence"] == {lesson: original_equivalence(root,
        evidence.load(root, parent), binding["available_source_versions"][lesson])
        for lesson, parent in binding["targets"].items()}, "actual_complete_original_equivalence")
    same_preserved(root, binding)
    result = summary(batch, binding, rows)
    require(result == whole.read(folder / "result.json"), "actual_saved_finite_result")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("command", choices=["run", "verify"])
    parser.add_argument("--batch")
    args = parser.parse_args()
    try:
        require(args.command == "run" or args.batch is not None, "verify_requires_batch")
        print(json.dumps(run(args.root) if args.command == "run" else verify(args.root, args.batch)))
    except BaseException as exc:
        print(json.dumps(whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
