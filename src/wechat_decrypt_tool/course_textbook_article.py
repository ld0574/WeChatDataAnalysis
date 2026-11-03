"""Clean reading articles and self-contained HTML, separate from private proof.

Only authored prose, new explanatory graphs and answered exercises cross this
boundary. Original screenshots, OCR, transcripts and provenance stay private.
This module validates delivery structure, not semantic correctness or coverage.
"""

from __future__ import annotations

import copy
import html
import json
import re
import unicodedata

import jsonschema

from .course_wiki import TEXT, object_schema

FORMAT = "course-textbook-reading-v1"
SLUG = {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"}
COURSE_ID = {"type": "string", "pattern": r"^[a-f0-9]{24}$"}
TEXTS = {"type": "array", "items": TEXT}
GRAPH = object_schema(
    {
        "kind": {"type": "string", "enum": ["flow", "relationships"]},
        "title": TEXT,
        "caption": TEXT,
        "nodes": {
            "type": "array",
            "minItems": 2,
            "maxItems": 16,
            "items": object_schema({"id": SLUG, "label": TEXT}),
        },
        "edges": {
            "type": "array",
            "minItems": 1,
            "maxItems": 32,
            "items": object_schema(
                {"from": SLUG, "to": SLUG, "label": {"type": "string", "maxLength": 48}}
            ),
        },
    }
)
SECTION = object_schema(
    {
        "id": SLUG,
        "heading": TEXT,
        "paragraphs": {**TEXTS, "minItems": 1},
        "diagrams": {"type": "array", "items": GRAPH},
    }
)
CHAPTER = object_schema(
    {
        "id": SLUG,
        "heading": TEXT,
        "sections": {"type": "array", "minItems": 1, "items": SECTION},
    }
)
QUIZ = object_schema(
    {
        "question": TEXT,
        "answer": TEXT,
        "explanation": TEXT,
        "criteria": {**TEXTS, "minItems": 1},
    }
)
BOOK = object_schema(
    {
        "format": {"const": FORMAT},
        "course_id": COURSE_ID,
        "title": TEXT,
        "introduction": {**TEXTS, "minItems": 1},
        "objectives": {**TEXTS, "minItems": 1},
        "prerequisites": TEXTS,
        "chapters": {"type": "array", "minItems": 1, "items": CHAPTER},
        "takeaways": {**TEXTS, "minItems": 1},
        "quiz": {"type": "array", "minItems": 1, "items": QUIZ},
        "related_courses": {"type": "array", "items": COURSE_ID, "uniqueItems": True},
    }
)


def validate_graph(graph):
    jsonschema.validate(graph, GRAPH)
    ids = [node["id"] for node in graph["nodes"]]
    if len(ids) != len(set(ids)):
        raise ValueError("textbook_diagram_duplicate_node")
    endpoints = {ref for edge in graph["edges"] for ref in (edge["from"], edge["to"])}
    if endpoints != set(ids):
        raise ValueError("textbook_diagram_unknown_or_unconnected_node")
    for node in graph["nodes"]:
        if not node["label"].strip():
            raise ValueError("textbook_diagram_empty_label")
    return graph


def validate_book(book):
    jsonschema.validate(book, BOOK)
    ids = []
    if book["course_id"] in book["related_courses"]:
        raise ValueError("textbook_article_related_course_is_self")
    for chapter in book["chapters"]:
        ids.append(chapter["id"])
        for section in chapter["sections"]:
            ids.append(section["id"])
            if any(not paragraph.strip() for paragraph in section["paragraphs"]):
                raise ValueError("textbook_article_empty_paragraph")
            for graph in section["diagrams"]:
                validate_graph(graph)
    if len(ids) != len(set(ids)) or "reading-self-test" in ids:
        raise ValueError("textbook_article_duplicate_reading_anchor")
    return book


def reading_book(private):
    """An explicit projection: never return or serialize the evidence packet."""

    def claims(values):
        return [row["text"] for row in values]

    chapters = []
    for chapter in private["chapters"]:
        sections = []
        for section in chapter["sections"]:
            diagrams = []
            for graph in section["diagrams"]:
                diagrams.append(
                    {
                        **{key: graph[key] for key in ("kind", "title", "caption")},
                        "nodes": [
                            {key: node[key] for key in ("id", "label")}
                            for node in graph["nodes"]
                        ],
                        "edges": [
                            {key: edge[key] for key in ("from", "to", "label")}
                            for edge in graph["edges"]
                        ],
                    }
                )
            sections.append(
                {
                    "id": section["id"],
                    "heading": section["heading"],
                    "paragraphs": claims(section["paragraphs"]),
                    "diagrams": diagrams,
                }
            )
        chapters.append(
            {"id": chapter["id"], "heading": chapter["heading"], "sections": sections}
        )
    book = {
        "format": FORMAT,
        "course_id": private["course_id"],
        "title": private["title"],
        "introduction": claims(private["introduction"]),
        "objectives": list(private["objectives"]),
        "prerequisites": claims(private["prerequisites"]),
        "chapters": chapters,
        "takeaways": claims(private["takeaways"]),
        "quiz": [
            {
                **{key: question[key] for key in ("question", "answer", "explanation")},
                "criteria": list(question["criteria"]),
            }
            for question in private["quiz"]
        ],
        "related_courses": list(private["related_courses"]),
    }
    validate_book(book)
    return copy.deepcopy(book)


def wrapped_lines(text, maximum=48):
    lines, current, width = [], "", 0
    for character in text:
        size = 2 if unicodedata.east_asian_width(character) in {"W", "F"} else 1
        if character == "\n" or (current and width + size > maximum):
            lines.append(current)
            current, width = "", 0
            if character == "\n":
                continue
        current += character
        width += size
    if current:
        lines.append(current)
    return lines or [""]


def diagram_svg(graph, *, prefix="diagram"):
    validate_graph(graph)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", prefix):
        raise ValueError("textbook_diagram_invalid_prefix")
    esc = html.escape
    rows, by_id, y = [], {}, 64
    for node in graph["nodes"]:
        lines = wrapped_lines(node["label"])
        row = {
            "id": node["id"],
            "lines": lines,
            "top": y,
            "height": 36 + len(lines) * 24,
        }
        rows.append(row)
        by_id[node["id"]] = row
        label_lines = max(
            (
                len(wrapped_lines(edge["label"], 18))
                for edge in graph["edges"]
                if edge["from"] == node["id"]
            ),
            default=0,
        )
        y += row["height"] + max(72, label_lines * 17 + 32)
    height = y - 30
    result = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 {height}" role="img" aria-labelledby="{prefix}-title {prefix}-description">',
        f'<title id="{prefix}-title">{esc(graph["title"])}</title>',
        f'<desc id="{prefix}-description">{esc(graph["caption"])}</desc>',
        f'<defs><marker id="{prefix}-arrow" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto"><path d="M0 0 L9 4.5 L0 9 Z" fill="#285743"/></marker></defs>',
    ]
    for index, edge in enumerate(graph["edges"]):
        first, second = by_id[edge["from"]], by_id[edge["to"]]
        first_index, second_index = rows.index(first), rows.index(second)
        if second_index == first_index + 1:
            y1, y2 = first["top"] + first["height"], second["top"]
            path = f"M400 {y1} V{y2}"
            label_x, label_y = 415, (y1 + y2) / 2
        else:
            side = 730 + (index % 3) * 14
            y1, y2 = (
                first["top"] + first["height"] / 2,
                second["top"] + second["height"] / 2,
            )
            path = f"M660 {y1} H{side} V{y2} H660"
            label_x, label_y = 670, y1
        result.append(
            f'<path d="{path}" fill="none" stroke="#285743" stroke-width="2" marker-end="url(#{prefix}-arrow)"/>'
        )
        if edge["label"]:
            lines = wrapped_lines(edge["label"], 18)
            label_y -= (len(lines) - 1) * 8.5
            for number, line in enumerate(lines):
                result.append(
                    f'<text x="{label_x}" y="{label_y + number * 17}" fill="#435f50" font-size="14" font-family="sans-serif">{esc(line)}</text>'
                )
    for row in rows:
        result.append(
            f'<rect x="140" y="{row["top"]}" width="520" height="{row["height"]}" rx="8" fill="#edf2ec" stroke="#a8bcae"/>'
        )
        for number, line in enumerate(row["lines"]):
            result.append(
                f'<text x="400" y="{row["top"] + 29 + number * 24}" text-anchor="middle" fill="#253d30" font-size="18" font-family="sans-serif">{esc(line)}</text>'
            )
    result.append("</svg>")
    return "".join(result)


