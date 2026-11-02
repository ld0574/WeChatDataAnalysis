"""Complete original-text contexts for thematic planning of allocated evidence.

Context boundaries are capacity limits, never proposed knowledge boundaries.
Every evidence page is retained exactly once, including scopes a model skips.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .course_knowledge import atomic_json, schema_lease
from .course_library import Library, canonical
from .course_lesson_exports import digest
from .course_wiki_evidence import TEMPORAL_POLICY as TEMPORAL_EVIDENCE_POLICY
from .course_wiki_evidence import materialize, read as read_partition

POLICY = "complete-allocated-original-planning-contexts-v1"
TEMPORAL_POLICY = "complete-time-ordered-original-planning-contexts-v1"
# Leave room for the frozen same-course topic directory and repair diagnostics.
MAX_CONTEXT = 10000


def context(lesson, partition, key, numbers):
    section = next(s for s in lesson["sections"] if s["id"] == partition["section"])
    pages = []
    for n in numbers:
        sources = materialize(partition["originals"], partition["pages"][n])
        pages.append(
            {
                "id": f"page-{n}",
                "evidence_page": n,
                "originals": [
                    {
                        "reference": ref,
                        "kind": source["kind"],
                        "start": source["start"],
                        "end": source["end"],
                        "text": source["text"],
                        "text_origin": source["text_origin"],
                        **(
                            {"image_sha256": source["image_sha256"]}
                            if source.get("image_sha256")
                            else {}
                        ),
                    }
                    for ref, source in sources.items()
                ],
            }
        )
    value = {
        "video": partition["video"],
        "course_title": lesson["title"],
        "original_section": {k: section[k] for k in ("id", "title", "summary")},
        "evidence_partition": key,
        "whole_window_evidence_pages": len(partition["pages"]),
        "eligible": [p["id"] for p in pages],
        "evidence_pages": pages,
        "semantic_boundary_verified": False,
        "full_topic_coverage_verified": False,
    }
    if partition["policy"] == TEMPORAL_EVIDENCE_POLICY:
        value.update(
            original_order=partition["original_order"],
            temporal_proximity_is_not_semantic_alignment=True,
        )
    return value


def recipe(lesson, partition, key):
    scopes, current = [], []
    for n in range(len(partition["pages"])):
        if len(canonical(context(lesson, partition, key, [n])).decode()) > MAX_CONTEXT:
            raise ValueError("theme_allocation_single_original_page_exceeds_context")
        candidate = [*current, n]
        if (
            current
            and len(canonical(context(lesson, partition, key, candidate)).decode())
            > MAX_CONTEXT
        ):
            scopes.append(current)
            current = []
        current.append(n)
    if current:
        scopes.append(current)
    if not scopes:
        raise ValueError("theme_allocation_empty_original_window")
    return {
        "policy": (
            TEMPORAL_POLICY
            if partition["policy"] == TEMPORAL_EVIDENCE_POLICY
            else POLICY
        ),
        "video": partition["video"],
        "section": partition["section"],
        "lesson_sha256": partition["lesson_sha256"],
        "partition": key,
        "maximum_context_characters": MAX_CONTEXT,
        "scopes": scopes,
        "all_evidence_pages_allocated": True,
        "semantic_boundary_verified": False,
        "topic_coverage_verified": False,
        "source_deletion_allowed": False,
    }


def create(store, partition_id):
    with schema_lease(store.root / "wiki-allocation-context.lock", timeout=0):
        store.checkpoint()
        partition = read_partition(store.root, partition_id)
        lesson = Library(store.root).lesson(partition["video"])
        value = recipe(lesson, partition, partition_id)
        key = digest(canonical(value))[:32]
        folder = store.root / "wiki/allocation-contexts" / key
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "manifest.json"
        if path.exists():
            if json.loads(path.read_bytes()) != value:
                raise ValueError("theme_allocation_context_manifest_changed")
        else:
            atomic_json(path, value)
        return {
            "allocation": key,
            "video": value["video"],
            "section": value["section"],
            "lesson_sha256": value["lesson_sha256"],
            "partition": partition_id,
            "scopes": len(value["scopes"]),
            "pages": len(partition["pages"]),
        }


def read(root, key):
    if not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{32}", key):
        raise ValueError("theme_allocation_invalid_context_id")
    root = Path(root)
    path = root / "wiki/allocation-contexts" / key / "manifest.json"
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("theme_allocation_context_outside_archive")
    value = json.loads(path.read_bytes())
    if digest(canonical(value))[:32] != key or value["policy"] not in {
        POLICY,
        TEMPORAL_POLICY,
    }:
        raise ValueError("theme_allocation_context_manifest_changed")
    partition = read_partition(root, value["partition"])
    lesson = Library(root).lesson(partition["video"])
    if value != recipe(lesson, partition, value["partition"]):
        raise ValueError("theme_allocation_contexts_incomplete_or_changed")
    return value, partition, lesson


def scope(root, key, number, video):
    value, partition, lesson = read(root, key)
    if (
        value["video"] != video
        or type(number) is not int
        or not 0 <= number < len(value["scopes"])
    ):
        raise ValueError("theme_allocation_invalid_scope")
    numbers = value["scopes"][number]
    return {
        "allocation": key,
        "number": number,
        "total": len(value["scopes"]),
        "context": context(lesson, partition, value["partition"], numbers),
        "selections": {
            f"page-{n}": {
                "video": video,
                "sections": [value["section"]],
                "evidence_partition": value["partition"],
                "evidence_page": n,
            }
            for n in numbers
        },
    }
