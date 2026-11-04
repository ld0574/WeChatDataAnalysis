"""A local reading surface backed only by projected textbook objects.

Original notes, raw materials and private authoring artifacts have no HTTP route.
Publishing a review draft is a separate, checked operation; missing articles
remain explicitly unfinished rather than falling back to source notes.
"""

from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path

import jsonschema

from .course_knowledge import atomic_json, schema_lease
from .course_library import Library, canonical
from .course_textbook_article import reading_book, validate_book
from .course_textbook_evidence import sha

FORMAT = "course-textbook-reader-publication-v1"
ID = re.compile(r"^[a-f0-9]{24}$")
DIGEST = re.compile(r"^[a-f0-9]{64}$")
LEARNING_ASSETS = {
    "course-learning.js": "course_learning.js",
    "course-reading.js": "course_textbook_reading.js",
}
LEARNING_SCRIPTS = (
    '<script defer src="/assets/course-learning.js"></script>'
    '<script defer src="/assets/course-reading.js"></script>'
)
LEARNING_STYLE = """
#learning-records{margin:1.5rem 0;padding:1rem 0;border-block:1px solid #d9dfd8}
.learning-content{padding:1rem 0;font-size:.95rem}.learning-content p{margin:.8rem 0}
.learning-content label{display:block;margin-top:.8rem}.learning-content input[type=file]{max-width:100%;font:inherit}
.learning-actions{display:flex;flex-wrap:wrap;align-items:center;gap:1rem;margin:1rem 0}
.learning-actions label,.assessment{display:flex;align-items:center;gap:.5rem}
.learning-content button{font:inherit;padding:.6rem 1rem;border:1px solid #456251;background:#edf2ec;color:#253d30;border-radius:4px;cursor:pointer}
.learning-content button:disabled{opacity:.55;cursor:default}.learning-content select{font:inherit;padding:.5rem;max-width:100%}
.learning-status,.learning-preview{color:#526455}.practice{margin:1rem 0;padding:1rem;border:1px solid #d9dfd8;border-radius:4px}
.practice textarea{display:block;width:100%;resize:vertical;margin:.5rem 0 1rem;padding:.8rem;font:inherit;background:#fffefa;color:inherit;border:1px solid #a8bcae}
.practice label{font-size:.9rem}.practice input[type=checkbox]{flex:none}.learning-content,.practice{overflow-wrap:anywhere}
"""


def learning_shell():
    return '<details id="learning-records"><summary>学习记录与备份</summary></details>'


class ArticleNotReady(LookupError):
    pass


def sealed(path):
    saved = json.loads(Path(path).read_bytes())
    if saved["data_sha256"] != sha(canonical(saved["data"])):
        raise ValueError("textbook_reader_private_artifact_changed")
    return saved["data"]


def editorial_status(archive, revision):
    """Read text-free readiness backed by a separate immutable review proof."""
    if not DIGEST.fullmatch(revision):
        raise ValueError("textbook_reader_invalid_editorial_revision")
    wiki = Path(archive) / "wiki"
    path = wiki / "textbook-reading/delivery-status" / (revision + ".json")
    if not path.exists():
        return None
    try:
        status = json.loads(path.read_bytes())
        jsonschema.validate(status, {
            "type": "object", "additionalProperties": False,
            "required": ["format", "article_sha256", "automatic_editorial_review_passed",
                "courseware_reference_scope_verified", "user_acceptance", "source_deletion_allowed", "proof_sha256"],
            "properties": {
                "format": {"const": "course-textbook-editorial-status-v1"},
                "article_sha256": {"const": revision},
                "automatic_editorial_review_passed": {"const": True},
                "courseware_reference_scope_verified": {"const": True},
                "user_acceptance": {"const": False},
                "source_deletion_allowed": {"const": False},
                "proof_sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
            },
        })
        proof = json.loads((wiki / "editorial-delivery-proofs" / (status["proof_sha256"] + ".json")).read_bytes())
        public = {key: value for key, value in status.items() if key != "proof_sha256"}
        if (sha(canonical(proof)) != status["proof_sha256"]
                or proof["format"] != "course-textbook-editorial-delivery-v1"
                or proof["public"] != public
                or proof["first_lesson_editorial_work_complete"] is not True
                or proof["reader_ready_for_user_acceptance"] is not True
                or proof["source_deletion_allowed"] is not False
                or proof["original_review_receipt"]["article_sha256"] != revision):
            raise ValueError("editorial_proof_changed")
    except (OSError, ValueError, KeyError, TypeError, jsonschema.ValidationError):
        raise ValueError("textbook_reader_editorial_receipt_changed") from None
    return public


