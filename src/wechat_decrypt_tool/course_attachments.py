"""Resolve immutable source downloads from portable resources, never original paths."""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import tempfile
import zipfile
from contextlib import ExitStack, contextmanager
from pathlib import Path, PurePosixPath, PureWindowsPath

MAX_MEMBER_BYTES = 512 * 1024**2
CACHE_BUDGET = 2 * 1024**3
RESERVE_BYTES = 32 * 1024**3
MAX_ARCHIVE_CHAIN = 6
MAX_NESTED_ZIP_BYTES = 64 * 1024**2
NESTED_ZIP_BUDGET = 256 * 1024**2


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


def archive_members(locator):
    if locator.startswith("archive-chain "):
        try:
            if len(locator) > 16384:
                raise ValueError()
            members = json.loads(locator[len("archive-chain ") :])
            if (
                not isinstance(members, list)
                or not 2 <= len(members) <= MAX_ARCHIVE_CHAIN
            ):
                raise ValueError()
            if any(not isinstance(name, str) for name in members):
                raise ValueError()
        except ValueError:
            raise AttachmentIntegrityError(
                "Invalid nested archive source locator"
            ) from None
        return [safe_member(name) for name in members]
    if locator.startswith("archive "):
        return [safe_member(locator[len("archive ") :])]
    return []


def archive_locator(members):
    if (
        not isinstance(members, (list, tuple))
        or not 1 <= len(members) <= MAX_ARCHIVE_CHAIN
    ):
        raise AttachmentIntegrityError("Invalid archive source depth")
    members = [safe_member(name) for name in members]
    if len(members) == 1:
        return "archive " + members[0]
    value = "archive-chain " + json.dumps(
        members, ensure_ascii=False, separators=(",", ":")
    )
    archive_members(value)
    return value


@contextmanager
def open_archive_member(path, members, *, checkpoint=lambda: None):
    """Stream a leaf through bounded in-memory ZIP containers; never extract paths."""
    members = archive_members(archive_locator(members))
    with ExitStack() as stack:
        archive = stack.enter_context(zipfile.ZipFile(path))
        expanded = 0
        for index, member in enumerate(members):
            checkpoint()
            matches = [
                entry
                for entry in archive.infolist()
                if entry.filename.replace("\\", "/") == member
            ]
            if len(matches) != 1:
                raise SourceVersionUnavailable(
                    "Source archive member missing or ambiguous"
                )
            entry = matches[0]
            if entry.is_dir() or entry.flag_bits & 1:
                raise SourceVersionUnavailable(
                    "Source archive member is not an accessible file"
                )
            if entry.file_size > MAX_MEMBER_BYTES:
                raise SourceVersionUnavailable(
                    "Source archive member exceeds bounded download limit"
                )
            if index == len(members) - 1:
                yield entry, stack.enter_context(archive.open(entry))
                return
            expanded += entry.file_size
            if entry.file_size > MAX_NESTED_ZIP_BYTES or expanded > NESTED_ZIP_BUDGET:
                raise SourceVersionUnavailable(
                    "Nested archive memory budget exceeded; raw container retained"
                )
            buffer = stack.enter_context(io.BytesIO())
            with archive.open(entry) as src:
                copied = 0
                for block in iter(lambda: src.read(8 * 1024**2), b""):
                    checkpoint()
                    copied += len(block)
                    if copied > entry.file_size:
                        raise AttachmentIntegrityError(
                            "Nested archive exceeds declared size"
                        )
                    buffer.write(block)
            if copied != entry.file_size:
                raise AttachmentIntegrityError("Truncated nested archive member")
            buffer.seek(0)
            archive = stack.enter_context(zipfile.ZipFile(buffer))


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
    context = json.loads(metadata[0]) if metadata else {}
    key = context.get("parent_source_key", row["source_key"])
    return retained_reference(store, row["path"], key, row["raw_digest"])


def retained_reference(store, original_path, source_key, expected_digest):
    manifest = store.root / "resources-manifest.json"
    if not manifest.exists() or not re.fullmatch("[a-f0-9]{64}", expected_digest or ""):
        return None
    value = json.loads(manifest.read_text(encoding="utf-8"))
    target = normalized_original(original_path)
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
    members = archive_members(source_key)
    member = members[-1] if members else None
    if member is None and expected_digest != resource["sha256"]:
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
        "members": members,
        "expected_digest": expected_digest,
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
    return resolve_reference(store, reference, container=container)


def resolve_retained_version(store, original_path, source_key):
    with store.connect() as db:
        row = db.execute(
            "SELECT raw_digest FROM resource_revisions WHERE path=? AND source_key=?",
            (str(original_path), source_key),
        ).fetchone()
    if row is None:
        raise SourceVersionUnavailable("No indexed retained source revision")
    reference = retained_reference(store, original_path, source_key, row[0])
    return resolve_reference(store, reference)


def resolve_reference(store, reference, *, container=False):
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
    with open_archive_member(path, reference.get("members") or [member]) as (
        entry,
        src,
    ):
        if container:
            member_hash = hashlib.sha256()
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
