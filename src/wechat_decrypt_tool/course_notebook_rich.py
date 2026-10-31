"""Retained notebook DOM tables and widget-state evidence, without executing code."""

from __future__ import annotations

import hashlib
import html
import json
import shutil
import tempfile
import time
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

from .course_attachments import resolve_reference, retained_reference
from .course_notebook_outputs import MAX_BYTES, NOTEBOOK_CREDENTIAL
from .course_text_resources import redact_credentials

EXTRACTOR = "notebook-rich-static-v1/dom-tables-widget-state-no-execution"
NOTICE = "已保存 DOM 文本与表格结构；这是静态重建，不代表原 CSS 外观、JavaScript、外部资源或交互运行结果。"
ALLOWED = set(
    "div span p pre code b strong i em u s ul ol li br hr h1 h2 h3 h4 h5 h6 blockquote sup sub details summary dl dt dd table thead tbody tfoot tr th td caption".split()
)
VOID = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
IGNORED = {"script", "style", "iframe", "object", "noscript", "template"}


def canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
        separators=(",", ":"),
    )


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def masked(value):
    if isinstance(value, dict):
        return {
            k: "[REDACTED]" if NOTEBOOK_CREDENTIAL.search(k) else masked(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [masked(v) for v in value]
    if isinstance(value, str):
        return redact_credentials(value, sensitive_pattern=NOTEBOOK_CREDENTIAL)[0]
    return value


class StaticHTML(HTMLParser):
    """Keep inert structural markup and table cell values; never evaluate styles/code."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.text, self.tables, self.dependencies = [], [], [], []
        self.stack, self.table_stack, self.ignored = [], [], []
        self.omitted = Counter()
        self.nodes = 0

    def handle_starttag(self, tag, attrs):
        self.nodes += 1
        if self.nodes > 200000 or len(self.stack) > 256:
            raise ValueError("Notebook HTML node/depth bound exceeded")
        attributes = dict(attrs)
        for name in ("src", "href", "poster", "data", "srcset", "action"):
            if attributes.get(name):
                self.dependencies.append(
                    {"tag": tag, "attribute": name, "value": masked(attributes[name])}
                )
        if self.ignored:
            if tag not in VOID:
                self.ignored.append(tag)
            return
        if tag in IGNORED:
            self.omitted[tag] += 1
            self.ignored.append(tag)
            self.parts.append("<p>（已保留原件：此处运行内容未执行）</p>")
            return
        if tag == "table":
            table = {"table": len(self.tables) + 1, "rows": []}
            self.tables.append(table)
            self.table_stack.append(table)
        if tag == "tr" and self.table_stack:
            rows = self.table_stack[-1]["rows"]
            rows.append({"row": len(rows) + 1, "cells": []})
        spans = {}
        if tag in ("td", "th") and self.table_stack:
            rows = self.table_stack[-1]["rows"]
            if not rows:
                raise ValueError("HTML table cell has no explicit source row")
            for name in ("rowspan", "colspan"):
                number = int(attributes.get(name, "1"))
                if not 1 <= number <= 1000:
                    raise ValueError("HTML table span bound exceeded")
                spans[name] = number
            rows[-1]["cells"].append(
                {
                    "cell": len(rows[-1]["cells"]) + 1,
                    "header": tag == "th",
                    **spans,
                    "text": "",
                }
            )
        if tag == "img":
            self.omitted["image_not_loaded"] += 1
            self.handle_data(
                "（图像未加载；原件和引用已保留）" + attributes.get("alt", "")
            )
        if tag == "br":
            self.handle_data("\n")
        if tag == "input":
            self.omitted["interactive_input"] += 1
            self.handle_data("（静态输入控件）" + attributes.get("value", ""))
        if tag in ALLOWED:
            safe_attrs = "".join(
                f' {name}="{number}"' for name, number in spans.items()
            )
            self.parts.append("<" + tag + safe_attrs + ">")
            if tag not in VOID:
                self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.ignored:
            if tag in self.ignored:
                index = len(self.ignored) - 1 - self.ignored[::-1].index(tag)
                del self.ignored[index:]
            return
        if tag == "table" and self.table_stack:
            self.table_stack.pop()
        if tag in self.stack:
            while self.stack:
                current = self.stack.pop()
                self.parts.append("</" + current + ">")
                if current == tag:
                    break
        if tag in {"p", "div", "tr", "li", "br"}:
            self.text.append("\n")

    def handle_data(self, value):
        if self.ignored:
            return
        value = masked(value)
        self.parts.append(html.escape(value))
        self.text.append(value)
        # Nested tables keep their own cells; outer cell text deliberately
        # excludes nested cell text rather than silently flattening its rows.
        if self.table_stack and self.table_stack[-1]["rows"]:
            cells = self.table_stack[-1]["rows"][-1]["cells"]
            if cells and any(tag in ("td", "th") for tag in self.stack):
                cells[-1]["text"] += value

    def finish(self):
        self.close()
        unclosed = bool(self.stack or self.ignored or self.table_stack)
        while self.stack:
            self.parts.append("</" + self.stack.pop() + ">")
        return {
            "dom_text": "".join(self.text),
            "tables": self.tables,
            "safe_markup": "".join(self.parts),
            "dependencies_not_loaded": self.dependencies,
            "omitted_runtime_elements": dict(self.omitted),
            "unclosed_source_structure": unclosed,
            "original_css_not_applied": True,
            "runtime_executed": False,
            "notice": NOTICE,
        }


def widget_references(value):
    if isinstance(value, str) and value.startswith("IPY_MODEL_"):
        yield value[len("IPY_MODEL_") :]
    elif isinstance(value, dict):
        for item in value.values():
            yield from widget_references(item)
    elif isinstance(value, list):
        for item in value:
            yield from widget_references(item)


def extract_rich(raw, checkpoint=lambda: None):
    if len(raw) > MAX_BYTES:
        raise ValueError("Notebook rich input byte bound exceeded")
    notebook = json.loads(raw.decode("utf-8-sig"))
    states = notebook.get("metadata", {}).get("widgets", {})
    widget_state = states.get("application/vnd.jupyter.widget-state+json", {}).get(
        "state", {}
    )
    if len(widget_state) > 4096:
        raise ValueError("Notebook widget model bound exceeded")
    records = []
    for n, cell in enumerate(notebook["cells"], 1):
        checkpoint()
        for output_n, output in enumerate(cell.get("outputs", []), 1):
            for mime, original in sorted(output.get("data", {}).items()):
                if mime not in (
                    "text/html",
                    "application/javascript",
                ) and not mime.startswith("application/vnd."):
                    continue
                value = (
                    "".join(original)
                    if isinstance(original, list)
                    and all(isinstance(x, str) for x in original)
                    else original
                )
                encoded = (
                    value
                    if isinstance(value, str)
                    else json.dumps(
                        value, ensure_ascii=False, sort_keys=True, allow_nan=False
                    )
                ).encode()
                record = {
                    "cell": n,
                    "output": output_n,
                    "mime": mime,
                    "representation_sha256": digest(encoded),
                    "runtime_executed": False,
                }
                if mime == "text/html":
                    if not isinstance(value, str) or len(value) > 4 * 1024**2:
                        raise ValueError("Notebook HTML type/character bound exceeded")
                    parser = StaticHTML()
                    parser.feed(value)
                    record.update(kind="html_dom", **parser.finish())
                elif mime == "application/vnd.jupyter.widget-view+json":
                    root_model = value.get("model_id")
                    pending = [root_model] if isinstance(root_model, str) else []
                    visited, missing, models = set(), set(), {}
                    while pending:
                        model_id = pending.pop()
                        if model_id in visited:
                            continue
                        visited.add(model_id)
                        if model_id not in widget_state:
                            missing.add(model_id)
                            continue
                        model = widget_state[model_id]
                        models[model_id] = masked(model)
                        pending.extend(widget_references(model.get("state", {})))
                    record.update(
                        kind="widget_view",
                        model_id=root_model,
                        retained_models=models,
                        missing_model_ids=sorted(missing),
                        root_model_resolved=isinstance(root_model, str)
                        and root_model in models,
                        interactive_runtime_reproduced=False,
                        notice="仅保留已保存的控件属性及引用关系；未运行控件或还原交互。缺少状态的视图无法从本文件恢复。",
                    )
                else:
                    record.update(
                        kind="inert_vendor_or_script",
                        recorded_data=masked(value),
                        notice="原始表示已保留并作为惰性数据展示，运行行为未执行或恢复。",
                    )
                records.append(record)
    for mime, value in sorted(states.items()):
        records.append(
            {
                "kind": "widget_state",
                "mime": mime,
                "retained_state": masked(value),
                "representation_sha256": digest(
                    json.dumps(
                        value, sort_keys=True, ensure_ascii=False, allow_nan=False
                    ).encode()
                ),
                "runtime_executed": False,
                "notice": "Notebook 已保存的控件状态；不证明交互可复现。",
            }
        )
    if len(canonical(records)) > 32 * 1024**2:
        raise ValueError("Notebook rich extraction character bound exceeded")
    return records


def render_review(record):
    header = "<!doctype html><meta charset=\"utf-8\"><meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'; form-action 'none'; base-uri 'none'\"><style>body{max-width:1100px;margin:32px auto;padding:0 20px;font:16px/1.7 system-ui}table{border-collapse:collapse}td,th{border:1px solid #bbb;padding:6px}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>"
    body = "<h1>Notebook 静态记录</h1><p>" + html.escape(record["notice"]) + "</p>"
    if record["kind"] == "html_dom":
        body += record["safe_markup"]
        body += (
            "<h2>未加载的资源引用及限制</h2><pre>"
            + html.escape(
                canonical(
                    {
                        k: record[k]
                        for k in (
                            "dependencies_not_loaded",
                            "omitted_runtime_elements",
                            "unclosed_source_structure",
                            "original_css_not_applied",
                        )
                    }
                )
            )
            + "</pre>"
        )
    else:
        body += (
            "<pre>"
            + html.escape(
                json.dumps(
                    record,
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                    allow_nan=False,
                )
            )
            + "</pre>"
        )
    return header + body


def index_rich_notebooks(store, audit_path):
    targets = json.loads(Path(audit_path).read_text("utf-8"))["completed"]
    completed, errors, parsed, seen = [], [], {}, set()
    directory = store.root / "document-reviews"
    directory.mkdir(exist_ok=True)
    totals = Counter()
    for target in targets:
        pointer = (target["path"], target["source_key"])
        if pointer in seen:
            raise ValueError("Duplicate rich notebook audit identity")
        seen.add(pointer)
        store.checkpoint()
        store.write_json(
            "notebook-rich-progress.json",
            {
                "stage": "indexing_retained_static_outputs",
                "pid": __import__("os").getpid(),
                "completed": len(completed),
                "total": len(targets),
                "errors": len(errors),
                "updated": time.time(),
            },
        )
        try:
            with store.connect() as db:
                parent = db.execute(
                    "SELECT raw_digest FROM resource_revisions WHERE path=? AND source_key=?",
                    pointer,
                ).fetchone()
            if parent is None or parent[0] != target["raw_digest"]:
                raise RuntimeError("Rich notebook parent is missing or changed")
            sha = target["raw_digest"]
            if sha not in parsed:
                source = resolve_reference(
                    store, retained_reference(store, *pointer, sha)
                )["path"]
                if source.stat().st_size > MAX_BYTES:
                    raise ValueError("Retained rich notebook input byte bound exceeded")
                raw = source.read_bytes()
                if digest(raw) != sha:
                    raise RuntimeError("Retained rich notebook hash differs")
                parsed[sha] = extract_rich(raw, store.checkpoint)
            records = parsed[sha]
            counts = Counter()
            for n, record in enumerate(records, 1):
                store.checkpoint()
                key = "notebook rich " + target["source_key"] + " record " + str(n)
                page = render_review(record).encode("utf-8")
                record_digest = digest(canonical(record).encode())
                page_digest = digest(page)
                relative = "document-reviews/" + record_digest + ".html"
                path = store.root / relative
                from .course_knowledge import atomic_json

                manifest = directory / (record_digest + ".json")
                if manifest.exists():
                    if json.loads(manifest.read_text("utf-8")) != record:
                        raise RuntimeError("Immutable notebook rich record changed")
                else:
                    atomic_json(manifest, record)
                if path.exists():
                    if digest(path.read_bytes()) != page_digest:
                        raise RuntimeError("Immutable notebook review asset changed")
                else:
                    if shutil.disk_usage(store.root).free < 32 * 1024**3:
                        raise InterruptedError(
                            "Notebook rich review disk reserve reached"
                        )
                    temporary = None
                    try:
                        with tempfile.NamedTemporaryFile(
                            dir=directory, delete=False
                        ) as stream:
                            temporary = Path(stream.name)
                            stream.write(page)
                        temporary.replace(path)
                    finally:
                        if temporary is not None:
                            temporary.unlink(missing_ok=True)
                index_record = {k: v for k, v in record.items() if k != "safe_markup"}
                text = canonical(index_record)
                metadata = {
                    "parent_source_key": target["source_key"],
                    "parent_raw_digest": sha,
                    "review_page": relative,
                    "review_page_digest": page_digest,
                    "rich_record": "document-reviews/" + record_digest + ".json",
                    "rich_record_digest": digest(manifest.read_bytes()),
                    "rich_kind": record["kind"],
                    "representation_sha256": record["representation_sha256"],
                    "runtime_executed": False,
                    "interactive_runtime_reproduced": False,
                }
                store.save_document_versions(
                    [
                        {
                            "path": target["path"],
                            "source_key": key,
                            "raw_digest": sha,
                            "extractor": EXTRACTOR,
                            "chunks": [
                                (
                                    key + " chars " + str(offset),
                                    text[offset : offset + 3000],
                                )
                                for offset in range(0, len(text), 3000)
                            ],
                            "metadata": metadata,
                        }
                    ]
                )
                counts[record["kind"]] += 1
                if (
                    record["kind"] == "widget_view"
                    and not record["root_model_resolved"]
                ):
                    counts["widget_views_missing_saved_state"] += 1
                if record["kind"] == "html_dom":
                    counts["html_tables"] += len(record["tables"])
                    counts["html_table_cells"] += sum(
                        len(row["cells"])
                        for table in record["tables"]
                        for row in table["rows"]
                    )
            totals.update(counts)
            completed.append(
                {**target, "records": len(records), "counts": dict(counts)}
            )
        except InterruptedError:
            raise
        except Exception as exc:
            errors.append(
                {**target, "error_type": type(exc).__name__, "reason": str(exc)}
            )
        store.write_json(
            "notebook-rich-report.json",
            {
                "completed": completed,
                "errors": errors,
                "requested": len(targets),
                "counts": dict(totals),
                "runtime_executed": False,
                "full_requested_scope_attempted": len(completed) + len(errors)
                == len(targets),
            },
        )
    store.write_json(
        "notebook-rich-progress.json",
        {
            "stage": "finished_with_errors" if errors else "batch_finished",
            "pid": __import__("os").getpid(),
            "completed": len(completed),
            "errors": len(errors),
            "total": len(targets),
            "updated": time.time(),
        },
    )
    return {
        "completed": completed,
        "errors": errors,
        "counts": dict(totals),
        "requested": len(targets),
    }


def main():
    import argparse
    from .course_knowledge import KnowledgeStore, schema_lease

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    args = parser.parse_args()
    store = KnowledgeStore(args.root)
    with schema_lease(store.root / "notebook-rich.lock", timeout=0):
        result = index_rich_notebooks(store, args.audit)
    print(
        json.dumps(
            {
                "completed": len(result["completed"]),
                "errors": len(result["errors"]),
                "counts": result["counts"],
            }
        )
    )


if __name__ == "__main__":
    main()