def publish(root, job):
    """Expose a clean review draft, without asserting final semantic acceptance."""
    from .course_textbook_authoring import load_inputs

    root = Path(root).resolve()
    if not re.fullmatch(r"[a-f0-9]{32}", job):
        raise ValueError("textbook_reader_invalid_authoring_job")
    folder = root / "wiki/textbook-authoring-jobs" / job
    result = sealed(folder / "result.json")
    if (
        result["job"] != job
        or result["state"] != "reading_draft_ready_for_actual_review"
        or result["quality_issues"] != 0
        or result["automatic_local_article_review_passed"] is not True
        or result["source_deletion_allowed"] is not False
        or sealed(folder / "quality-issues.json") != []
    ):
        raise ValueError("textbook_reader_article_not_ready_for_review")
    inventory, ledger = load_inputs(
        root, result["inventory_sha256"], result["ledger_job"]
    )
    private = sealed(folder / "private-article.json")
    if (
        private["inventory_sha256"] != result["inventory_sha256"]
        or private["ledger_job"] != ledger["job"]
        or private["course_id"] != inventory["lesson"]
        or set(private["private_coverage"]) != set(ledger["points"])
    ):
        raise ValueError("textbook_reader_article_evidence_binding_changed")
    book = reading_book(private)
    if json.loads((folder / "reading.json").read_bytes()) != book:
        raise ValueError("textbook_reader_reading_projection_changed")
    return store_book(root, book)


def publish_reviewed(root, job):
    """Publish an independently checked revision with its unfinished status."""
    from .course_textbook_reviewed_draft import load

    book, receipt = load(root, job)
    return store_book(root, book, receipt=receipt)


def store_book(root, book, *, receipt=None):
    validate_book(book)
    reader = Reader(root)
    reader.root.mkdir(parents=True, exist_ok=True)
    with schema_lease(reader.root / "publish.lock", timeout=0):
        raw = canonical(book)
        artifact = sha(raw)
        target = reader.root / "objects" / (artifact + ".json")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.read_bytes() != raw:
                raise ValueError("textbook_reader_object_changed")
        else:
            target.write_bytes(raw)
        if receipt is not None:
            proof = reader.root / "receipts" / (artifact + ".json")
            proof.parent.mkdir(parents=True, exist_ok=True)
            if proof.exists() and json.loads(proof.read_bytes()) != receipt:
                raise ValueError("textbook_reader_publication_receipt_changed")
            if not proof.exists():
                atomic_json(proof, receipt)
        index = reader.index()
        old = index["lessons"].get(book["course_id"])
        if old and old["artifact"] != artifact:
            index["history"].append({"course_id": book["course_id"], **old})
        index["lessons"][book["course_id"]] = {
            "artifact": artifact,
            "state": "review_draft",
        }
        atomic_json(reader.root / "catalog.json", index)
    return {
        "course_id": book["course_id"],
        "artifact": artifact,
        "state": "review_draft",
        "source_deletion_allowed": False,
    }


