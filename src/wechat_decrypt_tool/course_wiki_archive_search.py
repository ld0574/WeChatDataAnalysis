"""Bounded, read-only access to retained courseware, independent of publication."""

from __future__ import annotations

import hashlib
import re
import shutil
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from .course_knowledge import tokens

MAX_TEXT = 200_000
TEXT_PAGE = 8000


class ArchiveSearchUnavailable(Exception):
    pass


class SourceChanged(Exception):
    pass


@contextmanager
def connection(store, *, seconds=4):
    if getattr(store, "snapshot_info", None):
        raise ArchiveSearchUnavailable(
            "独立 Wiki 快照不含全量课件库，请使用本机工作库检索课件；文章和笔记检索仍可使用。"
        )
    path = Path(store.root).resolve() / "knowledge.sqlite3"
    if not path.is_file():
        raise ArchiveSearchUnavailable("全量课件库尚未就绪，文章和笔记检索仍可使用。")
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.5)
    db.row_factory = sqlite3.Row
    deadline = time.monotonic() + seconds
    # Check frequently: cold overflow pages and superseded FTS rows can make a
    # thousand VM steps take many seconds on a busy disk.
    db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 20)
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        yield db
    except sqlite3.DatabaseError:
        raise ArchiveSearchUnavailable(
            "课件库繁忙或本次检索超时，请缩小关键词范围后重试。"
        ) from None
    finally:
        db.close()


def query_tokens(query):
    if len(query) > 200:
        raise ValueError("关键词限 200 个字符。")
    words = list(dict.fromkeys(t for t in tokens(query) if t.strip("_")))
    if len(words) > 32:
        raise ValueError("关键词过多，请缩小到 32 个检索词以内。")
    return words


def prepare_index(root, *, seconds=120, seed_seconds=900, progress=None):
    """Seed a small current-ID map from the existing covering source index.

    Never called from HTTP or reader startup. Triggers work for producers that
    are already running. A bounded, atomic migration avoids scanning every
    text overflow page while holding the producer's writer lease.
    """
    from .course_knowledge import schema_lease

    root = Path(root).resolve()
    path = root / "knowledge.sqlite3"
    if not path.is_file() or shutil.disk_usage(root).free < 8 * 1024**3:
        raise ValueError("courseware_index_archive_or_disk_reserve_unavailable")

    def report(stage, **extra):
        if progress:
            progress(stage, **extra)

    @contextmanager
    def writer():
        db = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=1)
        db.execute("PRAGMA cache_size=-32768")
        deadline = time.monotonic() + seconds
        db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 20)
        try:
            with (
                schema_lease(root / "schema.lock"),
                schema_lease(root / "writer.lock"),
                db,
            ):
                db.execute("BEGIN IMMEDIATE")
                yield db
        finally:
            db.close()

    with schema_lease(root / "courseware-index-seed.lock", timeout=0):
        with writer() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS courseware_current_documents(id INTEGER PRIMARY KEY)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS courseware_search_metadata(id INTEGER PRIMARY KEY CHECK(id=1),policy TEXT,initialized INTEGER)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS courseware_search_changes(id INTEGER PRIMARY KEY)"
            )
            db.execute(
                "INSERT OR IGNORE INTO courseware_search_metadata VALUES(1,'current-rowids-covering-delta-v2',0)"
            )
            for statement in (
                """CREATE TRIGGER IF NOT EXISTS courseware_current_insert
                       AFTER INSERT ON documents WHEN new.active=1 BEGIN
                       INSERT OR IGNORE INTO courseware_current_documents VALUES(new.id);
                       INSERT OR IGNORE INTO courseware_search_changes SELECT new.id
                       WHERE (SELECT initialized FROM courseware_search_metadata WHERE id=1)=0; END""",
                """CREATE TRIGGER IF NOT EXISTS courseware_current_delete
                       AFTER DELETE ON documents BEGIN
                       DELETE FROM courseware_current_documents WHERE id=old.id;
                       INSERT OR IGNORE INTO courseware_search_changes SELECT old.id
                       WHERE (SELECT initialized FROM courseware_search_metadata WHERE id=1)=0; END""",
                """CREATE TRIGGER IF NOT EXISTS courseware_current_update
                       AFTER UPDATE OF active,id ON documents
                       WHEN old.active IS NOT new.active OR old.id IS NOT new.id BEGIN
                       DELETE FROM courseware_current_documents WHERE id=old.id;
                       INSERT OR IGNORE INTO courseware_current_documents
                       SELECT new.id WHERE new.active=1;
                       INSERT OR IGNORE INTO courseware_search_changes SELECT old.id
                       WHERE (SELECT initialized FROM courseware_search_metadata WHERE id=1)=0;
                       INSERT OR IGNORE INTO courseware_search_changes SELECT new.id
                       WHERE (SELECT initialized FROM courseware_search_metadata WHERE id=1)=0; END""",
            ):
                db.execute(statement)
            policy = db.execute(
                "SELECT policy,initialized FROM courseware_search_metadata WHERE id=1"
            ).fetchone()
            if policy[0] != "current-rowids-covering-delta-v2":
                raise ValueError("courseware_search_unknown_current_map")
            if policy[1]:
                return
        # Scan under a read snapshot into memory, holding no producer lease.
        # The touched-ID journal prevents seeding from reintroducing a source
        # deleted/superseded while this long read was in progress.
        report("seeding_current_ids_without_writer_lease")
        memory = sqlite3.connect(":memory:", uri=True)
        try:
            memory.execute("CREATE TABLE seed(id INTEGER PRIMARY KEY)")
            memory.execute(
                "ATTACH DATABASE ? AS archive", (path.as_uri() + "?mode=ro",)
            )
            deadline = time.monotonic() + seed_seconds
            memory.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            memory.execute(
                "INSERT INTO seed SELECT id FROM archive.documents INDEXED BY document_source WHERE active=1"
            )
            memory.commit()
            memory.execute("DETACH DATABASE archive")
            count = memory.execute("SELECT count(*) FROM seed").fetchone()[0]
            report("current_id_snapshot_seeded", count=count)
            with writer() as db:
                db.executemany(
                    "INSERT OR IGNORE INTO courseware_current_documents SELECT ? WHERE NOT EXISTS(SELECT 1 FROM courseware_search_changes WHERE id=?)",
                    (
                        (row[0], row[0])
                        for row in memory.execute("SELECT id FROM seed ORDER BY id")
                    ),
                )
                db.execute(
                    "UPDATE courseware_search_metadata SET initialized=1 WHERE id=1"
                )
                db.execute("DELETE FROM courseware_search_changes")
            report("current_document_map_ready", snapshot_ids=count)
        finally:
            memory.close()


