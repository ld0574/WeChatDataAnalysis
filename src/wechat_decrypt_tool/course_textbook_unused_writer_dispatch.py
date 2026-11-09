"""Dispatch an original unused writer after a proven pre-inference read collision.

Original controller/guard failures remain immutable. Only the original writer
roles run, with bounded ledger-reader waiting and unchanged prompts and schemas.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import time

from . import course_textbook_corpus_unused_tail as original
from . import course_textbook_read_lease as reads
from .course_knowledge import atomic_json, schema_lease

whole, guard, writing, fresh = original.whole, original.guard, original.writing, original.fresh
digest = original.digest
POLICY = "original-unspent-second-writer-after-read-lock-collision-v1"
DIRECTORY = "unused-writer-dispatch"
SUCCESS = "original_second_writer_completed_without_repeating_ledger"


def require(ok, code):
    if not ok:
        raise ValueError("requires_unused_writer_dispatch_" + code)


def bindings():
    return {"entrypoints": {Path(m.__file__).stem: whole.sha(Path(m.__file__).read_bytes())
        for m in (sys.modules[__name__], reads)}, "original": original.bindings()}


def folder_for(root, key):
    require(isinstance(key, str) and re.fullmatch(r"[a-f0-9]{32}", key), "writer_id")
    return Path(root).resolve() / "wiki" / DIRECTORY / key


def unspent(folder):
    require(not (folder / "progress.json").exists() and not list((folder / "attempts").glob("*.json"))
        and not list(folder.glob("response-*.json")) and not list(folder.glob("section-*.json"))
        and not list(folder.glob("concepts-*.json")) and not (folder / "private-working-inputs.json").exists(),
        "all_original_writer_roles_still_unused")


def prepare(root, batch):
    root = Path(root).resolve()
    guard.checkpoint(root)
    with schema_lease(root / "wiki/full-course-batch-prepare.lock", timeout=0), reads.bounded_ledger_reads(writing):
        _, target, _, _, tokenizer, job, contract, extra, tail = original.writer_bound(root, batch)
        status = whole.read(target / "progress.json")
        failed = target / "item-01-authoring-guard"
        old_guard = whole.read(failed / "guard-progress.json")
        raw = (failed / "worker.stdout.log").read_bytes()
        failure = json.loads(raw)
        require(status["state"] == "stopped" and not writing.identity_live(status)
            and old_guard["state"] == "worker_exited" and old_guard["returncode"] == 1
            and not writing.identity_live(old_guard), "actual_failed_original_controller_and_guard_exit")
        require(failure["error_type"] == "RuntimeError"
            and failure["error_sha256"] == whole.sha(b"Another course archive operation still holds ledger-worker.lock"),
            "exact_preinference_ledger_read_collision")
        unspent(job.root)
        folder = folder_for(root, job.key)
        require(not folder.exists(), "one_unspent_writer_continuation")
        paths = [target / "progress.json", failed / "guard-progress.json", failed / "guard-failure.json",
            failed / "worker.stdout.log", *target.glob("failure-*.json"), target / "execution.json",
            target / "first-course-proof.json", target / "first-course-result.json", job.root / "manifest.json",
            job.root / fresh.CONTRACT, job.root / original.WRITER_CONTRACT]
        execution = {"policy": POLICY, "writer": job.key, "batch": batch, "code": bindings(),
            "original_fresh_execution_sha256": digest(contract), "original_writer_execution_sha256": digest(extra),
            "original_tail_execution_sha256": digest(tail), "tokenizer": str(tokenizer.relative_to(root)),
            "files": {p.relative_to(root).as_posix(): whole.sha(p.read_bytes()) for p in paths},
            "initial_model_attempts": 0, "initial_wire_responses": 0, "ledger_reader_wait_seconds": 300,
            "original_budgets_reset": False, "prompts_or_schemas_changed": False,
            "automatic_retry_allowed": False, "automatic_thermal_resume_allowed": False,
            "reader_publication_allowed": False, "source_deletion_allowed": False}
        fresh.retain(folder / "execution.json", execution)
        backup = Path.home() / "Documents/CourseKnowledgeBackups/unspent-writer-dispatch" / job.key
        for path in [folder / "execution.json", Path(__file__), Path(reads.__file__), *paths]:
            name = path.relative_to(root) if path.is_relative_to(root) else Path("support") / path.name
            whole.backup(path, backup / name)
        return {"state": "original_unspent_writer_prepared_after_exact_read_collision", "writer": job.key,
            "batch": batch, "model_calls": 0, "ledger_groups_reexecuted": 0, "reader_publication_allowed": False}


def load(root, key):
    root = Path(root).resolve()
    folder = folder_for(root, key)
    contract = whole.read(folder / "execution.json")
    require(contract["policy"] == POLICY and contract["writer"] == key and contract["code"] == bindings(), "frozen_same_original_writer")
    for name, expected in contract["files"].items():
        path = (root / name).resolve()
        require(path.is_relative_to(root) and path.is_file() and whole.sha(path.read_bytes()) == expected,
            "actual_old_failure_and_writer_contracts_unchanged")
    return root, folder, contract


def worker(root, key):
    root, folder, contract = load(root, key)
    guard.checkpoint(root)
    unspent(root / "wiki/textbook-whole-authoring-jobs" / key)
    require(not (folder / "worker-started.json").exists(), "original_unspent_worker_once")
    fresh.retain(folder / "worker-started.json", {"writer": key, "started": time.time()})
    with reads.bounded_ledger_reads(writing, seconds=contract["ledger_reader_wait_seconds"]):
        return original.writer_worker(root, contract["batch"])


def verify(root, key):
    root, folder, contract = load(root, key)
    state = whole.read(folder / "guard-progress.json")
    require(state["state"] == "worker_exited" and state["returncode"] == 0 and not writing.identity_live(state),
        "real_successful_guard_exit")
    with reads.bounded_ledger_reads(writing):
        result = original.verify_writer(root, contract["batch"])
    require(result["summary"]["job"] == key and result["private"] is not None
        and result["summary"]["verified_units"] == result["summary"]["total_units"], "actual_complete_original_writer")
    result["proof"].update(unused_writer_dispatch_sha256=digest(contract), ledger_groups_reexecuted=0)
    return result


def run(root, key):
    root, folder, _ = load(root, key)
    with schema_lease(root / "wiki/full-course-batch-worker.lock", timeout=0):
        unspent(root / "wiki/textbook-whole-authoring-jobs" / key)
        return guard.run(root, folder, [sys.executable, "-m", __spec__.name, "--root", str(root), "--writer", key, "worker"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--batch")
    parser.add_argument("--writer")
    parser.add_argument("command", choices=["prepare", "load", "run", "worker", "verify"])
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = prepare(args.root, args.batch)
        elif args.command == "load":
            _, _, contract = load(args.root, args.writer)
            result = {"state": "original_unspent_writer_contract_authenticated", "writer": contract["writer"], "model_calls": 0}
        elif args.command == "verify":
            actual = verify(args.root, args.writer)
            path = original.original.mapped.snapshot.save(args.root, actual)
            result = {**actual["summary"], "state": SUCCESS, "snapshot": str(path), "ledger_groups_reexecuted": 0}
        else:
            result = globals()[args.command](args.root, args.writer)
        print(json.dumps(result))
    except BaseException as exc:
        status = whole.failure_status(exc)
        if args.writer:
            folder = folder_for(args.root, args.writer)
            if folder.exists():
                atomic_json(folder / ("dispatch-failure-" + str(time.time_ns()) + ".json"), status)
        print(json.dumps(status))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
