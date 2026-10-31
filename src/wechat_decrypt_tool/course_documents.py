"""Bounded, resumable extraction of retained course resources. Never executes code."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import zipfile
from importlib.metadata import version
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree

TEXT_EXTENSIONS = {
    ".py",
    ".md",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".ts",
    ".js",
    ".sql",
    ".html",
    ".css",
    ".ipynb",
    ".csv",
}
OFFICE_EXTENSIONS = {".pptx", ".docx"}
DOCUMENT_EXTENSIONS = TEXT_EXTENSIONS | OFFICE_EXTENSIONS | {".pdf"}
IGNORED_PARTS = {
    "node_modules",
    "site-packages",
    ".git",
    "__pycache__",
    "venv",
    ".venv",
    ".ipynb_checkpoints",
    "__MACOSX",
}
TEXT_LIMIT = 16 * 1024**2
PDF_LIMIT = 128 * 1024**2
OFFICE_LIMIT = 512 * 1024**2
ARCHIVE_BUDGET = 256 * 1024**2
XML_LIMIT = 16 * 1024**2


def extractor_identity():
    # Dependency upgrades can change glyph decoding and must create new evidence.
    return (
        "course-documents-v2/pypdf-"
        + version("pypdf")
        + "/fonttools-"
        + version("fonttools")
    )


def retire_ignored_legacy(store, source, candidate_ids):
    """Retire audited legacy cache rows from search, retaining every historical ID."""
    scope = str(source).replace("\\", "/").rstrip("/").casefold() + "/"
    candidates = list(dict.fromkeys(candidate_ids))
    if any(type(value) is not int or value <= 0 for value in candidates):
        raise ValueError("Legacy repair requires positive integer evidence IDs")
    retired = []
    inactive = 0
    missing = 0
    for offset in range(0, len(candidates), 100):
        store.checkpoint()
        with store.write() as db:
            changed = []
            for key in candidates[offset : offset + 100]:
                row = db.execute(
                    "SELECT id,path,locator,extractor,active FROM documents WHERE id=?",
                    (key,),
                ).fetchone()
                if row is None:
                    missing += 1
                    continue
                if not row["active"]:
                    inactive += 1
                    continue
                if not row["path"].replace("\\", "/").casefold().startswith(scope):
                    raise ValueError(
                        "Legacy repair source outside requested course root"
                    )
                location = row["locator"].removeprefix("archive ").replace("\\", "/")
                parts = set(
                    (row["path"].replace("\\", "/") + "/" + location).split("/")
                )
                if row["extractor"] != "legacy-v1" or not (
                    parts & IGNORED_PARTS or location.split("/")[-1].startswith("._")
                ):
                    raise ValueError(
                        "Refusing to retire a nonignored course evidence row"
                    )
                db.execute("UPDATE documents SET active=0 WHERE id=?", (key,))
                changed.append(key)
            if changed:
                db.execute(
                    "INSERT INTO events(time,kind,data) VALUES(?,?,?)",
                    (
                        time.time(),
                        "retired_ignored_legacy",
                        json.dumps(
                            {
                                "ids": changed,
                                "source": str(source),
                                "rows_and_history_preserved": True,
                            }
                        ),
                    ),
                )
            retired.extend(changed)
    result = {
        "requested": len(candidates),
        "retired": retired,
        "already_inactive": inactive,
        "missing": missing,
        "historical_ids_preserved": True,
        "source_files_deleted": False,
        "updated": time.time(),
    }
    store.write_json("legacy-search-repair-report.json", result)
    return result


def _limit(suffix):
    return (
        OFFICE_LIMIT
        if suffix in OFFICE_EXTENSIONS
        else PDF_LIMIT
        if suffix == ".pdf"
        else TEXT_LIMIT
    )


def _digest(data, checkpoint):
    h = hashlib.sha256()
    if isinstance(data, Path):
        with data.open("rb") as f:
            for block in iter(lambda: f.read(8 * 1024**2), b""):
                checkpoint()
                h.update(block)
    else:
        h.update(data)
    return h.hexdigest()


def _read(data):
    return data.read_bytes() if isinstance(data, Path) else data


def _pages(data, suffix, context, checkpoint):
    if suffix in OFFICE_EXTENSIONS:
        with zipfile.ZipFile(
            data if isinstance(data, Path) else BytesIO(data)
        ) as archive:
            names = (
                [
                    n
                    for n in archive.namelist()
                    if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)
                ]
                if suffix == ".pptx"
                else ["word/document.xml"]
            )
            names.sort(
                key=lambda n: (
                    int(re.search(r"(\d+)\.xml$", n)[1]) if suffix == ".pptx" else 0
                )
            )
            for n, name in enumerate(names):
                checkpoint()
                if archive.getinfo(name).file_size > XML_LIMIT:
                    raise ValueError("Office XML exceeds bounded extraction limit")
                page = "slide " + str(n + 1) if suffix == ".pptx" else "document"
                context["page"] = page
                node = ElementTree.fromstring(archive.read(name))
                context["has_visuals"] = any(
                    x.tag.rsplit("}", 1)[-1] in {"blip", "chart", "cxnSp"}
                    for x in node.iter()
                )
                yield (
                    page,
                    "\n".join(
                        x.text or ""
                        for x in node.iter()
                        if x.tag.rsplit("}", 1)[-1] == "t"
                    ),
                )
    elif suffix == ".pdf":
        from pypdf import PdfReader

        with data.open("rb") if isinstance(data, Path) else BytesIO(data) as stream:
            reader = PdfReader(stream)
            for n, page in enumerate(reader.pages):
                checkpoint()
                if n >= 2000:
                    raise ValueError("PDF exceeds bounded page limit")
                location = "page " + str(n + 1)
                context["page"] = location
                yield location, page.extract_text() or ""
    elif suffix == ".ipynb":
        for n, cell in enumerate(
            json.loads(_read(data).decode("utf-8-sig")).get("cells", [])
        ):
            value = cell.get("source", [])
            yield (
                "cell " + str(n + 1),
                "".join(value) if isinstance(value, list) else str(value),
            )
    else:
        yield "text", _read(data).decode("utf-8-sig", errors="replace")


def index_courseware(
    store, source, *, only_paths=None, report_name="courseware-index-report.json"
):
    source = Path(source).resolve()
    identity = extractor_identity()
    selected = (
        {str(Path(p).resolve()) for p in only_paths} if only_paths is not None else None
    )
    errors = []
    skipped = []
    warnings = []
    ocr_needed = []
    visual_pages = []
    files = 0
    cached = 0
    ignored_members = 0
    context = {}
    pending = []
    pending_chunks = 0

    class PdfWarnings(logging.Handler):
        def emit(self, record):
            warnings.append({**context, "warning": record.getMessage()[:1000]})

    handler = PdfWarnings(level=logging.WARNING)
    logger = logging.getLogger("pypdf")
    logger.addHandler(handler)

    def flush():
        nonlocal pending_chunks
        if pending:
            store.checkpoint()
            store.save_document_versions(pending)
            pending.clear()
            pending_chunks = 0

    def save(path, key, data, suffix):
        nonlocal cached, pending_chunks
        store.checkpoint()
        raw = _digest(data, store.checkpoint)
        saved = store.document_revision(path, key, raw, identity)
        if saved is not None:
            cached += 1
            warnings.extend(saved.get("warnings", []))
            ocr_needed.extend(saved.get("ocr_pages", []))
            visual_pages.extend(saved.get("visual_pages", []))
            return
        context.clear()
        context.update(path=str(path), location=key)
        first_warning = len(warnings)
        empty = []
        visuals = []
        chunks = []
        page_count = 0
        for page, text in _pages(data, suffix, context, store.checkpoint):
            page_count += 1
            page_ref = {
                "path": str(path),
                "location": key + " " + page,
                "kind": suffix.lstrip("."),
            }
            if suffix in OFFICE_EXTENSIONS | {".pdf"} and not text.strip():
                empty.append(page_ref)
            if suffix in OFFICE_EXTENSIONS and (
                not text.strip() or context.get("has_visuals")
            ):
                visuals.append(page_ref)
            text = re.sub(
                r"(?i)((?:api_key|password|secret|access_token)\s*[:=]\s*)[\"\'][^\"\'\n]{8,}[\"\']",
                r'\1"[REDACTED]"',
                text,
            )
            chunks.extend(
                (key + " " + page + " chars " + str(start), text[start : start + 3000])
                for start in range(0, len(text), 3000)
            )
        metadata = {
            "pages": page_count,
            "warnings": warnings[first_warning:],
            "ocr_pages": empty,
            "visual_pages": visuals,
        }
        ocr_needed.extend(empty)
        visual_pages.extend(visuals)
        pending.append(
            {
                "path": str(path),
                "source_key": key,
                "raw_digest": raw,
                "extractor": identity,
                "chunks": chunks,
                "metadata": metadata,
            }
        )
        pending_chunks += len(chunks)
        if pending_chunks >= 500 or len(pending) >= 100:
            flush()

    paths = (
        (Path(p) for p in sorted(selected))
        if selected is not None
        else source.rglob("*")
    )
    try:
        for path in paths:
            if (
                not path.is_file()
                or path.is_symlink()
                or not path.resolve().is_relative_to(source)
            ):
                continue
            if any(part in IGNORED_PARTS for part in path.relative_to(source).parts):
                continue
            suffix = path.suffix.lower()
            if suffix not in DOCUMENT_EXTENSIONS | {".zip"}:
                continue
            store.checkpoint()
            store.write_json(
                "courseware-index-progress.json",
                {
                    "stage": "reading",
                    "updated": time.time(),
                    "pid": __import__("os").getpid(),
                    "files": files,
                    "cached_entities": cached,
                    "errors": len(errors),
                    "current": str(path),
                    "extractor": identity,
                },
            )
            stat = path.stat()
            try:
                if suffix in DOCUMENT_EXTENSIONS:
                    if stat.st_size > _limit(suffix):
                        skipped.append(
                            {
                                "path": str(path),
                                "bytes": stat.st_size,
                                "reason": "bounded extraction limit; original retained",
                            }
                        )
                    else:
                        save(path, "", path, suffix)
                        files += 1
                else:
                    used = 0
                    with zipfile.ZipFile(path) as archive:
                        names = set()
                        for member in archive.infolist():
                            store.checkpoint()
                            name = Path(member.filename.replace("\\", "/"))
                            if (
                                name.is_absolute()
                                or ".." in name.parts
                                or any(p in IGNORED_PARTS for p in name.parts)
                                or name.name.startswith("._")
                            ):
                                ignored_members += 1
                                continue
                            extension = name.suffix.lower()
                            if extension not in DOCUMENT_EXTENSIONS:
                                continue
                            if member.filename in names:
                                errors.append(
                                    {
                                        "path": str(path),
                                        "member": member.filename,
                                        "error": "ambiguous duplicate ZIP member name",
                                    }
                                )
                                continue
                            names.add(member.filename)
                            if (
                                member.file_size > _limit(extension)
                                or used + member.file_size > ARCHIVE_BUDGET
                            ):
                                skipped.append(
                                    {
                                        "path": str(path),
                                        "member": member.filename,
                                        "bytes": member.file_size,
                                        "reason": "bounded member/archive limit; original retained",
                                    }
                                )
                                continue
                            used += member.file_size
                            try:
                                save(
                                    path,
                                    "archive " + member.filename,
                                    archive.read(member),
                                    extension,
                                )
                            except InterruptedError:
                                raise
                            except Exception as exc:
                                errors.append(
                                    {
                                        "path": str(path),
                                        "member": member.filename,
                                        "error": type(exc).__name__ + ": " + str(exc),
                                    }
                                )
                    files += 1
                after = path.stat()
                if (stat.st_size, stat.st_mtime_ns) != (
                    after.st_size,
                    after.st_mtime_ns,
                ):
                    raise RuntimeError(
                        "Resource changed during extraction; retain source and retry"
                    )
                flush()
            except InterruptedError:
                raise
            except Exception as exc:
                errors.append(
                    {"path": str(path), "error": type(exc).__name__ + ": " + str(exc)}
                )
            store.write_json("courseware-index-errors.json", errors)
            store.write_json("courseware-index-warnings.json", warnings)
        flush()
    except BaseException as exc:
        store.write_json(
            "courseware-index-progress.json",
            {
                "stage": "paused"
                if isinstance(exc, (InterruptedError, KeyboardInterrupt))
                else "error",
                "updated": time.time(),
                "pid": __import__("os").getpid(),
                "files": files,
                "errors": len(errors),
                "reason": str(exc),
            },
        )
        raise
    finally:
        logger.removeHandler(handler)
    report = {
        "source": str(source),
        "extractor": identity,
        "indexed_resource_files": files,
        "cached_entities": cached,
        "errors": errors,
        "skipped": skipped,
        "ignored_archive_members": ignored_members,
        "pdf_pages_needing_ocr": [r for r in ocr_needed if r["kind"] == "pdf"],
        "visual_pages_needing_ocr": visual_pages
        + [r for r in ocr_needed if r["kind"] == "pdf"],
        "pdf_warnings": warnings,
        "originals_retained": True,
        "partial_selection": selected is not None,
    }
    store.write_json(report_name, report)
    store.write_json(
        "courseware-index-progress.json",
        {
            "stage": "done",
            "updated": time.time(),
            "pid": __import__("os").getpid(),
            "files": files,
            "errors": len(errors),
            "skipped": len(skipped),
            "cached_entities": cached,
            "extractor": identity,
        },
    )
    return report
