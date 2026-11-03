"""Private, lossless whole-lesson inputs for independently organized articles.

These packets are internal evidence, never a reader payload. Bounded pages are
inference scheduling units, not proposed article chapters or teaching order.
"""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from pathlib import Path

from .course_knowledge import atomic_json, schema_lease
from .course_library import Library, canonical

POLICY = "textbook-complete-lesson-private-evidence-v1"
MAX_PIECE = 1800
MAX_PAGE_TEXT = 4800
MAX_PAGE_JSON = 12500


def sha(data):
    return hashlib.sha256(data).hexdigest()


def checked_sha(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError("textbook_invalid_digest")
    return value


def notebook_sources(raw, *, archive_sha256, member):
    """Retain complete cell sources and available textual execution outputs."""
    value = json.loads(raw)
    if value.get("nbformat") != 4 or not isinstance(value.get("cells"), list):
        raise ValueError("textbook_unsupported_notebook")
    sources = []
    for index, cell in enumerate(value["cells"]):
        if cell.get("cell_type") not in {"markdown", "code", "raw"}:
            raise ValueError("textbook_unknown_notebook_cell_type")
        parts = [("source", cell.get("source", []))]
        for number, output in enumerate(cell.get("outputs", [])):
            if "text" in output:
                parts.append((f"output-{number}-stream", output["text"]))
            for mime in ("text/plain", "text/markdown", "text/html"):
                if mime in output.get("data", {}):
                    parts.append((f"output-{number}-{mime}", output["data"][mime]))
        for component, text in parts:
            if isinstance(text, list) and all(isinstance(x, str) for x in text):
                text = "".join(text)
            if not isinstance(text, str):
                raise ValueError("textbook_invalid_notebook_text")
            sources.append(
                {
                    "kind": "document",
                    "text": text,
                    "origin": {
                        "archive_sha256": checked_sha(archive_sha256),
                        "member": member,
                        "member_sha256": sha(raw),
                        "cell": index,
                        "cell_type": cell["cell_type"],
                        "component": component,
                    },
                }
            )
    return sources


def split_sources(originals):
    """Preserve whitespace, empty records and exact offsets; never truncate."""
    result = {}
    for number, original in enumerate(originals):
        if not isinstance(original.get("text"), str):
            raise ValueError("textbook_missing_original_text")
        text = original["text"]
        for part, start in enumerate(range(0, len(text), MAX_PIECE) if text else [0]):
            end = min(start + MAX_PIECE, len(text))
            result[f"E{number:05d}_{part:03d}"] = {
                **original,
                "text": text[start:end],
                "text_origin": {
                    "original": number,
                    "source_sha256": sha(canonical(original)),
                    "text_sha256": sha(text.encode("utf-8")),
                    "start": start,
                    "end": end,
                    "characters": len(text),
                },
            }
    return result


def pages_for(sources):
    pages, current = [], []

    def fits(refs):
        rows = {ref: sources[ref] for ref in refs}
        return (
            sum(len(row["text"]) for row in rows.values()) <= MAX_PAGE_TEXT
            and len(canonical(rows).decode("utf-8")) <= MAX_PAGE_JSON
        )

    for ref in sources:
        if not fits([ref]):
            raise ValueError("textbook_single_evidence_metadata_exceeds_budget")
        if current and not fits([*current, ref]):
            pages.append(current)
            current = []
        current.append(ref)
    if current:
        pages.append(current)
    if not pages:
        raise ValueError("textbook_empty_lesson_evidence")
    return pages


def validate_inventory(value):
    """Prove exact reconstruction and once-only scheduling of every character."""
    if value.get("policy") != POLICY or value.get("reader_payload") is not False:
        raise ValueError("textbook_unknown_private_inventory")
    originals, sources = value["originals"], value["sources"]
    if not originals or split_sources(originals) != sources:
        raise ValueError("textbook_original_reconstruction_failed")
    if pages_for(sources) != value["pages"]:
        raise ValueError("textbook_page_allocation_changed")
    allocated = [ref for page in value["pages"] for ref in page]
    if allocated != list(sources):
        raise ValueError("textbook_evidence_omitted_or_repeated")
    if value["original_characters"] != sum(len(s["text"]) for s in originals):
        raise ValueError("textbook_character_count_changed")


def inventory(lesson, *, lesson_artifact, supplements=(), attachment_scope=None):
    originals = []
    for ref, source in sorted(
        lesson["evidence"].items(),
        key=lambda item: (item[1]["start"], item[1]["end"], item[0]),
    ):
        originals.append(
            {
                "kind": source["kind"],
                "text": source["text"],
                "origin": {
                    "lesson": lesson["id"],
                    "evidence": ref,
                    "start": source["start"],
                    "end": source["end"],
                    "image_sha256": source.get("image_sha256"),
                },
            }
        )
    originals.extend(supplements)
    sources = split_sources(originals)
    value = {
        "policy": POLICY,
        "lesson": lesson["id"],
        "lesson_artifact": checked_sha(lesson_artifact),
        "source_export_sha256": checked_sha(lesson["source_export_sha256"]),
        "originals": originals,
        "original_characters": sum(len(s["text"]) for s in originals),
        "sources": sources,
        "pages": pages_for(sources),
        "attachment_scope": attachment_scope
        if attachment_scope is not None
        else {"association_verified": False, "unresolved": True},
        "reader_payload": False,
        "scheduling_pages_are_article_chapters": False,
        "whole_lesson_knowledge_coverage_verified": False,
        "semantic_accuracy_verified": False,
        "source_deletion_allowed": False,
    }
    validate_inventory(value)
    return value


def prepare(root, lesson_id, *, notebook_selections=()):
    root = Path(root).resolve()
    library = Library(root)
    catalog = library.catalog()
    row = next(r for r in catalog["lessons"] if r["id"] == lesson_id)
    lesson = library.lesson(lesson_id)
    original = root / "lessons" / lesson_id / "evidence.json"
    if sha(original.read_bytes()) != lesson["source_export_sha256"]:
        raise ValueError("textbook_source_export_changed")
    manifest = json.loads((root / "resources-manifest.json").read_bytes())
    supplements, selected, checked = [], [], set()
    for selection in notebook_selections:
        digest = checked_sha(selection["archive_sha256"])
        member = selection["member"]
        if not isinstance(member, str) or not member.endswith(".ipynb"):
            raise ValueError("textbook_invalid_notebook_selection")
        if (digest, member) in checked:
            raise ValueError("textbook_duplicate_notebook_selection")
        checked.add((digest, member))
        candidates = [r for r in manifest["completed"] if r["sha256"] == digest]
        if not candidates:
            raise ValueError("textbook_notebook_archive_not_retained")
        path = (root / candidates[0]["object"]).resolve()
        if not path.is_relative_to(root) or sha(path.read_bytes()) != digest:
            raise ValueError("textbook_notebook_archive_identity_failed")
        with zipfile.ZipFile(path) as archive:
            if archive.getinfo(member).file_size > 16 * 1024**2:
                raise ValueError("textbook_notebook_member_too_large")
            raw = archive.read(member)
        supplements.extend(notebook_sources(raw, archive_sha256=digest, member=member))
        selected.append(
            {
                "archive_sha256": digest,
                "member": member,
                "member_sha256": sha(raw),
                "association_basis": selection["association_basis"],
                "association_verified": False,
            }
        )
    value = inventory(
        lesson,
        lesson_artifact=row["artifact"],
        supplements=supplements,
        attachment_scope={
            "selected_notebooks": selected,
            "association_verified": False,
            "unresolved": True,
            "remaining_work": [
                "verify_lesson_to_courseware_semantic_association",
                "reconcile_relevant_notebook_visual_evidence",
                "retain_other_course_attachments_in_full_archive_scope",
            ],
        },
    )
    key = sha(canonical(value))
    folder = root / "wiki" / "textbook-evidence" / key
    folder.mkdir(parents=True, exist_ok=True)
    with schema_lease(root / "textbook-evidence-prepare.lock", timeout=0):
        target = folder / "manifest.json"
        if target.exists():
            if json.loads(target.read_bytes()) != value:
                raise ValueError("textbook_inventory_checkpoint_changed")
        else:
            atomic_json(target, value)
    return key, value


def load(root, key):
    checked_sha(key)
    path = Path(root) / "wiki" / "textbook-evidence" / key / "manifest.json"
    value = json.loads(path.read_bytes())
    if sha(canonical(value)) != key:
        raise ValueError("textbook_inventory_digest_mismatch")
    validate_inventory(value)
    return value
