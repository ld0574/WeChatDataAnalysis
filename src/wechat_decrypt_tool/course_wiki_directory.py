"""Partition complete source directories without summarizing or dropping rows."""

from __future__ import annotations

import json
import re

from .course_knowledge import atomic_json, schema_lease
from .course_library import Library, canonical
from .course_lesson_exports import digest
from .course_wiki import prepare as freeze

POLICY = "complete-source-directory-pages-v1"
MAX_ROWS = 16
MAX_CHARACTERS = 8000


def descriptors(lesson):
    rows = [{k: s[k] for k in ("id", "title", "summary")} for s in lesson["sections"]]
    if len({r["id"] for r in rows}) != len(rows):
        raise ValueError("theme_directory_duplicate_section")
    return rows


def partition(rows):
    pages, current = [], []
    for row in rows:
        if len(canonical([row]).decode()) > MAX_CHARACTERS:
            raise ValueError("theme_directory_single_complete_row_exceeds_limit")
        if current and (
            len(current) == MAX_ROWS
            or len(canonical([*current, row]).decode()) > MAX_CHARACTERS
        ):
            pages.append(current)
            current = []
        current.append(row)
    if current:
        pages.append(current)
    # Avoid a one/two-row tail when a contiguous rebalance can preserve both
    # complete pages. Unwritable/too-small pages remain explicit, never dropped.
    if len(pages) > 1 and len(pages[-1]) < 3:
        needed = 3 - len(pages[-1])
        previous, tail = pages[-2:]
        candidate = [*previous[-needed:], *tail]
        if (
            len(previous) - needed >= 3
            and len(canonical(candidate).decode()) <= MAX_CHARACTERS
        ):
            pages[-2:] = [previous[:-needed], candidate]
    return [[row["id"] for row in page] for page in pages]


def create(store, video):
    with schema_lease(store.root / "wiki-theme-directory.lock", timeout=0):
        store.checkpoint()
        lesson = Library(store.root).lesson(video)
        rows = descriptors(lesson)
        eligible, ineligible = [], []
        for row in rows:
            store.checkpoint()
            try:
                freeze(
                    store.root,
                    "directory-candidate",
                    [{"video": video, "sections": [row["id"]]}],
                )
            except ValueError:
                ineligible.append(row["id"])
            else:
                eligible.append(row["id"])
        recipe = {
            "policy": POLICY,
            "video": video,
            "lesson_sha256": digest(canonical(lesson)),
            "maximum_rows": MAX_ROWS,
            "maximum_characters": MAX_CHARACTERS,
            "sections": rows,
            "eligible": eligible,
            "ineligible": ineligible,
            "pages": partition(rows),
        }
        key = digest(canonical(recipe))[:32]
        folder = store.root / "wiki/theme-directories" / key
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "manifest.json"
        if path.exists() and json.loads(path.read_bytes()) != recipe:
            raise ValueError("theme_directory_manifest_changed")
        atomic_json(path, recipe)
        return {
            "directory": key,
            "video": video,
            "pages": len(recipe["pages"]),
            "sections": len(rows),
            "lesson_sha256": recipe["lesson_sha256"],
            "all_complete_directory_rows_partitioned": True,
            "topic_coverage_verified": False,
            "source_deletion_allowed": False,
        }


def read(root, key):
    if not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{32}", key):
        raise ValueError("theme_directory_invalid_id")
    value = json.loads(
        (root / "wiki/theme-directories" / key / "manifest.json").read_bytes()
    )
    if (
        digest(canonical(value))[:32] != key
        or value["policy"] != POLICY
        or value["maximum_rows"] != MAX_ROWS
        or value["maximum_characters"] != MAX_CHARACTERS
    ):
        raise ValueError("theme_directory_manifest_changed")
    lesson = Library(root).lesson(value["video"])
    if (
        digest(canonical(lesson)) != value["lesson_sha256"]
        or descriptors(lesson) != value["sections"]
    ):
        raise ValueError("theme_directory_lesson_changed")
    ids = [r["id"] for r in value["sections"]]
    if (
        value["pages"] != partition(value["sections"])
        or [s for page in value["pages"] for s in page] != ids
        or set(value["eligible"]) & set(value["ineligible"])
        or sorted([*value["eligible"], *value["ineligible"]]) != sorted(ids)
    ):
        raise ValueError("theme_directory_incomplete_or_repeated_partition")
    return value


def page(root, key, number, video):
    value = read(root, key)
    if (
        value["video"] != video
        or type(number) is not int
        or not 0 <= number < len(value["pages"])
    ):
        raise ValueError("theme_directory_invalid_page_scope")
    ids = value["pages"][number]
    return {
        "directory": key,
        "number": number,
        "total": len(value["pages"]),
        "sections": [s for s in value["sections"] if s["id"] in ids],
        "eligible": [s for s in value["eligible"] if s in ids],
        "ineligible": [s for s in value["ineligible"] if s in ids],
        "whole_directory_sections": len(value["sections"]),
    }