class Reader:
    def __init__(self, root):
        self.archive = Path(root).resolve()
        self.root = self.archive / "wiki/textbook-reading"
        self.cache = {}

    def index(self):
        path = self.root / "catalog.json"
        if not path.exists():
            return {"format": FORMAT, "lessons": {}, "history": []}
        value = json.loads(path.read_bytes())
        if value["format"] != FORMAT or not isinstance(value["history"], list):
            raise ValueError("textbook_reader_invalid_publication_index")
        for key, row in value["lessons"].items():
            if (
                not ID.fullmatch(key)
                or not DIGEST.fullmatch(row["artifact"])
                or row["state"] != "review_draft"
            ):
                raise ValueError("textbook_reader_invalid_publication_record")
        return value

    def object(self, artifact):
        if not DIGEST.fullmatch(artifact):
            raise ValueError("textbook_reader_invalid_reading_object")
        # Check disk integrity even when an earlier revision was cached.
        raw = (self.root / "objects" / (artifact + ".json")).read_bytes()
        if sha(raw) != artifact:
            raise ValueError("textbook_reader_reading_object_changed")
        if artifact not in self.cache:
            book = json.loads(raw)
            validate_book(book)
            self.cache[artifact] = book
        return self.cache[artifact]

    def article(self, course):
        if not ID.fullmatch(course):
            raise ArticleNotReady("textbook_reader_unknown_course")
        row = self.index()["lessons"].get(course)
        if row is None:
            raise ArticleNotReady("textbook_reader_article_still_organizing")
        book = self.object(row["artifact"])
        if book["course_id"] != course:
            raise ValueError("textbook_reader_course_identity_changed")
        value = {"article": book, "revision": row["artifact"], "state": "review_draft"}
        status = editorial_status(self.archive, row["artifact"])
        if status is not None:
            value.update(state="reading_ready", editorial_status=status)
        return value

    def catalog(self):
        index = self.index()
        source = self.archive / "library/catalog.json"
        originals = (
            Library(self.archive).catalog()["lessons"] if source.exists() else []
        )
        rows, seen = [], set()
        for lesson in originals:
            key = lesson["id"]
            if not ID.fullmatch(key) or key in seen:
                raise ValueError("textbook_reader_invalid_course_directory")
            seen.add(key)
            ready = key in index["lessons"]
            article = self.article(key) if ready else None
            title = article["article"]["title"] if ready else lesson["title"]
            rows.append(
                {
                    "id": key,
                    "title": title,
                    "collection": lesson.get("collection", "课程"),
                    "state": article["state"] if ready else "organizing",
                }
            )
        for key in index["lessons"]:
            if key not in seen:
                rows.append(
                    {
                        "id": key,
                        "title": self.article(key)["article"]["title"],
                        "collection": "课程",
                        "state": self.article(key)["state"],
                    }
                )
        return {
            "lessons": rows,
            "requested": len(rows),
            "reading_drafts": len(index["lessons"]),
            "checked_readings": sum(row["state"] == "reading_ready" for row in rows),
            "semantic_accuracy_verified": False,
            "source_deletion_allowed": False,
        }

    def search(self, query, *, limit=30):
        query = query.strip()[:200]
        if not 2 <= len(query) or type(limit) is not int or not 1 <= limit <= 50:
            return []
        tokens = query.casefold().split()
        found = []
        for course in self.index()["lessons"]:
            book = self.article(course)["article"]
            sections = [
                {
                    "id": "",
                    "heading": book["title"],
                    "paragraphs": book["introduction"]
                    + book["objectives"]
                    + book["prerequisites"]
                    + book["takeaways"],
                }
            ]
            sections += [
                {**section, "chapter_heading": chapter["heading"]}
                for chapter in book["chapters"]
                for section in chapter["sections"]
            ]
            sections += [
                {
                    "id": "reading-self-test",
                    "heading": "自测与参考答案",
                    "paragraphs": [
                        " ".join(
                            [
                                question["question"],
                                question["answer"],
                                question["explanation"],
                                *question["criteria"],
                            ]
                        )
                    ],
                }
                for question in book["quiz"]
            ]
            for section in sections:
                diagrams = section.get("diagrams", [])
                text = " ".join(
                    [
                        book["title"],
                        section.get("chapter_heading", ""),
                        section["heading"],
                        *section["paragraphs"],
                        *(
                            graph["title"]
                            + " "
                            + graph["caption"]
                            + " "
                            + " ".join(node["label"] for node in graph["nodes"])
                            + " "
                            + " ".join(edge["label"] for edge in graph["edges"])
                            for graph in diagrams
                        ),
                    ]
                )
                folded = text.casefold()
                if all(token in folded for token in tokens):
                    position = min(folded.find(token) for token in tokens)
                    start = max(0, position - 50)
                    found.append(
                        {
                            "course_id": course,
                            "title": book["title"],
                            "heading": section["heading"],
                            "section_id": section["id"],
                            "excerpt": text[start : start + 260],
                            "url": "/read/"
                            + course
                            + ("#" + section["id"] if section["id"] else ""),
                        }
                    )
        return found[:limit]


