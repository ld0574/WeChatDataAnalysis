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
PAGED_POLICY = "finite-complete-directory-pages-batch-v1"
ALLOCATION_POLICY = "finite-complete-allocated-originals-batch-v1"
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
        or recipe["policy"] not in {POLICY, PAGED_POLICY, ALLOCATION_POLICY}
        or not recipe["fixed_assessments"]
    ):
        raise ValueError("wiki_batch_manifest_changed")
    items = [item_key(r) for r in recipe["courses"]]
    if len(items) != len(set(items)):
        raise ValueError("wiki_batch_duplicate_course")
    return folder, recipe


def item_key(spec):
    return spec.get("item", spec["video"])


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


def prepare_directories(store, videos, *, model="qwen3.5:9b"):
    from .course_wiki_directory import create

    if not videos or len(videos) != len(set(videos)):
        raise ValueError("wiki_batch_invalid_directory_courses")
    with schema_lease(store.root / "wiki-theme-batch.lock", timeout=0):
        courses = []
        for video in videos:
            directory = create(store, video)
            for page in range(directory["pages"]):
                spec = {
                    "video": video,
                    "lesson_sha256": directory["lesson_sha256"],
                    "directory": directory["directory"],
                    "page": page,
                }
                spec["item"] = digest(canonical(spec))[:32]
                courses.append(spec)
        if not courses:
            raise ValueError("wiki_batch_no_complete_directory_pages")
        recipe = {
            "policy": PAGED_POLICY,
            "model": model,
            "model_digest": local_model_digest(model),
            "fixed_assessments": True,
            "courses": courses,
        }
        key = digest(canonical(recipe))[:32]
        folder = store.root / "wiki/theme-batches" / key
        if folder.exists():
            _, previous = manifest(store.root, key)
            if previous != recipe:
                raise ValueError("wiki_batch_directory_recipe_changed")
            return {
                "state": "already_prepared",
                "batch": key,
                "pages": len(courses),
                "courses": len(videos),
            }
        requested = {r["item"] for r in courses}
        for path in (store.root / "wiki/theme-batches").glob("*/manifest.json"):
            _, previous = manifest(store.root, path.parent.name)
            if requested & {r.get("item") for r in previous["courses"]}:
                raise ValueError("wiki_batch_directory_page_already_reserved")
        folder.mkdir(parents=True, exist_ok=False)
        atomic_json(folder / "manifest.json", {"id": key, "recipe": recipe})
        return {
            "state": "prepared",
            "batch": key,
            "pages": len(courses),
            "courses": len(videos),
        }


def prepare_allocations(store, partitions, *, model="qwen3.5:9b"):
    from .course_wiki_allocation import create

    if not partitions or len(partitions) != len(set(partitions)):
        raise ValueError("wiki_batch_invalid_evidence_partitions")
    with schema_lease(store.root / "wiki-theme-batch.lock", timeout=0):
        courses = []
        for partition in partitions:
            allocation = create(store, partition)
            for number in range(allocation["scopes"]):
                spec = {
                    "video": allocation["video"],
                    "lesson_sha256": allocation["lesson_sha256"],
                    "allocation": allocation["allocation"],
                    "allocation_scope": number,
                }
                spec["item"] = digest(canonical(spec))[:32]
                courses.append(spec)
        recipe = {
            "policy": ALLOCATION_POLICY,
            "model": model,
            "model_digest": local_model_digest(model),
            "fixed_assessments": True,
            "courses": courses,
        }
        key = digest(canonical(recipe))[:32]
        folder = store.root / "wiki/theme-batches" / key
        state = "prepared"
        if folder.exists():
            _, previous = manifest(store.root, key)
            if previous != recipe:
                raise ValueError("wiki_batch_allocation_recipe_changed")
            state = "already_prepared"
        else:
            requested = {r["item"] for r in courses}
            for path in (store.root / "wiki/theme-batches").glob("*/manifest.json"):
                _, previous = manifest(store.root, path.parent.name)
                if requested & {r.get("item") for r in previous["courses"]}:
                    raise ValueError("wiki_batch_allocation_scope_already_reserved")
            folder.mkdir(parents=True, exist_ok=False)
            atomic_json(folder / "manifest.json", {"id": key, "recipe": recipe})
        return {
            "state": state,
            "batch": key,
            "partitions": len(partitions),
            "scopes": len(courses),
            "source_deletion_allowed": False,
        }


