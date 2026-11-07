"""Process a finite unattempted source batch after one exact healthy predecessor.

The existing ASR/OCR/notes pipeline, local models and GPU lease are unchanged.
A pause, failed predecessor or failed lesson stops this batch without retrying.
Completed exports are checked and published through the existing source API.
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

from . import course_library_queue as publication
from . import course_textbook_whole_sources as whole
from .course_archive_gpu import checked_sample
from .course_gpu_guard import run as guarded
from .course_knowledge import atomic_json, schema_lease
from .course_lesson_exports import validate_lesson
from .course_video_visual_audit import ReadOnlyArchive

POLICY = "finite-unattempted-sources-exact-predecessor-v1"
PRODUCTION = ("course_knowledge", "course_gpu_guard", "course_archive_gpu", "course_lesson_exports", "course_library_queue")


def require(ok, code):
    if not ok:
        raise ValueError("requires_source_batch_" + code)


def checkpoint(root):
    whole.TextbookStore(root).checkpoint()
    if (root / "supervisor-stop.flag").exists():
        raise InterruptedError("requires_source_batch_supervisor_stop_preserved")


def bindings():
    return {name: whole.sha(Path(__file__).with_name(name + ".py").read_bytes()) for name in (*PRODUCTION, Path(__file__).stem)}


def identity_live(identity):
    return publication.identity_alive(identity["pid"], identity["created"])


def item_id(item):
    return whole.sha(str(Path(item["path"]).resolve()).encode())[:24]


def retained_inputs(root, key, item):
    """An audio-only archive is an input, never a previous transcription attempt."""
    folder = root / "lessons" / key
    if not folder.exists():
        return {}
    require(not folder.is_symlink(), "source_folder_not_symlink")
    if {path.name for path in folder.iterdir()} != {"audio.json", "audio.m4a"}:
        return None
    manifest, audio = folder / "audio.json", folder / "audio.m4a"
    require(manifest.is_file() and audio.is_file() and not manifest.is_symlink() and not audio.is_symlink(), "retained_audio_files")
    value = whole.read(manifest)
    require(value.get("verified") is True and value.get("video") == key
        and Path(value["source"]).resolve() == Path(item["path"]).resolve()
        and value["source_size"] == item["bytes"] and value["source_mtime_ns"] == item["mtime_ns"]
        and value["file"] == "audio.m4a" and value["file_bytes"] == audio.stat().st_size
        and (not item.get("sha256") or value["source_digest"] == item["sha256"]), "unchanged_verified_audio_input")
    return {"manifest_sha256": whole.sha(manifest.read_bytes()), "file_digest": value["file_digest"],
        "file_bytes": value["file_bytes"], "file_mtime_ns": audio.stat().st_mtime_ns}


def select_unattempted(root, inventory, limit):
    require(type(limit) is int and 1 <= limit <= 3, "finite_one_to_three_source_limit")
    store = ReadOnlyArchive(root)
    with store.connect() as db:
        registered = {row[0] for row in db.execute("SELECT id FROM videos")}
    selected = []
    for item in inventory["videos"]:
        if item.get("error") or not item.get("audio_streams") or not item.get("duration_seconds"):
            continue
        key = item_id(item)
        if key in registered:
            continue
        audio = retained_inputs(root, key, item)
        if audio is None:
            continue
        source = Path(item["path"])
        stat = source.stat()
        require(stat.st_size == item["bytes"] and stat.st_mtime_ns == item["mtime_ns"], "frozen_new_source_stat")
        selected.append({"video": key, "item": item, "retained_audio": audio})
        if len(selected) == limit:
            break
    require(len(selected) == limit and len({row["video"] for row in selected}) == limit, "enough_distinct_unattempted_sources")
    return selected


def prepare(root, model, predecessor, worker, *, limit=3):
    root, model = Path(root).resolve(), Path(model).resolve()
    checkpoint(root)
    guard = whole.read(root / "gpu-thermal-guard.json")
    status = whole.read(root / "job-progress.json")
    require(identity_live(predecessor) and identity_live(worker), "both_exact_predecessor_processes_live")
    require(all(guard[key] == predecessor[key] for key in ("pid", "created")) and guard["state"] == "running"
        and status["pid"] == worker["pid"], "actual_predecessor_guard_and_worker")
    require((model / "config.json").is_file() and (model / "model.safetensors").is_file(), "existing_local_asr_model")
    arguments = psutil.Process(worker["pid"]).cmdline()
    require("wechat_decrypt_tool.course_knowledge" in arguments and "--video" in arguments
        and Path(arguments[arguments.index("--asr-model") + 1]).resolve() == model
        and arguments[arguments.index("--limit") + 1] == "1", "single_source_predecessor_role")
    previous_source = Path(arguments[arguments.index("--video") + 1]).resolve()
    inventory_path = root / "inventory-unique.json"
    inventory = whole.read(inventory_path)
    require(inventory["unique_readable_videos"] == 340, "complete_original_audio_inventory")
    previous_item = next(item for item in inventory["videos"] if Path(item["path"]).resolve() == previous_source)
    plan = {"policy": POLICY, "inventory_sha256": whole.sha(inventory_path.read_bytes()), "inventory_audio_courses": 340,
        "predecessor_guard": predecessor, "predecessor_worker": worker, "predecessor_video": item_id(previous_item),
        "sources": select_unattempted(root, inventory, limit), "asr_model": str(model),
        "model_files": {name: {"bytes": (model / name).stat().st_size, "mtime_ns": (model / name).stat().st_mtime_ns}
            for name in ("config.json", "model.safetensors")}, "module_sha256": bindings(),
        "asr_batch_size": 4, "asr_precision": "float32", "knowledge_model": "qwen3.5:9b",
        "maximum_power_w": 200, "maximum_temperature_c": 70, "minimum_free_gib_each_volume": 32,
        "automatic_retry_allowed": False, "automatic_thermal_resume_allowed": False,
        "source_deletion_allowed": False, "reader_article_published": False}
    key = whole.sha(publication.canonical(plan))[:32]
    folder = root / "wiki/source-batches" / key
    path = folder / "plan.json"
    require(not path.exists(), "batch_already_prepared")
    folder.mkdir(parents=True)
    atomic_json(path, plan)
    for source in (path, Path(__file__), inventory_path):
        whole.backup(source, Path.home() / "Documents/CourseKnowledgeBackups/source-batches" / key / source.name)
    return key, plan


def load_plan(root, key):
    require(re.fullmatch(r"[a-f0-9]{32}", key) is not None, "batch_id")
    folder = root / "wiki/source-batches" / key
    plan = whole.read(folder / "plan.json")
    require(plan["policy"] == POLICY and key == whole.sha(publication.canonical(plan))[:32]
        and plan["module_sha256"] == bindings(), "unchanged_finite_plan_and_code")
    require(whole.sha((root / "inventory-unique.json").read_bytes()) == plan["inventory_sha256"], "unchanged_full_inventory")
    for name, expected in plan["model_files"].items():
        stat = (Path(plan["asr_model"]) / name).stat()
        require({"bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns} == expected, "unchanged_existing_model_files")
    return folder, plan


def terminal_predecessor(root, plan):
    guard, status = whole.read(root / "gpu-thermal-guard.json"), whole.read(root / "job-progress.json")
    require(all(guard[key] == plan["predecessor_guard"][key] for key in ("pid", "created"))
        and guard["state"] == "worker_exited" and guard.get("returncode") == 0, "predecessor_guard_success")
    require(status["pid"] == plan["predecessor_worker"]["pid"]
        and status["stage"] == "batch_finished" and status.get("processed") == 1
        and status.get("failures") == 0, "predecessor_single_source_success")


def archive_state(root, folder, label):
    for name in ("gpu-thermal-guard.json", "job-progress.json", "run-plan.json"):
        path = root / name
        if path.exists():
            target = folder / "retained-states" / label / name
            whole.backup(path, target)
            whole.backup(target, Path.home() / "Documents/CourseKnowledgeBackups/source-batches" / folder.name
                / "retained-states" / label / name)


def worker_command(root, plan, row):
    return [sys.executable, "-m", "wechat_decrypt_tool.course_knowledge", "--root", str(root), "--model", plan["knowledge_model"],
        "run", "--inventory", str(root / "inventory-unique.json"), "--asr-model", plan["asr_model"],
        "--video", row["item"]["path"], "--limit", "1", "--enrich", "--asr-batch-size", "4"]


def run(root, key, reader_url, *, sleep=time.sleep):
    root = Path(root).resolve()
    folder, plan = load_plan(root, key)
    identity = {"pid": psutil.Process().pid, "created": psutil.Process().create_time()}
    completed = []

    def progress(state, **extra):
        atomic_json(folder / "progress.json", {**identity, "state": state, "updated": time.time(),
            "batch": key, "target_sources": len(plan["sources"]), "completed_sources": list(completed),
            "automatic_retry_allowed": False, "automatic_thermal_resume_allowed": False,
            "reader_article_published": False, "source_deletion_allowed": False, **extra})

    with schema_lease(root / "wiki/finite-source-batches.lock", timeout=0):
        require(not (folder / "started.json").exists(), "one_launch_no_automatic_retry")
        atomic_json(folder / "started.json", {**identity, "updated": time.time()})
        try:
            while any(identity_live(plan[key]) for key in ("predecessor_guard", "predecessor_worker")):
                checkpoint(root)
                progress("waiting_for_exact_predecessor", predecessor_video=plan["predecessor_video"])
                sleep(15)
            checkpoint(root)
            terminal_predecessor(root, plan)
            validate_lesson(ReadOnlyArchive(root), plan["predecessor_video"])
            archive_state(root, folder, "predecessor")
            with httpx.Client(base_url=publication.reader_url(reader_url), trust_env=False, timeout=90) as client:
                for row in plan["sources"]:
                    checkpoint(root)
                    load_plan(root, key)
                    video, item = row["video"], row["item"]
                    with ReadOnlyArchive(root).connect() as db:
                        require(db.execute("SELECT 1 FROM videos WHERE id=?", (video,)).fetchone() is None, "next_source_still_unattempted")
                    require(video == item_id(item)
                        and retained_inputs(root, video, item) == row.get("retained_audio", {}), "new_source_identity_and_output_scope")
                    stat = Path(item["path"]).stat()
                    require(stat.st_size == item["bytes"] and stat.st_mtime_ns == item["mtime_ns"], "unchanged_next_source")
                    prior = whole.read(root / "gpu-thermal-guard.json")
                    require(not identity_live(prior) and prior["state"] == "worker_exited" and prior.get("returncode") == 0,
                        "prior_guard_exited_successfully")
                    progress("processing_one_guarded_source", video=video)

                    def sample():
                        checkpoint(root)
                        return checked_sample(root)

                    guarded(root, worker_command(root, plan, row), maximum_temperature=70, maximum_power=200,
                        guard_name="gpu-thermal-guard", sampler=sample)
                    checkpoint(root)
                    guard, status = whole.read(root / "gpu-thermal-guard.json"), whole.read(root / "job-progress.json")
                    require(guard["state"] == "worker_exited" and guard.get("returncode") == 0
                        and status["stage"] == "batch_finished" and status.get("processed") == 1
                        and status.get("failures") == 0, "current_source_successful_terminal")
                    checked = validate_lesson(ReadOnlyArchive(root), video, item)
                    progress("publishing_verified_source_export", video=video)
                    while True:
                        checkpoint(root)
                        receipt = publication.publish_once(ReadOnlyArchive(root), client)
                        if receipt["state"] != "waiting_for_existing_publisher":
                            break
                        sleep(5)
                    catalog = publication.Library(root).catalog()
                    require(video in publication.available(catalog), "completed_source_in_full_catalog")
                    if receipt["state"] == "waiting_for_complete_source_exports":
                        receipt = publication.verify_publication(ReadOnlyArchive(root), client,
                            publication.available(catalog) - {video}, catalog)
                    result = {"video": video, "export": checked, "publication": receipt, "reader_article_published": False,
                        "source_deletion_allowed": False}
                    path = folder / (video + ".json")
                    atomic_json(path, result)
                    for source in (path, root / "lessons" / video / "evidence.json", root / "lessons" / video / "graph.json"):
                        whole.backup(source, Path.home() / "Documents/CourseKnowledgeBackups/source-batches" / key / video / source.name)
                    archive_state(root, folder, video)
                    completed.append(video)
                    progress("source_export_and_http_publication_verified", video=video)
            progress("finite_source_batch_finished")
        except BaseException as exc:
            failure = whole.failure_status(exc)
            (folder / "failures").mkdir(exist_ok=True)
            atomic_json(folder / "failures" / (str(time.time_ns()) + ".json"), failure)
            progress(failure["state"], **{k: v for k, v in failure.items() if k != "state"})
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--asr-model", type=Path, required=True)
    prepare_parser.add_argument("--guard-pid", type=int, required=True)
    prepare_parser.add_argument("--guard-created", type=float, required=True)
    prepare_parser.add_argument("--worker-pid", type=int, required=True)
    prepare_parser.add_argument("--worker-created", type=float, required=True)
    worker = sub.add_parser("worker")
    worker.add_argument("--batch", required=True)
    worker.add_argument("--reader-url", type=publication.reader_url, default="http://127.0.0.1:3003")
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            key, plan = prepare(args.root, args.asr_model, {"pid": args.guard_pid, "created": args.guard_created},
                {"pid": args.worker_pid, "created": args.worker_created})
            print(json.dumps({"state": "finite_source_batch_prepared", "batch": key, "sources": len(plan["sources"]),
                "model_calls": 0, "inventory_audio_courses": 340}))
        else:
            run(args.root, args.batch, args.reader_url)
            print(json.dumps({"state": "finite_source_batch_returned"}))
    except Exception as exc:
        print(json.dumps(whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
