"""Continue one authenticated interrupted source after the reserved source queues.

The known failed-frame source is excluded. No transcription, capture, model
recipe change or repeated accepted OCR is permitted by this continuation.
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

from . import course_source_recovery_tail as queue
from . import course_frame_ocr
from . import course_knowledge as knowledge
from . import course_lesson_exports as exports
from . import course_wiki_private_guard as guard
from .course_library import canonical

whole, publication = queue.whole, queue.publication
POLICY = "one-authenticated-interrupted-source-original-enrichment-after-seven-queue-v1"
TARGET = "2f239df40b6a630ea9e531a3"
AUDIT = "a13328e4111015e3e79fd9d61ba4fcda5ea5fdbb10861691cbee1aab56d0be42"
MODEL = "qwen3.5:9b"
DIRECTORY = "partial-source-continuations"
TERMINAL = "interrupted_source_remaining_enrichment_export_checked_and_published"


def require(value, code):
    if not value:
        raise ValueError("requires_partial_source_" + code)


def digest(value):
    return whole.sha(canonical(value))


def identity(row):
    return {key: row[key] for key in ("pid", "created")}


def bindings():
    return {"predecessor": queue.bindings(), "modules": {
        Path(module.__file__).name: whole.sha(Path(module.__file__).read_bytes())
        for module in (sys.modules[__name__], course_frame_ocr, knowledge, exports, guard)}}


def file_proof(root, path):
    path = Path(path)
    require(not path.is_symlink() and path.resolve().is_relative_to(root) and path.is_file(), "retained_regular_input_file")
    return {"sha256": whole.sha(path.read_bytes()), "bytes": path.stat().st_size}


def audit_load(root):
    folder = root / "wiki/partial-source-integrity-audits" / AUDIT
    audit = whole.read(folder / "manifest.json")
    before = whole.read(folder / "original-rows.json")
    require(digest(audit) == AUDIT and audit["video"] == TARGET and audit["model"] == MODEL
        and audit["model_calls"] == 0 and audit["negative_cache_records"] == 0
        and audit["pending_frames_with_cache"] == 0 and audit["source_current_error_empty"]
        and digest(before) == audit["source_rows_sha256"], "exact_real_readonly_cache_audit")
    require((audit["asr_segments"], audit["ready_frames"], audit["pending_frames"]) == (316, 45, 178)
        and before["video"]["status"] == "transcribed" and not before["video"]["error"]
        and not before["notes"], "same_interrupted_source_not_failed_frame_exception")
    return folder, audit, before


def check_inputs(root, plan):
    _, audit, before = audit_load(root)
    require(exports.read_rows(exports.ReadOnlyArchive(root), TARGET) == before, "all_original_rows_unchanged_before_enrichment")
    require(audit["model_digest"] == plan["model_digest"], "same_audited_model")
    for name, proof in plan["input_files"].items():
        require(file_proof(root, root / name) == proof, "all_original_images_success_cache_and_checkpoints_unchanged")
    for frame in audit["frames"]:
        if not frame["ready"]:
            require(not any((root / frame["cache_path"]).glob("*.json")), "pending_frames_not_attempted_after_audit")
    return before


def check_source_stat(plan):
    item = plan["source_item"]
    stat = Path(item["path"]).stat()
    require((stat.st_size, stat.st_mtime_ns) == (item["bytes"], item["mtime_ns"]), "unchanged_original_video_stat")


def prepare(root, predecessor, reader_url):
    root = Path(root).resolve()
    guard.checkpoint(root)
    _, previous_folder, previous, _, _ = queue.load(root, predecessor)
    current, started = [whole.read(previous_folder / name) for name in ("progress.json", "started.json")]
    require(queue.original.identity_live(current) and identity(current) == identity(started)
        and current["queue"] == predecessor and current["state"] == "waiting_for_exact_existing_inventory_queue",
        "exact_healthy_reserved_seven_source_predecessor")
    audit_folder, audit, before = audit_load(root)
    require(TARGET not in {row["video"] for row in previous["sources"]}, "not_in_predecessor_source_reservations")
    inventory = whole.read(root / "inventory-unique.json")
    item = next(row for row in inventory["videos"] if queue.original.item_id(row) == TARGET)
    require(len(inventory["videos"]) == 340 and not item.get("error") and item.get("audio_streams"), "full_original_audio_scope")
    require((before["video"]["size"], before["video"]["mtime_ns"], before["video"]["duration"])
        == (item["bytes"], item["mtime_ns"], item["duration_seconds"]), "same_registered_inventory_source")
    selected = {audit_folder / "manifest.json", audit_folder / "original-rows.json"}
    for frame in before["frames"]:
        selected.add(root / frame["path"])
    for frame in audit["frames"]:
        selected.update(root / frame["cache_path"] / name for name in frame["cache_files"])
    lesson = root / "lessons" / TARGET
    selected.update(lesson / name for name in ("frames-completed.json", "audio.json", "evidence.json", "graph.json"))
    plan = {"policy": POLICY, "video": TARGET, "audit": AUDIT, "source_item": item,
        "inventory_sha256": whole.sha((root / "inventory-unique.json").read_bytes()),
        "predecessor": predecessor, "predecessor_controller": identity(current),
        "predecessor_plan_sha256": whole.sha((previous_folder / "plan.json").read_bytes()),
        "predecessor_started_sha256": whole.sha((previous_folder / "started.json").read_bytes()),
        "input_files": {path.relative_to(root).as_posix(): file_proof(root, path) for path in sorted(selected)},
        "model": MODEL, "model_digest": audit["model_digest"], "code": bindings(),
        "reader_url": publication.reader_url(reader_url), "original_interruption_reason_proven": False,
        "asr_reexecution_allowed": False, "frame_capture_allowed": False, "old_ready_frame_reexecution_allowed": False,
        "automatic_retry_allowed": False, "automatic_thermal_resume_allowed": False, "model_budgets_reset": False,
        "maximum_temperature_c": 70, "maximum_power_w": 200, "minimum_free_gib_each_volume": 32,
        "inventory_audio_courses": 340, "silent_scope": 2, "all_recoverable_courseware_required": True,
        "reader_article_published": False, "source_deletion_allowed": False}
    check_inputs(root, plan)
    check_source_stat(plan)
    require(whole.local_model_digest(MODEL) == plan["model_digest"], "same_local_original_vision_weights")
    key = digest(plan)[:32]
    folder = root / "wiki" / DIRECTORY / key
    registry = root / "wiki/partial-source-continuation-registrations"
    registry.mkdir(parents=True, exist_ok=True)
    with knowledge.schema_lease(registry / "prepare.lock", timeout=0):
        registration = registry / (TARGET + ".json")
        require(not registration.exists() and not folder.exists()
            and not (root / "wiki/source-enrichment-registrations" / (TARGET + ".json")).exists(),
            "one_original_source_continuation_even_after_failure")
        folder.mkdir(parents=True)
        knowledge.atomic_json(registration, {"job": key, "video": TARGET, "audit": AUDIT})
        knowledge.atomic_json(folder / "plan.json", plan)
    return {"state": "one_interrupted_source_remaining_enrichment_prepared", "job": key,
        "remaining_frames": audit["pending_frames"], "preserved_ready_frames": audit["ready_frames"], "model_calls": 0}


def load(root, key):
    root = Path(root).resolve()
    require(isinstance(key, str) and re.fullmatch(r"[a-f0-9]{32}", key), "job_id")
    folder = root / "wiki" / DIRECTORY / key
    plan = whole.read(folder / "plan.json")
    registration = whole.read(root / "wiki/partial-source-continuation-registrations" / (TARGET + ".json"))
    require(plan["policy"] == POLICY and plan["video"] == TARGET and plan["audit"] == AUDIT
        and digest(plan)[:32] == key and plan["code"] == bindings() and registration["job"] == key,
        "exact_immutable_once_only_plan_and_code")
    require(whole.sha((root / "inventory-unique.json").read_bytes()) == plan["inventory_sha256"], "same_full_inventory")
    previous_folder = root / "wiki/recovered-source-tails" / plan["predecessor"]
    require(whole.sha((previous_folder / "plan.json").read_bytes()) == plan["predecessor_plan_sha256"]
        and whole.sha((previous_folder / "started.json").read_bytes()) == plan["predecessor_started_sha256"], "same_reserved_predecessor_plan_and_identity")
    return root, folder, plan


def predecessor_finished(root, plan):
    folder = root / "wiki/recovered-source-tails" / plan["predecessor"]
    current = whole.read(folder / "progress.json")
    require(identity(current) == plan["predecessor_controller"] and current["state"] != "stopped"
        and not any(folder.glob("failure-*.json")), "exact_predecessor_not_failed_or_replaced")
    if queue.original.identity_live(plan["predecessor_controller"]):
        return False
    previous = whole.read(folder / "plan.json")
    require(current["state"] == "seven_preserved_audio_sources_finished_existing_unresolved_scope_retained"
        and current["completed_sources"] == [row["video"] for row in previous["sources"]], "all_seven_sources_actually_finished")
    queue.validate_exports(root, folder, previous["sources"], predecessor=True)
    return True


def check_preserved_result(before, current):
    require(current["video"] == before["video"] and current["segments"] == before["segments"], "complete_asr_and_source_rows_preserved")
    previous = {row["id"]: row for row in before["frames"]}
    require(len(current["frames"]) == len(previous) and {row["id"] for row in current["frames"]} == set(previous), "all_original_frame_identities_preserved")
    for row in current["frames"]:
        old = previous[row["id"]]
        require(row["status"] == "ready" and row["model"] == MODEL, "all_remaining_frames_ready")
        require(old["status"] != "ready" or row == old, "accepted_ocr_rows_unchanged")
        require(all(row[field] == old[field] for field in ("id", "video", "time", "path")), "frame_pixels_and_clock_not_recaptured")


def worker(root, key):
    root, folder, plan = load(root, key)
    owner = psutil.Process()
    with knowledge.schema_lease(root / "run.lock", timeout=0), knowledge.schema_lease(folder / "worker.lock", timeout=0):
        require(not (folder / "worker-started.json").exists(), "worker_once_no_retry")
        knowledge.atomic_json(folder / "worker-started.json", identity({"pid": owner.pid, "created": owner.create_time()}))
        try:
            guard.checkpoint(root)
            before = check_inputs(root, plan)
            check_source_stat(plan)
            require(whole.local_model_digest(MODEL) == plan["model_digest"], "same_model_before_remaining_enrichment")
            store = knowledge.KnowledgeStore(root)

            def progress(stage, **extra):
                knowledge.atomic_json(folder / "enrichment-progress.json", {"pid": owner.pid, "created": owner.create_time(),
                    "stage": stage, "video": TARGET, "updated": time.time(), **extra})

            store.progress = progress
            knowledge.enrich(store, TARGET, MODEL, cooperative=True)
            current = exports.read_rows(exports.ReadOnlyArchive(root), TARGET)
            check_preserved_result(before, current)
            for name, proof in plan["input_files"].items():
                if "/frames/" in name or "/frame-ocr-cache/" in name or name.endswith("frames-completed.json"):
                    require(file_proof(root, root / name) == proof, "all_old_pixels_and_accepted_cache_preserved")
            knowledge.export_review(store, TARGET)
            checked = exports.validate_lesson(exports.ReadOnlyArchive(root), TARGET, plan["source_item"])
            knowledge.atomic_json(folder / "source-export-checked.json", checked)
            progress("remaining_enrichment_full_export_checked")
            return {"state": "remaining_enrichment_full_export_checked", "job": key, "video": TARGET}
        except BaseException as exc:
            knowledge.atomic_json(folder / "worker-failure.json", whole.failure_status(exc))
            raise


def run(root, key, *, sleep=time.sleep):
    root, folder, plan = load(root, key)
    owner = psutil.Process()

    def progress(state, **extra):
        knowledge.atomic_json(folder / "progress.json", {"pid": owner.pid, "created": owner.create_time(),
            "state": state, "job": key, "video": TARGET, "updated": time.time(),
            "automatic_retry_allowed": False, "automatic_thermal_resume_allowed": False, **extra})

    with knowledge.schema_lease(folder / "dispatch.lock", timeout=0):
        require(not (folder / "started.json").exists(), "dispatch_once_no_retry")
        restore = whole.read(folder / "independent-input-restore.json")
        require(restore["state"] == "partial_source_inputs_independently_restored" and restore["job"] == key
            and restore["original_archive_reads"] == 0 and restore["model_calls"] == 0,
            "actual_independent_input_restore_before_dispatch")
        knowledge.atomic_json(folder / "started.json", {"pid": owner.pid, "created": owner.create_time()})
        try:
            progress("waiting_for_exact_reserved_seven_source_queue")
            while True:
                guard.checkpoint(root)
                if predecessor_finished(root, plan):
                    break
                sleep(15)
            guard.checkpoint(root)
            with knowledge.schema_lease(root / "wiki/finite-source-batches.lock", timeout=0):
                check_inputs(root, plan)
                private = folder / "guard"
                private.mkdir()
                result = guard.run(root, private, [sys.executable, "-m", __spec__.name,
                    "--root", str(root), "--job", key, "worker"])
                require(result["state"] == "private_worker_exited" and result.get("returncode") == 0, "actual_guarded_enrichment_success")
                checked = exports.validate_lesson(exports.ReadOnlyArchive(root), TARGET, plan["source_item"])
                with httpx.Client(base_url=plan["reader_url"], trust_env=False, timeout=90) as client:
                    published = queue.prior.publish_verified(root, client, TARGET)
                result = {"state": TERMINAL, "job": key, "video": TARGET, "export": checked, "publication": published,
                    "original_asr_and_ready_frames_preserved": True, "reader_article_published": False, "source_deletion_allowed": False}
                knowledge.atomic_json(folder / "result.json", result)
                destination = Path.home() / "Documents/CourseKnowledgeBackups/partial-source-results" / key
                for path in [*folder.glob("*.json"), root / "lessons" / TARGET / "evidence.json", root / "lessons" / TARGET / "graph.json"]:
                    if path.name not in {"progress.json", "enrichment-progress.json"}:
                        whole.backup(path, destination / path.name)
                progress(TERMINAL)
                return result
        except BaseException as exc:
            knowledge.atomic_json(folder / "dispatch-failure.json", whole.failure_status(exc))
            progress("stopped")
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--predecessor")
    parser.add_argument("--job")
    parser.add_argument("--reader-url", default="http://127.0.0.1:3003")
    parser.add_argument("command", choices=["prepare", "load", "run", "worker"])
    args = parser.parse_args()
    try:
        require(bool(args.predecessor if args.command == "prepare" else args.job), "execution_id_required")
        if args.command == "prepare":
            result = prepare(args.root, args.predecessor, args.reader_url)
        elif args.command == "load":
            root, _, plan = load(args.root, args.job)
            check_inputs(root, plan)
            result = {"state": "partial_source_inputs_authenticated_without_inference", "job": args.job, "model_calls": 0}
        else:
            result = (worker if args.command == "worker" else run)(args.root, args.job)
        print(json.dumps(result))
    except BaseException as exc:
        print(json.dumps(whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