def check_constraints(folder, key, spec, row, requested):
    """Pin a new item's evidence grammar before any model can be called.

    Legacy attempts keep their original grammar. A durable intent also survives
    a crash between its creation and the first progress write.
    """
    from .course_wiki_check_constraints import GENERATION_POLICY

    spec_sha = digest(canonical(spec))
    path = folder / "check-execution-intents" / (spec_sha + ".json")
    expected = {
        "policy": "new-batch-item-check-execution-v1",
        "batch": key,
        "item": item_key(spec),
        "spec_sha256": spec_sha,
        "generation_constraints": GENERATION_POLICY,
    }
    if path.exists():
        value = json.loads(path.read_bytes())
        sha = digest(canonical(value))
        if (
            value != expected
            or requested is False
            or row.get("check_execution_sha256", sha) != sha
            or ("check_execution_sha256" not in row and row["state"] != "pending")
        ):
            raise ValueError("wiki_batch_check_execution_intent_changed")
    else:
        if "check_execution_sha256" in row:
            raise ValueError("wiki_batch_check_execution_intent_missing")
        if requested is not True:
            return False
        if row["state"] != "pending" or "planning_job" in row or "article" in row:
            raise ValueError("wiki_batch_check_constraints_require_unstarted_item")
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(path, expected)
        sha = digest(canonical(expected))
    row["check_execution_sha256"] = sha
    return True


def topic_structure(folder, key, spec, row, requested):
    """Only unstarted allocations may opt into material-shaped planning."""
    from .course_wiki_planning import FLEXIBLE_ALLOCATION_POLICY

    spec_sha = digest(canonical(spec))
    path = folder / "topic-structure-intents" / (spec_sha + ".json")
    expected = {
        "policy": "new-batch-item-topic-structure-v1",
        "batch": key,
        "item": item_key(spec),
        "spec_sha256": spec_sha,
        "planning_strategy": FLEXIBLE_ALLOCATION_POLICY,
    }
    if path.exists():
        value = json.loads(path.read_bytes())
        sha = digest(canonical(value))
        if (
            value != expected
            or requested is False
            or row.get("topic_execution_sha256", sha) != sha
            or ("topic_execution_sha256" not in row and row["state"] != "pending")
            or "allocation" not in spec
        ):
            raise ValueError("wiki_batch_topic_execution_intent_changed")
    else:
        if "topic_execution_sha256" in row:
            raise ValueError("wiki_batch_topic_execution_intent_missing")
        if requested is not True:
            return False
        if "allocation" not in spec:
            raise ValueError("wiki_batch_flexible_requires_allocated_scope")
        if row["state"] != "pending" or "planning_job" in row or "article" in row:
            raise ValueError("wiki_batch_flexible_requires_unstarted_item")
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(path, expected)
        sha = digest(canonical(expected))
    row["topic_execution_sha256"] = sha
    return True


