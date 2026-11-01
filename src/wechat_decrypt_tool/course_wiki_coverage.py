"""Whole-inventory Wiki ledger, without model calls or copying course prose.

An extracted course, a draft citation and a reviewed topic are separate facts.
This receipt deliberately cannot certify semantic coverage or video deletion.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter, defaultdict
from pathlib import Path

from .course_knowledge import atomic_json, schema_lease
from .course_library import Library, canonical, natural_key, source_folders
from .course_lesson_exports import digest
from .course_video_visual_audit import ReadOnlyArchive
from .course_wiki import Wiki, versioned_articles

POLICY = "wiki-whole-source-ledger-v1"


def path_key(path):
    return str(Path(path).resolve()).casefold()


def citations(article):
    found = set(article["overview"]["citations"])
    for value in article["prerequisites"] + article["pitfalls"] + article["quiz"]:
        found.update(value["citations"])
    for section in article["sections"]:
        for paragraph in section["paragraphs"]:
            found.update(paragraph["citations"])
        found.update(section["code_citations"])
        if section["image"]:
            found.add(section["image"])
    return found


def build(store):
    with schema_lease(store.root / "wiki-coverage.lock", timeout=0):
        return _build(store)


def _build(store):
    started = time.time()
    frozen = {}
    absent = []

    def load(relative, *, optional=False):
        path = store.root / relative
        if not path.exists() and optional:
            absent.append(relative)
            return None
        raw = path.read_bytes()
        frozen[path] = raw
        return json.loads(raw)

    inventory = load("inventory-unique.json")
    catalog = load("library/catalog.json")
    resources = load("resources-manifest.json", optional=True)
    extraction = load("courseware-index-report.json", optional=True)
    if catalog["inventory_sha256"] != digest(
        frozen[store.root / "inventory-unique.json"]
    ):
        raise ValueError("source_catalog_inventory_changed_republish_sources_first")
    sources = {}
    for item in inventory["videos"]:
        if (
            item.get("error")
            or not item.get("audio_streams")
            or not item.get("duration_seconds")
        ):
            raise ValueError("canonical_audio_inventory_contains_ineligible_source")
        key = digest(str(Path(item["path"]).resolve()).encode())[:24]
        if key in sources:
            raise ValueError("duplicate_canonical_course")
        source_folders(
            item
        )  # Reject paths that cannot be safely displayed as source-relative.
        sources[key] = item
    if len(sources) != inventory["unique_readable_videos"]:
        raise ValueError("canonical_course_count_mismatch")
    if {r["id"] for r in catalog["lessons"]} != set(sources):
        raise ValueError("source_catalog_does_not_account_for_whole_audio_inventory")

    wiki_catalog = load("wiki/catalog.json", optional=True) or {"articles": []}
    article_versions = versioned_articles(wiki_catalog["articles"])
    wiki = Wiki(store.root)
    values = {a["id"]: wiki.article(a["id"]) for a in article_versions}

    store.checkpoint()
    with store.connect() as db:
        db.execute("BEGIN")
        db.execute("PRAGMA temp_store=MEMORY")
        videos = {
            r["id"]: dict(r)
            for r in db.execute(
                "SELECT id,path,size,mtime_ns,duration,status FROM videos"
            )
        }
        segments = defaultdict(set)
        for row in db.execute("SELECT id,video FROM segments"):
            segments[row["video"]].add(row["id"])
        frames = defaultdict(set)
        for row in db.execute("SELECT id,video FROM frames"):
            frames[row["video"]].add(-row["id"])
        notes = {
            r["video"]: r["n"]
            for r in db.execute(
                "SELECT video,count(*) n FROM notes WHERE status='ready' GROUP BY video"
            )
        }
        documents = {
            path_key(r["path"]): r["n"]
            for r in db.execute(
                "SELECT path,count(*) n FROM documents INDEXED BY document_source WHERE active=1 GROUP BY path"
            )
        }
        from .course_wiki_documents import current_matches

        document_current = {
            (article_id, ref): current_matches(
                db, source, value["packet"]["bindings"][ref]
            )
            for article_id, value in values.items()
            for ref, source in value["packet"]["sources"].items()
            if source["kind"] == "document"
        }
    observed = time.time()

    library = Library(store.root)
    source_notes = set()
    for row in catalog["lessons"]:
        if row["state"] == "draft_available":
            # Object integrity is a separate check from a catalog label.
            library.lesson(row["id"])
            source_notes.add(row["id"])

    planned = defaultdict(set)
    for path in (store.root / "wiki" / "jobs").glob("*/manifest.json"):
        store.checkpoint()
        manifest = json.loads(path.read_text("utf-8"))
        recipe = manifest["recipe"]
        recipe_hash = digest(canonical(recipe))
        if (
            manifest["recipe_sha256"] != recipe_hash
            or manifest["id"] != recipe_hash[:32]
            or path.parent.name != manifest["id"]
        ):
            raise ValueError("wiki_planned_source_manifest_changed")
        for ref, evidence in recipe["packet"]["sources"].items():
            if evidence.get("video") in sources:
                planned[evidence["video"]].add(ref)
    cited_speech, cited_frames, article_ids = (
        defaultdict(set),
        defaultdict(set),
        defaultdict(set),
    )
    issues = []
    cited_documents = defaultdict(set)
    retained_sources = (
        {r["relative_path"]: r["sha256"] for r in resources["completed"]}
        if resources
        else {}
    )
    for article in article_versions:
        store.checkpoint()
        value = values[article["id"]]
        if article.get("superseded_by"):
            continue
        for ref in citations(value["article"]):
            source = value["packet"]["sources"][ref]
            if source["kind"] == "document":
                binding = value["packet"]["bindings"][ref]
                cited_documents[binding["relative_path"]].add((article["id"], ref))
                if (
                    retained_sources.get(binding["relative_path"])
                    != binding["retained_source"]["container_sha256"]
                ):
                    issues.append(
                        {
                            "article": article["id"],
                            "source": ref,
                            "code": "frozen_document_container_differs_from_current_manifest",
                        }
                    )
                if not document_current[(article["id"], ref)]:
                    issues.append(
                        {
                            "article": article["id"],
                            "source": ref,
                            "code": "frozen_document_differs_from_current_revision",
                        }
                    )
                continue
            key, number = ref.split(":", 1)
            identity = str(source["id"])
            if "text_origin" in source:
                origin = source["text_origin"]
                identity += f"@{origin['start']}-{origin['end']}"
            if (
                not re.fullmatch(r"[a-f0-9]{24}", key)
                or number != identity
                or source["video"] != key
            ):
                raise ValueError("article_source_identity_mismatch")
            if key not in sources:
                issues.append(
                    {
                        "article": article["id"],
                        "code": "citation_outside_current_course_inventory",
                    }
                )
                continue
            article_ids[key].add(article["id"])
            target = cited_speech if source["kind"] == "speech" else cited_frames
            known = segments[key] if source["kind"] == "speech" else frames[key]
            if source["id"] not in known:
                issues.append(
                    {
                        "article": article["id"],
                        "code": "citation_not_in_current_database_snapshot",
                        "source": ref,
                    }
                )
            else:
                target[key].add(source["id"])

    courses = []
    for key, item in sources.items():
        row = videos.get(key)
        identity_matches = row is not None and (
            path_key(row["path"]),
            row["size"],
            row["mtime_ns"],
            row["duration"],
        ) == (
            path_key(item["path"]),
            item["bytes"],
            item["mtime_ns"],
            item["duration_seconds"],
        )
        courses.append(
            {
                "id": key,
                "title": item["title"],
                "relative_path": item["relative_path"],
                "collection": (source_folders(item) or ["课程"])[0],
                "database_state": row["status"] if row else "not_registered",
                "source_identity_matches_inventory": identity_matches,
                "source_notes_published": key in source_notes,
                "extracted_speech_segments": len(segments[key]),
                "ready_note_windows": notes.get(key, 0),
                "planned_reference_count": len(planned[key]),
                "draft_cited_speech_segments": len(cited_speech[key]),
                "draft_cited_frames": len(cited_frames[key]),
                "unreferenced_extracted_speech_segments": len(
                    segments[key] - cited_speech[key]
                ),
                "draft_articles": sorted(article_ids[key]),
                "semantic_coverage_verified": False,
            }
        )
    courses.sort(key=lambda r: natural_key(r["relative_path"]))

    attachments = []
    if resources is not None:
        for row in resources["completed"]:
            relative = row["relative_path"]
            source_folders({"relative_path": relative})
            attachments.append(
                {
                    "relative_path": relative,
                    "raw_sha256": row["sha256"],
                    "retained_in_manifest": True,
                    "silent_demo": bool(row.get("silent_demo")),
                    "indexed_chunks": documents.get(
                        path_key(Path(resources["source"]) / relative), 0
                    ),
                    "wiki_document_citation_supported": bool(cited_documents[relative]),
                    "draft_cited_chunks": len(
                        {ref for _, ref in cited_documents[relative]}
                    ),
                    "draft_articles": sorted({a for a, _ in cited_documents[relative]}),
                    "complete_document_coverage_verified": False,
                }
            )
    gaps = [
        {"code": "semantic_review_not_complete"},
        {"code": "selected_document_chunks_do_not_prove_full_courseware_coverage"},
        {"code": "draft_citation_counts_do_not_prove_topic_coverage"},
        {"code": "backup_and_restore_acceptance_not_proven_by_this_ledger"},
    ]
    if resources is None:
        gaps.append({"code": "retained_resource_manifest_missing"})
    result = {
        "version": 1,
        "policy": POLICY,
        "started": started,
        "database_snapshot_observed": observed,
        "updated": time.time(),
        "courses": courses,
        "attachments": attachments,
        "scope": {
            "canonical_audio_courses": len(sources),
            "original_video_paths": inventory.get("original_video_count"),
            "verified_duplicate_paths": inventory.get("verified_duplicate_paths"),
            "excluded_no_audio_sources": len(
                inventory.get("excluded_without_audio", [])
            ),
            "excluded_source_errors": len(inventory.get("excluded_errors", [])),
            "retained_resource_paths": len(attachments),
            "pending_resource_paths": len(resources["pending"]) if resources else None,
        },
        "summary": {
            "draft_cited_document_chunks": len(
                {ref for rows in cited_documents.values() for _, ref in rows}
            ),
            "resource_paths_with_draft_citations": sum(
                bool(rows) for rows in cited_documents.values()
            ),
            "registered_audio_courses": sum(
                r["database_state"] != "not_registered" for r in courses
            ),
            "transcribed_courses": sum(
                r["database_state"] in ("transcribed", "transcribed_with_gaps")
                for r in courses
            ),
            "source_notes_published": len(source_notes),
            "draft_articles": sum(not a.get("superseded_by") for a in article_versions),
            "draft_article_versions": len(article_versions),
            "courses_with_draft_citations": sum(
                bool(r["draft_articles"]) for r in courses
            ),
            "courses_without_draft_citations": sum(
                not r["draft_articles"] for r in courses
            ),
            "courses_with_planned_references": sum(
                bool(r["planned_reference_count"]) for r in courses
            ),
            "source_identity_mismatches": sum(
                r["database_state"] != "not_registered"
                and not r["source_identity_matches_inventory"]
                for r in courses
            ),
            "active_document_chunks": sum(documents.values()),
            "indexed_document_source_paths": len(documents),
            "retained_paths_with_some_indexed_chunks": sum(
                r["indexed_chunks"] > 0 for r in attachments
            ),
        },
        "pending_resources": resources["pending"] if resources else [],
        "recorded_index_findings": {
            "errors": extraction.get("errors", []) if extraction else [],
            "skipped": extraction.get("skipped", []) if extraction else [],
            "pdf_warnings": extraction.get("pdf_warnings", []) if extraction else [],
            "historical_findings_require_current_resolution_check": True,
        },
        "no_audio_sources": inventory.get("excluded_without_audio", []),
        "excluded_source_errors": inventory.get("excluded_errors", []),
        "database_states": dict(Counter(r["database_state"] for r in courses)),
        "issues": issues,
        "gaps": gaps,
        "input_sha256": {
            str(p.relative_to(store.root)): digest(raw) for p, raw in frozen.items()
        },
        "observed_absent_inputs": absent,
        "source_bytes_reverified": False,
        "semantic_accuracy_verified": False,
        "full_source_coverage_verified": False,
        "source_deletion_allowed": False,
    }
    store.checkpoint()
    if any(path.read_bytes() != raw for path, raw in frozen.items()) or any(
        (store.root / relative).exists() for relative in absent
    ):
        raise ValueError("coverage_inputs_changed_during_snapshot_rebuild")
    (store.root / "wiki").mkdir(exist_ok=True)
    atomic_json(store.root / "wiki" / "coverage.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    value = build(ReadOnlyArchive(args.root))
    print(
        json.dumps(
            {
                "scope": value["scope"],
                "summary": value["summary"],
                "source_deletion_allowed": False,
            }
        )
    )


if __name__ == "__main__":
    main()
