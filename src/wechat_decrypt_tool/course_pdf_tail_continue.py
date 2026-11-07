"""Run only the unattempted tail of a stopped, retained-renderer PDF queue.

Old controllers, failed attempts and partial-batch bindings remain unchanged.
The partial predecessor must finish its existing independently verified resume.
Future batches bind the retained executable's actual location before starting.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time

import psutil

from . import course_pdf_queue as queue
from . import course_pdf_queue_continue as continuation
from . import course_pdf_renderer_resume as renderer
from .course_archive_gpu import checked_sample
from .course_knowledge import atomic_json, schema_lease
from .course_pdf_plan import frozen_pdf_scope

POLICY = "unattempted-pdf-tail-identical-retained-renderer-v1"


def require(ok, code):
    if not ok:
        raise ValueError("pdf_tail_" + code)


def preserved_terminal(root, prefix, plan, expected):
    """Verify a finished predecessor against its independent complete backup."""
    binding = queue.read(root / (prefix + "-binding.json"))
    proof = queue.read(root / (prefix + "-validation.json"))
    scope = frozen_pdf_scope(queue.read(plan))
    require(proof["state"] == "finite_batch_verified_and_backed_up"
        and proof["source_deletion_allowed"] is False
        and proof["selected_page_scope"] is True
        and proof["manifest_sha256"] == expected["manifest_sha256"]
        and proof["page_occurrences"] == expected["page_occurrences"] == len(scope)
        and proof["plan_sha256"] == binding["plan_sha256"] == queue.sha(plan.read_bytes())
        and proof["full_native_page_counts"] == binding["full_native_page_counts"], "old_terminal_scope_changed")
    queue.verify_backup(proof)
    backup = Path(proof["backup"])
    manifest = queue.read(backup / "manifest.json")
    require(all(manifest.get(k) == proof.get(k) for k in ("plan_sha256", "report_sha256",
        "independent_validation_sha256", "full_native_page_counts", "unique_raw_pages", "selected_page_scope")),
        "old_terminal_manifest_changed")
    for name in (prefix + "-binding.json", prefix + "-plan.json"):
        require(any(row["path"] == name for row in manifest["files"])
            and (backup / name).read_bytes() == (root / name).read_bytes(), "old_terminal_input_changed")
    return proof


def retained_controls(root, controls, manual_resume):
    """Resolve only the old stop marker explicitly retained on manual resume."""
    resolved, relocations = {}, []
    for path, digest in controls.items():
        if path.exists():
            require(queue.sha(path.read_bytes()) == digest, "old_control_changed")
            resolved[path] = digest
            continue
        require(path == root / "supervisor-stop.flag" and manual_resume is not None,
            "missing_old_control_requires_manual_receipt")
        folder = Path(manual_resume).resolve()
        require(folder.is_relative_to(root / "wiki"), "manual_receipt_outside_archive")
        receipt = folder / "receipt.json"
        data = queue.read(receipt)
        require(data.get("manual_user_confirmation") is True
            and data.get("automatic_thermal_resume_allowed") is False
            and data.get("old_budgets_reset") is False, "explicit_manual_resume_required")
        row = next((r for r in data["original_markers"] if r["name"] == path.name), {})
        require(row.get("sha256") == digest and row.get("original_retained") is True,
            "original_marker_identity_changed")
        saved = folder / (path.name + "." + digest)
        backup = Path.home() / "Documents/CourseKnowledgeBackups" / folder.name
        for local in (receipt, saved):
            other = backup / local.name
            require(other.read_bytes() == local.read_bytes(), "manual_resume_backup_changed")
            resolved[local] = queue.sha(local.read_bytes())
            resolved[other] = resolved[local]
        require(saved.stat().st_size == row["bytes"] and queue.sha(saved.read_bytes()) == digest,
            "retained_marker_changed")
        relocations.append({"original_path": str(path), "retained_path": str(saved), "sha256": digest,
            "manual_receipt": str(receipt), "manual_receipt_sha256": queue.sha(receipt.read_bytes())})
    return resolved, relocations


def bind(root, folder, previous_first, last, after_prefix, pending, manual_resume=None):
    family, first = after_prefix[:-4], int(after_prefix[-4:]) + 1
    require(1 < previous_first < first <= last, "invalid_tail_range")
    launch_path = root / f"pdf-selected-queue-continuation-{previous_first:04}-{last:04}-launched.json"
    status_path = launch_path.with_name(launch_path.name.replace("-launched", "-progress"))
    launch, status = queue.read(launch_path), queue.read(status_path)
    require(not queue.alive(launch) and not queue.alive(status), "old_controller_still_live")
    require(status["state"] == "paused" and status["number"] == first - 1
        and status["prefix"] == after_prefix
        and all(status[k] == launch[k] for k in ("pid", "created", "first", "last", "after_prefix"))
        and launch["first"] == previous_first and launch["last"] == last,
        "original_pause_scope_changed")
    controls = {Path(p): digest for p, digest in launch["bound_control_sha256"].items()}
    controls, relocations = retained_controls(root, controls, manual_resume)
    folder = queue.inside(root, Path(folder) / "validation.json").parent
    require(folder / "validation.json" in controls, "original_queue_required")
    plans = dict(queue.queue_plans(folder))
    require(all(n in plans and plans[n] in controls for n in range(previous_first, last + 1)), "original_plan_partition_required")
    completed = status["completed_batches"]
    require([row["number"] for row in completed] == list(range(previous_first, first - 1)), "old_completed_partition_changed")
    for row in completed:
        prefix = family + f"{row['number']:04}"
        require(row["prefix"] == prefix, "old_completed_prefix_changed")
        preserved_terminal(root, prefix, plans[row["number"]], row)
        for suffix in ("-binding.json", "-validation.json"):
            p = root / (prefix + suffix)
            controls[p] = queue.sha(p.read_bytes())
    binding_path, resolved = renderer.verified_binding(root, after_prefix)
    original = queue.read(binding_path)
    require(original["plan_sha256"] == queue.sha(plans[first - 1].read_bytes()), "partial_plan_changed")
    partial = queue.read(root / (after_prefix + "-resume-status.json"))
    require(partial["state"] in {"continuing_validated_partial_batch", "finite_batch_verified_and_backed_up"}, "partial_not_healthy")
    require(pending and {k: partial[k] for k in ("pid", "created")} in pending, "exact_partial_worker_required")
    for identity in pending:
        require(type(identity.get("pid")) is int and identity["pid"] > 0
            and type(identity.get("created")) in (int, float) and identity["created"] > 0, "invalid_predecessor_identity")
        if queue.alive(identity):
            command = psutil.Process(identity["pid"]).cmdline()
            require("wechat_decrypt_tool.course_pdf_renderer_resume" in command
                and "--prefix" in command and command[command.index("--prefix") + 1] == after_prefix,
                "predecessor_role_changed")
    require(any(queue.alive(p) for p in pending)
        or partial["state"] == "finite_batch_verified_and_backed_up", "partial_worker_exited_without_result")
    store = queue.ReadOnlyArchive(root)
    for number in range(first, last + 1):
        require(not list(root.glob(f"{family}{number:04}*")), "future_batch_already_attempted")
        queue.require_unattempted(store, frozen_pdf_scope(queue.read(plans[number])))
    relocation = renderer.relocation_path(root, after_prefix)
    receipt = queue.read(relocation)
    for p in (launch_path, status_path, binding_path, relocation, Path(receipt["retained_resolver_source"]),
        Path(receipt["environment_receipt_path"]), Path(__file__), Path(renderer.__file__), Path(continuation.__file__)):
        controls[p] = queue.sha(p.read_bytes())
    stop = root / "supervisor-stop.flag"
    if stop.exists():
        controls[stop] = queue.sha(stop.read_bytes())
    return plans, {**original, "renderer": resolved["renderer"]}, controls, {**launch, "control_relocations": relocations}


def run(root, folder, previous_first, last, after_prefix, pending, manual_resume=None, *, sleep=time.sleep):
    root = Path(root).resolve()
    renderer.relocation_path(root, after_prefix)
    first, family = int(after_prefix[-4:]) + 1, after_prefix[:-4]
    stem = f"pdf-retained-tail-{first:04}-{last:04}"
    launch_path, status_path = root / (stem + "-launched.json"), root / (stem + "-progress.json")
    identity = {"pid": os.getpid(), "created": psutil.Process().create_time()}
    state = {"policy": POLICY, "first": first, "last": last, "after_prefix": after_prefix,
        "completed_batches": [], "automatic_retry_allowed": False, "automatic_thermal_resume_allowed": False,
        "source_deletion_allowed": False, "maximum_power_w": 200, "maximum_temperature_c": 70,
        "minimum_free_gib_each_volume": 32}

    def progress(phase, **extra):
        state.update(extra)
        atomic_json(status_path, {**identity, **state, "state": phase, "updated": time.time()})

    with schema_lease(root / "pdf-selected-queue.lock", timeout=0):
        require(not launch_path.exists() and not status_path.exists(), "tail_already_launched")
        queue.checkpoint(root)
        plans, template, controls, old = bind(root, folder, previous_first, last, after_prefix, pending, manual_resume)
        launch = {**identity, **state, "pending_processes": pending,
            "inputs_base": old["inputs_base"], "results_base": old["results_base"],
            "control_relocations": old.get("control_relocations", []),
            "retained_renderer": template["renderer"], "renderer_sha256": template["renderer_sha256"],
            "bound_control_sha256": {str(p): digest for p, digest in controls.items()}}
        with launch_path.open("x", encoding="utf-8") as stream:
            json.dump(launch, stream, indent=2)
        backup = Path.home() / "Documents/CourseKnowledgeBackups/pdf-tail-controls" / stem
        for p in (launch_path, Path(__file__)):
            renderer_source = backup / p.name
            renderer_source.parent.mkdir(parents=True, exist_ok=True)
            require(not renderer_source.exists() or renderer_source.read_bytes() == p.read_bytes(), "immutable_tail_backup")
            renderer_source.write_bytes(p.read_bytes())
            require(renderer_source.read_bytes() == p.read_bytes(), "tail_backup_changed")
        controls[launch_path] = queue.sha(launch_path.read_bytes())

        def check():
            queue.checkpoint(root)
            for path, digest in controls.items():
                require(queue.sha(path.read_bytes()) == digest, "bound_control_changed")

        try:
            while any(queue.alive(p) for p in pending):
                check()
                progress("waiting_for_verified_partial_batch")
                sleep(20)
            check()
            with renderer.resolve_renderer(root, after_prefix):
                continuation.verify_terminal(root, after_prefix, plans[first - 1])
            predecessor = after_prefix
            for number in range(first, last + 1):
                check()
                prefix = family + f"{number:04}"
                progress("binding_next_unattempted_batch", number=number, prefix=prefix)
                queue.prepare(root, plans[number], prefix, template, predecessor + "-validation.json",
                    old["inputs_base"], old["results_base"])
                progress("running_one_guarded_finite_batch", number=number, prefix=prefix)
                queue.guarded(root, [sys.executable, "-m", "wechat_decrypt_tool.course_pdf_queue",
                    "--root", str(root), "worker", "--prefix", prefix], maximum_temperature=70,
                    maximum_power=200, guard_name="extraction-thermal-guard", sampler=lambda: checked_sample(root))
                check()
                proof = continuation.verify_terminal(root, prefix, plans[number])
                receipt_path = root / (prefix + "-validation.json")
                controls[receipt_path] = queue.sha(receipt_path.read_bytes())
                state["completed_batches"].append({"number": number, "prefix": prefix,
                    "page_occurrences": proof["page_occurrences"], "manifest_sha256": proof["manifest_sha256"]})
                progress("finite_batch_terminal_backup_verified")
                predecessor = prefix
            progress("requested_finite_queue_finished")
        except BaseException as exc:
            progress("paused" if isinstance(exc, InterruptedError) or (root / "pause.flag").exists() else "failed",
                error_type=type(exc).__name__, error_sha256=queue.sha(str(exc).encode()))
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--previous-first", type=int, required=True)
    parser.add_argument("--last", type=int, required=True)
    parser.add_argument("--after-prefix", required=True)
    parser.add_argument("--predecessors", type=Path, required=True)
    parser.add_argument("--manual-resume", type=Path)
    args = parser.parse_args()
    try:
        run(args.root, args.queue, args.previous_first, args.last, args.after_prefix, queue.read(args.predecessors), args.manual_resume)
    except Exception as exc:
        print(json.dumps({"state": "stopped", "error_type": type(exc).__name__, "error_sha256": queue.sha(str(exc).encode())}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
