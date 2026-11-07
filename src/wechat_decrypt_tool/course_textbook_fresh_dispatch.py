"""Start one prepared fresh article after an exact finite courseware worker.

This is a finite local dependency, not a retry supervisor. Pauses, failures and
identity changes stop the dispatch. The predecessor is reconstructed without
inference before its guard slot can be used by the next article.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

from . import course_textbook_fresh_authoring as fresh
from . import course_textbook_whole_courseware as courseware
from .course_knowledge import atomic_json, schema_lease

whole = fresh.writing.whole
POLICY = "exact-successful-courseware-to-fresh-authoring-once-v1"


def require(ok, code):
    if not ok:
        raise ValueError("requires_fresh_dispatch_" + code)


def checkpoint(root):
    fresh.writing.TextbookStore(root).checkpoint()
    require(not (root / "supervisor-stop.flag").exists(), "supervisor_stop_preserved")


def identity(row):
    return {"pid": row["pid"], "created": row["created"]}


def predecessor(root, plan):
    guard = whole.read(root / "review-experiment-thermal-guard.json")
    status = whole.read(root / "wiki/whole-courseware-relation-jobs" / plan["predecessor_job"] / "progress.json")
    require(identity(guard) == plan["predecessor_guard"] and identity(status) == plan["predecessor_worker"],
        "exact_predecessor_identities")
    require(status["job"] == plan["predecessor_job"] and status["inventory"] == plan["predecessor_inventory"],
        "exact_predecessor_job_and_inventory")
    require(guard["state"] in {"running", "worker_exited"} and status["state"] != "stopped",
        "predecessor_not_failed_or_paused")
    if fresh.writing.identity_live(guard) or fresh.writing.identity_live(status):
        return False
    require(guard["state"] == "worker_exited" and guard.get("returncode") == 0
        and status["state"] == "finite_courseware_text_relations_checked" and status["stage"] == "complete",
        "predecessor_successful_terminal")
    return True


def prepare(root, inventory_key, ledger_job, tokenizer):
    root, _, _, job, contract = fresh.bound(root, inventory_key, ledger_job, tokenizer)
    checkpoint(root)
    require(not (job.root / "progress.json").exists(), "fresh_writer_not_started")
    status = whole.read(root / "wiki/whole-courseware-relation-current.json")
    guard = whole.read(root / "review-experiment-thermal-guard.json")
    require(fresh.writing.identity_live(status) and fresh.writing.identity_live(guard)
        and guard["state"] == "running", "actual_live_predecessor_at_preparation")
    plan = {"policy": POLICY, "job": job.key, "inventory": inventory_key, "ledger_job": ledger_job,
        "fresh_execution_sha256": fresh.digest(contract), "predecessor_job": status["job"],
        "predecessor_inventory": status["inventory"], "predecessor_guard": identity(guard),
        "predecessor_worker": identity(status), "module_sha256": whole.sha(Path(__file__).read_bytes()),
        "fresh_module_sha256": whole.sha(Path(fresh.__file__).read_bytes()),
        "predecessor_module_sha256": whole.sha(Path(courseware.__file__).read_bytes()),
        "tokenizer_sha256": whole.sha(Path(tokenizer).read_bytes()), "automatic_retry_allowed": False,
        "automatic_thermal_resume_allowed": False, "reader_publication_allowed": False}
    predecessor(root, plan)
    folder = root / "wiki/fresh-authoring-dispatch" / job.key
    folder.mkdir(parents=True, exist_ok=True)
    fresh.retain(folder / "plan.json", plan)
    return {"state": "exact_predecessor_dispatch_prepared", "job": job.key,
        "predecessor_job": status["job"], "model_calls": 0, "reader_publication_allowed": False}


def worker(root, key, tokenizer, *, sleep=time.sleep):
    root = Path(root).resolve()
    folder = root / "wiki/fresh-authoring-dispatch" / key
    plan = whole.read(folder / "plan.json")
    checkpoint(root)
    require(plan["policy"] == POLICY and plan["job"] == key
        and plan["module_sha256"] == whole.sha(Path(__file__).read_bytes())
        and plan["fresh_module_sha256"] == whole.sha(Path(fresh.__file__).read_bytes())
        and plan["predecessor_module_sha256"] == whole.sha(Path(courseware.__file__).read_bytes())
        and plan["tokenizer_sha256"] == whole.sha(Path(tokenizer).read_bytes()), "unchanged_prepared_execution")
    _, _, _, job, contract = fresh.bound(root, plan["inventory"], plan["ledger_job"], tokenizer)
    require(fresh.digest(contract) == plan["fresh_execution_sha256"] and job.key == key, "same_fresh_writer")
    owner = fresh.writing.psutil.Process()
    own = {"pid": owner.pid, "created": owner.create_time()}

    def progress(state, **extra):
        atomic_json(folder / "progress.json", {**own, "job": key, "state": state, "updated": time.time(),
            "automatic_retry_allowed": False, "automatic_thermal_resume_allowed": False,
            "reader_publication_allowed": False, **extra})

    with schema_lease(root / "wiki/fresh-authoring-next-worker.lock", timeout=0):
        require(not (folder / "started.json").exists(), "dispatch_once_no_automatic_retry")
        atomic_json(folder / "started.json", {**own, "updated": time.time()})
        try:
            while True:
                checkpoint(root)
                if predecessor(root, plan):
                    break
                progress("waiting_for_exact_courseware_predecessor")
                sleep(10)
            progress("reconstructing_actual_predecessor_without_inference")
            checked = courseware.run(root, plan["predecessor_inventory"], tokenizer, read_only=True)
            require(checked["state"] == "courseware_text_relations_reconstructed_without_inference"
                and checked["job"] == plan["predecessor_job"] and checked["model_calls"] == 0,
                "actual_predecessor_reconstructed")
            fresh.retain(folder / "predecessor-reconstruction.json", checked)
            checkpoint(root)
            require(predecessor(root, plan), "predecessor_still_owns_finished_guard_slot")
            progress("starting_prepared_fresh_authoring")
            fresh.guard(root, plan["inventory"], plan["ledger_job"], tokenizer)
            guard = whole.read(root / "review-experiment-thermal-guard.json")
            require(guard["state"] == "worker_exited" and guard.get("returncode") == 0,
                "fresh_writer_guard_successful_terminal")
            progress("reconstructing_fresh_manuscript_without_inference")
            result = fresh.reconstruct(root, plan["inventory"], plan["ledger_job"], tokenizer)
            proof_path = fresh.snapshot.save(root, result)
            fresh.retain(folder / "result.json", {**result["summary"], "snapshot": str(proof_path),
                "snapshot_sha256": fresh.digest(result["proof"]), "goal_complete": False})
            backup = Path.home() / "Documents/CourseKnowledgeBackups/fresh-whole-authoring" / key / "dispatch"
            for source in [*folder.glob("*.json"), proof_path, Path(__file__)]:
                if source.name != "progress.json":
                    whole.backup(source, backup / source.name)
            progress("finite_fresh_manuscript_finished_not_published")
            return {"state": "finite_fresh_manuscript_finished_not_published", "job": key,
                "reader_publication_allowed": False, "goal_complete": False}
        except Exception as exc:
            failure = whole.failure_status(exc)
            destination = folder / "failures"
            destination.mkdir(exist_ok=True)
            atomic_json(destination / (str(time.time_ns()) + ".json"), failure)
            progress(failure["state"], **{k: v for k, v in failure.items() if k != "state"})
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--tokenizer", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    prepared = sub.add_parser("prepare")
    prepared.add_argument("--inventory", required=True)
    prepared.add_argument("--ledger-job", required=True)
    run = sub.add_parser("worker")
    run.add_argument("--job", required=True)
    args = parser.parse_args()
    try:
        result = prepare(args.root, args.inventory, args.ledger_job, args.tokenizer) if args.command == "prepare" else (
            worker(args.root, args.job, args.tokenizer))
        print(json.dumps(result))
    except Exception as exc:
        print(json.dumps(whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
