"""One finite continuation for preserved alternate and gap-aware audio inputs.

The exact existing inventory controller must finish first. Existing failures,
source folders and model identities are retained; this never retries its queue.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import time
from types import SimpleNamespace

import psutil

from . import course_audio_archive as audio
from . import course_audio_recovery as alternate
from . import course_gap_asr as gap_asr
from . import course_gap_audio as gaps
from . import course_source_inventory_tail as inventory
from .course_knowledge import atomic_json, schema_lease

original, prior, whole, guard = inventory.original, inventory.prior, inventory.whole, inventory.guard
publication, digest = inventory.publication, inventory.digest
POLICY = "preserved-audio-exceptions-exact-inventory-predecessor-v1"
PROOFS = ("wiki/excluded-audio-input-preflight-20261110.json",
          "wiki/registered-alternate-audio-input-validation-20261110.json")
GAP_PROOF = "wiki/partial-gap-audio-input-validation-20261110.json"


def require(ok, code):
    if not ok:
        raise ValueError("requires_source_recovery_tail_" + code)


def checkpoint(root):
    guard.checkpoint(root)
    require(all(shutil.disk_usage(drive).free >= 32 * 1024**3
                for drive in {root.anchor, Path.home().anchor}), "each_volume_32gib_reserve")


def bindings():
    names = ["course_audio_archive", "course_audio_recovery", "course_gap_audio", "course_gap_asr",
             "course_mp4_edits", "course_audio_preparation", "course_textbook_whole_sources", "course_pdf_ocr",
             "course_library", "course_video_visual_audit", Path(__file__).stem]
    return {**inventory.bindings(), **{name: whole.sha(Path(__file__).with_name(name + ".py").read_bytes()) for name in names}}


def folder_for(root, key):
    require(isinstance(key, str) and re.fullmatch(r"[a-f0-9]{32}", key) is not None, "queue_id")
    return root / "wiki/recovered-source-tails" / key


def frozen_files(folder):
    require(folder.is_dir() and not folder.is_symlink(), "retained_source_directory")
    result = {}
    for path in sorted(folder.rglob("*")):
        require(not path.is_symlink(), "no_source_input_symlinks")
        if path.is_file() and path.suffix != ".lock":
            result[path.relative_to(folder).as_posix()] = {
                "sha256": audio.file_digest(path), "bytes": path.stat().st_size}
    require(0 < len(result) <= 64, "finite_preserved_source_files")
    return result


def evidence(root):
    first, second = (whole.read(root / name) for name in PROOFS)
    require(first["state"] == "three_excluded_audio_inputs_preflighted_without_asr"
            and second["state"] == "four_registered_pending_alternate_audio_inputs_reverified",
            "actual_prior_input_proofs")
    a, b = first["rows"], second["rows"]
    require(len(a) == 3 and len(b) == 4 and len({r["video"] for r in a + b}) == 7, "exact_seven_distinct_exceptions")
    return a, b


def readonly_store(root):
    def forbidden(*args, **kwargs):
        require(False, "existing_audio_binding_without_any_write")
    return SimpleNamespace(root=root, checkpoint=lambda: checkpoint(root), write_json=forbidden)


def input_provenance(root, row):
    key, item, kind = row["video"], row["item"], row["kind"]
    folder, store = root / "lessons" / key, readonly_store(root)
    if kind == "verified_original":
        value = audio.verified_audio(store, key)
        require(value is not None, "verified_original_audio_manifest")
        require((value["source"], value["source_size"], value["source_mtime_ns"])
                == (item["path"], item["bytes"], item["mtime_ns"]), "original_audio_source_binding")
        return {"manifest_sha256": whole.sha((folder / "audio.json").read_bytes()), "audio_sha256": value["file_digest"]}
    if kind == "alternate":
        require((folder / "asr-audio-source.json").is_file(), "existing_alternate_asr_binding")
        video = row["initial_video"]
        path, binding = alternate.transcription_audio(store, video, has_segments=False)
        require(path == folder / "alternate-audio.m4a" and binding is not None, "alternate_source_binding")
        return {"binding_sha256": digest(binding), "original_audio_verified": False}
    require(kind == "gaps", "known_explicit_audio_kind")
    value = gaps.gap_evidence(store, key, rehash=False)
    require(value is not None and (value["source"], value["source_size"], value["source_mtime_ns"])
            == (item["path"], item["bytes"], item["mtime_ns"]), "partial_audio_source_binding")
    plan = gap_asr.window_plan(value)
    return {"plan_sha256": digest(plan), "windows": len(plan), "clips": len(value["clips"]),
            "original_audio_verified": False, "required_status": "transcribed_with_gaps"}


def no_live_source_worker(item):
    for process in psutil.process_iter(["cmdline"]):
        args = process.info.get("cmdline") or []
        if "wechat_decrypt_tool.course_knowledge" not in args or "run" not in args:
            continue
        require("--video" in args and args.index("--video") + 1 < len(args), "no_broad_live_source_worker")
        require(Path(args[args.index("--video") + 1]).resolve() != Path(item["path"]).resolve(),
                "selected_source_worker_not_running")


def check_source(root, row):
    checkpoint(root)
    item, key = row["item"], row["video"]
    require(original.item_id(item) == key, "same_source_id")
    stat = Path(item["path"]).stat()
    require(stat.st_size == item["bytes"] and stat.st_mtime_ns == item["mtime_ns"], "same_original_media_stat")
    no_live_source_worker(item)
    with original.ReadOnlyArchive(root).connect() as db:
        current = db.execute("SELECT * FROM videos WHERE id=?", (key,)).fetchone()
        require((dict(current) if current else None) == row["initial_video"], "same_initial_registered_or_unregistered_state")
        for table in ("segments", "frames", "notes"):
            require(db.execute("SELECT COUNT(*) FROM " + table + " WHERE video=?", (key,)).fetchone()[0] == 0,
                    "no_committed_extraction_outputs_repeated")
    require(frozen_files(root / "lessons" / key) == row["input_files"], "same_exact_retained_input_files")
    require(input_provenance(root, row) == row["audio_provenance"], "same_original_alternate_or_gap_binding")


def verify_input_files(root, row):
    for name, value in row["input_files"].items():
        checkpoint(root)
        path = root / "lessons" / row["video"] / name
        require(path.is_file() and not path.is_symlink() and path.stat().st_size == value["bytes"]
                and audio.file_digest(path, lambda: checkpoint(root)) == value["sha256"],
                "original_audio_inputs_and_failures_preserved_after_extraction")


def select(root, previous):
    a, b = evidence(root)
    items = {original.item_id(item): item for item in whole.read(root / "inventory-unique.json")["videos"]}
    excluded = {r["video"]: r["code"] for r in previous["excluded"]}
    selected_ids = {r["video"] for r in previous["sources"]}
    partial = {r["video"]: r for r in whole.read(root / GAP_PROOF)["rows"]}
    rows = []
    with original.ReadOnlyArchive(root).connect() as db:
        for proof in a + b:
            key, item = proof["video"], items[proof["video"]]
            require(key not in selected_ids and excluded[key] == ("registered_source_not_retried" if proof in b
                    else "existing_output_requires_separate_recovery"), "same_explicit_predecessor_exception")
            current = db.execute("SELECT * FROM videos WHERE id=?", (key,)).fetchone()
            if proof in b:
                require(current is not None and current["status"] == "pending" and not current["error"], "existing_pending_alternate_source")
                kind = "alternate"
            else:
                require(current is None, "unregistered_preserved_source")
                kind = "gaps" if key in partial else "verified_original"
            row = {"video": key, "item": item, "kind": kind, "initial_video": dict(current) if current else None,
                   "input_files": frozen_files(root / "lessons" / key)}
            if kind == "verified_original":
                require(row["input_files"] == proof["files"], "actual_original_audio_and_repair_files_unchanged")
            elif kind == "alternate":
                require({name: value["sha256"] for name, value in row["input_files"].items()} == proof["files"],
                        "actual_alternate_audio_files_unchanged")
            else:
                folder = root / "lessons" / key
                require(whole.sha((folder / "audio-gap-recovery.json").read_bytes()) == partial[key]["manifest_sha256"],
                        "same_independently_verified_gap_manifest")
                value = whole.read(folder / "audio-gap-recovery.json")
                expected = {"audio-gap-recovery.json", value["original_packets"]["file"], *(r["file"] for r in value["clips"])}
                require(set(row["input_files"]) == expected, "only_actual_gap_audio_inputs")
            row["audio_provenance"] = input_provenance(root, row)
            rows.append(row)
    require(sum(r["kind"] == "alternate" for r in rows) == 4 and sum(r["kind"] == "gaps" for r in rows) == 2
            and sum(r["kind"] == "verified_original" for r in rows) == 1, "complete_original_exception_partition")
    return rows


def prepare(root, predecessor):
    root = Path(root).resolve()
    checkpoint(root)
    with schema_lease(root / "wiki/recovered-source-tail-prepare.lock", timeout=0):
        _, previous_folder, previous, _, _ = inventory.load(root, predecessor)
        require(previous["asr_batch_size"] == 4 and previous["asr_precision"] == "float32", "same_original_asr_execution_options")
        status = whole.read(previous_folder / "progress.json")
        started = whole.read(previous_folder / "started.json")
        require(original.identity_live(status) and status["queue"] == predecessor
                and all(status[k] == started[k] for k in ("pid", "created"))
                and status["state"] in {"waiting_for_exact_existing_source_tail", "processing_frozen_unattempted_inventory_source",
                                        "publishing_verified_source_export", "new_source_export_and_http_publication_verified"},
                "exact_live_existing_inventory_controller")
        rows = select(root, previous)
        for existing in (root / "wiki/recovered-source-tails").glob("*/plan.json"):
            old = whole.read(existing)
            require(old["predecessor"] != predecessor and not {r["video"] for r in old["sources"]} & {r["video"] for r in rows},
                    "exceptions_not_already_reserved_even_after_failure")
        for row in rows:
            check_source(root, row)
        files = [root / name for name in (*PROOFS, GAP_PROOF)]
        plan = {"policy": POLICY, "code": bindings(), "predecessor": predecessor,
                "predecessor_plan_sha256": digest(previous), "predecessor_controller": {k: status[k] for k in ("pid", "created")},
                "predecessor_started_sha256": whole.sha((previous_folder / "started.json").read_bytes()),
                "evidence_files": {p.relative_to(root).as_posix(): whole.sha(p.read_bytes()) for p in files},
                "sources": rows, "inventory_audio_courses": 340, "asr_model": previous["asr_model"],
                "model_files": previous["model_files"], "knowledge_model": previous["knowledge_model"],
                "knowledge_model_digest": whole.local_model_digest(previous["knowledge_model"]),
                "asr_batch_size": 4, "asr_precision": "float32", "model_budgets_reset": False,
                "automatic_retry_allowed": False, "automatic_thermal_resume_allowed": False,
                "maximum_temperature_c": 70, "maximum_power_w": 200, "minimum_free_gib_each_volume": 32,
                "reader_article_published": False, "source_deletion_allowed": False}
        key = digest(plan)[:32]
        folder = folder_for(root, key)
        require(not folder.exists(), "single_recovery_queue_registration")
        folder.mkdir(parents=True)
        atomic_json(folder / "plan.json", plan)
        destination = Path.home() / "Documents/CourseKnowledgeBackups/recovered-source-tail-plans" / key
        for path in [folder / "plan.json", Path(__file__), *files]:
            whole.backup(path, destination / path.name)
        return {"state": "seven_preserved_audio_sources_prepared", "queue": key, "sources": 7, "model_calls": 0}


def load(root, key):
    root = Path(root).resolve()
    folder = folder_for(root, key)
    plan = whole.read(folder / "plan.json")
    require(plan["policy"] == POLICY and digest(plan)[:32] == key and plan["code"] == bindings(), "same_frozen_recovery_plan_and_code")
    _, previous_folder, previous, _, _ = inventory.load(root, plan["predecessor"])
    require(digest(previous) == plan["predecessor_plan_sha256"]
            and whole.sha((previous_folder / "started.json").read_bytes()) == plan["predecessor_started_sha256"],
            "same_original_predecessor_plan_and_execution")
    require(all(whole.sha((root / name).read_bytes()) == expected for name, expected in plan["evidence_files"].items()),
            "all_original_input_proofs_unchanged")
    require(whole.local_model_digest(plan["knowledge_model"]) == plan["knowledge_model_digest"], "same_original_knowledge_model_weights")
    return root, folder, plan, previous_folder, previous


def validate_exports(root, folder, sources, *, predecessor=False):
    available = publication.available(publication.Library(root).catalog())
    owner = whole.read(folder / "started.json")
    for number, row in enumerate(sources):
        checkpoint(root)
        key = row["video"]
        receipt = whole.read(folder / (key + ".json"))
        checked = original.validate_lesson(original.ReadOnlyArchive(root), key, row["item"])
        state = whole.read(folder / f"source-{number:04d}-guard/guard-progress.json")
        require(receipt["video"] == key and receipt["export"] == checked and key in available
                and state["state"] == "worker_exited" and state.get("returncode") == 0,
                "real_source_export_publication_and_guard_success")
        require(all(state[k] == owner[k] for k in ("pid", "created")), "same_real_guard_controller_identity")
        if predecessor:
            require(not original.identity_live(state), "predecessor_guard_owner_actually_exited")
        else:
            verify_input_files(root, row)


def wait(root, plan, previous_folder, previous, *, live=original.identity_live, sleep=time.sleep):
    while live(plan["predecessor_controller"]):
        checkpoint(root)
        sleep(15)
    checkpoint(root)
    status = whole.read(previous_folder / "progress.json")
    require(all(status[k] == value for k, value in plan["predecessor_controller"].items())
            and status["state"] == "frozen_unattempted_inventory_tail_finished_existing_exceptions_remain"
            and status["completed_sources"] == [r["video"] for r in previous["sources"]],
            "exact_predecessor_finished_successfully_without_omissions")
    validate_exports(root, previous_folder, previous["sources"], predecessor=True)


def run(root, key, reader_url):
    root, folder, plan, previous_folder, previous = load(root, key)
    owner, completed = psutil.Process(), []

    def progress(state, **extra):
        atomic_json(folder / "progress.json", {"pid": owner.pid, "created": owner.create_time(), "updated": time.time(),
            "state": state, "queue": key, "completed_sources": list(completed), "target_sources": len(plan["sources"]),
            "inventory_audio_courses": 340, "automatic_retry_allowed": False, "automatic_thermal_resume_allowed": False,
            "reader_article_published": False, "source_deletion_allowed": False, **extra})

    with schema_lease(root / "wiki/recovered-source-tail-controller.lock", timeout=0):
        require(not (folder / "started.json").exists(), "controller_once_without_automatic_retry")
        receipt = whole.read(folder / "independent-metadata-restore.json")
        require(receipt["state"] == "preserved_audio_execution_metadata_independently_restored"
                and receipt["queue"] == key and receipt["model_calls"] == 0 and receipt["original_root_reads"] == 0,
                "actual_independent_execution_metadata_restore_before_dispatch")
        atomic_json(folder / "started.json", {"pid": owner.pid, "created": owner.create_time()})
        try:
            progress("waiting_for_exact_existing_inventory_queue")
            wait(root, plan, previous_folder, previous)
            with schema_lease(root / "wiki/finite-source-batches.lock", timeout=0), original.httpx.Client(
                    base_url=publication.reader_url(reader_url), trust_env=False, timeout=90) as client:
                for number, row in enumerate(plan["sources"]):
                    checkpoint(root)
                    load(root, key)
                    check_source(root, row)
                    private = folder / f"source-{number:04d}-guard"
                    private.mkdir()
                    progress("extracting_preserved_audio_source", video=row["video"], audio_kind=row["kind"])
                    result = guard.run(root, private, original.worker_command(root, plan, row))
                    if result["state"] == "paused_without_automatic_resume":
                        raise InterruptedError("requires_source_recovery_tail_private_guard_paused_no_auto_resume")
                    require(result["state"] == "private_worker_exited" and result.get("returncode") == 0, "actual_worker_successful_exit")
                    state = whole.read(root / "job-progress.json")
                    require(state["stage"] == "batch_finished" and state.get("processed") == 1 and state.get("failures") == 0,
                            "actual_single_source_completed_without_failure")
                    verify_input_files(root, row)
                    checked = original.validate_lesson(original.ReadOnlyArchive(root), row["video"], row["item"])
                    progress("publishing_verified_preserved_source", video=row["video"])
                    receipt = {"video": row["video"], "audio_kind": row["kind"], "export": checked,
                        "publication": prior.publish_verified(root, client, row["video"]),
                        "reader_article_published": False, "source_deletion_allowed": False}
                    atomic_json(folder / (row["video"] + ".json"), receipt)
                    destination = Path.home() / "Documents/CourseKnowledgeBackups/recovered-source-tail-results" / key / row["video"]
                    for path in [folder / (row["video"] + ".json"), root / "job-progress.json", root / "run-plan.json",
                                 *private.glob("*.json"), root / "lessons" / row["video"] / "evidence.json",
                                 root / "lessons" / row["video"] / "graph.json"]:
                        whole.backup(path, destination / path.relative_to(root))
                    completed.append(row["video"])
                    progress("preserved_source_export_and_http_publication_verified")
            validate_exports(root, folder, plan["sources"])
            progress("seven_preserved_audio_sources_finished_existing_unresolved_scope_retained")
            return {"state": "seven_preserved_audio_sources_finished_existing_unresolved_scope_retained", "sources": len(completed),
                    "reader_article_published": False, "source_deletion_allowed": False}
        except BaseException as exc:
            failure = whole.failure_status(exc)
            atomic_json(folder / ("failure-" + str(time.time_ns()) + ".json"), failure)
            progress(failure["state"], reason_code=failure["reason_code"])
            raise


def verify(root, key):
    root, folder, plan, _, _ = load(root, key)
    status, started = whole.read(folder / "progress.json"), whole.read(folder / "started.json")
    require(all(status[k] == started[k] for k in ("pid", "created")) and not original.identity_live(started)
            and status["state"] == "seven_preserved_audio_sources_finished_existing_unresolved_scope_retained"
            and status["completed_sources"] == [r["video"] for r in plan["sources"]],
            "actual_complete_recovery_controller_exit_before_readonly_verification")
    validate_exports(root, folder, plan["sources"])
    return {"state": "all_seven_preserved_source_results_reverified", "sources": len(plan["sources"]),
            "model_calls": 0, "reader_article_published": False, "source_deletion_allowed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--predecessor")
    parser.add_argument("--queue")
    parser.add_argument("--reader-url", default="http://127.0.0.1:3003", type=publication.reader_url)
    parser.add_argument("command", choices=["prepare", "load", "run", "verify"])
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            require(args.predecessor is not None, "predecessor_required")
            result = prepare(args.root, args.predecessor)
        elif args.command == "load":
            _, _, plan, _, _ = load(args.root, args.queue)
            result = {"state": "preserved_audio_execution_metadata_authenticated", "sources": len(plan["sources"]), "model_calls": 0}
        elif args.command == "verify":
            result = verify(args.root, args.queue)
        else:
            result = run(args.root, args.queue, args.reader_url)
        print(json.dumps(result))
    except BaseException as exc:
        print(json.dumps(whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
