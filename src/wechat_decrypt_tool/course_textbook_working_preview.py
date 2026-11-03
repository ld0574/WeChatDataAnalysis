"""Read-only, explicitly unfinished snapshots of a running manuscript.

This is a local work preview, never a publication or quality acceptance. It
does not render originals, allocate model calls, or alter the running writer.
"""

from __future__ import annotations

import argparse
import copy
import html
import json
import re
import time
from pathlib import Path

import jsonschema

from .course_knowledge import atomic_json
from .course_library import canonical
from .course_textbook_article import (
    FORMAT,
    standalone_html,
    validate_graph,
)
from .course_textbook_evidence import sha

POLICY = "unfinished-manuscript-local-work-preview-v1"
NOTICE = (
    "未完成的工作稿，不是已验收文章。正文可能存在遗漏、事实、引用或图文问题；"
    "自测答案同样尚未最终核验。此预览仅用于查看实际写作进度，不能据此删除原始资料。"
)


def checked(path, observed):
    raw = path.read_bytes()
    observed[path] = sha(raw)
    return json.loads(raw)


def sealed(path, observed):
    value = checked(path, observed)
    if value["data_sha256"] != sha(canonical(value["data"])):
        raise ValueError("working_preview_changed_saved_artifact")
    return value["data"]


def candidate(folder, section, observed):
    """Use an explicitly retained candidate, not a guessed truncated response."""
    partial = folder / (section + "-partial-fields-private-result.json")
    retained = folder / "private-manuscript-continuation" / (section + ".json")
    provisional = folder / (section + "-provisional-continuation.json")
    if partial.exists() or retained.exists():
        value = checked(partial if partial.exists() else retained, observed)
        result = value["candidate"]
        if value["candidate_sha256"] != sha(canonical(result)):
            raise ValueError("working_preview_changed_retained_candidate")
        return result
    if provisional.exists():
        value = checked(provisional, observed)
        for name, digest in value["original_repair_attempts"].items():
            if not re.fullmatch(section + r"-repair-0[12]\.json", name):
                raise ValueError("working_preview_invalid_attempt_path")
            path = folder / "attempts" / name
            attempt = checked(path, observed)
            if observed[path] != digest:
                raise ValueError("working_preview_changed_original_attempt")
            try:
                result = json.loads(attempt["raw_response"])
            except (json.JSONDecodeError, KeyError):
                continue
            if sha(canonical(result)) == value["candidate_sha256"]:
                return result
        raise ValueError("working_preview_retained_candidate_not_found")
    for suffix in ("-repair.json", "-write.json"):
        path = folder / (section + suffix)
        if path.exists():
            return sealed(path, observed)
    return None


