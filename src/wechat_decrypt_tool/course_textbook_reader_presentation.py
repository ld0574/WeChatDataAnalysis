"""Safe article formatting and a compact interactive practice surface."""
from __future__ import annotations

import html
import re
import textwrap
from pathlib import Path
from urllib.parse import urlsplit

from markdown_it import MarkdownIt

from . import course_textbook_practice as practice
from .course_textbook_article import standalone_html

DIVIDER = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")
STYLE = """
pre{padding:1rem;background:#eef0eb;border:1px solid #d9dfd8;white-space:pre-wrap;overflow-wrap:anywhere;tab-size:4}
code{font-family:Consolas,'Cascadia Code',monospace;font-size:.88em}p,li{overflow-wrap:anywhere}
.table-scroll{max-width:100%;overflow-x:auto;margin:1.5rem 0;border:1px solid #d9dfd8;border-radius:6px;background:#fffefa}
table{border-collapse:collapse;min-width:12rem;max-width:100%;margin:0}th,td{min-width:3rem;padding:.65rem 1rem;border:1px solid #d9dfd8;text-align:center;vertical-align:middle;line-height:1.6}
th{background:#edf2ec;font-weight:600}.table-scroll:focus-visible{outline:3px solid #a67824;outline-offset:3px}
"""


def readable_table_source(text):
    lines = textwrap.dedent(text).splitlines()
    result, divider_seen = [], False
    for index, line in enumerate(lines):
        if DIVIDER.fullmatch(line):
            if divider_seen:
                continue
            # Some generated grids include a row-label column in the header,
            # but omit its separator cell. Keep every header and body cell;
            # only supply the missing formatting marker.
            if result and "|" in result[-1] and "\\" not in result[-1]:
                header_cells = result[-1].strip().strip("|").split("|")
                divider_cells = line.strip().strip("|").split("|")
                if len(header_cells) == len(divider_cells) + 1:
                    line = "|" + "|".join([*divider_cells, "---"]) + "|"
            if index > 1 and "|" in lines[index - 1] and lines[index - 2].strip() and "|" not in lines[index - 2]:
                result.insert(len(result) - 1, "")
            divider_seen = True
        elif "|" not in line and line.strip():
            divider_seen = False
        result.append(line)
    return "\n".join(result)


def markdown_renderer():
    markdown = MarkdownIt("commonmark", {"html": False, "linkify": False}).enable("table")
    markdown.renderer.rules["image"] = lambda tokens, index, options, env: html.escape(tokens[index].content)

    def link_open(tokens, index, options, env):
        url = tokens[index].attrGet("href") or ""
        if not (url.startswith("#") or urlsplit(url).scheme.lower() in {"http", "https"}):
            return '<a aria-disabled="true">'
        return '<a rel="noreferrer" href="' + html.escape(url, quote=True) + '">'

    original_fence = markdown.renderer.rules["fence"]

    def fence(tokens, index, options, env):
        token = tokens[index]
        if token.info.strip().casefold() in {"", "text", "txt", "markdown", "md"} and not env.get("inside_table_fence"):
            text = readable_table_source(token.content)
            if any(DIVIDER.fullmatch(line) for line in text.splitlines()):
                parsed = markdown.parse(text, {"inside_table_fence": True})
                if any(row.type == "table_open" for row in parsed) and not any(row.type in {"fence", "code_block"} for row in parsed):
                    return markdown.renderer.render(parsed, options, {"inside_table_fence": True})
        return original_fence(tokens, index, options, env)

    markdown.renderer.rules["link_open"] = link_open
    markdown.renderer.rules["fence"] = fence
    markdown.renderer.rules["table_open"] = lambda *args: '<div class="table-scroll" role="region" tabindex="0" aria-label="课程表格"><table>\n'
    markdown.renderer.rules["table_close"] = lambda *args: '</table></div>\n'
    markdown.renderer.rules["th_open"] = lambda *args: '<th scope="col">'
    return markdown


def reading_html(book, pack=None, *, offline=False):
    markdown = markdown_renderer()
    content = standalone_html(book)
    start = content.index('<section id="reading-self-test">')
    end = content.index("</section><footer>", start) + len("</section>")
    # No private quotes or provenance are included in the offline exercise key.
    rendered_pack = None if pack is None else {**pack, "questions": [
        {key: row for key, row in item.items() if key != "support"} for item in pack["questions"]]}
    content = content[:start] + practice.practice_html(rendered_pack, book["course_id"], offline=offline) + content[end:]
    paragraphs = [*book["introduction"], *book["prerequisites"]]
    for chapter in book["chapters"]:
        for section in chapter["sections"]:
            paragraphs.extend(section["paragraphs"])
    for text in set(paragraphs):
        content = content.replace("<p>" + html.escape(text) + "</p>", markdown.render(text))
    for text in set([*book["objectives"], *book["takeaways"]]):
        content = content.replace("<li>" + html.escape(text) + "</li>", "<li>" + markdown.renderInline(text) + "</li>")
    content = content.replace('href="#reading-self-test">自测与参考答案', 'href="#reading-self-test">课后自测')
    content = content.replace("</style>", STYLE + practice.STYLE + "</style>", 1)
    if pack:
        if offline:
            script = (Path(__file__).parent / "resources/course_textbook_practice.js").read_text("utf-8")
            content = content.replace("</body>", "<script>" + script + "</script></body>", 1)
        else:
            content = content.replace("</body>", '<script defer src="/assets/course-practice.js"></script></body>', 1)
    return content
