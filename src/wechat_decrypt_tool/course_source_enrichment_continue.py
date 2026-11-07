"""Continue one failed source after an exact, successful pixel recovery.

This explicitly planned continuation never transcribes or captures frames.
Original failures stay in their stopped jobs, immutable inputs and a database
event; only after a complete checked export is the current source error resolved.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import time

import httpx
import psutil

from . import course_frame_ocr_contextual_recovery as recovery
from . import course_library_queue as publication
from . import course_lesson_exports as exports
from .course_archive_gpu import checked_sample
from .course_gpu_guard import run as guarded

knowledge, whole = recovery.knowledge, recovery.whole
POLICY = "exact-successful-frame-recovery-to-one-retained-source-enrichment-v1"


def require(ok, code):
    if not ok:
        raise ValueError("requires_source_enrichment_continue_" + code)


def identity(value):
    return {k: value[k] for k in ("pid", "created")}


def bindings():
    return {name: whole.sha(Path(__file__).with_name(name + ".py").read_bytes()) for name in
        (Path(__file__).stem, "course_frame_ocr_recovery", "course_frame_ocr_contextual_recovery", "course_knowledge", "course_frame_ocr",
         "course_lesson_exports", "course_library_queue", "course_gpu_guard", "course_archive_gpu")}


def prepare(root, frame_job, reader_url):
    root, frame_folder, frame_plan = recovery.load(root, frame_job)
    recovery.checkpoint(root)
    guard = whole.read(root / "gpu-thermal-guard.json")
    worker = whole.read(frame_folder / "progress.json")
    started = whole.read(frame_folder / "guard-started.json")
    require(identity(guard) == identity(started) and guard["state"] in {"running", "worker_exited"}
        and worker["stage"] != "stopped", "exact_frame_predecessor_not_failed_or_paused")
    if not recovery.live(guard) and not recovery.live(worker):
        require(guard["state"] == "worker_exited" and guard.get("returncode") == 0
            and worker["stage"] == "all_tiles_saved", "finished_frame_predecessor_success")
        recovery.verify(root, frame_job)
    rows = exports.read_rows(exports.ReadOnlyArchive(root), frame_plan["video"])
    require(rows["video"] == frame_plan["original_source"] and rows["video"]["error"] == recovery.LEGACY_FAILURE,
        "original_failed_source_preserved")
    inventory_path = root / "inventory-unique.json"
    inventory = whole.read(inventory_path)
    item = next(item for item in inventory["videos"] if Path(item["path"]).resolve() == Path(rows["video"]["path"]).resolve())
    require(item["bytes"] == rows["video"]["size"] and item["mtime_ns"] == rows["video"]["mtime_ns"], "frozen_source_identity")
    plan = {"policy": POLICY, "frame_job": frame_job, "video": frame_plan["video"],
        "frame_plan_sha256": recovery.digest(frame_plan), "predecessor_guard": identity(guard),
        "predecessor_worker": identity(worker), "rows_before_recovery": rows, "source_item": item,
        "inventory_sha256": whole.sha(inventory_path.read_bytes()), "code_sha256": bindings(),
        "reader_url": publication.reader_url(reader_url), "asr_reexecution_allowed": False,
        "frame_capture_allowed": False, "automatic_retry_allowed": False,
        "automatic_thermal_resume_allowed": False, "reader_article_published": False}
    key = recovery.digest(plan)[:32]
    folder, registry = root / "wiki/source-enrichment-continuations" / key, root / "wiki/source-enrichment-registrations"
    registry.mkdir(parents=True, exist_ok=True)
    with knowledge.schema_lease(registry / "prepare.lock", timeout=0):
        registration = registry / (plan["video"] + ".json")
        require(not registration.exists() and not folder.exists(), "one_registered_source_continuation")
        folder.mkdir(parents=True)
        knowledge.atomic_json(registration, {"job": key, "video": plan["video"], "frame_job": frame_job})
        knowledge.atomic_json(folder / "plan.json", plan)
    return {"state": "exact_frame_to_source_continuation_prepared", "job": key, "video": plan["video"], "model_calls": 0}


def load(root, key):
    root = Path(root).resolve()
    require(re.fullmatch(r"[a-f0-9]{32}", key) is not None, "job_id")
    folder = root / "wiki/source-enrichment-continuations" / key
    plan = whole.read(folder / "plan.json")
    registration = whole.read(root / "wiki/source-enrichment-registrations" / (plan["video"] + ".json"))
    require(plan["policy"] == POLICY and recovery.digest(plan)[:32] == key and registration["job"] == key
        and plan["code_sha256"] == bindings(), "unchanged_original_plan_and_once_only_registration")
    require(whole.sha((root / "inventory-unique.json").read_bytes()) == plan["inventory_sha256"], "unchanged_inventory")
    _, _, frame_plan = recovery.load(root, plan["frame_job"])
    require(recovery.digest(frame_plan) == plan["frame_plan_sha256"], "same_bounded_pixel_recovery")
    item = plan["source_item"]
    stat = Path(item["path"]).stat()
    require(stat.st_size == item["bytes"] and stat.st_mtime_ns == item["mtime_ns"], "source_unchanged")
    return root, folder, plan


def predecessor(root, plan):
    guard = whole.read(root / "gpu-thermal-guard.json")
    _, folder, _ = recovery.load(root, plan["frame_job"])
    status = whole.read(folder / "progress.json")
    require(identity(guard) == plan["predecessor_guard"] and identity(status) == plan["predecessor_worker"],
        "exact_predecessor_identities")
    require(guard["state"] in {"running", "worker_exited"} and status["stage"] != "stopped"
        and not (folder / "failure.json").exists(), "predecessor_not_failed_or_paused")
    if recovery.live(guard) or recovery.live(status):
        return False
    require(guard["state"] == "worker_exited" and guard.get("returncode") == 0
        and status["stage"] == "all_tiles_saved", "predecessor_successful_full_terminal")
    recovery.verify(root, plan["frame_job"])
    return True


def complete_source(store, folder, plan):
    """Resolve current error only after all enrichment, retaining the old error."""
    video, before = plan["video"], plan["rows_before_recovery"]
    current = exports.read_rows(exports.ReadOnlyArchive(store.root), video)
    require(current["video"] == before["video"] and current["segments"] == before["segments"],
        "source_and_full_asr_rows_unchanged")
    old_ready = {r["id"]: r for r in before["frames"] if r["status"] == "ready" and r["model"] == recovery.MODEL}
    require(len(current["frames"]) == len(before["frames"]) and all(r["status"] == "ready"
        and r["model"] == recovery.MODEL and (r["id"] not in old_ready or r == old_ready[r["id"]])
        for r in current["frames"]), "all_frames_ready_and_old_successes_unchanged")
    preserved = folder / "original-failure-retained.json"
    knowledge.atomic_json(preserved, {"video": before["video"], "frame_recovery": plan["frame_job"],
        "original_source_snapshot_sha256": recovery.digest(before), "original_failure_resolved_not_deleted": True})
    with knowledge.schema_lease(store.root / "wiki/next-source-publication.lock", timeout=0):
        with store.write() as db:
            row = dict(db.execute("SELECT * FROM videos WHERE id=?", (video,)).fetchone())
            require(row == before["video"], "same_failure_before_resolution")
            db.execute("INSERT INTO events(time,kind,data) VALUES(?,?,?)", (time.time(),
                "source_enrichment_failure_resolution_attempt", json.dumps({"job": folder.name,
                    "video": video, "original_error": row["error"], "immutable_record": str(preserved.relative_to(store.root))})))
            db.execute("UPDATE videos SET error='',updated=? WHERE id=?", (time.time(), video))
        try:
            knowledge.export_review(store, video)
            checked = exports.validate_lesson(exports.ReadOnlyArchive(store.root), video, plan["source_item"])
        except Exception:
            with store.write() as db:
                db.execute("UPDATE videos SET error=?,updated=? WHERE id=?", (before["video"]["error"], time.time(), video))
            raise
    knowledge.atomic_json(folder / "source-export-checked.json", checked)
    return checked


def enrich_one(root, key):
    root, folder, plan = load(root, key)
    store = knowledge.KnowledgeStore(root)
    process = psutil.Process()

    def progress(stage, **extra):
        knowledge.atomic_json(folder / "enrichment-progress.json", {"pid": process.pid, "created": process.create_time(),
            "job": key, "video": plan["video"], "stage": stage, "updated": time.time(), **extra})

    store.progress = progress
    with knowledge.schema_lease(root / "run.lock", timeout=0):
        require(not (folder / "enrichment-started.json").exists(), "one_enrichment_without_retry")
        knowledge.atomic_json(folder / "enrichment-started.json", {"pid": process.pid, "created": process.create_time()})
        try:
            recovery.checkpoint(root)
            current = exports.read_rows(exports.ReadOnlyArchive(root), plan["video"])
            require(current["video"] == plan["rows_before_recovery"]["video"]
                and current["segments"] == plan["rows_before_recovery"]["segments"], "complete_original_asr_reused")
            _, frame_folder, _ = recovery.load(root, plan["frame_job"])
            applied = whole.read(frame_folder / "applied.json")
            require(applied["video"] == plan["video"] and applied["source_failure_unchanged"], "actual_checked_frame_applied")
            progress("continuing_existing_frames_and_notes_without_asr")
            knowledge.enrich(store, plan["video"], recovery.MODEL, cooperative=True)
            checked = complete_source(store, folder, plan)
            progress("full_source_export_checked", frames=checked["frames"], note_windows=checked["note_windows"])
            return {"state": "full_retained_source_enrichment_checked", "job": key, "video": plan["video"]}
        except Exception as exc:
            failure = whole.failure_status(exc)
            knowledge.atomic_json(folder / "enrichment-failure.json", failure)
            knowledge.atomic_json(folder / "enrichment-failure-private.json", {"error": str(exc)})
            progress("stopped", failure=failure)
            raise


def dispatch(root, key, *, sleep=time.sleep):
    root, folder, plan = load(root, key)
    process = psutil.Process()

    def progress(state, **extra):
        knowledge.atomic_json(folder / "progress.json", {"pid": process.pid, "created": process.create_time(),
            "job": key, "video": plan["video"], "state": state, "updated": time.time(),
            "automatic_retry_allowed": False, "automatic_thermal_resume_allowed": False, **extra})

    with knowledge.schema_lease(folder / "dispatch.lock", timeout=0):
        require(not (folder / "started.json").exists(), "dispatch_once_without_retry")
        knowledge.atomic_json(folder / "started.json", {"pid": process.pid, "created": process.create_time()})
        try:
            while True:
                recovery.checkpoint(root)
                if predecessor(root, plan):
                    break
                progress("waiting_for_exact_successful_frame_recovery")
                sleep(10)
            require(exports.read_rows(exports.ReadOnlyArchive(root), plan["video"]) == plan["rows_before_recovery"],
                "unchanged_source_before_frame_application")
            recovery.apply(root, plan["frame_job"])
            whole.backup(root / "gpu-thermal-guard.json", folder / "successful-frame-predecessor-guard.json")
            progress("running_one_guarded_source_enrichment")
            guarded(root, [sys.executable, "-m", __spec__.name, "--root", str(root), "enrich", "--job", key],
                maximum_temperature=70, maximum_power=200, sampler=lambda: checked_sample(root))
            terminal = whole.read(root / "gpu-thermal-guard.json")
            require(terminal["state"] == "worker_exited" and terminal.get("returncode") == 0, "source_guard_success")
            checked = exports.validate_lesson(exports.ReadOnlyArchive(root), plan["video"], plan["source_item"])
            with httpx.Client(base_url=plan["reader_url"], trust_env=False, timeout=90) as client:
                while True:
                    recovery.checkpoint(root)
                    published = publication.publish_once(exports.ReadOnlyArchive(root), client)
                    if published["state"] != "waiting_for_existing_publisher":
                        break
                    sleep(5)
            require(plan["video"] in publication.available(publication.Library(root).catalog()), "checked_source_present_in_catalog")
            result = {"state": "one_recovered_source_enriched_export_checked_and_published", "job": key,
                "video": plan["video"], "export": checked, "publication": published, "original_failures_retained": True,
                "reader_article_published": False, "source_deletion_allowed": False}
            knowledge.atomic_json(folder / "result.json", result)
            base = Path.home() / "Documents/CourseKnowledgeBackups/source-enrichment-results" / key
            for path in [*folder.glob("*.json"), root / "lessons" / plan["video"] / "evidence.json",
                    root / "lessons" / plan["video"] / "graph.json"]:
                if path.name not in {"progress.json", "enrichment-progress.json"}:
                    whole.backup(path, base / path.name)
            progress(result["state"])
            return {k: result[k] for k in ("state", "job", "video", "reader_article_published")}
        except Exception as exc:
            failure = whole.failure_status(exc)
            knowledge.atomic_json(folder / "dispatch-failure.json", failure)
            progress(failure["state"], **{k: v for k, v in failure.items() if k != "state"})
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--frame-job", required=True)
    prepare_parser.add_argument("--reader-url", default="http://127.0.0.1:3003")
    for name in ("dispatch", "enrich"):
        sub.add_parser(name).add_argument("--job", required=True)
    args = parser.parse_args()
    try:
        result = prepare(args.root, args.frame_job, args.reader_url) if args.command == "prepare" else (
            {"dispatch": dispatch, "enrich": enrich_one}[args.command](args.root, args.job))
        print(json.dumps(result))
    except Exception as exc:
        print(json.dumps(whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
