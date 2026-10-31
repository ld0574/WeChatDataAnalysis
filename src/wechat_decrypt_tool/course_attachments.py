"""Resolve immutable source downloads from portable resources, never original paths."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath

MAX_MEMBER_BYTES = 512 * 1024**2
CACHE_BUDGET = 2 * 1024**3
RESERVE_BYTES = 32 * 1024**3


class SourceVersionUnavailable(LookupError):
    pass


class AttachmentIntegrityError(RuntimeError):
    pass


class AttachmentCapacityError(RuntimeError):
    pass


def normalized_original(value):
    value = str(value).replace("\\", "/").rstrip("/")
    return (
        value.casefold()
        if re.match(r"^[A-Za-z]:/", value) or value.startswith("//")
        else value
    )


def safe_member(name):
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or path.is_absolute()
        or ".." in path.parts
        or PureWindowsPath(normalized).drive
    ):
        raise AttachmentIntegrityError("Unsafe source archive member")
    return normalized


def source_reference(store, document_id):
    manifest = store.root / "resources-manifest.json"
    if not manifest.exists():
        return None
    with store.connect() as db:
        row = db.execute(
            "SELECT * FROM documents WHERE id=?", (document_id,)
        ).fetchone()
        if row is None:
            return None
        metadata = db.execute(
            "SELECT metadata FROM document_version_metadata WHERE path=? AND source_key=? AND revision=?",
            (row["path"], row["source_key"], row["digest"]),
        ).fetchone()
    if not re.fullmatch("[a-f0-9]{64}", row["raw_digest"] or ""):
        return None
    value = json.loads(manifest.read_text(encoding="utf-8"))
    target = normalized_original(row["path"])
    resource = None
    for candidate in value["completed"]:
        relative = safe_member(candidate["relative_path"])
        if normalized_original(value["source"] + "/" + relative) == target:
            resource = candidate
            break
    if resource is None:
        return None
    if not re.fullmatch("[a-f0-9]{64}", resource["sha256"]):
        raise AttachmentIntegrityError("Invalid retained source digest")
    path = (store.root / resource["object"]).resolve()
    if not path.is_relative_to((store.root / "resources" / "objects").resolve()):
        raise AttachmentIntegrityError("Source object outside retained resources root")
    context = json.loads(metadata[0]) if metadata else {}
    key = context.get("parent_source_key", row["source_key"])
    member = safe_member(key[len("archive ") :]) if key.startswith("archive ") else None
    if member is None and row["raw_digest"] != resource["sha256"]:
        # Never label a current source object as an older document's original.
        return None
    filename = PurePosixPath(
        member or resource["relative_path"].replace("\\", "/")
    ).name
    return {
        "path": path,
        "container_digest": resource["sha256"],
        "container_bytes": resource["bytes"],
        "container_filename": PurePosixPath(
            resource["relative_path"].replace("\\", "/")
        ).name,
        "member": member,
        "expected_digest": row["raw_digest"],
        "filename": filename,
    }


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024**2), b""):
            result.update(block)
    return result.hexdigest()


def resolve_source(store, document_id, *, container=False):
    reference = source_reference(store, document_id)
    if reference is None:
        raise SourceVersionUnavailable(
            "No hash-bound retained source for this document version"
        )
    path = reference["path"]
    if (
        not path.is_file()
        or path.stat().st_size != reference["container_bytes"]
        or digest(path) != reference["container_digest"]
    ):
        raise AttachmentIntegrityError("Retained source object is missing or changed")
    if reference["member"] is None:
        return {"path": path, "filename": reference["filename"]}
    from .course_knowledge import schema_lease

    with schema_lease(store.root / "attachment-cache.lock", timeout=60):
        return resolve_archive_member(store, reference, container=container)


def resolve_archive_member(store, reference, *, container=False):
    path = reference["path"]
    state = path.stat()
    member = reference["member"]
    expected = reference["expected_digest"]
    cache = store.root / "resources" / "derived"
    cache.mkdir(parents=True, exist_ok=True)
    extension = PurePosixPath(member).suffix.lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,12}", extension):
        extension = ".bin"
    target = (cache / (expected + extension)).resolve()
    if not target.is_relative_to(cache.resolve()):
        raise AttachmentIntegrityError("Derived attachment outside cache root")
    with zipfile.ZipFile(path) as archive:
        matches = [
            entry
            for entry in archive.infolist()
            if entry.filename.replace("\\", "/") == member
        ]
        if len(matches) != 1:
            raise SourceVersionUnavailable("Source archive member missing or ambiguous")
        entry = matches[0]
        if entry.is_dir() or entry.flag_bits & 1:
            raise SourceVersionUnavailable(
                "Source archive member is not an accessible file"
            )
        if entry.file_size > MAX_MEMBER_BYTES:
            raise SourceVersionUnavailable(
                "Source archive member exceeds bounded download limit"
            )
        if container:
            member_hash = hashlib.sha256()
            with archive.open(entry) as src:
                for block in iter(lambda: src.read(8 * 1024**2), b""):
                    member_hash.update(block)
            if member_hash.hexdigest() != expected:
                raise SourceVersionUnavailable(
                    "Retained archive member differs from cited revision"
                )
        elif target.exists():
            if target.stat().st_size != entry.file_size or digest(target) != expected:
                raise AttachmentIntegrityError(
                    "Derived attachment cache changed; preserve and investigate"
                )
        else:
            used = sum(p.stat().st_size for p in cache.iterdir() if p.is_file())
            if (
                used + entry.file_size > CACHE_BUDGET
                or shutil.disk_usage(cache).free < RESERVE_BYTES + entry.file_size
            ):
                raise AttachmentCapacityError(
                    "Attachment cache budget or disk reserve reached"
                )
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(
                    dir=cache, prefix="source-", suffix=".part", delete=False
                ) as dst:
                    temporary = Path(dst.name)
                    copied = 0
                    raw_hash = hashlib.sha256()
                    with archive.open(entry) as src:
                        for block in iter(lambda: src.read(8 * 1024**2), b""):
                            copied += len(block)
                            if copied > entry.file_size:
                                raise AttachmentIntegrityError(
                                    "Archive member exceeds declared size"
                                )
                            raw_hash.update(block)
                            dst.write(block)
                if copied != entry.file_size or raw_hash.hexdigest() != expected:
                    raise SourceVersionUnavailable(
                        "Retained archive member differs from cited revision"
                    )
                if digest(temporary) != expected:
                    raise AttachmentIntegrityError("Derived source copy hash mismatch")
                temporary.replace(target)
                temporary = None
            finally:
                if temporary is not None and temporary.exists():
                    temporary.unlink()
    after = path.stat()
    if (state.st_size, state.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise AttachmentIntegrityError(
            "Source archive changed during download preparation"
        )
    return {
        "path": path if container else target,
        "filename": reference["container_filename"]
        if container
        else reference["filename"],
    }
