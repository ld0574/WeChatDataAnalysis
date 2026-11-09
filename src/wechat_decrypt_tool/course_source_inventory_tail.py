"""Freeze all remaining unattempted inventory sources behind the exact live tail.

Registered failures and already reserved sources are explicit exceptions. Each
new source runs once, using the unchanged local pipeline and a private guard;
any failure or pause stops the finite queue without an automatic retry.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import time

import psutil

from . import course_source_tail as prior
from .course_knowledge import atomic_json, schema_lease

original, whole, publication, guard = prior.original, prior.whole, prior.publication, prior.guard
digest = prior.digest
POLICY = "complete-inventory-unattempted-source-tail-exact-predecessor-v1"


def require(ok, code):
    if not ok:
        raise ValueError("requires_inventory_source_tail_" + code)


def bindings():
    return {**prior.bindings(), Path(__file__).stem: whole.sha(Path(__file__).read_bytes())}


def select(root, inventory, predecessor):
    with original.ReadOnlyArchive(root).connect() as db:
        registered = {row[0] for row in db.execute("SELECT id FROM videos")}
    reserved = {row["video"] for row in predecessor["sources"]}
    for path in (root / "wiki/source-batches").glob("*/plan.json"):
        reserved.update(row["video"] for row in whole.read(path)["sources"])
    for path in (root / "wiki/inventory-source-tails").glob("*/plan.json"):
        reserved.update(row["video"] for row in whole.read(path)["sources"])
    rows, excluded = [], []
    for item in inventory["videos"]:
        video = original.item_id(item)
        if item.get("error") or not item.get("audio_streams") or not item.get("duration_seconds"):
            excluded.append({"video": video, "code": "not_recoverable_audio_source"})
        elif video in registered:
            excluded.append({"video": video, "code": "registered_source_not_retried"})
        elif video in reserved:
            excluded.append({"video": video, "code": "reserved_by_original_source_plan"})
        else:
            audio = original.retained_inputs(root, video, item)
            if audio is None:
                excluded.append({"video": video, "code": "existing_output_requires_separate_recovery"})
                continue
            stat = Path(item["path"]).stat()
            require(stat.st_size == item["bytes"] and stat.st_mtime_ns == item["mtime_ns"], "unchanged_unattempted_media_stat")
            rows.append({"video": video, "item": item, "retained_audio": audio})
    all_ids = [original.item_id(item) for item in inventory["videos"]]
    require(len(all_ids) == len(set(all_ids)) and {r["video"] for r in rows} | {r["video"] for r in excluded} == set(all_ids)
        and len(rows) + len(excluded) == len(all_ids), "complete_inventory_partition_without_duplicates")
    return rows, excluded


def prepare(root, predecessor_key):
    root = Path(root).resolve()
    guard.checkpoint(root)
    with schema_lease(root / "wiki/inventory-source-tail-prepare.lock", timeout=0):
        _, _, target, old, previous = prior.load(root, predecessor_key)
        status = whole.read(target / "progress.json")
        require(original.identity_live(status) and status["state"] in {"processing_original_unattempted_source",
            "publishing_verified_source_export", "new_source_export_and_http_publication_verified"},
            "exact_healthy_original_tail_before_registration")
        live_guards = [whole.read(p) for p in target.glob("item-*-guard/guard-progress.json")]
        require(any(row.get("state") == "running" and all(row.get(k) == status[k] for k in ("pid", "created"))
            and time.time() - row["updated"] < 30 for row in live_guards), "fresh_actual_predecessor_private_guard")
        inventory = whole.read(root / "inventory-unique.json")
        require(inventory["unique_readable_videos"] == 340, "full_original_audio_inventory")
        selected, excluded = select(root, inventory, previous)
        require(bool(selected), "nonempty_remaining_unattempted_inventory")
        plan = {"policy": POLICY, "code": bindings(), "inventory_sha256": old["inventory_sha256"],
            "inventory_audio_courses": 340, "predecessor_batch": predecessor_key,
            "predecessor_controller": {k: status[k] for k in ("pid", "created")},
            "predecessor_execution_sha256": digest(previous), "sources": selected, "excluded": excluded,
            "asr_model": old["asr_model"], "model_files": old["model_files"],
            "asr_batch_size": old["asr_batch_size"], "asr_precision": old["asr_precision"],
            "knowledge_model": old["knowledge_model"], "maximum_power_w": 200, "maximum_temperature_c": 70,
            "minimum_free_gib_each_volume": 32, "automatic_retry_allowed": False,
            "automatic_thermal_resume_allowed": False, "model_budgets_reset": False,
            "reader_article_published": False, "source_deletion_allowed": False}
        key = digest(plan)[:32]
        folder = root / "wiki/inventory-source-tails" / key
        require(not folder.exists(), "single_finite_queue_registration")
        folder.mkdir(parents=True)
        atomic_json(folder / "plan.json", plan)
        backup = Path.home() / "Documents/CourseKnowledgeBackups/inventory-source-tail-plans" / key
        for path in [folder / "plan.json", root / "inventory-unique.json", Path(__file__)]:
            whole.backup(path, backup / path.name)
        return {"state": "complete_unattempted_inventory_tail_prepared", "queue": key,
            "selected_new_sources": len(selected), "explicit_existing_or_reserved_sources": len(excluded),
            "model_calls": 0, "reader_article_published": False}


def load(root, key):
    root = Path(root).resolve()
    require(re.fullmatch(r"[a-f0-9]{32}", key) is not None, "queue_id")
    folder = root / "wiki/inventory-source-tails" / key
    plan = whole.read(folder / "plan.json")
    require(plan["policy"] == POLICY and digest(plan)[:32] == key and plan["code"] == bindings(), "same_frozen_queue_and_code")
    _, _, previous_folder, old, previous = prior.load(root, plan["predecessor_batch"])
    require(digest(previous) == plan["predecessor_execution_sha256"] and old["inventory_sha256"] == plan["inventory_sha256"]
        and whole.sha((root / "inventory-unique.json").read_bytes()) == plan["inventory_sha256"], "same_complete_inventory_and_predecessor")
    for name, expected in plan["model_files"].items():
        stat = (Path(plan["asr_model"]) / name).stat()
        require({"bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns} == expected, "same_original_local_asr_weights")
    return root, folder, plan, previous_folder, previous


def wait(root, plan, previous_folder, previous, *, live=original.identity_live, sleep=time.sleep):
    while live(plan["predecessor_controller"]):
        guard.checkpoint(root)
        sleep(15)
    guard.checkpoint(root)
    status = whole.read(previous_folder / "progress.json")
    require(all(status[k] == v for k, v in plan["predecessor_controller"].items())
        and status["state"] == "original_unattempted_source_tail_finished"
        and status["completed_sources"] == [r["video"] for r in previous["sources"]], "actual_successful_exact_predecessor_completion")
    available = publication.available(publication.Library(root).catalog())
    for number, row in enumerate(previous["sources"]):
        receipt = whole.read(previous_folder / (row["video"] + ".json"))
        checked = original.validate_lesson(original.ReadOnlyArchive(root), row["video"], row["item"])
        state = whole.read(previous_folder / f"item-{number:02d}-guard/guard-progress.json")
        require(receipt["export"] == checked and row["video"] in available and state["state"] == "worker_exited"
            and state.get("returncode") == 0 and not live(state), "actual_predecessor_exports_publication_and_guards")


def run(root, key, reader_url):
    root, folder, plan, previous_folder, previous = load(root, key)
    owner, completed = psutil.Process(), []

    def progress(state, **extra):
        atomic_json(folder / "progress.json", {"pid": owner.pid, "created": owner.create_time(), "state": state,
            "updated": time.time(), "queue": key, "completed_sources": list(completed), "target_sources": len(plan["sources"]),
            "inventory_audio_courses": 340, "automatic_retry_allowed": False, "automatic_thermal_resume_allowed": False,
            "reader_article_published": False, "source_deletion_allowed": False, **extra})

    with schema_lease(root / "wiki/inventory-source-tail-controller.lock", timeout=0):
        require(not (folder / "started.json").exists(), "controller_once_without_automatic_retry")
        atomic_json(folder / "started.json", {"pid": owner.pid, "created": owner.create_time()})
        try:
            progress("waiting_for_exact_existing_source_tail")
            wait(root, plan, previous_folder, previous)
            with schema_lease(root / "wiki/finite-source-batches.lock", timeout=0), original.httpx.Client(
                    base_url=publication.reader_url(reader_url), trust_env=False, timeout=90) as client:
                for number, row in enumerate(plan["sources"]):
                    guard.checkpoint(root)
                    load(root, key)
                    prior.check_new(root, row)
                    private = folder / f"source-{number:04d}-guard"
                    private.mkdir()
                    progress("processing_frozen_unattempted_inventory_source", video=row["video"], source_number=number)
                    result = guard.run(root, private, original.worker_command(root, plan, row))
                    if result["state"] == "paused_without_automatic_resume":
                        raise InterruptedError("requires_inventory_source_tail_private_guard_paused_no_auto_resume")
                    require(result["state"] == "private_worker_exited" and result.get("returncode") == 0, "actual_source_successful_guard_exit")
                    status = whole.read(root / "job-progress.json")
                    require(status["stage"] == "batch_finished" and status.get("processed") == 1
                        and status.get("failures") == 0, "actual_single_source_success")
                    checked = original.validate_lesson(original.ReadOnlyArchive(root), row["video"], row["item"])
                    progress("publishing_verified_source_export", video=row["video"], source_number=number)
                    receipt = {"video": row["video"], "export": checked,
                        "publication": prior.publish_verified(root, client, row["video"]),
                        "private_guard": private.relative_to(root).as_posix(),
                        "reader_article_published": False, "source_deletion_allowed": False}
                    path = folder / (row["video"] + ".json")
                    atomic_json(path, receipt)
                    destination = Path.home() / "Documents/CourseKnowledgeBackups/inventory-source-tail-results" / key / row["video"]
                    for p in [path, root / "job-progress.json", root / "run-plan.json", *private.glob("*.json"),
                            root / "lessons" / row["video"] / "evidence.json", root / "lessons" / row["video"] / "graph.json"]:
                        whole.backup(p, destination / p.name)
                    completed.append(row["video"])
                    progress("new_source_export_and_http_publication_verified")
            progress("frozen_unattempted_inventory_tail_finished_existing_exceptions_remain")
            return {"state": "frozen_unattempted_inventory_tail_finished_existing_exceptions_remain",
                "completed_new_sources": len(completed), "explicit_existing_or_reserved_sources": len(plan["excluded"]),
                "inventory_audio_courses": 340, "reader_article_published": False, "source_deletion_allowed": False}
        except BaseException as exc:
            failure = whole.failure_status(exc)
            atomic_json(folder / ("failure-" + str(time.time_ns()) + ".json"), failure)
            progress(failure["state"], reason_code=failure["reason_code"])
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--predecessor")
    parser.add_argument("--queue")
    parser.add_argument("--reader-url", default="http://127.0.0.1:3003", type=publication.reader_url)
    parser.add_argument("command", choices=["prepare", "load", "run"])
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            require(args.predecessor is not None, "predecessor_required")
            result = prepare(args.root, args.predecessor)
        else:
            require(args.queue is not None, "queue_required")
            if args.command == "load":
                _, _, plan, _, _ = load(args.root, args.queue)
                result = {"state": "full_unattempted_source_inventory_plan_authenticated", "sources": len(plan["sources"]), "model_calls": 0}
            else:
                result = run(args.root, args.queue, args.reader_url)
        print(json.dumps(result))
    except BaseException as exc:
        print(json.dumps(whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