INDEX_STYLE = """
:root{color-scheme:light;color:#2f3437;background:#f7f6f3;font-family:'Segoe UI','Microsoft YaHei',sans-serif}
*{box-sizing:border-box}body{margin:0;line-height:1.8}main{max-width:68rem;margin:auto;padding:4rem 2rem 7rem}
a{color:inherit;text-underline-offset:.25em}.meta,.status{color:#666960;font-size:.88rem}header{padding:1rem 0 3rem;border-bottom:1px solid #eaeaea}
h1{font-family:Georgia,'Noto Serif CJK SC','SimSun',serif;font-size:clamp(2.4rem,6vw,4.5rem);line-height:1.2;letter-spacing:-.03em;margin:.5em 0}
h2{font-size:1.35rem;font-weight:600;margin:3rem 0 1rem}form{display:flex;gap:.7rem;margin:2rem 0;flex-wrap:wrap}
input{flex:1;min-width:12rem;padding:.8rem 1rem;background:#fff;border:1px solid #c4c8c1;border-radius:4px;font:inherit}
button{background:#111;color:#fff;border:0;border-radius:4px;padding:.7rem 1.5rem;font:inherit;cursor:pointer}button:active{transform:scale(.98)}
ul{padding:0;list-style:none}.course{display:grid;grid-template-columns:1fr auto;gap:1.5rem;border-bottom:1px solid #eaeaea;padding:1.2rem 0}.course a{font-weight:600}
.collection{display:block;font-size:.82rem;color:#666960}.badge{color:#346538;background:#edf3ec;padding:.2rem .6rem;font-size:.8rem;align-self:start;border-radius:4px}
.pending{font-size:.8rem;color:#666960;align-self:start;padding:.2rem 0}.result{margin-bottom:2rem}.result p{margin:.5rem 0;color:#555b53}
footer{margin-top:4rem;border-top:1px solid #eaeaea;padding-top:1.5rem;font-size:.88rem;color:#666960}:focus-visible{outline:3px solid #9b7837;outline-offset:4px}
@media(prefers-reduced-motion:no-preference){header,.results,.directory{animation:arrive .5s ease-out}@keyframes arrive{from{transform:translateY(12px);opacity:0}to{transform:translateY(0);opacity:1}}}
@media(max-width:600px){main{padding:2rem 1.2rem 4rem}.course{gap:.8rem}.collection{max-width:16rem}h1{font-size:2.5rem}}
"""


def reading_document(value, *, offline=False):
    from .course_textbook_gap_preview import reading_html

    book = value["article"]
    course = book["course_id"]
    links = "" if offline else (
        f'<p><a href="/">课程目录与搜索</a> · <a href="/export/{course}">保存离线文章</a></p>'
    )
    notice = (
        '<aside role="status" class="draft-notice">整篇阅读草稿 · 待验收。'
        '课件归属仍在核对，正文可先阅读和练习。</aside>'
    )
    if value.get("state") == "reading_ready" and value.get("editorial_status"):
        notice = ('<aside role="status" class="draft-notice">全文已整理并通过本地内容核对，'
                  '待你阅读确认。</aside>')
    content = reading_html(book).replace("<body><main>", "<body><main>" + links + notice, 1)
    if not offline:
        revision = value["revision"]
        if not DIGEST.fullmatch(revision):
            raise ValueError("textbook_reader_invalid_learning_revision")
        content = content.replace("<body>", f'<body data-article-revision="{revision}">', 1)
        content = content.replace("</head>", LEARNING_SCRIPTS + "</head>", 1)
        content = content.replace(notice, notice + learning_shell(), 1)
    return content.replace(
        "</style>",
        "h1,h2,h3,summary,nav a,figcaption,aside{overflow-wrap:anywhere}"
        ".draft-notice{padding:1rem;background:#fff0d4;margin:1rem 0 2rem}"
        + (LEARNING_STYLE if not offline else "") + "</style>", 1,
    )


