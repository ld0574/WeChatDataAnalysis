"""Index nested course ZIP documents from verified retained objects, without execution."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import time
import zipfile
from pathlib import Path, PurePosixPath

from .course_attachments import (
    MAX_ARCHIVE_CHAIN,
    MAX_NESTED_ZIP_BYTES,
    NESTED_ZIP_BUDGET,
    archive_locator,
    archive_members,
    digest,
    normalized_original,
    open_archive_member,
    safe_member,
)
from .course_documents import (
    DOCUMENT_EXTENSIONS,
    IGNORED_PARTS,
    OFFICE_EXTENSIONS,
    _limit,
    _pages,
    extractor_identity,
)

EXTRA_TEXT = {
    ".jsonl",
    ".tsv",
    ".table",
    ".examples",
    ".tagged",
    ".sh",
    ".xml",
    ".env",
    ".templ",
    ".iml",
}


def index_nested_courseware(store, audit_path, *, limit=0):
    audit = json.loads(Path(audit_path).read_text(encoding="utf-8"))
    manifest = json.loads(
        (store.root / "resources-manifest.json").read_text(encoding="utf-8")
    )
    resources = {
        normalized_original(
            manifest["source"] + "/" + safe_member(row["relative_path"])
        ): row
        for row in manifest["completed"]
    }
    identity = "nested-course-documents-v1/" + extractor_identity()
    targets = audit["nested_archives_not_expanded"]
    completed, errors, skipped, warnings, ocr, visuals = [], [], [], [], [], []
    pending, seen, checked = [], set(), set()
    files = cached = chunks = 0
    context = {}

    class PdfWarnings(logging.Handler):
        def emit(self, record):
            warnings.append({**context, "warning": record.getMessage()[:1000]})

    handler = PdfWarnings(level=logging.WARNING)
    logger = logging.getLogger("pypdf")
    logger.addHandler(handler)

    def progress(stage, **extra):
        store.write_json(
            "nested-courseware-progress.json",
            {
                "stage": stage,
                "pid": __import__("os").getpid(),
                "updated": time.time(),
                "files": files,
                "cached": cached,
                "errors": len(errors),
                "completed_nested_source_occurrences": len(completed),
                "total_nested_source_occurrences": len(targets),
                **extra,
            },
        )

    def flush():
        nonlocal chunks
        if pending:
            store.checkpoint()
            store.save_document_versions(pending)
            pending.clear()
            chunks = 0

    def save(path, chain, raw, suffix):
        nonlocal files, cached, chunks
        key = archive_locator(chain)
        source_digest = hashlib.sha256(raw).hexdigest()
        previous = store.document_revision(path, key, source_digest, identity)
        if previous is not None:
            cached += 1
            warnings.extend(previous.get("warnings", []))
            ocr.extend(previous.get("ocr_pages", []))
            visuals.extend(previous.get("visual_pages", []))
            files += 1
            return
        context.clear()
        context.update(path=path, location=key)
        warning_start = len(warnings)
        entity_chunks, empty, visual = [], [], []
        pages = 0
        for page, text in _pages(raw, suffix, context, store.checkpoint):
            pages += 1
            page_ref = {
                "path": path,
                "location": key + " " + page,
                "kind": suffix.lstrip("."),
            }
            if suffix in OFFICE_EXTENSIONS | {".pdf"} and not text.strip():
                empty.append(page_ref)
            if suffix in OFFICE_EXTENSIONS and (
                not text.strip() or context.get("has_visuals")
            ):
                visual.append(page_ref)
            text = re.sub(
                r"(?i)((?:api_key|password|secret|access_token)\s*[:=]\s*)[\"\'][^\"\'\n]{8,}[\"\']",
                r'\1"[REDACTED]"',
                text,
            )
            entity_chunks.extend(
                (key + " " + page + " chars " + str(n), text[n : n + 3000])
                for n in range(0, len(text), 3000)
            )
        pending.append(
            {
                "path": path,
                "source_key": key,
                "raw_digest": source_digest,
                "extractor": identity,
                "chunks": entity_chunks,
                "metadata": {
                    "pages": pages,
                    "warnings": warnings[warning_start:],
                    "ocr_pages": empty,
                    "visual_pages": visual,
                    "archive_members": list(chain),
                    "nested_archive": True,
                    "raw_source_retained": True,
                },
            }
        )
        chunks += len(entity_chunks)
        files += 1
        ocr.extend(empty)
        visuals.extend(visual)
        if len(pending) >= 100 or chunks >= 500:
            flush()

    def visit(path, stream, chain, budget):
        if len(chain) >= MAX_ARCHIVE_CHAIN:
            raise ValueError("Nested course archive depth exceeds bound; raw retained")
        with zipfile.ZipFile(stream) as archive:
            names = set()
            for entry in archive.infolist():
                store.checkpoint()
                name = safe_member(entry.filename)
                member = PurePosixPath(name)
                if (
                    entry.is_dir()
                    or set(member.parts) & IGNORED_PARTS
                    or member.name.startswith("._")
                ):
                    continue
                key = archive_locator(chain + [name])
                if name in names:
                    errors.append(
                        {
                            "path": path,
                            "source_key": key,
                            "error": "Ambiguous duplicate nested ZIP member",
                        }
                    )
                    continue
                names.add(name)
                if entry.flag_bits & 1:
                    skipped.append(
                        {
                            "path": path,
                            "source_key": key,
                            "reason": "encrypted member; raw container retained",
                        }
                    )
                    continue
                budget[0] += entry.file_size
                if budget[0] > NESTED_ZIP_BUDGET:
                    raise ValueError(
                        "Nested expanded-byte budget exceeded; raw retained"
                    )
                suffix = member.suffix.lower()
                if suffix == ".zip":
                    if entry.file_size > MAX_NESTED_ZIP_BYTES:
                        skipped.append(
                            {
                                "path": path,
                                "source_key": key,
                                "reason": "nested ZIP input size exceeds bound; raw retained",
                            }
                        )
                        continue
                    visit(path, io.BytesIO(archive.read(entry)), chain + [name], budget)
                elif suffix in DOCUMENT_EXTENSIONS | EXTRA_TEXT:
                    if entry.file_size > _limit(suffix):
                        skipped.append(
                            {
                                "path": path,
                                "source_key": key,
                                "bytes": entry.file_size,
                                "reason": "bounded document extraction limit; raw retained",
                            }
                        )
                        continue
                    try:
                        save(path, chain + [name], archive.read(entry), suffix)
                    except InterruptedError:
                        raise
                    except Exception as exc:
                        errors.append(
                            {
                                "path": path,
                                "source_key": key,
                                "error": type(exc).__name__ + ": " + str(exc),
                            }
                        )
                    if files % 100 == 0:
                        progress("indexing_nested_documents")
                else:
                    skipped.append(
                        {
                            "path": path,
                            "source_key": key,
                            "bytes": entry.file_size,
                            "reason": "unsupported document type; raw retained",
                        }
                    )

    def report():
        value = {
            "completed": completed,
            "errors": errors,
            "skipped": skipped,
            "indexed_entities": files,
            "cached_entities": cached,
            "extractor": identity,
            "pdf_warnings": warnings,
            "pdf_pages_needing_ocr": [r for r in ocr if r["kind"] == "pdf"],
            "visual_pages_needing_ocr": visuals,
            "raw_sources_retained": True,
            "requested_nested_source_occurrences": len(targets),
            "limit": limit,
        }
        store.write_json("nested-courseware-report.json", value)
        return value

    try:
        for row in targets:
            if limit and len(completed) >= limit:
                break
            store.checkpoint()
            path = row["path"]
            chain = archive_members(row["source_key"])
            pointer = (path, tuple(chain))
            if pointer in seen:
                continue
            seen.add(pointer)
            progress("reading_nested_archive")
            try:
                resource = resources[normalized_original(path)]
                if resource["sha256"] != row["container_sha256"]:
                    raise RuntimeError(
                        "Nested source differs from audited resource revision"
                    )
                source = (store.root / resource["object"]).resolve()
                if not source.is_relative_to(
                    (store.root / "resources" / "objects").resolve()
                ):
                    raise ValueError("Nested source outside retained resource root")
                before = source.stat()
                state = (str(source), before.st_size, before.st_mtime_ns)
                if before.st_size != resource["bytes"] or (
                    state not in checked and digest(source) != resource["sha256"]
                ):
                    raise RuntimeError(
                        "Nested source object changed; preserve evidence"
                    )
                checked.add(state)
                with open_archive_member(
                    source, chain, checkpoint=store.checkpoint
                ) as (entry, member):
                    if entry.file_size > MAX_NESTED_ZIP_BYTES:
                        raise ValueError("Nested ZIP input exceeds bound; raw retained")
                    raw = member.read(MAX_NESTED_ZIP_BYTES + 1)
                    if len(raw) != entry.file_size:
                        raise RuntimeError("Nested ZIP byte count mismatch")
                visit(path, io.BytesIO(raw), chain, [len(raw)])
                after = source.stat()
                if (before.st_size, before.st_mtime_ns) != (
                    after.st_size,
                    after.st_mtime_ns,
                ):
                    raise RuntimeError("Nested source object changed during indexing")
                flush()
                completed.append({"path": path, "source_key": row["source_key"]})
            except InterruptedError:
                raise
            except Exception as exc:
                errors.append(
                    {
                        "path": path,
                        "source_key": row["source_key"],
                        "error": type(exc).__name__ + ": " + str(exc),
                    }
                )
            report()
        flush()
        result = report()
        progress("finished_with_errors" if errors else "batch_finished")
        return result
    except BaseException as exc:
        # Preserve complete pending entities on pause; no partial file revisions.
        if pending:
            store.save_document_versions(pending)
        report()
        progress(
            "paused"
            if isinstance(exc, (InterruptedError, KeyboardInterrupt))
            else "error",
            reason=str(exc),
        )
        raise
    finally:
        logger.removeHandler(handler)
