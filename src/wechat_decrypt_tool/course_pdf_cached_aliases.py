"""Reuse byte-verified committed PDF OCR for another retained source occurrence.

This is a cache-only operation. It cannot invoke rendering or a model, supersede
an existing target, resume a PDF job, or certify semantic/visual completeness.
"""

from __future__ import annotations

import hashlib
import json

from .course_attachments import resolve_retained_version

PREFIX = "页面转录与图示解读（模型草稿，需对照页面复核）\n"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def retained(root, name):
    path = (root / name).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("pdf_alias_artifact_outside_or_missing")
    return path


def prepare(store, proof):
    """Recheck every donor, target and retained parent without changing the DB."""
    root = store.root.resolve()
    if not (root / "resources-manifest.json").is_file():
        raise ValueError("pdf_alias_retained_manifest_required")
    if (
        proof.get("state")
        != "existing_pdf_ocr_cached_alias_donors_byte_and_committed_text_verified"
    ):
        raise ValueError("pdf_alias_donor_proof_required")
    donors = {}
    with store.connect() as db:
        for row in db.execute(
            "SELECT * FROM resource_revisions WHERE extractor LIKE 'pdf-vision-v2/%' "
            "OR extractor LIKE 'pdf-vision-v3-tiled-v1/%'"
        ):
            donors[
                sha(row["path"].encode()),
                sha(row["source_key"].encode()),
                row["revision"],
            ] = dict(row)
    versions, seen, checked_files, checked_donors = [], set(), {}, {}
    skipped = 0

    def checked(name, expected):
        path = retained(root, name)
        if path not in checked_files:
            checked_files[path] = (sha(path.read_bytes()), path.stat().st_size)
        if checked_files[path][0] != expected:
            raise ValueError("pdf_alias_retained_bytes_changed")
        return path

    for item in proof["rows"]:
        store.checkpoint()
        path, parent, raw, page = (
            item["path"],
            item["parent_source_key"],
            item["parent_raw_digest"],
            item["page"],
        )
        if type(page) is not int or page < 1:
            raise ValueError("pdf_alias_invalid_page")
        key = "visual " + parent + " page " + str(page)
        identity = (path, key)
        if identity in seen:
            raise ValueError("pdf_alias_duplicate_target")
        seen.add(identity)
        with store.connect() as db:
            exists = (
                db.execute(
                    "SELECT 1 FROM resource_revisions WHERE path=? AND source_key=?",
                    identity,
                ).fetchone()
                or db.execute(
                    "SELECT 1 FROM documents WHERE path=? AND source_key=? LIMIT 1",
                    identity,
                ).fetchone()
            )
        if exists:
            skipped += 1
            continue
        donor = item["donor"]
        donor_id = (
            donor["donor_path_sha256"],
            donor["donor_source_key_sha256"],
            donor["donor_revision"],
        )
        row = donors.get(donor_id)
        if (
            row is None
            or row["raw_digest"] != raw
            or row["extractor"] != donor["extractor"]
            or sha(row["metadata"].encode()) != donor["metadata_sha256"]
        ):
            raise ValueError("pdf_alias_committed_donor_changed")
        if donor_id not in checked_donors:
            metadata = json.loads(row["metadata"])
            image = checked(donor["image"], donor["image_sha256"])
            cache = checked(donor["cache"], donor["cache_sha256"])
            value = json.loads(cache.read_bytes())
            if (
                metadata.get("parent_raw_digest") != raw
                or metadata.get("page") != page
                or retained(root, metadata["image"]) != image
                or any(
                    value.get(k) != metadata.get(k)
                    for k in ("image_digest", "model", "model_digest", "tile_coverage")
                )
                or metadata.get("image_digest") != donor["image_sha256"]
            ):
                raise ValueError("pdf_alias_cache_binding_changed")
            text = PREFIX + value["text"]
            if (
                not value["text"].strip()
                or sha(value["text"].encode()) != donor["cache_text_sha256"]
                or sha(text.encode()) != donor["committed_text_sha256"]
            ):
                raise ValueError("pdf_alias_cached_text_changed")
            with store.connect() as db:
                chunks = list(
                    db.execute(
                        "SELECT locator,text,active FROM documents WHERE path=? AND source_key=? AND digest=?",
                        (row["path"], row["source_key"], row["revision"]),
                    )
                )
            expected = {
                row["source_key"] + " chars " + str(i): text[i : i + 3000]
                for i in range(0, len(text), 3000)
            }
            if (
                not chunks
                or any(x["active"] != 1 for x in chunks)
                or {x["locator"]: x["text"] for x in chunks} != expected
            ):
                raise ValueError("pdf_alias_committed_text_changed")
            checked_donors[donor_id] = (metadata, text)
        metadata, text = checked_donors[donor_id]
        if metadata["page"] != page or metadata["parent_raw_digest"] != raw:
            raise ValueError("pdf_alias_donor_page_mismatch")
        result = resolve_retained_version(store, path, parent)
        source = result["path"].resolve()
        checked(str(source), raw)
        metadata = {
            **metadata,
            "parent_source_key": parent,
            "cached_alias_donor": {
                k: donor[k]
                for k in (
                    "donor_path_sha256",
                    "donor_source_key_sha256",
                    "donor_revision",
                    "cache_sha256",
                )
            },
        }
        versions.append(
            {
                "path": path,
                "source_key": key,
                "raw_digest": raw,
                "extractor": row["extractor"],
                "metadata": metadata,
                "require_absent": True,
                "chunks": [
                    (key + " chars " + str(i), text[i : i + 3000])
                    for i in range(0, len(text), 3000)
                ],
            }
        )
    return {
        "versions": versions,
        "skipped_existing": skipped,
        "checked_files": {
            str(p): {"sha256": h, "bytes": size}
            for p, (h, size) in checked_files.items()
        },
        "donors_verified": len(checked_donors),
    }


def apply(store, prepared):
    """Write one atomic absent-target batch; retain every existing revision."""
    store.checkpoint()
    for name, row in prepared["checked_files"].items():
        path = retained(store.root.resolve(), name)
        if sha(path.read_bytes()) != row["sha256"]:
            raise ValueError("pdf_alias_prepared_file_changed")
    store.save_document_versions(prepared["versions"])
    return {
        "state": "committed_pdf_ocr_reused_for_absent_retained_aliases",
        "page_occurrences": len(prepared["versions"]),
        "skipped_existing": prepared["skipped_existing"],
        "donors_verified": prepared["donors_verified"],
        "model_calls": 0,
        "original_course_reads": 0,
        "old_pdf_jobs_resumed": False,
        "semantic_accuracy_verified": False,
        "source_deletion_allowed": False,
    }
