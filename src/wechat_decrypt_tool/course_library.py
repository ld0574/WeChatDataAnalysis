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
        found = []
        for row in self.catalog()["lessons"]:
            if row["state"] != "draft_available" or (video and video != row["id"]):
                continue
            for section in self.lesson(row["id"])["sections"]:
                text = (
                    row["title"]
                    + " "
                    + section["summary"]
                    + " "
                    + " ".join(
                        c["name"] + " " + c["explanation"] for c in section["concepts"]
                    )
                ).casefold()
                if anchors and not all(a in text for a in anchors):
                    continue
                matched = sum(g in text for g in grams)
                score = (
                    12 * (query in text) + 6 * sum(a in text for a in anchors) + matched
                )
                if score:
                    found.append(
                        {
                            "video": row["id"],
                            "title": row["title"],
                            "section": section["id"],
                            "heading": section["title"],
                            "start": section["start"],
                            "summary": section["summary"],
                            "score": score,
                        }
                    )
        return sorted(found, key=lambda x: -x["score"])[:limit]


def create_app(store, model="qwen3.5:9b"):
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import FileResponse, Response

    app = FastAPI(title="本地课程 Wiki")
    library = Library(store.root)
    resources = Path(__file__).parent / "resources"

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
            return Wiki(store.root).catalog()
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
        return FileResponse(resources / "course_library.html", media_type="text/html")

    @app.get("/library-assets/{name}")
    def asset(name: str):
        allowed = {
            "course_library.js": "text/javascript",
            "course_library.css": "text/css",
        }
        if name not in allowed:
            raise HTTPException(404)
        return FileResponse(resources / name, media_type=allowed[name])

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
    def search(q: str, video: str | None = None):
        try:
            return {"method": "local_keyword_search", "items": library.search(q, video)}
        except (OSError, ValueError, KeyError):
            raise HTTPException(503, "知识笔记检索暂不可用") from None

    return app


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--model", default="qwen3.5:9b")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build")
    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=3003)
    args = parser.parse_args(argv)
    if args.command == "build":
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
