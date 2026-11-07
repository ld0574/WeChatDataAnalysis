"""One native-image job after an exact, successfully finished coverage audit."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

from . import course_textbook_notebook_image_semantics as images
from . import course_textbook_whole_coverage_assignments as assignments
from .course_knowledge import atomic_json, schema_lease

whole = images.whole
POLICY = "exact-complete-coverage-to-native-notebook-images-once-v1"


def require(ok, code):
    if not ok:
        raise ValueError("requires_image_dispatch_" + code)


def checkpoint(root):
    whole.TextbookStore(root).checkpoint()
    require(not (root / "supervisor-stop.flag").exists(), "supervisor_stop_preserved")


def identity(value):
    return {"pid": value["pid"], "created": value["created"]}


def predecessor(root, plan):
    guard = whole.read(root / "wiki-revision-thermal-guard.json")
    status = whole.read(root / "wiki/whole-manuscript-coverage-jobs" / plan["coverage_job"] / "progress.json")
    require(identity(guard) == plan["coverage_guard"] and identity(status) == plan["coverage_worker"]
        and status["job"] == plan["coverage_job"], "exact_coverage_process_identities")
    require(guard["state"] in {"running", "worker_exited"} and status["state"] != "stopped",
        "coverage_not_failed_or_paused")
    if images.courseware.live(guard) or images.courseware.live(status):
        return False
    require(guard["state"] == "worker_exited" and guard.get("returncode") == 0 and status["stage"] == "complete"
        and status["state"] in {"automatic_whole_source_coverage_complete", "whole_manuscript_requires_coverage_or_classification_repair"}
        and status["completed_batches"] == status["total_batches"]
        and status["completed_clauses"] == status["total_clauses"], "coverage_successful_full_terminal")
    return True


def prepare(root, inventory_key, parent, tokenizer):
    root = Path(root).resolve()
    checkpoint(root)
    _, _, _, job, contract = images.prepared(root, inventory_key, tokenizer, read_only=True)
    require(not (job.root / "progress.json").exists(), "prepared_image_worker_not_started")
    coverage_contract = whole.read(root / "wiki/textbook-whole-authoring-jobs" / parent / assignments.coverage.CONTRACT)
    require(coverage_contract["parent_writer"] == parent, "actual_coverage_parent")
    coverage_job = coverage_contract["job"]
    coverage_folder = root / "wiki/whole-manuscript-coverage-jobs" / coverage_job
    require(whole.read(coverage_folder / "review-plan.json")["inventory_sha256"] == inventory_key, "same_literal_inventory")
    status, guard = whole.read(coverage_folder / "progress.json"), whole.read(root / "wiki-revision-thermal-guard.json")
    require(images.courseware.live(status) and images.courseware.live(guard) and guard["state"] == "running",
        "live_coverage_predecessor_at_preparation")
    plan = {"policy": POLICY, "job": job.key, "inventory": inventory_key, "coverage_parent": parent,
        "coverage_job": coverage_job, "coverage_worker": identity(status), "coverage_guard": identity(guard),
        "image_execution_sha256": images.digest(whole.read(contract)),
        "module_sha256": whole.sha(Path(__file__).read_bytes()),
        "image_module_sha256": whole.sha(Path(images.__file__).read_bytes()),
        "coverage_adapter_sha256": whole.sha(Path(assignments.__file__).read_bytes()),
        "tokenizer_sha256": whole.sha(Path(tokenizer).read_bytes()), "automatic_retry_allowed": False,
        "automatic_thermal_resume_allowed": False, "reader_publication_allowed": False}
    predecessor(root, plan)
    folder = root / "wiki/notebook-image-dispatch" / job.key
    folder.mkdir(parents=True, exist_ok=True)
    images.retain(folder / "plan.json", plan)
    return {"state": "exact_coverage_to_native_images_prepared", "job": job.key, "coverage_job": coverage_job,
        "model_calls": 0, "reader_publication_allowed": False}


def worker(root, key, tokenizer, *, sleep=time.sleep):
    from .course_archive_gpu import checked_sample
    from .course_gpu_guard import run as guarded

    root = Path(root).resolve()
    folder = root / "wiki/notebook-image-dispatch" / key
    plan = whole.read(folder / "plan.json")
    require(plan["policy"] == POLICY and plan["job"] == key
        and plan["module_sha256"] == whole.sha(Path(__file__).read_bytes())
        and plan["image_module_sha256"] == whole.sha(Path(images.__file__).read_bytes())
        and plan["coverage_adapter_sha256"] == whole.sha(Path(assignments.__file__).read_bytes())
        and plan["tokenizer_sha256"] == whole.sha(Path(tokenizer).read_bytes()), "unchanged_prepared_dispatch")
    _, _, _, job, contract = images.prepared(root, plan["inventory"], tokenizer, read_only=True)
    require(job.key == key and images.digest(whole.read(contract)) == plan["image_execution_sha256"], "same_original_image_budget")
    owner = whole.psutil.Process()

    def progress(state, **extra):
        atomic_json(folder / "progress.json", {"pid": owner.pid, "created": owner.create_time(), "job": key,
            "state": state, "updated": time.time(), "automatic_retry_allowed": False,
            "automatic_thermal_resume_allowed": False, "reader_publication_allowed": False, **extra})

    with schema_lease(root / "wiki/notebook-image-dispatch.lock", timeout=0):
        checkpoint(root)
        require(not (folder / "started.json").exists(), "dispatch_once_without_retry")
        atomic_json(folder / "started.json", {"pid": owner.pid, "created": owner.create_time(), "updated": time.time()})
        try:
            while True:
                checkpoint(root)
                if predecessor(root, plan):
                    break
                progress("waiting_for_exact_full_coverage_predecessor")
                sleep(10)
            progress("reconstructing_full_coverage_without_inference")
            checked = assignments.reconstruct(root, plan["coverage_parent"], tokenizer, "section-0007")
            require(checked["summary"]["state"] == "complete_whole_coverage_reconstructed_without_inference"
                and checked["summary"]["job"] == plan["coverage_job"] and checked["summary"]["model_calls"] == 0,
                "actual_complete_coverage_reconstruction")
            proof = assignments.checked.save(root, checked)
            images.retain(folder / "coverage-reconstruction.json", {**checked["summary"], "proof": str(proof),
                "proof_sha256": images.digest(checked["proof"])})
            checkpoint(root)
            require(predecessor(root, plan), "unchanged_finished_coverage_guard")
            whole.backup(root / "wiki-revision-thermal-guard.json", folder / "predecessor-guard.json")
            progress("running_one_guarded_native_image_job")
            guarded(root, [sys.executable, "-m", images.__spec__.name, "--root", str(root), "--inventory", plan["inventory"],
                "--tokenizer", str(Path(tokenizer).resolve()), "worker"], maximum_temperature=70, maximum_power=200,
                guard_name="wiki-revision-thermal-guard", sampler=lambda: checked_sample(root))
            terminal = whole.read(root / "wiki-revision-thermal-guard.json")
            require(terminal["state"] == "worker_exited" and terminal.get("returncode") == 0, "native_image_worker_successful_terminal")
            progress("reconstructing_native_images_without_inference")
            result = images.run(root, plan["inventory"], tokenizer, read_only=True)
            require(result["state"] == "native_notebook_image_checks_reconstructed_without_inference"
                and result["job"] == key and result["model_calls"] == 0, "native_image_results_reconstructed")
            images.retain(folder / "result.json", result)
            backup = Path.home() / "Documents/CourseKnowledgeBackups/notebook-image-semantics" / key / "dispatch"
            for source in [*folder.glob("*.json"), proof, Path(__file__)]:
                if source.name != "progress.json":
                    whole.backup(source, backup / source.name)
            progress("finite_native_image_checks_finished_not_published")
            return {"state": "finite_native_image_checks_finished_not_published", "job": key,
                "different_model_pixel_review": False, "reader_publication_allowed": False}
        except Exception as exc:
            failure = whole.failure_status(exc)
            path = folder / "failures" / (str(time.time_ns()) + ".json")
            path.parent.mkdir(exist_ok=True)
            atomic_json(path, failure)
            progress(failure["state"], **{k: v for k, v in failure.items() if k != "state"})
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--tokenizer", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    prepared = sub.add_parser("prepare")
    prepared.add_argument("--inventory", required=True)
    prepared.add_argument("--coverage-parent", required=True)
    run = sub.add_parser("worker")
    run.add_argument("--job", required=True)
    args = parser.parse_args()
    try:
        result = prepare(args.root, args.inventory, args.coverage_parent, args.tokenizer) if args.command == "prepare" else (
            worker(args.root, args.job, args.tokenizer))
        print(json.dumps(result))
    except Exception as exc:
        print(json.dumps(whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
