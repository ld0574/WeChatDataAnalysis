"""Freeze selected courseware chunks and raster evidence without extractor writes.

The packet contains the full selected chunk, not a claim of whole-document
coverage. Resolve archive members through retained objects, never original paths.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
from pathlib import Path

from .course_attachments import (
    archive_members,
    normalized_original,
    open_archive_member,
    retained_reference,
)
from .course_library import canonical
from .course_lesson_exports import digest
from .course_video_visual_audit import ReadOnlyArchive

MAX_IMAGE_BYTES = 16 * 1024**2
IMAGE_TYPES = {
    "PNG": ("png", "image/png"),
    "JPEG": ("jpg", "image/jpeg"),
    "WEBP": ("webp", "image/webp"),
}


def sha(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError("Invalid courseware evidence digest")
    return value


def image_type(data):
    """Accept decoded, bounded static rasters only, irrespective of extension."""
    from PIL import Image

    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Courseware image exceeds bounded read")
    try:
        with Image.open(io.BytesIO(data)) as image:
            if (
                image.format not in IMAGE_TYPES
                or getattr(image, "n_frames", 1) != 1
                or image.width * image.height > 16_000_000
            ):
                raise ValueError("Unsupported courseware raster")
            kind = IMAGE_TYPES[image.format]
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
    except Image.DecompressionBombError:
        raise ValueError("Courseware raster exceeds pixel budget") from None
    return kind


def retained_image(root, expected, extension):
    """Read only the content-addressed Wiki image store, with real MIME checks."""
    sha(expected)
    if extension not in {v[0] for v in IMAGE_TYPES.values()}:
        raise ValueError("Unsupported courseware image extension")
    folder = Path(root).resolve() / "wiki" / "media"
    path = folder / (expected + "." + extension)
    if not folder.resolve().is_relative_to(
        Path(root).resolve()
    ) or not path.resolve().is_relative_to(folder.resolve()):
        raise ValueError("Courseware image outside retained Wiki media")
    if path.stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError("Courseware image exceeds bounded read")
    with path.open("rb") as stream:
        data = stream.read(MAX_IMAGE_BYTES + 1)
    actual_extension, mime = image_type(data)
    if digest(data) != expected or actual_extension != extension:
        raise ValueError("Retained courseware image integrity failure")
    return data, mime


def freeze_image(root, metadata):
    if not metadata.get("image"):
        return {}
    expected = sha(metadata.get("image_digest"))
    # All extractor-produced courseware images live here. Do not turn arbitrary
    # metadata paths into a file-serving capability, even within the archive.
    folder = root / "document-visuals"
    path = (root / metadata["image"]).resolve()
    if (
        not folder.resolve().is_relative_to(root.resolve())
        or not path.is_relative_to(folder.resolve())
        or path.stat().st_size > MAX_IMAGE_BYTES
    ):
        raise ValueError("Courseware image outside bounded visual evidence")
    with path.open("rb") as stream:
        data = stream.read(MAX_IMAGE_BYTES + 1)
    if digest(data) != expected:
        raise ValueError("Courseware image changed since extraction")
    return retain_raster(root, data)


def retain_raster(root, data):
    expected = digest(data)
    extension, mime = image_type(data)
    target = root / "wiki" / "media" / (expected + "." + extension)
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("Wiki image store outside archive root")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        retained_image(root, expected, extension)
    else:
        temporary = target.with_suffix(".part")
        temporary.write_bytes(data)
        temporary.replace(target)
    return {"image_sha256": expected, "image_extension": extension, "image_mime": mime}


def pdf_page(row, reference):
    if not reference["filename"].lower().endswith(".pdf"):
        return None
    match = re.fullmatch(
        re.escape(row["source_key"]) + r" page (\d+) chars \d+", row["locator"]
    )
    return int(match[1]) if match else None


def render_selected_page(store, row, reference, selected):
    """Explicit native PDF page rendering, separate from the live OCR workers."""
    from .course_attachments import resolve_reference
    from .course_pdf_ocr import render_page

    page = pdf_page(row, reference)
    if type(selected) is not int or selected < 1 or selected != page:
        raise ValueError("Selected PDF page does not match chunk locator")
    poppler = shutil.which("pdftoppm")
    if not poppler:
        raise ValueError("Local PDF page renderer is required")
    renderer = digest(Path(poppler).read_bytes())
    provenance = {
        "policy": "wiki-native-pdf-page-scale2000-dpi150-v1",
        "page": page,
        "source_sha256": reference["expected_digest"],
        "renderer_sha256": renderer,
    }
    key = digest(canonical(provenance))
    folder = store.root / "wiki" / "rendered"
    if not folder.resolve().is_relative_to(store.root):
        raise ValueError("Wiki render store outside archive root")
    folder.mkdir(parents=True, exist_ok=True)
    receipt = folder / (key + ".json")
    if receipt.exists():
        value = json.loads(receipt.read_text("utf-8"))
        if value["provenance"] != provenance:
            raise ValueError("Native PDF render identity mismatch")
        retained_image(
            store.root,
            value["image"]["image_sha256"],
            value["image"]["image_extension"],
        )
        return value
    retained = resolve_reference(store, reference)
    destination = folder / (key + ".png")
    store.checkpoint()
    try:
        render_page(retained["path"], page, destination, poppler)
        with destination.open("rb") as stream:
            data = stream.read(MAX_IMAGE_BYTES + 1)
        image = retain_raster(store.root, data)
    finally:
        destination.unlink(missing_ok=True)
    from .course_knowledge import atomic_json

    value = {"provenance": provenance, "image": image}
    atomic_json(receipt, value)
    return value


def revision_chain(db, row):
    """Check current leaf and all recorded parents in the same DB snapshot."""
    key = row["source_key"]
    expected = row["raw_digest"]
    chain, seen = [], set()
    for depth in range(8):
        if key in seen:
            raise ValueError("Cyclic courseware evidence ancestry")
        seen.add(key)
        current = db.execute(
            "SELECT * FROM resource_revisions WHERE path=? AND source_key=?",
            (row["path"], key),
        ).fetchone()
        if current is None:
            # Some visual extractors retain a raw parent without registering a
            # text revision. Only direct files or explicit ZIP chains qualify.
            if depth == 0 or (key and not archive_members(key)):
                raise ValueError("Unresolved courseware evidence ancestor")
            return chain, key, sha(expected)
        current = dict(current)
        if current["raw_digest"] != expected:
            raise ValueError("Courseware parent revision changed")
        revision = digest(
            (expected + "\0" + current["extractor"] + "\0" + key).encode()
        )
        if current["revision"] != revision or (
            depth == 0
            and (
                current["revision"] != row["digest"]
                or current["extractor"] != row["extractor"]
            )
        ):
            raise ValueError("Courseware revision identity mismatch")
        metadata = json.loads(current["metadata"])
        metadata_basis = "current_revision_snapshot"
        if depth == 0:
            frozen = db.execute(
                "SELECT metadata FROM document_version_metadata WHERE path=? AND source_key=? AND revision=?",
                (row["path"], key, row["digest"]),
            ).fetchone()
            if frozen is not None and canonical(json.loads(frozen[0])) != canonical(
                metadata
            ):
                raise ValueError("Courseware version metadata mismatch")
            if frozen is not None:
                metadata_basis = "version_receipt"
        chain.append(
            {
                "source_key": key,
                "raw_digest": expected,
                "revision": revision,
                "extractor": current["extractor"],
                "metadata": metadata,
                "metadata_basis": metadata_basis,
            }
        )
        if "parent_source_key" not in metadata:
            if key and not archive_members(key):
                raise ValueError("Unsupported courseware root source locator")
            return chain, key, sha(expected)
        key = metadata["parent_source_key"]
        expected = sha(metadata.get("parent_raw_digest"))
    raise ValueError("Courseware evidence ancestry exceeds bounded depth")


def verify_reference(reference, store, checked):
    if reference is None:
        raise ValueError("No retained original for courseware evidence")
    path = reference["path"]
    if not path.resolve().is_relative_to(store.root):
        raise ValueError("Courseware container outside archive root")
    identity = (str(path), reference["container_digest"])
    if identity not in checked:
        if path.stat().st_size != reference["container_bytes"]:
            raise ValueError("Retained courseware container changed")
        value = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(8 * 1024**2), b""):
                store.checkpoint()
                value.update(block)
        if value.hexdigest() != reference["container_digest"]:
            raise ValueError("Retained courseware container checksum mismatch")
        checked.add(identity)
    if reference["members"]:
        identity = (
            *identity,
            tuple(reference["members"]),
            reference["expected_digest"],
        )
        if identity not in checked:
            with open_archive_member(
                path, reference["members"], checkpoint=store.checkpoint
            ) as (entry, stream):
                value, copied = hashlib.sha256(), 0
                for block in iter(lambda: stream.read(8 * 1024**2), b""):
                    store.checkpoint()
                    copied += len(block)
                    if copied > entry.file_size:
                        raise ValueError(
                            "Courseware archive member exceeds declared size"
                        )
                    value.update(block)
                if (
                    copied != entry.file_size
                    or value.hexdigest() != reference["expected_digest"]
                ):
                    raise ValueError("Retained courseware member checksum mismatch")
            checked.add(identity)


def freeze(root, selections):
    """Explicit {document, revision, text_sha256} selections; no heuristic picks."""
    store = ReadOnlyArchive(root)
    sources, bindings, checked = {}, {}, set()
    if not 1 <= len(selections) <= 16:
        raise ValueError("Select 1..16 courseware chunks")
    with store.connect() as db:
        db.execute("BEGIN")
        for selection in selections:
            store.checkpoint()
            if type(selection["document"]) is not int or selection["document"] < 1:
                raise ValueError("Invalid courseware document ID")
            row = db.execute(
                "SELECT * FROM documents WHERE id=?", (selection["document"],)
            ).fetchone()
            if row is None or not row["active"]:
                raise ValueError("Courseware chunk is absent or superseded")
            row = dict(row)
            if row["digest"] != sha(selection["revision"]) or digest(
                row["text"].encode()
            ) != sha(selection["text_sha256"]):
                raise ValueError("Courseware plan no longer matches source chunk")
            if not row["text"].strip() or len(row["text"]) > 3200:
                raise ValueError("Courseware chunk exceeds bounded context")
            chain, parent, raw = revision_chain(db, row)
            reference = retained_reference(store, row["path"], parent, raw)
            verify_reference(reference, store, checked)
            manifest = json.loads(
                (store.root / "resources-manifest.json").read_text("utf-8")
            )
            relative = next(
                r["relative_path"]
                for r in manifest["completed"]
                if normalized_original(manifest["source"] + "/" + r["relative_path"])
                == normalized_original(row["path"])
            )
            identity = {
                k: row[k]
                for k in (
                    "id",
                    "path",
                    "source_key",
                    "digest",
                    "locator",
                    "raw_digest",
                    "extractor",
                )
            }
            identity["text_sha256"] = selection["text_sha256"]
            ref = "doc:" + digest(canonical(identity))[:32]
            if ref in sources:
                raise ValueError("Duplicate selected courseware chunk")
            metadata = chain[0]["metadata"]
            image = freeze_image(store.root, metadata)
            image_evidence = {
                "provenance": {
                    "kind": "extractor_metadata",
                    "metadata_sha256": digest(canonical(metadata)),
                },
                "image": image,
            }
            if "render_page" in selection:
                if image:
                    raise ValueError("Ambiguous extracted/native PDF image selection")
                image_evidence = render_selected_page(
                    store, row, reference, selection["render_page"]
                )
                image = image_evidence["image"]
            sources[ref] = {
                "kind": "document",
                "id": row["id"],
                "title": reference["filename"],
                "text": row["text"],
                "locator": row["locator"],
                "relative_path": relative,
                "source_key": row["source_key"],
                "revision": row["digest"],
                "raw_digest": row["raw_digest"],
                "extractor": row["extractor"],
                "text_sha256": selection["text_sha256"],
                "review_required": bool(metadata.get("review_required")),
                "page": metadata.get("page") or pdf_page(row, reference),
                "slide": metadata.get("slide"),
                "region": metadata.get("box"),
                "evidence_scope": "selected_chunk_only",
                **image,
            }
            bindings[ref] = {
                "identity": identity,
                "revision_chain": chain,
                "retained_source": {
                    "object": reference["path"].relative_to(store.root).as_posix(),
                    "container_sha256": reference["container_digest"],
                    "source_sha256": reference["expected_digest"],
                    "members": reference["members"],
                },
                "relative_path": relative,
                "image_evidence": image_evidence,
                "semantic_accuracy_verified": False,
            }
    return sources, bindings


def current_matches(db, source, binding):
    """Cheap point lookup for coverage; freezing/reading the old packet is valid."""
    row = db.execute("SELECT * FROM documents WHERE id=?", (source["id"],)).fetchone()
    if row is None or not row["active"]:
        return False
    identity = binding["identity"]
    if any(row[k] != v for k, v in identity.items() if k != "text_sha256"):
        return False
    if digest(row["text"].encode()) != identity["text_sha256"]:
        return False
    try:
        chain, _, _ = revision_chain(db, row)
        return chain == binding["revision_chain"]
    except (ValueError, KeyError, TypeError):
        return False


def validate_source(ref, source, binding):
    identity = binding["identity"]
    if (
        ref != "doc:" + digest(canonical(identity))[:32]
        or any(
            source[field] != identity[original]
            for field, original in (
                ("id", "id"),
                ("locator", "locator"),
                ("source_key", "source_key"),
                ("revision", "digest"),
                ("raw_digest", "raw_digest"),
                ("extractor", "extractor"),
            )
        )
        or digest(source["text"].encode()) != identity["text_sha256"]
    ):
        raise ValueError("Frozen courseware source identity mismatch")
    if (
        source["text_sha256"] != identity["text_sha256"]
        or source["relative_path"] != binding["relative_path"]
    ):
        raise ValueError("Frozen courseware text/path mismatch")
    if source.get("image_sha256"):
        sha(source["image_sha256"])
        if any(
            source.get(k) != v for k, v in binding["image_evidence"]["image"].items()
        ):
            raise ValueError("Frozen courseware image provenance mismatch")
        if (
            source.get("image_extension"),
            source.get("image_mime"),
        ) not in IMAGE_TYPES.values():
            raise ValueError("Frozen courseware image format mismatch")
