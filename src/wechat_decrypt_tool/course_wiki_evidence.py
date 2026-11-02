"""Allocate every original character to bounded, explicitly partial source pages."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .course_knowledge import atomic_json, schema_lease
from .course_library import Library, canonical
from .course_lesson_exports import digest

POLICY = "complete-original-evidence-pages-v1"
TEMPORAL_POLICY = "complete-time-ordered-window-original-evidence-pages-v1"
MAX_TEXT = 3200
MAX_JSON = 11500
MAX_PIECE = 2000
RANGE_NOTICE = """
sources 中的 text_origin 标明该段在完整原文内的字符范围及总长度。
本次只提供明确分配的连续原文，未提供部分另有保留，不得猜测或声称已阅读完整窗口。
字符范围不是句意、代码块或知识边界；缺少上下文时如实说明限制，不补全未展示的代码或步骤。
单个分配页及本次文章均不代表原文窗口、主题或课程已完整覆盖。"""


def spans(text):
    """Prefer line/sentence ends; boundaries never assert semantic independence."""
    if not text:
        return [(0, 0)]
    result, start = [], 0
    while start < len(text):
        end = min(start + MAX_PIECE, len(text))
        if end < len(text):
            boundary = max(
                text.rfind(c, start + MAX_PIECE // 2, end) for c in "\n。！？；"
            )
            if boundary >= 0:
                end = boundary + 1
        result.append((start, end))
        start = end
    return result


def materialize(originals, ranges):
    sources = {}
    for spec in ranges:
        ref, start, end = spec["source"], spec["start"], spec["end"]
        original = originals[ref]
        sources[f"{ref}@{start}-{end}"] = {
            **original,
            "text": original["text"][start:end],
            "text_origin": {
                "reference": ref,
                "source_sha256": digest(canonical(original)),
                "text_sha256": digest(original["text"].encode()),
                "characters": len(original["text"]),
                "start": start,
                "end": end,
                "semantic_boundary_verified": False,
            },
        }
    return sources


def fits(sources):
    return (
        sum(len(s["text"]) for s in sources.values()) <= MAX_TEXT
        and len(canonical(sources).decode()) <= MAX_JSON
    )


def partition(originals):
    pages, current = [], []
    for ref, source in originals.items():
        for start, end in spans(source["text"]):
            spec = {"source": ref, "start": start, "end": end}
            if not fits(materialize(originals, [spec])):
                raise ValueError("wiki_evidence_single_range_metadata_exceeds_limit")
            if current and not fits(materialize(originals, [*current, spec])):
                pages.append(current)
                current = []
            current.append(spec)
    if current:
        pages.append(current)
    if not pages:
        raise ValueError("wiki_evidence_empty_section")
    return pages


def recipe(lesson, video, section_id, *, temporal=False):
    if lesson["id"] != video:
        raise ValueError("wiki_evidence_lesson_identity_mismatch")
    sections = [s for s in lesson["sections"] if s["id"] == section_id]
    if len(sections) != 1:
        raise ValueError("wiki_evidence_unknown_section")
    section = sections[0]
    ids = list(section["context"])
    ids.extend(
        int(n)
        for n, e in lesson["evidence"].items()
        if e["kind"] == "frame" and section["start"] <= e["start"] < section["end"]
    )
    if temporal:
        # Context references are not a licence to omit other retained speech.
        # Include complete overlapping speech records, then order raw evidence
        # by time instead of putting all speech before every frame.
        ids.extend(
            int(n)
            for n, source in lesson["evidence"].items()
            if source["kind"] == "speech"
            and source["start"] < section["end"]
            and source["end"] > section["start"]
        )
        ids = sorted(
            set(ids),
            key=lambda n: (
                lesson["evidence"][str(n)]["start"],
                0 if lesson["evidence"][str(n)]["kind"] == "speech" else 1,
                lesson["evidence"][str(n)]["end"],
                n,
            ),
        )
    originals = {
        video + ":" + str(n): {
            **lesson["evidence"][str(n)],
            "video": video,
            "title": lesson["title"],
        }
        for n in dict.fromkeys(ids)
    }
    value = {
        "policy": TEMPORAL_POLICY if temporal else POLICY,
        "video": video,
        "section": section_id,
        "lesson_sha256": digest(canonical(lesson)),
        "maximum_text_characters": MAX_TEXT,
        "maximum_json_characters": MAX_JSON,
        "maximum_piece_characters": MAX_PIECE,
        "originals": originals,
        "pages": partition(originals),
        "all_original_characters_allocated": True,
        "semantic_boundary_verified": False,
        "topic_coverage_verified": False,
        "source_deletion_allowed": False,
    }
    if temporal:
        value.update(
            original_order="start_time_speech_before_frame_end_time_id",
            complete_overlapping_speech_records_included=True,
            temporal_proximity_is_not_semantic_alignment=True,
        )
    return value


def create(store, video, section, *, temporal=False):
    with schema_lease(store.root / "wiki-evidence-partition.lock", timeout=0):
        store.checkpoint()
        value = recipe(
            Library(store.root).lesson(video), video, section, temporal=temporal
        )
        key = digest(canonical(value))[:32]
        folder = store.root / "wiki/evidence-partitions" / key
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "manifest.json"
        if path.exists():
            if json.loads(path.read_bytes()) != value:
                raise ValueError("wiki_evidence_manifest_changed")
        else:
            atomic_json(path, value)
        return {
            "partition": key,
            "pages": len(value["pages"]),
            "video": video,
            "section": section,
        }


def read(root, key, *, current=True):
    if not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{32}", key):
        raise ValueError("wiki_evidence_invalid_partition_id")
    root = Path(root)
    path = root / "wiki/evidence-partitions" / key / "manifest.json"
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("wiki_evidence_partition_outside_archive")
    value = json.loads(path.read_bytes())
    if digest(canonical(value))[:32] != key or value["policy"] not in {
        POLICY,
        TEMPORAL_POLICY,
    }:
        raise ValueError("wiki_evidence_manifest_changed")
    library = Library(root)
    lesson = (
        library.lesson(value["video"])
        if current
        else library.object(value["lesson_sha256"])
    )
    if digest(canonical(lesson)) != value["lesson_sha256"]:
        raise ValueError("wiki_evidence_original_lesson_changed")
    if (
        recipe(
            lesson,
            value["video"],
            value["section"],
            temporal=value["policy"] == TEMPORAL_POLICY,
        )
        != value
    ):
        raise ValueError("wiki_evidence_incomplete_or_changed_allocation")
    return value


def page(root, key, number, video, sections):
    value = read(root, key)
    if (
        value["video"] != video
        or sections != [value["section"]]
        or type(number) is not int
        or not 0 <= number < len(value["pages"])
    ):
        raise ValueError("wiki_evidence_invalid_page_scope")
    return materialize(value["originals"], value["pages"][number])


def validate(root, packet):
    expected = {}
    for key, pages in packet.get("evidence_partitions", {}).items():
        value = read(root, key, current=False)
        lesson = Library(root).object(value["lesson_sha256"])
        if packet["bindings"].get(value["video"]) != {
            "title": lesson["title"],
            "artifact": value["lesson_sha256"],
            "source_export_sha256": lesson["source_export_sha256"],
        }:
            raise ValueError("wiki_evidence_frozen_lesson_binding_changed")
        if (
            not isinstance(pages, list)
            or not pages
            or any(
                type(n) is not int or not 0 <= n < len(value["pages"]) for n in pages
            )
            or sorted(set(pages)) != pages
        ):
            raise ValueError("wiki_evidence_invalid_bound_pages")
        for n in pages:
            for ref, source in materialize(
                value["originals"], value["pages"][n]
            ).items():
                if ref in expected and expected[ref] != source:
                    raise ValueError("wiki_evidence_bound_sources_conflict")
                expected[ref] = source
    actual = {r: s for r, s in packet["sources"].items() if "text_origin" in s}
    if actual != expected:
        raise ValueError("wiki_evidence_frozen_ranges_changed")
