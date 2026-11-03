"""Version-bound original page and retained source reads for the Wiki reader."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

from .course_attachments import (
    AttachmentCapacityError,
    AttachmentIntegrityError,
    SourceVersionUnavailable,
    resolve_reference,
    retained_reference,
)
from .course_wiki_archive_search import SourceChanged, connection, pinned_row

MAX_SOURCE_BYTES = 64 * 1024**2


class AssetUnavailable(Exception):
    pass


class AssetLimitError(Exception):
    pass


class AssetCapacityError(Exception):
    pass


def reference_context(store, row, parent_key, parent_digest):
    # Attachment helpers need read-only DB access; no KnowledgeStore constructor.
    archive = SimpleNamespace(
        root=Path(store.root).resolve(), connect=lambda: connection(store)
    )
    return archive, retained_reference(archive, row["path"], parent_key, parent_digest)


def visual_path(root, metadata):
    if (
        not isinstance(metadata.get("image"), str)
        or not isinstance(metadata.get("image_digest"), str)
        or not re.fullmatch(r"[a-f0-9]{64}", metadata["image_digest"])
    ):
        raise AssetUnavailable("这个片段尚未保留原页图片。")
    root = Path(root).resolve()
    path = (root / metadata["image"]).resolve()
    if not path.is_relative_to(root / "document-visuals") or not path.is_file():
        raise AssetUnavailable("原页图片未保留或已不可读。")
    return path


def asset_links(store, row, chain, parent_key, parent_digest):
    params = urlencode(
        {
            "revision": row["digest"],
            "text_sha256": hashlib.sha256(row["text"].encode()).hexdigest(),
        }
    )
    prefix = "/wiki/api/courseware/document/" + str(row["id"])
    result = {"visual": None, "source": None}
    page = chain[0]["metadata"].get("page")
    if type(page) is int and page > 0:
        result["page"] = page
    try:
        visual_path(store.root, chain[0]["metadata"])
        result["visual"] = prefix + "/visual?" + params
    except (OSError, ValueError, AssetUnavailable):
        pass
    try:
        _, reference = reference_context(store, row, parent_key, parent_digest)
        if reference and reference["path"].is_file():
            result["source"] = prefix + "/source?" + params
            result["source_is_pdf"] = reference["filename"].lower().endswith(".pdf")
    except (OSError, ValueError, AttachmentIntegrityError):
        pass
    return result


def pinned_context(store, key, revision, text_sha256):
    from .course_wiki_documents import revision_chain

    with connection(store) as db:
        row = dict(pinned_row(db, key, revision, text_sha256))
        try:
            chain, parent_key, parent_digest = revision_chain(db, row)
        except (ValueError, KeyError, TypeError):
            raise SourceChanged(
                "课件来源版本需要重新核对，请重新搜索后再打开。"
            ) from None
    return row, chain, parent_key, parent_digest


def visual(store, key, revision, text_sha256):
    from .course_wiki_documents import MAX_IMAGE_BYTES, image_type

    _, chain, _, _ = pinned_context(store, key, revision, text_sha256)
    metadata = chain[0]["metadata"]
    path = visual_path(store.root, metadata)
    try:
        with path.open("rb") as stream:
            data = stream.read(MAX_IMAGE_BYTES + 1)
    except OSError:
        raise AssetUnavailable("原页图片当前不可读，请核对本地归档。") from None
    if len(data) > MAX_IMAGE_BYTES:
        raise AssetLimitError("原页图片超过在线预览大小限制，请从本地归档打开。")
    if hashlib.sha256(data).hexdigest() != metadata["image_digest"]:
        raise SourceChanged("原页图片与归档记录不一致，请核对本地归档。")
    try:
        _, mime = image_type(data)
    except (OSError, ValueError):
        raise AssetUnavailable("原页图片格式不可预览，请从本地归档打开。") from None
    return data, mime


def source(store, key, revision, text_sha256):
    row, _, parent_key, parent_digest = pinned_context(
        store, key, revision, text_sha256
    )
    try:
        archive, reference = reference_context(store, row, parent_key, parent_digest)
        if reference is None:
            raise AssetUnavailable("这个版本的原文件尚未保留。")
        retained = reference["path"].resolve()
        if (
            not retained.is_relative_to(Path(store.root).resolve() / "resources")
            or not retained.is_file()
        ):
            raise AssetUnavailable("这个版本的原文件当前未保留或不可读。")
        resolved = resolve_reference(archive, reference)
        path = resolved["path"].resolve()
        if not path.is_relative_to(Path(store.root).resolve() / "resources"):
            raise AssetUnavailable("原文件不在当前保留归档中。")
        if path.stat().st_size > MAX_SOURCE_BYTES:
            raise AssetLimitError("原文件超过在线读取大小限制，请从本地归档打开。")
        with path.open("rb") as stream:
            data = stream.read(MAX_SOURCE_BYTES + 1)
        if len(data) > MAX_SOURCE_BYTES:
            raise AssetLimitError("原文件超过在线读取大小限制，请从本地归档打开。")
        if hashlib.sha256(data).hexdigest() != parent_digest:
            raise SourceChanged("原文件与所选片段的来源版本不一致。")
        return data, resolved["filename"]
    except AttachmentCapacityError:
        raise AssetCapacityError(
            "归档恢复空间不足，请释放空间后再打开原文件。"
        ) from None
    except AttachmentIntegrityError:
        raise SourceChanged("原文件与归档记录不一致，请核对本地归档。") from None
    except (OSError, SourceVersionUnavailable):
        raise AssetUnavailable("这个版本的原文件当前不可读，请核对本地归档。") from None
