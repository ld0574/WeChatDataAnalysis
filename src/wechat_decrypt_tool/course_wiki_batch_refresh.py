"""Rebind an unstarted allocated batch after provenance-only export changes.

No source text, image, section, scope or attempted job is replaced. A durable
one-to-one binding prevents obtaining multiple batches from one old reservation.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import course_wiki_allocation as allocation
from . import course_wiki_evidence as evidence
from .course_knowledge import atomic_json, schema_lease
from .course_library import Library, canonical
from .course_lesson_exports import digest
from .course_pdf_ocr import local_model_digest
from .course_video_visual_audit import ReadOnlyArchive
from .course_wiki_batch import ALLOCATION_POLICY, item_key, job_id, manifest

POLICY = "unstarted-allocated-batch-provenance-refresh-v1"
PROVENANCE = {"source_export_sha256", "rows_sha256"}


def content(lesson):
    return {k: v for k, v in lesson.items() if k not in PROVENANCE}


def require_unattempted(root, allocations):
    for pattern in (
        "wiki/theme-planning-sources/*.json",
        "wiki/planning-jobs/*/manifest.json",
    ):
        for path in root.glob(pattern):
            if json.loads(path.read_bytes()).get("allocation") in allocations:
                raise ValueError("wiki_batch_refresh_attempted_allocation")


def old_allocation(root, key):
    job_id(key)
    path = root / "wiki/allocation-contexts" / key / "manifest.json"
    value = json.loads(path.read_bytes())
    if digest(canonical(value))[:32] != key:
        raise ValueError("wiki_batch_refresh_allocation_changed")
    partition = evidence.read(root, value["partition"], current=False)
    lesson = Library(root).object(value["lesson_sha256"])
    if value != allocation.recipe(lesson, partition, value["partition"]):
        raise ValueError("wiki_batch_refresh_allocation_incomplete")
    return value, partition, lesson


def validate_binding(root, source, value):
    if (
        value["policy"] != POLICY
        or value["source_recipe_sha256"] != digest(canonical(source))
        or value["source_batch"] != digest(canonical(source))[:32]
        or value["model_calls"] != 0
    ):
        raise ValueError("wiki_batch_refresh_binding_changed")
    target = value["target_recipe"]
    if value["target_batch"] != digest(canonical(target))[:32]:
        raise ValueError("wiki_batch_refresh_target_changed")
    expected = []
    for spec in source["courses"]:
        old, old_partition, old_lesson = old_allocation(root, spec["allocation"])
        new_key = value["allocation_bindings"][spec["allocation"]]
        new, new_partition, new_lesson = old_allocation(root, new_key)
        if (
            old["video"] != spec["video"]
            or old["lesson_sha256"] != spec["lesson_sha256"]
            or type(spec["allocation_scope"]) is not int
            or not 0 <= spec["allocation_scope"] < len(old["scopes"])
            or content(old_lesson) != content(new_lesson)
            or old["scopes"] != new["scopes"]
            or {k: v for k, v in old_partition.items() if k != "lesson_sha256"}
            != {k: v for k, v in new_partition.items() if k != "lesson_sha256"}
        ):
            raise ValueError("wiki_batch_refresh_content_or_scope_changed")
        row = {**spec, "allocation": new_key, "lesson_sha256": new["lesson_sha256"]}
        row.pop("item")
        row["item"] = digest(canonical(row))[:32]
        expected.append(row)
    if target != {**source, "courses": expected}:
        raise ValueError("wiki_batch_refresh_recipe_changed")
    if len(set(map(item_key, expected))) != len(expected):
        raise ValueError("wiki_batch_refresh_duplicate_scope")


def refresh(store, key):
    with schema_lease(store.root / "wiki-theme-batch.lock", timeout=0):
        store.checkpoint()
        folder, source = manifest(store.root, key)
        if source["policy"] != ALLOCATION_POLICY:
            raise ValueError("wiki_batch_refresh_requires_allocated_batch")
        pointer = store.root / "wiki/theme-batch-refreshes" / (key + ".json")
        if pointer.exists():
            value = json.loads(pointer.read_bytes())
            validate_binding(store.root, source, value)
        else:
            if (folder / "progress.json").exists():
                raise ValueError("wiki_batch_refresh_started_batch")
            allocations = {s["allocation"] for s in source["courses"]}
            # Even an abandoned planning manifest reserves its original budget.
            require_unattempted(store.root, allocations)
            if local_model_digest(source["model"]) != source["model_digest"]:
                raise ValueError("wiki_batch_refresh_model_changed")
            lib = Library(store.root)
            bindings = {}
            changed = False
            # Check every source before creating any replacement partitions.
            for old_key in allocations:
                old, _, lesson = old_allocation(store.root, old_key)
                current = lib.lesson(old["video"])
                if content(lesson) != content(current):
                    raise ValueError("wiki_batch_refresh_substantive_source_change")
                changed |= digest(canonical(current)) != old["lesson_sha256"]
            if not changed:
                return {"state": "no_provenance_change", "batch": key, "model_calls": 0}
            for old_key in sorted(allocations):
                old, partition, _ = old_allocation(store.root, old_key)
                new_partition = evidence.create(
                    store,
                    old["video"],
                    old["section"],
                    temporal=partition["policy"] == evidence.TEMPORAL_POLICY,
                )
                checked = evidence.read(store.root, new_partition["partition"])
                if {k: v for k, v in checked.items() if k != "lesson_sha256"} != {
                    k: v for k, v in partition.items() if k != "lesson_sha256"
                }:
                    raise ValueError("wiki_batch_refresh_original_ranges_changed")
                bindings[old_key] = allocation.create(
                    store, new_partition["partition"]
                )["allocation"]
            courses = []
            for spec in source["courses"]:
                new, _, _ = old_allocation(store.root, bindings[spec["allocation"]])
                row = {
                    **spec,
                    "allocation": bindings[spec["allocation"]],
                    "lesson_sha256": new["lesson_sha256"],
                }
                row.pop("item")
                row["item"] = digest(canonical(row))[:32]
                courses.append(row)
            target = {**source, "courses": courses}
            value = {
                "policy": POLICY,
                "source_batch": key,
                "source_recipe_sha256": digest(canonical(source)),
                "target_batch": digest(canonical(target))[:32],
                "target_recipe": target,
                "allocation_bindings": bindings,
                "model_calls": 0,
            }
            validate_binding(store.root, source, value)
            require_unattempted(store.root, set(bindings.values()))
            requested = set(map(item_key, courses))
            for path in (store.root / "wiki/theme-batches").glob("*/manifest.json"):
                _, previous = manifest(store.root, path.parent.name)
                if requested & set(map(item_key, previous["courses"])):
                    raise ValueError("wiki_batch_refresh_target_already_reserved")
            store.checkpoint()
            # Freeze the only permitted successor before making it executable.
            pointer.parent.mkdir(parents=True, exist_ok=True)
            atomic_json(pointer, value)
        target_key = value["target_batch"]
        target_folder = store.root / "wiki/theme-batches" / target_key
        target_folder.mkdir(parents=True, exist_ok=True)
        target_path = target_folder / "manifest.json"
        expected = {"id": target_key, "recipe": value["target_recipe"]}
        if target_path.exists():
            if json.loads(target_path.read_bytes()) != expected:
                raise ValueError("wiki_batch_refresh_reserved_target_changed")
        else:
            atomic_json(target_path, expected)
        return {
            "state": "provenance_refreshed_without_model_calls",
            "batch": target_key,
            "source_batch": key,
            "scopes": len(value["target_recipe"]["courses"]),
            "allocations": len(value["allocation_bindings"]),
            "model_calls": 0,
            "old_reservations_preserved": True,
            "source_deletion_allowed": False,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--batch", required=True)
    args = parser.parse_args()
    print(json.dumps(refresh(ReadOnlyArchive(args.root), args.batch)))


if __name__ == "__main__":
    main()