def clean_section(section, value):
    """Project authored reading fields only; never serialize a private record."""
    diagrams, withheld = [], 0
    for graph in value["diagrams"]:
        try:
            graph = {
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
            validate_graph(graph)
        except (ValueError, KeyError, TypeError, jsonschema.ValidationError):
            withheld += 1
        else:
            diagrams.append(graph)
    return {
        "id": section,
        "heading": value["heading"],
        "paragraphs": [row["text"] for row in value["paragraphs"]],
        "diagrams": diagrams,
    }, withheld


def snapshot(root, job):
    if not re.fullmatch(r"[a-f0-9]{32}", job):
        raise ValueError("working_preview_invalid_job")
    folder = Path(root).resolve() / "wiki/textbook-composition-jobs" / job
    observed = {}
    state = checked(folder / "progress.json", observed)
    plan = sealed(folder / "concept-plan.json", observed)
    units = sealed(folder / "writing-allocation.json", observed)
    inputs = sealed(folder / "private-composition-inputs.json", observed)
    lesson = inputs["lesson"]
    if not re.fullmatch(r"[a-f0-9]{24}", lesson):
        raise ValueError("working_preview_invalid_course")
    whole = folder / "private-article.json"
    complete_private = sealed(whole, observed) if whole.exists() else None
    complete_sections = (
        {s["id"]: s for c in complete_private["chapters"] for s in c["sections"]}
        if complete_private is not None
        else {}
    )
    chapters, statuses, quizzes, first = [], {}, [], None
    retained = withheld = characters = 0
    for unit in units:
        section = unit["id"]
        if not re.fullmatch(r"section-[0-9]{4}", section):
            raise ValueError("working_preview_invalid_section")
        value = complete_sections.get(section)
        if value is None:
            value = candidate(folder, section, observed)
        if not chapters or chapters[-1]["id"] != unit["chapter_id"]:
            chapters.append(
                {
                    "id": unit["chapter_id"],
                    "heading": unit["chapter"]["heading"],
                    "sections": [],
                }
            )
        if value is None:
            statuses[section] = "not_written"
            row = {
                "id": section,
                "heading": "整理中：" + unit["concept"]["heading"],
                "paragraphs": [
                    "本节尚无可展示的完整正文，完成写作后才会出现在工作稿中。"
                ],
                "diagrams": [],
            }
        else:
            statuses[section] = "unaccepted_working_draft"
            row, invalid = clean_section(section, value)
            withheld += invalid
            first = first or value
            retained += 1
            characters += sum(map(len, row["paragraphs"]))
            quizzes.append(
                {
                    **{
                        k: value["quiz"][k]
                        for k in ("question", "answer", "explanation")
                    },
                    "criteria": list(value["quiz"]["criteria"]),
                }
            )
        chapters[-1]["sections"].append(row)
    if first is None:
        raise ValueError("working_preview_no_authored_body")
    book = {
        "format": FORMAT,
        "course_id": lesson,
        "title": plan["title"],
        "introduction": [first["overview"]["text"]],
        "objectives": ["查看正在按知识结构重写的课程正文；各节仍需完成最终核验。"],
        "prerequisites": [],
        "chapters": chapters,
        "takeaways": ["本工作稿尚未验收，完整知识覆盖与最终学习结论仍在核对中。"],
        "quiz": quizzes,
        "related_courses": [],
    }
    # Validate the same strict clean projection used by reader exports, without
    # producing reading.json, publishing a catalog entry, or touching job files.
    projected = copy.deepcopy(book)
    page = standalone_html(projected)
    banner = (
        '<aside role="note" class="work-warning"><strong>工作稿 · 未完成 · 未验收</strong>'
        f"<p>{html.escape(NOTICE)}</p><p>已保留正文 {retained}/{len(units)} 小节；"
        "这不是知识覆盖率或验收通过率。</p></aside>"
    )
    page = page.replace("<main>", "<main>" + banner, 1)
    page = page.replace("课程文章 · 整理稿", "课程文章 · 未完成工作稿", 1)
    css = (
        ".work-warning{border:2px solid #a56621;background:#fff5e6;padding:20px;"
        "margin-bottom:32px}.work-state{color:#89551f;font-size:14px}"
    )
    page = page.replace("</style>", css + "</style>", 1)
    for section, status in statuses.items():
        notice = (
            "尚未写完" if status == "not_written" else "正文工作稿 · 尚未最终通过审校"
        )
        anchor = f'<section id="{section}">'
        page = page.replace(anchor, anchor + f'<p class="work-state">{notice}</p>', 1)
    receipt = {
        "policy": POLICY,
        "job": job,
        "snapshot_at": time.time(),
        "writer_stage_at_snapshot": state["stage"],
        "draft_sections_retained": retained,
        "total_sections": len(units),
        "authored_body_characters": characters,
        "withheld_invalid_diagrams": withheld,
        "sections": statuses,
        "inputs": {
            str(p.relative_to(folder)): digest for p, digest in observed.items()
        },
        "html_sha256": sha(page.encode("utf-8")),
        "final_article_ready": False,
        "reader_article_published": False,
        "quality_acceptance_changed": False,
        "source_deletion_allowed": False,
    }
    return page, receipt


def write_preview(root, job):
    page, receipt = snapshot(root, job)
    target = Path(root).resolve() / "wiki/working-previews" / job
    target.mkdir(parents=True, exist_ok=True)
    path = target / "working-draft.html"
    temporary = target / "working-draft.html.tmp"
    temporary.write_text(page, encoding="utf-8")
    temporary.replace(path)
    atomic_json(target / "snapshot.json", receipt)
    return {
        "state": "unfinished_local_work_preview_written",
        "path": str(path),
        **{
            k: receipt[k]
            for k in (
                "draft_sections_retained",
                "total_sections",
                "authored_body_characters",
                "withheld_invalid_diagrams",
                "final_article_ready",
                "reader_article_published",
            )
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--job", required=True)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(write_preview(args.root, args.job)))
    except Exception as exc:
        # A parser/schema exception can contain the actual draft. Never print it.
        print(
            json.dumps(
                {
                    "state": "stopped",
                    "error_type": type(exc).__name__,
                    "error_sha256": sha(str(exc).encode()),
                }
            )
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