def identity(row):
    return {
        "id": row["id"],
        "revision": row["digest"],
        "text_sha256": hashlib.sha256(row["text"].encode()).hexdigest(),
        "path": row["path"],
        "source_key": row["source_key"],
        "locator": row["locator"],
        "extractor": row["extractor"],
        "raw_digest": row["raw_digest"],
    }


def excerpt(text, words):
    lower = text.lower()
    positions = [lower.find(word) for word in words if word in lower]
    start = max(0, min(positions, default=0) - 80)
    end = min(len(text), start + 320)
    return ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")


def search(store, query, *, after=0, limit=20):
    words = query_tokens(query)
    size = max(1, min(30, limit))
    page = {
        "method": "retained_courseware_token_and_document_id_order",
        "items": [],
        "next_after": None,
        "order": "document_id",
        "live_index": True,
        "whole_source_coverage_verified": False,
    }
    # Even an empty query reports the offline snapshot limitation.
    with connection(store) as db:
        if not words:
            return page
        floor = max(0, after)
        mapped = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='courseware_search_metadata'"
        ).fetchone()
        if mapped:
            state = db.execute(
                "SELECT policy,initialized FROM courseware_search_metadata WHERE id=1"
            ).fetchone()
            if (
                state is None
                or state[0] != "current-rowids-covering-delta-v2"
                or state[1] != 1
            ):
                raise ArchiveSearchUnavailable(
                    "课件检索索引正在准备，后台整理与文章阅读继续可用，请稍后重试。"
                )
        if mapped:
            first = db.execute(
                "SELECT min(id) FROM courseware_current_documents"
            ).fetchone()[0]
            if first is None:
                return page
            floor = max(floor, first - 1)
        match = " AND ".join('"' + word + '"' for word in words)
        # Rowid order permits FTS5's bounded ascending iterator. BM25 sorting a
        # common single-character term would read/sort the entire match set.
        joins = (
            " CROSS JOIN courseware_current_documents a CROSS JOIN documents d"
            if mapped
            else " JOIN documents d ON d.id=f.rowid"
        )
        current = " AND a.id=f.rowid AND d.id=a.id" if mapped else ""
        rows = db.execute(
            """SELECT d.id,d.path,d.locator,d.digest,d.source_key,d.extractor,d.raw_digest,
                      length(d.text) characters,
                      CASE WHEN length(d.text)<=? THEN d.text ELSE substr(d.text,1,320) END text
               FROM document_fts f"""
            + joins
            + """
               WHERE document_fts MATCH ? AND f.rowid>? AND d.active=1"""
            + current
            + """
               ORDER BY f.rowid LIMIT ?""",
            (MAX_TEXT, match, floor, size + 1),
        ).fetchall()
        page["items"] = [
            {
                **identity(row),
                "text_sha256": hashlib.sha256(row["text"].encode()).hexdigest()
                if row["characters"] <= MAX_TEXT
                else None,
                "readable": row["characters"] <= MAX_TEXT,
                "characters": row["characters"],
                "excerpt": excerpt(row["text"], words),
            }
            for row in rows[:size]
        ]
        if len(rows) > size:
            page["next_after"] = rows[size - 1]["id"]
        # Oversized chunks are explicitly outside this bounded reader.
        page["maximum_chunk_characters"] = MAX_TEXT
    return page


def document(store, key, revision, text_sha256, *, offset=0):
    if key < 1 or not all(
        re.fullmatch(r"[a-f0-9]{64}", x) for x in (revision, text_sha256)
    ):
        raise ValueError("无效的课件来源标识。")
    if offset < 0:
        raise ValueError("无效的正文位置。")
    with connection(store) as db:
        row = db.execute(
            "SELECT * FROM documents WHERE id=? AND length(text)<=?", (key, MAX_TEXT)
        ).fetchone()
        if row is None:
            raise KeyError(key)
        value = identity(row)
        if (
            not row["active"]
            or value["revision"] != revision
            or value["text_sha256"] != text_sha256
        ):
            raise SourceChanged("课件片段已更新，请重新搜索后打开当前版本。")
        from .course_wiki_documents import revision_chain

        try:
            chain, _, _ = revision_chain(db, row)
            provenance = {
                "revision_chain_verified": True,
                "metadata_basis": chain[0]["metadata_basis"],
                "ancestor_count": len(chain),
            }
        except (ValueError, KeyError, TypeError):
            provenance = {"revision_chain_verified": False}
        text = row["text"]
        end = min(len(text), offset + TEXT_PAGE)
        return {
            **value,
            **provenance,
            "text": text[offset:end],
            "offset": offset,
            "total_characters": len(text),
            "next_offset": end if end < len(text) else None,
            "original_binary_rehashed_on_read": False,
            "whole_source_coverage_verified": False,
        }
