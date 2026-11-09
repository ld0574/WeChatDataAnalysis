"""Continue only an unattempted source tail after the old in-process guard stopped.

The completed prefix, old failure and all original source/model identities stay
unchanged. Each new source uses its own private guard receipt, so the controller's
own PID is never mistaken for a still-running previous worker.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

import psutil

from . import course_source_batch as original
from . import course_wiki_private_guard as guard
from .course_knowledge import atomic_json, schema_lease

whole, publication = original.whole, original.publication
DIRECTORY = "unattempted-tail"
POLICY = "original-source-batch-unused-tail-private-guards-v1"


def require(ok, code):
    if not ok:
        raise ValueError("requires_source_tail_" + code)


def digest(value):
    return whole.sha(publication.canonical(value))


def bindings():
    return {**original.bindings(), Path(__file__).stem: whole.sha(Path(__file__).read_bytes()),
        Path(guard.__file__).stem: whole.sha(Path(guard.__file__).read_bytes())}


def check_new(root, row):
    video, item = row["video"], row["item"]
    with original.ReadOnlyArchive(root).connect() as db:
        require(db.execute("SELECT 1 FROM videos WHERE id=?", (video,)).fetchone() is None,
            "selected_tail_source_still_unattempted")
    require(video == original.item_id(item)
        and original.retained_inputs(root, video, item) == row.get("retained_audio", {}),
        "same_original_source_and_retained_audio")
    stat = Path(item["path"]).stat()
    require(stat.st_size == item["bytes"] and stat.st_mtime_ns == item["mtime_ns"], "same_original_source_stat")


def completed_prefix(root, folder, plan, status):
    done = status["completed_sources"]
    require(status["state"] == "stopped"
        and status["reason_code"] == "requires_source_batch_prior_guard_exited_successfully"
        and not original.identity_live(status) and 0 < len(done) < len(plan["sources"]),
        "exact_old_controller_failure_after_completed_source")
    require(done == [row["video"] for row in plan["sources"][:len(done)]], "contiguous_completed_original_prefix")
    proof = {}
    catalog = publication.Library(root).catalog()
    for row in plan["sources"][:len(done)]:
        path = folder / (row["video"] + ".json")
        receipt = whole.read(path)
        checked = original.validate_lesson(original.ReadOnlyArchive(root), row["video"], row["item"])
        require(receipt["video"] == row["video"] and receipt["export"] == checked
            and row["video"] in publication.available(catalog), "actual_completed_prefix_export_and_publication")
        proof[row["video"]] = {"receipt_sha256": whole.sha(path.read_bytes()), "export_sha256": digest(checked)}
    return proof


def prepare(root, key):
    root = Path(root).resolve()
    original.checkpoint(root)
    original.checked_sample(root)
    folder, plan = original.load_plan(root, key)
    status = whole.read(folder / "progress.json")
    prefix = completed_prefix(root, folder, plan, status)
    selected = plan["sources"][len(prefix):]
    for row in selected:
        check_new(root, row)
    target = folder / DIRECTORY
    require(not target.exists(), "single_tail_registration_without_budget_refresh")
    paths = [folder / "plan.json", folder / "started.json", folder / "progress.json",
        *folder.glob("failures/*.json"), *(folder / (video + ".json") for video in prefix)]
    contract = {"policy": POLICY, "batch": key, "code": bindings(), "old_plan_sha256": digest(plan),
        "old_files": {p.relative_to(root).as_posix(): whole.sha(p.read_bytes()) for p in paths},
        "original_completed_prefix": prefix, "sources": selected, "model_budgets_reset": False,
        "automatic_retry_allowed": False, "automatic_thermal_resume_allowed": False,
        "reader_article_published": False, "source_deletion_allowed": False}
    target.mkdir()
    atomic_json(target / "execution.json", contract)
    backup = Path.home() / "Documents/CourseKnowledgeBackups/source-tails" / key
    for path in [target / "execution.json", Path(__file__), Path(guard.__file__), *paths]:
        relative = path.relative_to(root) if path.is_relative_to(root) else Path("code") / path.name
        whole.backup(path, backup / relative)
    return {"state": "unused_source_tail_prepared", "batch": key,
        "completed_original_sources_reused": len(prefix), "new_sources": len(selected), "model_calls": 0}


def load(root, key):
    root = Path(root).resolve()
    folder, plan = original.load_plan(root, key)
    target = folder / DIRECTORY
    contract = whole.read(target / "execution.json")
    require(contract["policy"] == POLICY and contract["batch"] == key and contract["code"] == bindings()
        and contract["old_plan_sha256"] == digest(plan), "frozen_original_inputs_and_tail_code")
    require(all(whole.sha((root / name).read_bytes()) == checksum for name, checksum in contract["old_files"].items()),
        "old_failure_completed_prefix_and_start_records_preserved")
    count = len(contract["original_completed_prefix"])
    require(contract["sources"] == plan["sources"][count:] and 0 < count < len(plan["sources"]),
        "only_original_unattempted_tail")
    return root, folder, target, plan, contract


def publish_verified(root, client, video, *, sleep=time.sleep):
    while True:
        original.checkpoint(root)
        receipt = publication.publish_once(original.ReadOnlyArchive(root), client)
        if receipt["state"] != "waiting_for_existing_publisher":
            break
        sleep(5)
    catalog = publication.Library(root).catalog()
    require(video in publication.available(catalog), "finished_source_in_actual_catalog")
    if receipt["state"] == "waiting_for_complete_source_exports":
        receipt = publication.verify_publication(original.ReadOnlyArchive(root), client,
            publication.available(catalog) - {video}, catalog)
    return receipt


def run(root, key, reader_url):
    root, folder, target, plan, contract = load(root, key)
    owner, completed = psutil.Process(), []

    def progress(state, **extra):
        atomic_json(target / "progress.json", {"pid": owner.pid, "created": owner.create_time(),
            "state": state, "updated": time.time(), "batch": key, "completed_sources": list(completed),
            "target_sources": len(contract["sources"]), "original_completed_sources_reused": len(contract["original_completed_prefix"]),
            "automatic_retry_allowed": False, "automatic_thermal_resume_allowed": False,
            "reader_article_published": False, "source_deletion_allowed": False, **extra})

    with schema_lease(root / "wiki/finite-source-batches.lock", timeout=0):
        require(not (target / "started.json").exists(), "controller_once_no_automatic_retry")
        original.checkpoint(root)
        require(completed_prefix(root, folder, plan, whole.read(folder / "progress.json"))
            == contract["original_completed_prefix"], "same_verified_completed_prefix")
        atomic_json(target / "started.json", {"pid": owner.pid, "created": owner.create_time()})
        try:
            with original.httpx.Client(base_url=publication.reader_url(reader_url), trust_env=False, timeout=90) as client:
                for number, row in enumerate(contract["sources"]):
                    original.checkpoint(root)
                    load(root, key)
                    check_new(root, row)
                    private = target / f"item-{number:02d}-guard"
                    private.mkdir()
                    progress("processing_original_unattempted_source", video=row["video"])
                    result = guard.run(root, private, original.worker_command(root, plan, row))
                    if result["state"] == "paused_without_automatic_resume":
                        raise InterruptedError("requires_source_tail_private_guard_paused_no_auto_resume")
                    require(result["state"] == "private_worker_exited" and result["returncode"] == 0,
                        "actual_private_worker_successful_exit")
                    status = whole.read(root / "job-progress.json")
                    require(status["stage"] == "batch_finished" and status.get("processed") == 1
                        and status.get("failures") == 0, "actual_single_source_success")
                    checked = original.validate_lesson(original.ReadOnlyArchive(root), row["video"], row["item"])
                    progress("publishing_verified_source_export", video=row["video"])
                    publication_receipt = publish_verified(root, client, row["video"])
                    receipt = {"video": row["video"], "export": checked, "publication": publication_receipt,
                        "private_guard": private.relative_to(root).as_posix(),
                        "reader_article_published": False, "source_deletion_allowed": False}
                    path = target / (row["video"] + ".json")
                    atomic_json(path, receipt)
                    backup = Path.home() / "Documents/CourseKnowledgeBackups/source-tails" / key / row["video"]
                    for source in [path, root / "job-progress.json", root / "run-plan.json",
                            *private.glob("*.json"), root / "lessons" / row["video"] / "evidence.json",
                            root / "lessons" / row["video"] / "graph.json"]:
                        whole.backup(source, backup / source.name)
                    completed.append(row["video"])
                    progress("new_source_export_and_http_publication_verified")
            progress("original_unattempted_source_tail_finished")
            return {"state": "original_unattempted_source_tail_finished", "new_sources": len(completed),
                "original_sources_reused": len(contract["original_completed_prefix"]), "reader_article_published": False}
        except BaseException as exc:
            failure = whole.failure_status(exc)
            atomic_json(target / ("failure-" + str(time.time_ns()) + ".json"), failure)
            progress(failure["state"], reason_code=failure["reason_code"])
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--batch", required=True)
    parser.add_argument("--reader-url", default="http://127.0.0.1:3003", type=publication.reader_url)
    parser.add_argument("command", choices=["prepare", "load", "run"])
    args = parser.parse_args()
    try:
        if args.command == "load":
            *_, contract = load(args.root, args.batch)
            result = {"state": "unused_source_tail_inputs_authenticated", "sources": len(contract["sources"]), "model_calls": 0}
        elif args.command == "prepare":
            result = prepare(args.root, args.batch)
        else:
            result = run(args.root, args.batch, args.reader_url)
        print(json.dumps(result))
    except BaseException as exc:
        print(json.dumps(whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