STYLE = """
:root{color-scheme:light;font-family:'Segoe UI','Microsoft YaHei',sans-serif;color:#272e2b;background:#f7f6f2}
*{box-sizing:border-box}body{margin:0;line-height:1.9}a{color:#285743;text-underline-offset:.2em}
main{max-width:53rem;margin:auto;padding:3rem 1.5rem 6rem}header{padding-bottom:2rem;border-bottom:1px solid #d9dfd8}
h1,h2,h3{line-height:1.4;text-wrap:balance}h1{font-size:clamp(1.9rem,5vw,2.8rem)}h2{margin-top:3.8rem;font-size:1.8rem}
h3{margin-top:2.2rem;font-size:1.3rem}p,li{text-wrap:pretty}nav{margin:2rem 0;padding:1rem 1.5rem;background:#edf2ec}
nav a{display:block;padding:.25rem 0}figure{margin:2rem 0}figcaption{color:#69716b;font-size:.9rem;margin-top:.6rem}
.diagram-scroll{overflow:auto;border:1px solid #d9dfd8;border-radius:.5rem;background:#fffefa}
svg{display:block;min-width:40rem;width:100%;height:auto}details{padding:1rem 0;border-bottom:1px solid #d9dfd8}
summary{cursor:pointer;font-weight:600}details p{margin:.75rem 0}.eyebrow,footer{color:#69716b;font-size:.85rem}
footer{margin-top:4rem;border-top:1px solid #d9dfd8;padding-top:1.5rem}:focus-visible{outline:3px solid #a67824;outline-offset:4px}
@media print{main{max-width:none;padding:0}nav{break-after:page}.diagram-scroll{overflow:visible}svg{min-width:0}figure,details{break-inside:avoid}}
"""