def run(
    store,
    key,
    *,
    wait_seconds=3600,
    max_items=None,
    constrained_new_checks=None,
    flexible_new_topics=None,
):
    if flexible_new_topics is not None and type(flexible_new_topics) is not bool:
        raise ValueError("wiki_batch_invalid_topic_structure_option")
    if flexible_new_topics is True and constrained_new_checks is False:
        raise ValueError("wiki_batch_flexible_requires_constrained_checks")
    if constrained_new_checks is not None and type(constrained_new_checks) is not bool:
        raise ValueError("wiki_batch_invalid_check_constraint_option")
    if type(wait_seconds) is not int or not 0 <= wait_seconds <= 3600:
        raise ValueError("wiki_batch_invalid_gpu_wait")
    if max_items is not None and (
        type(max_items) is not int or not 1 <= max_items <= 10000
    ):
        raise ValueError("wiki_batch_invalid_item_limit")
    with schema_lease(store.root / "wiki-theme-batch.lock", timeout=0):
        folder, recipe = manifest(store.root, key)
        if (store.root / "wiki/theme-batch-refreshes" / (key + ".json")).exists():
            raise ValueError("wiki_batch_superseded_by_provenance_refresh")
        path = folder / "progress.json"
        rows = {
            item_key(r): {
                "video": r["video"],
                "state": "pending",
                **{
                    k: r[k]
                    for k in ("directory", "page", "allocation", "allocation_scope")
                    if k in r
                },
            }
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

        processed = 0
        for spec in recipe["courses"]:
            video = spec["video"]
            row = rows[item_key(spec)]
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
                if max_items is not None and processed >= max_items:
                    return progress(
                        "slice_finished",
                        processed_items=processed,
                        remaining_items=sum(
                            r["state"] not in TERMINAL for r in rows.values()
                        ),
                    )
                processed += 1
                flexible = topic_structure(folder, key, spec, row, flexible_new_topics)
                if flexible and constrained_new_checks is False:
                    raise ValueError("wiki_batch_flexible_requires_constrained_checks")
                check_options = (
                    {"constrained_evidence": True}
                    if check_constraints(
                        folder,
                        key,
                        spec,
                        row,
                        True if flexible else constrained_new_checks,
                    )
                    else {}
                )
                if (
                    digest(canonical(Library(store.root).lesson(video)))
                    != spec["lesson_sha256"]
                ):
                    raise ValueError("wiki_batch_frozen_lesson_changed")
                if local_model_digest(model) != recipe["model_digest"]:
                    raise ValueError("wiki_batch_model_revision_changed")
                row["state"] = "planning"
                progress("running", current_video=video)
                options = (
                    {"directory": spec["directory"], "page_number": spec["page"]}
                    if "directory" in spec
                    else {}
                )
                if "allocation" in spec:
                    options = {
                        "allocation": spec["allocation"],
                        "allocation_scope": spec["allocation_scope"],
                    }
                if flexible:
                    options["flexible_structure"] = True
                planned = propose(
                    store, video, model, wait_seconds=wait_seconds, **options
                )
                row["planning_job"] = job_id(planned["job"])
                if "directory_page_sections" in planned:
                    row["directory_page_sections"] = planned["directory_page_sections"]
                if "evidence_partition" in planned:
                    row.update(
                        {
                            k: planned[k]
                            for k in (
                                "evidence_partition",
                                "scope_evidence_pages",
                                "selected_evidence_pages",
                                "unselected_evidence_pages",
                                "partially_selected_sections",
                                "complete_sections_selected",
                            )
                        }
                    )
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
                    **check_options,
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
                    **check_options,
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
    directories = sub.add_parser("prepare-directories")
    directories.add_argument("--video", action="append", required=True)
    directories.add_argument("--model", default="qwen3.5:9b")
    allocations = sub.add_parser("prepare-allocations")
    allocations.add_argument("--partition", action="append", required=True)
    allocations.add_argument("--model", default="qwen3.5:9b")
    for name in ("run", "guard"):
        execute = sub.add_parser(name)
        execute.add_argument("--batch", required=True)
        execute.add_argument("--wait-gpu-seconds", type=int, default=3600)
        execute.add_argument("--max-items", type=int)
        execute.add_argument(
            "--constrained-new-checks", action="store_true", default=None
        )
        execute.add_argument("--flexible-new-topics", action="store_true", default=None)
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
                *(
                    ["--max-items", str(args.max_items)]
                    if args.max_items is not None
                    else []
                ),
                *(["--constrained-new-checks"] if args.constrained_new_checks else []),
                *(["--flexible-new-topics"] if args.flexible_new_topics else []),
            ],
            maximum_power=200,
            maximum_temperature=70,
            guard_name="wiki-revision-thermal-guard",
        )
        return
    store = ReadOnlyArchive(args.root)
    if args.command == "prepare":
        result = prepare(store, limit=args.limit, model=args.model)
    elif args.command == "prepare-directories":
        result = prepare_directories(store, args.video, model=args.model)
    elif args.command == "prepare-allocations":
        result = prepare_allocations(store, args.partition, model=args.model)
    else:
        result = run(
            store,
            args.batch,
            wait_seconds=args.wait_gpu_seconds,
            max_items=args.max_items,
            constrained_new_checks=args.constrained_new_checks,
            flexible_new_topics=args.flexible_new_topics,
        )
    print(json.dumps({k: v for k, v in result.items() if k in {"state", "batch"}}))


if __name__ == "__main__":
    main()
