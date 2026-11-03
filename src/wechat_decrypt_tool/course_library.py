"""Source-bound course reading and topic navigation over checked exports.

Publication is a read-only archive operation. Existing extraction workers and
their modules are not modified; the optional web entry reuses existing evidence,
search, audio and model-answer routes.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path, PureWindowsPath

from .course_gap_asr import note_pages
from .course_knowledge import atomic_json, schema_lease
from .course_lesson_exports import digest, validate_exports
from .course_video_visual_audit import ReadOnlyArchive


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def source_folders(item):
    parts = PureWindowsPath(item["relative_path"]).parts
    if (
        any(p in ("..", ".") for p in parts)
        or PureWindowsPath(item["relative_path"]).is_absolute()
    ):
        raise ValueError("Unsafe source-relative course path")
    return list(parts[:-1])


def natural_key(value):
    return [
        (0, int(x)) if x.isdigit() else (1, x.casefold())
        for x in re.split(r"(\d+)", value)
    ]


def make_lesson(payload, item, checked):
    rows = payload["segments"]
    pages = note_pages(rows, payload.get("audio_gap_recovery"))
    if len(pages) != len(payload["notes"]):
        raise ValueError("Course notes do not match source windows")
    evidence = {
        str(s["id"]): {
            "id": s["id"],
            "kind": "speech",
            "start": s["start"],
            "end": s["end"],
            "text": s["text"],
        }
        for s in rows
    }
    evidence.update(
        {
            str(-f["id"]): {
                "id": -f["id"],
                "kind": "frame",
                "start": f["time"],
                "end": f["time"],
                "text": f["description"],
            }
            for f in payload["frames"]
        }
    )
    sections = []
    for i, (page, note) in enumerate(zip(pages, payload["notes"], strict=True)):
        for claim in note["concepts"] + note["relations"]:
            if not claim["evidence"] or any(
                str(n) not in evidence for n in claim["evidence"]
            ):
                raise ValueError("Course claim has missing evidence")
        names = list(dict.fromkeys(c["name"] for c in note["concepts"]))
        sections.append(
            {
                "id": "section-" + str(i),
                "window": i,
                "title": " · ".join(names[:2]) or "讲解片段 " + str(i + 1),
                "start": page[0]["start"],
                "end": page[-1]["end"],
                "summary": note["summary"],
                "concepts": note["concepts"],
                "questions": note.get("questions", []),
                "relations": note["relations"],
                "context": [s["id"] for s in page],
            }
        )
    return {
        "version": 1,
        "id": checked["video"],
        "title": payload["video"]["title"],
        "duration": payload["video"]["duration"],
        "folders": source_folders(item),
        "relative_path": item["relative_path"],
        "sections": sections,
        "evidence": evidence,
        "audio_kind": "gaps"
        if payload.get("audio_gap_recovery")
        else "alternate"
        if payload.get("audio_recovery")
        else "original"
        if payload.get("audio")
        else "unavailable",
        "source_export_sha256": checked["files_sha256"]["evidence.json"],
        "rows_sha256": checked["rows_sha256"],
        "semantic_accuracy_verified": False,
        "section_titles_are_generated": True,
        "source_deletion_allowed": False,
    }


def publish(store):
    with schema_lease(store.root / "library-publish.lock", timeout=0):
        return _publish(store)


def _publish(store):
    catalog_path = store.root / "library" / "catalog.json"
    previous = json.loads(catalog_path.read_bytes()) if catalog_path.exists() else None
    previous_sources = (
        {
            lesson["id"]
            for lesson in previous["lessons"]
            if lesson["state"] == "draft_available"
        }
        if previous is not None
        else set()
    )
    gate = validate_exports(store)
    raw = (store.root / "inventory-unique.json").read_bytes()
    if digest(raw) != gate["inventory_sha256"]:
        raise ValueError("Inventory changed after export checks")
    inv = json.loads(raw)
    sources = {
        digest(str(Path(v["path"]).resolve()).encode())[:24]: v
        for v in inv["videos"]
        if not v.get("error") and v.get("audio_streams") and v.get("duration_seconds")
    }
    folder = store.root / "library"
    objects = folder / "objects"
    objects.mkdir(parents=True, exist_ok=True)

    def put(value):
        data = canonical(value)
        sha = digest(data)
        target = objects / (sha + ".json")
        if target.exists():
            if digest(target.read_bytes()) != sha:
                raise ValueError("Published library object changed")
        else:
            temporary = target.with_suffix(".tmp")
            temporary.write_bytes(data)
            temporary.replace(target)
        return sha

    completed, terms = {}, {}
    for checked in gate["completed"]:
        store.checkpoint()
        key = checked["video"]
        data = (store.root / "lessons" / key / "evidence.json").read_bytes()
        if digest(data) != checked["files_sha256"]["evidence.json"]:
            continue  # Currently publishing lesson; expose it only in the next refresh.
        payload = json.loads(data)
        lesson = make_lesson(payload, sources[key], checked)
        media = folder / "media"
        media.mkdir(exist_ok=True)
        for frame in payload["frames"]:
            path = (store.root / frame["path"]).resolve()
            if not path.is_relative_to((store.root / "lessons").resolve()):
                raise ValueError("Frame path outside retained lessons")
            if path.stat().st_size > 16 * 1024**2:
                raise ValueError("Frame exceeds publication size limit")
            image = path.read_bytes()
            sha = digest(image)
            target = media / (sha + ".jpg")
            if target.exists():
                if digest(target.read_bytes()) != sha:
                    raise ValueError("Retained Wiki image changed")
            else:
                temporary = target.with_suffix(".tmp")
                temporary.write_bytes(image)
                temporary.replace(target)
            lesson["evidence"][str(-frame["id"])]["image_sha256"] = sha
        completed[key] = {"artifact": put(lesson), "sections": len(lesson["sections"])}
        for section in lesson["sections"]:
            for concept in section["concepts"]:
                term_id = digest(concept["name"].encode())[:32]
                term = terms.setdefault(
                    term_id, {"id": term_id, "name": concept["name"], "occurrences": []}
                )
                term["occurrences"].append(
                    {
                        "video": key,
                        "section": section["id"],
                        "title": lesson["title"],
                        "start": section["start"],
                        "end": section["end"],
                        "explanation": concept["explanation"],
                        "evidence": concept["evidence"],
                    }
                )
    lessons = []
    for key, item in sources.items():
        folders = source_folders(item)
        lessons.append(
            {
                "id": key,
                "title": item["title"],
                "folders": folders,
                "collection": folders[0] if folders else "课程",
                "relative_path": item["relative_path"],
                "duration": item["duration_seconds"],
                "state": "draft_available" if key in completed else "not_published",
                **completed.get(key, {}),
            }
        )
    lessons.sort(key=lambda lesson: natural_key(lesson["relative_path"]))
    for term in terms.values():
        term["lesson_count"] = len({o["video"] for o in term["occurrences"]})
    if (store.root / "inventory-unique.json").read_bytes() != raw:
        raise ValueError("Inventory changed while building library")
    # A live producer can temporarily invalidate an old export, or replace it
    # between validation and loading. Do not publish a smaller course library.
    # Retain the last complete catalog until all previously published sources
    # pass the same checks again; never mark the changing export as valid.
    if not previous_sources.issubset(completed):
        raise ValueError("library_publication_would_remove_existing_sources")
    catalog = {
        "version": 1,
        "updated": time.time(),
        "inventory_sha256": gate["inventory_sha256"],
        "lessons": lessons,
        "published": len(completed),
        "requested": len(sources),
        "terms_artifact": put(list(terms.values())),
        "term_count": len(terms),
        "organization": "original_source_directories_and_time_windows",
        "unpublished": gate["errors"],
        "semantic_accuracy_verified": False,
        "source_deletion_allowed": False,
    }
    atomic_json(folder / "catalog.json", catalog)
    return catalog


class Library:
    def __init__(self, root):
        self.root = Path(root).resolve() / "library"
        self.cache = {}

    def catalog(self):
        return json.loads((self.root / "catalog.json").read_text("utf-8"))

    def object(self, sha):
        if not re.fullmatch(r"[a-f0-9]{64}", sha):
            raise ValueError("Invalid library object ID")
        if sha not in self.cache:
            path = self.root / "objects" / (sha + ".json")
            if not path.resolve().is_relative_to(self.root.resolve()):
                raise ValueError("Library object is outside publication")
            raw = path.read_bytes()
            if digest(raw) != sha:
                raise ValueError("Library object failed checksum")
            self.cache[sha] = json.loads(raw)
        return self.cache[sha]

    def lesson(self, key):
        row = next(
            (lesson for lesson in self.catalog()["lessons"] if lesson["id"] == key),
            None,
        )
        if row is None or row.get("state") != "draft_available":
            raise KeyError("Course notes have not been published")
        return self.object(row["artifact"])

    def terms(self):
        return self.object(self.catalog()["terms_artifact"])

    def search(self, query, video=None, limit=20):
        query = query.strip().casefold()[:300]
        if len(query) < 2:
            return []
        anchors = re.findall(r"[a-z][a-z0-9_+.-]+", query)
        chinese = re.sub(
            r"如何|怎么|为什么|什么是|是什么|请问|一下|的|了|吗|呢|？|\?", " ", query
        )
        chunks = re.findall(r"[\u3400-\u9fff]{2,}", chinese)
        grams = {s[i : i + 2] for s in chunks for i in range(len(s) - 1)}

        def score(text, *, require_all=True):
            if require_all and anchors and not all(a in text for a in anchors):
                return 0
            return (
                12 * (query in text)
                + 6 * sum(a in text for a in anchors)
                + sum(g in text for g in grams)
            )

        found = []
        for row in self.catalog()["lessons"]:
            if row["state"] != "draft_available" or (video and video != row["id"]):
                continue
            lesson = self.lesson(row["id"])
            evidence = lesson.get("evidence", {})
            frames = [e for e in evidence.values() if e.get("kind") == "frame"]
            for section in lesson["sections"]:
                text = (
                    row["title"]
                    + " "
                    + section["summary"]
                    + " "
                    + " ".join(
                        c["name"] + " " + c["explanation"] for c in section["concepts"]
                    )
                ).casefold()
                sources = [evidence[str(ref)] for ref in section.get("context", [])]
                sources += [
                    e
                    for e in frames
                    if section["start"]
                    <= e["start"]
                    <= section.get("end", section["start"])
                ]
                combined = text + " " + " ".join(e["text"] for e in sources).casefold()
                rank = score(combined)
                if rank:
                    matched_sources = []
                    remaining = list(sources)
                    uncovered = set(anchors) or set(grams)
                    while remaining and len(matched_sources) < 3:
                        best = max(
                            remaining,
                            key=lambda e: (
                                sum(t in e["text"].casefold() for t in uncovered),
                                score(e["text"].casefold(), require_all=False),
                            ),
                        )
                        remaining.remove(best)
                        folded = best["text"].casefold()
                        if score(folded, require_all=False):
                            position = folded.find(query)
                            if position < 0:
                                position = min(
                                    (
                                        folded.find(t)
                                        for t in [*anchors, *sorted(grams)]
                                        if t in folded
                                    ),
                                    default=0,
                                )
                            start = max(0, position - 60)
                            excerpt = best["text"][start : start + 240]
                            matched_sources.append(
                                {
                                    "id": best["id"],
                                    "kind": best["kind"],
                                    "start": best["start"],
                                    "end": best["end"],
                                    "excerpt": (
                                        ("…" if start else "")
                                        + excerpt
                                        + (
                                            "…"
                                            if start + 240 < len(best["text"])
                                            else ""
                                        )
                                    ),
                                }
                            )
                            uncovered = {t for t in uncovered if t not in folded}
                        else:
                            break
                    found.append(
                        {
                            "video": row["id"],
                            "title": row["title"],
                            "section": section["id"],
                            "heading": section["title"],
                            "start": section["start"],
                            "summary": section["summary"],
                            "score": rank,
                            "match_source": matched_sources[0]
                            if matched_sources
                            else None,
                            "match_sources": matched_sources,
                        }
                    )
        return sorted(found, key=lambda x: -x["score"])[:limit]


def create_app(store, model="qwen3.5:9b"):
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import FileResponse, Response

    app = FastAPI(title="本地课程 Wiki")
    library = Library(store.root)
    resources = Path(__file__).parent / "resources"

    from threading import BoundedSemaphore

    archive_slots = BoundedSemaphore(2)

    def archive_read(operation, *args, **kwargs):
        from .course_wiki_archive_search import ArchiveSearchUnavailable, SourceChanged
        from .course_wiki_archive_assets import (
            AssetUnavailable,
            AssetLimitError,
            AssetCapacityError,
        )

        if not archive_slots.acquire(blocking=False):
            raise HTTPException(429, "课件检索正在处理，请稍后重试。")
        try:
            return operation(store, *args, **kwargs)
        except ArchiveSearchUnavailable as exc:
            raise HTTPException(503, str(exc)) from None
        except SourceChanged as exc:
            raise HTTPException(409, str(exc)) from None
        except AssetUnavailable as exc:
            raise HTTPException(404, str(exc)) from None
        except AssetLimitError as exc:
            raise HTTPException(413, str(exc)) from None
        except AssetCapacityError as exc:
            raise HTTPException(503, str(exc)) from None
        except KeyError:
            raise HTTPException(404, "未找到保留的课件片段。") from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        finally:
            archive_slots.release()

    @app.get("/wiki/api/courseware/search")
    def courseware_search(q: str = "", after: int = 0, limit: int = 20):
        from .course_wiki_archive_search import search

        return archive_read(search, q, after=after, limit=limit)

    @app.get("/wiki/api/courseware/document/{key}")
    def courseware_document(key: int, revision: str, text_sha256: str, offset: int = 0):
        from .course_wiki_archive_search import document

        return archive_read(document, key, revision, text_sha256, offset=offset)

    @app.get("/wiki/api/courseware/document/{key}/visual")
    def courseware_document_visual(key: int, revision: str, text_sha256: str):
        from .course_wiki_archive_assets import visual

        data, mime = archive_read(visual, key, revision, text_sha256)
        return Response(
            data,
            media_type=mime,
            headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"},
        )

    @app.get("/wiki/api/courseware/document/{key}/source")
    def courseware_document_source(key: int, revision: str, text_sha256: str):
        from urllib.parse import quote
        from .course_wiki_archive_assets import source

        data, filename = archive_read(source, key, revision, text_sha256)
        pdf = filename.lower().endswith(".pdf") and data.startswith(b"%PDF-")
        disposition = "inline" if pdf else "attachment"
        return Response(
            data,
            media_type="application/pdf" if pdf else "application/octet-stream",
            headers={
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "no-store",
                "Content-Disposition": disposition
                + "; filename*=UTF-8''"
                + quote(filename, safe=""),
            },
        )

    @app.get("/wiki/image/{sha}.{extension}")
    def courseware_image(sha: str, extension: str):
        from .course_wiki_documents import retained_image

        try:
            data, mime = retained_image(store.root, sha, extension)
            return Response(
                data, media_type=mime, headers={"X-Content-Type-Options": "nosniff"}
            )
        except (OSError, ValueError):
            raise HTTPException(404, "课件原图未保留或校验失败") from None

    @app.get("/library/frame/{sha}")
    def frame(sha: str):
        if not re.fullmatch(r"[a-f0-9]{64}", sha):
            raise HTTPException(404)
        path = library.root / "media" / (sha + ".jpg")
        if not path.resolve().is_relative_to(library.root) or not path.is_file():
            raise HTTPException(404, "原始画面尚未保留")
        if path.stat().st_size > 16 * 1024**2:
            raise HTTPException(503, "原始画面超过读取限制")
        data = path.read_bytes()
        if digest(data) != sha:
            raise HTTPException(503, "原始画面校验失败")
        return Response(data, media_type="image/jpeg")

    @app.get("/wiki/api/catalog")
    def wiki_catalog():
        from .course_wiki import Wiki

        try:
            value = Wiki(store.root).catalog()
            if getattr(store, "snapshot_info", None):
                value = {**value, "published_snapshot": store.snapshot_info}
            return value
        except (OSError, ValueError):
            raise HTTPException(503, "主题文章目录读取失败") from None

    @app.get("/wiki/api/article/{key}")
    def wiki_article(key: str):
        from .course_wiki import Wiki

        try:
            return Wiki(store.root).article(key)
        except KeyError:
            raise HTTPException(404, "未找到主题文章") from None
        except (OSError, ValueError):
            raise HTTPException(503, "主题文章校验失败") from None

    @app.get("/wiki/api/coverage")
    def wiki_coverage():
        # Read a published receipt only: HTTP requests must never scan the archive.
        if getattr(store, "snapshot_info", None):
            raise HTTPException(
                503,
                "已发布 Wiki 快照不包含实时全量整理进度；文章、图片和检索可离线使用",
            )
        try:
            value = json.loads((store.root / "wiki" / "coverage.json").read_bytes())
            if value["policy"] != "wiki-whole-source-ledger-v1":
                raise ValueError("unknown_coverage_policy")
            stale = []

            def safe_path(relative):
                path = (store.root / relative).resolve()
                if not path.is_relative_to(store.root.resolve()):
                    raise ValueError("unsafe_coverage_input")
                return path

            for relative, expected in value["input_sha256"].items():
                path = safe_path(relative)
                if not path.is_file() or digest(path.read_bytes()) != expected:
                    stale.append(relative)
            for relative in value["observed_absent_inputs"]:
                if safe_path(relative).exists():
                    stale.append(relative)
            return {**value, "stale_inputs": stale, "snapshot_only": True}
        except (OSError, ValueError, KeyError, TypeError):
            raise HTTPException(
                503, "整理进度快照尚未就绪或校验失败，原有阅读与检索仍可用"
            ) from None

    @app.get("/wiki/api/review/{key}")
    def wiki_review(key: str, job: str | None = None):
        from .course_wiki_review import published_review

        try:
            return published_review(store.root, key, job=job)
        except KeyError:
            raise HTTPException(404, "未找到主题文章或对照记录不完整") from None
        except (OSError, ValueError, TypeError):
            raise HTTPException(503, "来源对照记录校验失败，文章仍可阅读") from None

    @app.get("/wiki/api/visual/{key}")
    def wiki_visual(key: str, job: str | None = None):
        from .course_wiki_visual_review import published_reports

        try:
            return published_reports(store.root, key, job=job)
        except KeyError:
            raise HTTPException(404, "未找到主题文章或原图核对记录") from None
        except (OSError, ValueError, TypeError):
            raise HTTPException(503, "原图核对记录暂不可读，文章仍可阅读") from None

    @app.get("/wiki/api/completeness/{key}")
    def wiki_completeness(key: str, job: str | None = None):
        from .course_wiki_completeness import published_report

        try:
            return published_report(store.root, key, job=job)
        except KeyError:
            raise HTTPException(404, "未找到主题文章或遗漏检查记录") from None
        except (OSError, ValueError, TypeError):
            raise HTTPException(503, "遗漏检查记录校验失败，文章仍可阅读") from None

    @app.get("/wiki/api/search")
    def wiki_search(q: str):
        from .course_wiki import Wiki

        try:
            return {
                "method": "local_article_keyword_search",
                "items": Wiki(store.root).search(q),
            }
        except (OSError, ValueError, KeyError):
            raise HTTPException(503, "主题正文检索暂不可用") from None

    @app.get("/")
    def home():
        return FileResponse(
            resources / "course_library.html",
            media_type="text/html",
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/library-assets/{name}")
    def asset(name: str):
        allowed = {
            "course_library.js": "text/javascript",
            "course_learning.js": "text/javascript",
            "course_library.css": "text/css",
        }
        if name not in allowed:
            raise HTTPException(404)
        return FileResponse(
            resources / name,
            media_type=allowed[name],
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/library/api/catalog")
    def catalog():
        try:
            return library.catalog()
        except (OSError, ValueError):
            raise HTTPException(503, "课程目录尚未发布，请先生成知识阅读目录") from None

    @app.get("/library/api/lesson/{key}")
    def lesson(key: str):
        try:
            return library.lesson(key)
        except KeyError:
            raise HTTPException(404, "这节课还在整理中，暂未发布阅读笔记") from None
        except (OSError, ValueError):
            raise HTTPException(503, "阅读笔记校验失败，请重新发布目录") from None

    @app.get("/library/api/terms")
    def terms(q: str = "", offset: int = 0):
        try:
            rows = [
                t for t in library.terms() if q[:100].casefold() in t["name"].casefold()
            ]
            rows.sort(key=lambda t: (-t["lesson_count"], natural_key(t["name"])))
            start = max(0, offset)
            return {
                "total": len(rows),
                "items": [
                    {k: t[k] for k in ("id", "name", "lesson_count")}
                    for t in rows[start : start + 60]
                ],
            }
        except (OSError, ValueError):
            raise HTTPException(503, "词条索引暂不可用") from None

    @app.get("/library/api/term/{key}")
    def term(key: str):
        try:
            row = next((t for t in library.terms() if t["id"] == key), None)
            if row is None:
                raise HTTPException(404, "未找到词条")
            return row
        except (OSError, ValueError):
            raise HTTPException(503, "词条索引暂不可用") from None

    @app.get("/library/api/search")
    def search(q: str, video: str | None = None, offset: int = 0, limit: int = 20):
        try:
            # Rank the complete match set before paging; the default top twenty
            # must not make later source sections unreachable.
            matches = library.search(q, video, limit=None)
            start, size = max(0, offset), max(1, min(100, limit))
            items = matches[start : start + size]
            end = start + len(items)
            return {
                "method": "local_keyword_search",
                "items": items,
                "total": len(matches),
                "offset": start,
                "limit": size,
                "next_offset": end if end < len(matches) else None,
            }
        except (OSError, ValueError, KeyError):
            raise HTTPException(503, "知识笔记检索暂不可用") from None

    return app


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--model", default="qwen3.5:9b")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build")
    sub.add_parser("prepare-courseware-search")
    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=3003)
    args = parser.parse_args(argv)
    if args.command == "prepare-courseware-search":
        from .course_wiki_archive_search import prepare_index

        prepare_index(args.root)
        print(json.dumps({"state": "courseware_search_index_ready"}))
    elif args.command == "build":
        value = publish(ReadOnlyArchive(args.root))
        print(
            json.dumps({k: value[k] for k in ("published", "requested", "term_count")})
        )
    else:
        import uvicorn

        uvicorn.run(
            create_app(ReadOnlyArchive(args.root), args.model),
            host="127.0.0.1",
            port=args.port,
        )


if __name__ == "__main__":
    main()