def standalone_html(book):
    """Export a script-free article with safe SVG; no source asset dependencies."""
    validate_book(book)
    esc = html.escape

    def paragraphs(texts):
        return "".join(f"<p>{esc(text)}</p>" for text in texts)

    def bullets(texts):
        return "<ul>" + "".join(f"<li>{esc(text)}</li>" for text in texts) + "</ul>"

    result = [
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>{esc(book['title'])}</title><style>{STYLE}</style></head><body><main>",
        f'<header><p class="eyebrow">课程文章 · 整理稿</p><h1>{esc(book["title"])}</h1>{paragraphs(book["introduction"])}</header>',
        '<nav aria-label="文章目录">',
    ]
    result.extend(
        f'<a href="#{chapter["id"]}">{esc(chapter["heading"])}</a>'
        for chapter in book["chapters"]
    )
    result.append('<a href="#reading-self-test">自测与参考答案</a></nav>')
    result.append("<h2>读完能掌握什么</h2>" + bullets(book["objectives"]))
    if book["prerequisites"]:
        result.append("<h2>先备知识</h2>" + bullets(book["prerequisites"]))
    diagram_index = 0
    for chapter in book["chapters"]:
        result.append(
            f'<section id="{chapter["id"]}"><h2>{esc(chapter["heading"])}</h2>'
        )
        for section in chapter["sections"]:
            result.append(
                f'<section id="{section["id"]}"><h3>{esc(section["heading"])}</h3>{paragraphs(section["paragraphs"])}'
            )
            for index, graph in enumerate(section["diagrams"]):
                prefix = "diagram-" + str(diagram_index)
                diagram_index += 1
                result.append(
                    '<figure><div class="diagram-scroll">'
                    + diagram_svg(graph, prefix=prefix)
                    + "</div><figcaption>"
                    + esc(graph["caption"])
                    + "</figcaption></figure>"
                )
            result.append("</section>")
        result.append("</section>")
    result.append(
        "<section><h2>关键结论</h2>"
        + bullets(book["takeaways"])
        + '</section><section id="reading-self-test"><h2>自测与参考答案</h2><p>先独立作答，再展开参考答案，按掌握标准检查自己的解释。</p>'
    )
    for index, question in enumerate(book["quiz"], 1):
        result.append(
            f"<section><h3>{index}. {esc(question['question'])}</h3><details><summary>参考答案与掌握标准</summary><p>{esc(question['answer'])}</p><p>{esc(question['explanation'])}</p>{bullets(question['criteria'])}</details></section>"
        )
    result.append(
        "</section><footer>本地整理稿。内容、图示及自测仍需质量复核；学习判断以自己的解释和练习为准。</footer></main></body></html>"
    )
    return "".join(result)


def reading_bytes(private):
    book = reading_book(private)
    return json.dumps(book, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
