"""A clean, explicitly unfinished reading view of focused lesson writing."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from . import course_textbook_gap_write as writer
from .course_textbook_article import standalone_html, validate_book
from .course_library import canonical
from .course_textbook_evidence import sha


def cached(path):
    value = writer.read(path)
    writer.require(value["data_sha256"] == sha(canonical(value["data"])), "preview_cache_integrity")
    return value["data"]


def projection(root, parent):
    root = Path(root).resolve()
    writer.require(re.fullmatch(r"[a-f0-9]{32}", parent), "preview_parent_id")
    book = validate_book(writer.read(root / "wiki/whole-lesson-repair-jobs" / parent / "corrected-reading.json"))
    status = {"state": "checking_previous_draft", "units_written": 0, "total_units": None,
              "units_with_issues": 0, "quality_complete": False, "source_deletion_allowed": False}
    path = root / "wiki/textbook-gap-write-current.json"
    if not path.exists():
        return book, status
    current = writer.read(path)
    if current["parent"] != parent:
        return book, status
    key = current["job"]
    writer.require(re.fullmatch(r"[a-f0-9]{32}", key), "preview_job_id")
    folder = root / "wiki/textbook-gap-write-jobs" / key
    manifest = writer.read(folder / "manifest.json")
    writer.require(manifest["id"] == key == sha(canonical(manifest["recipe"]))[:32], "preview_manifest_integrity")
    recipe = manifest["recipe"]
    inputs, plan, binding = writer.load_plan(root, recipe["extra"]["plan"])
    writer.require(inputs["parent"] == parent and recipe["extra"]["plan_bindings"] == binding
                   and recipe["extra"]["parent_bindings"] == inputs["bindings"]
                   and recipe["packet"]["sources"] == inputs["sources"], "preview_same_original_inputs")
    candidates, selected, issues = {}, [], 0
    for unit in plan["units"]:
        proof_path = folder / (unit["unit"] + "-private-result.json")
        if not proof_path.exists():
            continue
        proof = writer.read(proof_path)
        value = writer.check_candidate(proof["candidate"], unit)
        writer.validate_review(proof["review"], value, unit)
        writer.require(proof["unit"] == unit["unit"] and proof["checks_passed"] == writer.passed(proof["review"])
                       and proof["reader_publication_allowed"] is False, "preview_private_unit_proof")
        repair = (folder / (unit["unit"] + "-repair.json")).is_file()
        stage = unit["unit"] + ("-repair" if repair else "-write")
        writer.require(cached(folder / (stage + ".json")) == value, "preview_actual_written_candidate")
        review_stage = unit["unit"] + ("-review-1" if repair else "-review-0")
        writer.require(cached(folder / (review_stage + ".json")) == proof["review"], "preview_actual_unit_review")
        selected.append(unit)
        candidates[unit["unit"]] = value
        issues += not proof["checks_passed"]
    if selected:
        book = writer.assemble(inputs, {**plan, "units": selected}, candidates)
    status.update(state="focused_writing" if current["stage"] != "complete" else "draft_checks_finished",
                  units_written=len(selected), total_units=len(plan["units"]), units_with_issues=issues)
    return book, status


def create_app(root, parent):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def current():
        try:
            return projection(root, parent)
        except Exception:
            raise HTTPException(503, "文章正在整理，请稍后刷新。") from None

    @app.get("/", response_class=HTMLResponse)
    def reading():
        book, state = current()
        count = state["units_written"]
        progress = f'已补写 {count}/{state["total_units"]} 个知识单元。' if state["total_units"] is not None else "正在核查原稿的遗漏。"
        notice = '首课整理中 · 尚未验收。' + progress + '正文、图示和自测仍可能存在遗漏或错误；原始资料继续保留。'
        content = standalone_html(book).replace("<body><main>",
            '<body><main><aside role="status" style="padding:1rem 1.2rem;background:#fff0d4;border-left:3px solid #ad843c;margin-bottom:2rem">'
            + notice + '</aside>', 1)
        return HTMLResponse(content, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'"})

    @app.get("/status")
    def status():
        book, state = current()
        body = writer.planmod.body_index(book)[0]
        return {**state, "body_characters": sum(map(len, body.values())), "paragraphs": len(body),
                "chapters": len(book["chapters"]), "quiz": len(book["quiz"])}

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--parent-job", required=True)
    parser.add_argument("--port", type=int, default=3006)
    args = parser.parse_args()
    import uvicorn
    uvicorn.run(create_app(args.root, args.parent_job), host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