def home_html(catalog, query="", results=()):
    esc = html.escape
    checked = catalog.get("checked_readings", 0)
    available = (f'{checked} 篇已整理文章 · {catalog["reading_drafts"] - checked} 篇待核对'
                 if checked else f'{catalog["reading_drafts"]} 篇阅读草稿')
    parts = [
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>课程知识库</title>',
        f'<style>{INDEX_STYLE}{LEARNING_STYLE}</style>{LEARNING_SCRIPTS}</head><body><main><header><p class="meta">本地阅读 · 理解与练习</p><h1>课程知识库</h1><p>按知识逻辑读完整文章，用解释图理解关系，用自测检验掌握。</p><p class="status">{catalog["requested"]} 份课程来源 · {available}</p></header>',
        '<form method="get" action="/"><label for="q" class="meta">搜索文章</label>',
        f'<input id="q" name="q" type="search" maxlength="200" value="{esc(query, quote=True)}" placeholder="输入想理解的概念或问题"><button type="submit">搜索</button></form>',
    ]
    if query:
        parts.append('<section class="results"><h2>文章中的结果</h2>')
        if not results:
            parts.append(
                '<p role="status">暂未找到匹配的阅读文章。整理中的课程还没有进入正文检索。</p>'
            )
        for row in results:
            parts.append(
                f'<article class="result"><a href="{esc(row["url"], quote=True)}">{esc(row["heading"])}</a><p>{esc(row["excerpt"])}</p><span class="meta">{esc(row["title"])}</span></article>'
            )
        parts.append("</section>")
    parts.append(learning_shell() + '<section class="directory"><h2>课程目录</h2><ul>')
    for row in catalog["lessons"]:
        label = (
            f'<a href="/read/{row["id"]}">{esc(row["title"])}</a>'
            if row["state"] in {"review_draft", "reading_ready"}
            else f"<span>{esc(row['title'])}</span>"
        )
        state = (
            '<span class="badge">阅读草稿 · 待复核</span>'
            if row["state"] == "review_draft"
            else '<span class="pending">整理中</span>'
        )
        if row["state"] == "reading_ready":
            state = '<span class="badge">已整理 · 可阅读</span>'
        parts.append(
            f'<li class="course"><div>{label}<span class="collection">{esc(row["collection"])}</span></div>{state}</li>'
        )
    parts.append(
        "</ul></section><footer>课程中的历史版本与工具信息以授课语境为准。</footer></main></body></html>"
    )
    return "".join(parts)


def create_app(root):
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import HTMLResponse, Response

    reader = Reader(root)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
        )
        return response

    def safe(operation, *args):
        try:
            return operation(*args)
        except ArticleNotReady:
            raise HTTPException(404, "这节课程仍在整理中，尚无完整阅读文章。") from None
        except (
            ValueError,
            OSError,
            TypeError,
            KeyError,
            AttributeError,
            jsonschema.ValidationError,
        ):
            raise HTTPException(503, "阅读文章校验未通过，暂不可用。") from None

    @app.get("/api/catalog")
    def catalog():
        return safe(reader.catalog)

    @app.get("/api/article/{course}")
    def article(course: str):
        return safe(reader.article, course)

    @app.get("/api/search")
    def search(q: str = ""):
        return {"results": safe(reader.search, q)}

    @app.get("/assets/{name}")
    def learning_asset(name: str):
        if name not in LEARNING_ASSETS:
            raise HTTPException(404, "资源不存在。")
        asset = Path(__file__).parent / "resources" / LEARNING_ASSETS[name]
        return Response(asset.read_bytes(), media_type="text/javascript")

    @app.get("/", response_class=HTMLResponse)
    def home(q: str = ""):
        return home_html(safe(reader.catalog), q[:200], safe(reader.search, q))

    @app.get("/read/{course}", response_class=HTMLResponse)
    def read_article(course: str):
        return reading_document(safe(reader.article, course))

    @app.get("/export/{course}")
    def export(course: str):
        return Response(
            reading_document(safe(reader.article, course), offline=True),
            media_type="text/html",
            headers={
                "Content-Disposition": f'attachment; filename="course-{course}.html"'
            },
        )

    return app


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=3005)
    release = sub.add_parser("publish")
    release.add_argument("--job", required=True)
    reviewed = sub.add_parser("publish-reviewed")
    reviewed.add_argument("--job", required=True)
    args = parser.parse_args(argv)
    if args.command == "publish":
        print(json.dumps(publish(args.root, args.job)))
    elif args.command == "publish-reviewed":
        print(json.dumps(publish_reviewed(args.root, args.job)))
    else:
        import uvicorn

        uvicorn.run(
            create_app(args.root), host="127.0.0.1", port=args.port, access_log=False
        )


if __name__ == "__main__":
    main()
