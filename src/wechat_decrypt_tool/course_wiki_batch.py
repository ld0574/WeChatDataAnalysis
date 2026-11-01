"""Finite first-topic batches over published sources, without retry loops."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import traceback
from pathlib import Path

import psutil

from .course_knowledge import GpuBusyError, atomic_json, schema_lease
from .course_gpu_guard import run as gpu_guard
from .course_library import Library, canonical
from .course_lesson_exports import digest
from .course_pdf_ocr import local_model_digest
from .course_video_visual_audit import ReadOnlyArchive
from .course_wiki import Wiki
from .course_wiki_completeness import published_report, review as omissions
from .course_wiki_planning import propose
from .course_wiki_review import published_review, review
from .course_wiki_synthesis import generate

POLICY = "finite-first-topic-source-batch-v1"
TERMINAL = {"draft_checks_complete", "no_supported_topic", "needs_review"}


def job_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise ValueError("wiki_batch_invalid_job_id")
    return value


def manifest(root, key):
    folder = root / "wiki/theme-batches" / job_id(key)
    value = json.loads((folder / "manifest.json").read_bytes())
    recipe = value["recipe"]
    if (
        value["id"] != key
        or digest(canonical(recipe))[:32] != key
        or recipe["policy"] != POLICY
        or not recipe["fixed_assessments"]
    ):
        raise ValueError("wiki_batch_manifest_changed")
    videos = [r["video"] for r in recipe["courses"]]
    if len(videos) != len(set(videos)):
        raise ValueError("wiki_batch_duplicate_course")
    return folder, recipe


def completed_artifacts(store, row):
    """Revalidate real publications and reports without running any models."""
    article = job_id(row["article"])
    return {
        "article": digest(canonical(Wiki(store.root).article(article))),
        "forward": digest(
            canonical(
                published_review(store.root, article, job=job_id(row["forward_job"]))
            )
        ),
        "omissions": digest(
            canonical(
                published_report(store.root, article, job=job_id(row["omission_job"]))
            )
        ),
    }


def prepare(store, *, limit=4, model="qwen3.5:9b"):
    if type(limit) is not int or not 1 <= limit <= 340:
        raise ValueError("wiki_batch_invalid_course_limit")
    with schema_lease(store.root / "wiki-theme-batch.lock", timeout=0):
        store.checkpoint()
        library = Library(store.root)
        # Exclude all previously attempted or reserved courses, including failed
        # jobs. Preparing another batch must not reset their attempt budgets.
        excluded = set()
        for pointer in (store.root / "wiki/theme-planning-sources").glob("*.json"):
            excluded.add(json.loads(pointer.read_bytes())["video"])
        for path in (store.root / "wiki/theme-batches").glob("*/manifest.json"):
            _, prior = manifest(store.root, path.parent.name)
            excluded.update(r["video"] for r in prior["courses"])
        wiki = Wiki(store.root)
        for row in wiki.catalog()["articles"]:
            excluded.update(
                source["video"]
                for source in wiki.article(row["id"])["packet"]["sources"].values()
                if source.get("video")
            )
        courses = []
        for row in library.catalog()["lessons"]:
            if row["state"] != "draft_available" or row["id"] in excluded:
                continue
            store.checkpoint()
            courses.append(
                {
                    "video": row["id"],
                    "lesson_sha256": digest(canonical(library.lesson(row["id"]))),
                }
            )
            if len(courses) == limit:
                break
        if not courses:
            return {"state": "no_unattempted_published_courses", "courses": 0}
        recipe = {
            "policy": POLICY,
            "model": model,
            "model_digest": local_model_digest(model),
            "fixed_assessments": True,
            "courses": courses,
        }
        key = digest(canonical(recipe))[:32]
        folder = store.root / "wiki/theme-batches" / key
        folder.mkdir(parents=True, exist_ok=False)
        atomic_json(folder / "manifest.json", {"id": key, "recipe": recipe})
        return {"state": "prepared", "batch": key, "courses": len(courses)}


def run(store, key, *, wait_seconds=3600):
    if type(wait_seconds) is not int or not 0 <= wait_seconds <= 3600:
        raise ValueError("wiki_batch_invalid_gpu_wait")
    with schema_lease(store.root / "wiki-theme-batch.lock", timeout=0):
        folder, recipe = manifest(store.root, key)
        path = folder / "progress.json"
        rows = {
            r["video"]: {"video": r["video"], "state": "pending"}
            for r in recipe["courses"]
        }
        if path.exists():
            saved = json.loads(path.read_bytes())
            if saved["batch"] != key or set(saved["courses"]) != set(rows):
                raise ValueError("wiki_batch_progress_scope_changed")
            rows = saved["courses"]
        model = recipe["model"]
        identity = {"pid": os.getpid(), "created": psutil.Process().create_time()}

        def progress(state, **extra):
            value = {
                "batch": key,
                "state": state,
                "courses": rows,
                "updated": time.time(),
                **identity,
                "automatic_revision_loop_allowed": False,
                "semantic_accuracy_verified": False,
                "full_course_coverage_verified": False,
                "source_deletion_allowed": False,
                **extra,
            }
            atomic_json(path, value)
            return value

        for spec in recipe["courses"]:
            video = spec["video"]
            row = rows[video]
            try:
                store.checkpoint()
                if row["state"] == "draft_checks_complete":
                    if (
                        completed_artifacts(store, row)
                        != row["checked_artifacts_sha256"]
                    ):
                        raise ValueError("wiki_batch_completed_artifacts_changed")
                    continue
                # Explicit skips and failures never receive new model attempts.
                if row["state"] in TERMINAL:
                    continue
                if (
                    digest(canonical(Library(store.root).lesson(video)))
                    != spec["lesson_sha256"]
                ):
                    raise ValueError("wiki_batch_frozen_lesson_changed")
                if local_model_digest(model) != recipe["model_digest"]:
                    raise ValueError("wiki_batch_model_revision_changed")
                row["state"] = "planning"
                progress("running", current_video=video)
                planned = propose(store, video, model, wait_seconds=wait_seconds)
                row["planning_job"] = job_id(planned["job"])
                if planned["state"] == "no_supported_topic_proposed":
                    row["state"] = "no_supported_topic"
                    progress("running", current_video=video)
                    continue
                if planned["state"] != "prepared_for_writing":
                    raise ValueError("wiki_batch_unknown_planning_state")
                plan_path = (
                    store.root
                    / "wiki/planning-jobs"
                    / row["planning_job"]
                    / "plan.json"
                )
                plan = json.loads(plan_path.read_bytes())
                if digest(canonical(plan)) != planned["plan_sha256"]:
                    raise ValueError("wiki_batch_prepared_plan_changed")
                row.update(
                    state="writing",
                    selected_sections=planned["selected_sections"],
                    unselected_sections=planned["unselected_sections"],
                )
                progress("running", current_video=video)
                draft = generate(
                    store,
                    plan,
                    model,
                    wait_seconds=wait_seconds,
                    fixed_assessments=True,
                )
                row.update(article=job_id(draft["id"]), state="checking_sources")
                progress("running", current_video=video)
                forward = review(
                    store,
                    row["article"],
                    model,
                    wait_seconds=wait_seconds,
                    single_targets=True,
                )
                published_review(store.root, row["article"], job=forward["job"])
                row.update(
                    forward_job=forward["job"],
                    forward_summary=forward["summary"],
                    state="checking_omissions",
                )
                progress("running", current_video=video)
                reverse = omissions(
                    store,
                    row["article"],
                    model,
                    wait_seconds=wait_seconds,
                    per_source=True,
                )
                published_report(store.root, row["article"], job=reverse["job"])
                row.update(
                    omission_job=reverse["job"],
                    omission_summary=reverse["summary"],
                    state="draft_checks_complete",
                )
                row["checked_artifacts_sha256"] = completed_artifacts(store, row)
                progress("running", current_video=video)
            except GpuBusyError:
                row["state"] = "deferred_gpu"
                return progress("deferred_gpu", current_video=video)
            except InterruptedError:
                row["state"] = "paused"
                progress("paused", current_video=video)
                raise
            except Exception as exc:
                row.update(
                    state="needs_review",
                    error_type=type(exc).__name__,
                    error_sha256=digest(str(exc).encode()),
                    technical_stack=[
                        {
                            "file": Path(f.filename).name,
                            "line": f.lineno,
                            "function": f.name,
                        }
                        for f in traceback.extract_tb(exc.__traceback__)
                    ],
                )
                progress("running", current_video=video)
        return progress("finite_batch_finished")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("prepare")
    create.add_argument("--limit", type=int, default=4)
    create.add_argument("--model", default="qwen3.5:9b")
    for name in ("run", "guard"):
        execute = sub.add_parser(name)
        execute.add_argument("--batch", required=True)
        execute.add_argument("--wait-gpu-seconds", type=int, default=3600)
    args = parser.parse_args()
    if args.command == "guard":
        job_id(args.batch)
        gpu_guard(
            args.root,
            [
                sys.executable,
                "-m",
                __spec__.name,
                "--root",
                str(args.root.resolve()),
                "run",
                "--batch",
                args.batch,
                "--wait-gpu-seconds",
                str(args.wait_gpu_seconds),
            ],
            maximum_power=200,
            maximum_temperature=70,
            guard_name="wiki-revision-thermal-guard",
        )
        return
    store = ReadOnlyArchive(args.root)
    result = (
        prepare(store, limit=args.limit, model=args.model)
        if args.command == "prepare"
        else run(store, args.batch, wait_seconds=args.wait_gpu_seconds)
    )
    print(json.dumps({k: v for k, v in result.items() if k in {"state", "batch"}}))


if __name__ == "__main__":
    main()
