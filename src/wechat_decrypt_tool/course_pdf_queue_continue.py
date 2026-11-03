"""Continue a frozen PDF queue after an independently verified partial batch."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import psutil

from . import course_pdf_queue as queue
from .course_archive_gpu import checked_sample
from .course_knowledge import atomic_json, schema_lease
from .course_pdf_plan import frozen_pdf_scope


def require(ok, reason):
    if not ok:
        raise ValueError("pdf_queue_continue_" + reason)


def verify_terminal(root, prefix, plan):
    """A page counter is insufficient: bind the full independent backup to its plan."""
    binding = queue.validate_inputs(root, prefix)
    proof = queue.read(queue.inside(root, prefix + "-validation.json"))
    scope = frozen_pdf_scope(queue.read(plan))
    require(
        proof.get("state") == "finite_batch_verified_and_backed_up"
        and proof.get("source_deletion_allowed") is False
        and proof.get("selected_page_scope") is True
        and proof["page_occurrences"] == binding["page_occurrences"] == len(scope)
        and proof["plan_sha256"]
        == binding["plan_sha256"]
        == queue.sha(plan.read_bytes())
        and proof["full_native_page_counts"] == binding["full_native_page_counts"],
        "predecessor_terminal_scope_differs",
    )
    queue.verify_backup(proof)
    manifest = queue.read(Path(proof["backup"]) / "manifest.json")
    require(
        all(
            manifest.get(key) == proof.get(key)
            for key in (
                "plan_sha256",
                "report_sha256",
                "independent_validation_sha256",
                "full_native_page_counts",
                "unique_raw_pages",
                "selected_page_scope",
            )
        ),
        "terminal_manifest_binding_differs",
    )
    return proof


def bind(root, folder, template_path, after_prefix, first, last, pending):
    """Freeze original controls without changing their stopped controller history."""
    require(
        type(first) is int
        and type(last) is int
        and 1 < first <= last
        and re.fullmatch(r"pdf-queue-[0-9]{8}-[0-9]{4}", after_prefix)
        and int(after_prefix[-4:]) == first - 1,
        "invalid_continuation_range",
    )
    old_launch = queue.inside(root, "pdf-selected-queue-launched.json")
    old_status = queue.inside(root, "pdf-selected-queue-progress.json")
    original, status = queue.read(old_launch), queue.read(old_status)
    require(not queue.alive(original), "original_controller_still_running")
    require(
        status["state"] == "paused"
        and status["number"] == first - 1
        and status["prefix"] == after_prefix
        and status["pid"] == original["pid"]
        and status["created"] == original["created"]
        and original["first"] < first <= last <= original["last"],
        "original_pause_scope_differs",
    )
    plans = dict(queue.queue_plans(folder))
    numbers = range(original["first"], original["last"] + 1)
    require(all(number in plans for number in numbers), "original_range_missing")
    template_path = queue.inside(root, template_path)
    expected = {
        Path(folder).resolve() / "validation.json",
        template_path,
        *(plans[n] for n in numbers),
        *(Path(queue.__file__).with_name(n) for n in queue.RUNTIME),
    }
    controls = {
        Path(p): digest for p, digest in original["bound_control_sha256"].items()
    }
    require(set(controls) == expected, "original_control_partition_differs")
    for path, digest in controls.items():
        require(queue.sha(path.read_bytes()) == digest, "original_control_changed")
    completed = status["completed_batches"]
    require(
        [row["number"] for row in completed]
        == list(range(original["first"], first - 1)),
        "completed_predecessor_partition_differs",
    )
    family = after_prefix[:-4]
    for row in completed:
        prefix = family + f"{row['number']:04}"
        require(row["prefix"] == prefix, "completed_predecessor_prefix_differs")
        proof = verify_terminal(root, prefix, plans[row["number"]])
        require(
            proof["manifest_sha256"] == row["manifest_sha256"]
            and proof["page_occurrences"] == row["page_occurrences"],
            "original_completed_receipt_differs",
        )
        for suffix in ("-validation.json", "-binding.json", "-prepared.json"):
            path = queue.inside(root, prefix + suffix)
            controls[path] = queue.sha(path.read_bytes())
    require(pending, "exact_predecessor_identities_required")
    for identity in pending:
        require(
            type(identity.get("pid")) is int
            and identity["pid"] > 0
            and type(identity.get("created")) in (int, float)
            and identity["created"] > 0,
            "invalid_predecessor_identity",
        )
        if queue.alive(identity):
            command = psutil.Process(identity["pid"]).cmdline()
            require(
                "wechat_decrypt_tool.course_pdf_queue_resume" in command
                and "--prefix" in command
                and command[command.index("--prefix") + 1] == after_prefix,
                "predecessor_process_role_differs",
            )
    binding = queue.validate_inputs(root, after_prefix)
    require(
        binding["plan_sha256"] == queue.sha(plans[first - 1].read_bytes()),
        "partial_batch_plan_differs",
    )
    resume_status = queue.read(root / (after_prefix + "-resume-status.json"))
    require(
        resume_status["state"]
        in {
            "continuing_validated_partial_batch",
            "finite_batch_verified_and_backed_up",
        },
        "partial_batch_not_healthy_or_verified",
    )
    if queue.alive(resume_status):
        require(
            any(
                identity == {k: resume_status[k] for k in ("pid", "created")}
                for identity in pending
            ),
            "partial_worker_identity_missing",
        )
    store = queue.ReadOnlyArchive(root)
    for number in range(first, last + 1):
        require(
            not list(root.glob(f"{family}{number:04}*")),
            "future_batch_already_attempted_or_prepared",
        )
        queue.require_unattempted(store, frozen_pdf_scope(queue.read(plans[number])))
    for suffix in (
        "-binding.json",
        "-prepared.json",
        "-resume-binding.json",
        "-resume-prepared.json",
        "-resume-original-rows.json",
        "-resume-runtime.py",
        "-resume-preflight-validation.json",
    ):
        path = queue.inside(root, after_prefix + suffix)
        controls[path] = queue.sha(path.read_bytes())
    for path in (
        old_launch,
        old_status,
        Path(__file__),
        Path(__file__).with_name("course_archive_gpu.py"),
    ):
        controls[path] = queue.sha(path.read_bytes())
    stop = root / "supervisor-stop.flag"
    if stop.exists():
        controls[stop] = queue.sha(stop.read_bytes())
    return plans, queue.read(template_path), controls


def run(
    root,
    folder,
    template_path,
    after_prefix,
    first,
    last,
    inputs,
    results,
    pending,
    *,
    sleep=time.sleep,
):
    """One explicit continuation; never retry a failed, partial, or prepared batch."""
    root = Path(root).resolve()
    folder = queue.inside(root, Path(folder) / "validation.json").parent
    stem = f"pdf-selected-queue-continuation-{first:04}-{last:04}"
    launch_path, progress_path = [
        root / (stem + suffix) for suffix in ("-launched.json", "-progress.json")
    ]
    identity = {"pid": os.getpid(), "created": psutil.Process().create_time()}
    state = {
        "first": first,
        "last": last,
        "after_prefix": after_prefix,
        "completed_batches": [],
        "automatic_retry_allowed": False,
        "automatic_thermal_resume_allowed": False,
        "source_deletion_allowed": False,
        "maximum_power_w": 200,
        "maximum_temperature_c": 70,
        "minimum_free_gib_each_volume": 32,
    }

    def progress(phase, **extra):
        state.update(extra)
        atomic_json(
            progress_path, {**identity, **state, "state": phase, "updated": time.time()}
        )

    with schema_lease(root / "pdf-selected-queue.lock", timeout=0):
        require(
            not launch_path.exists() and not progress_path.exists(),
            "continuation_already_launched",
        )
        queue.checkpoint(root)
        plans, template, controls = bind(
            root, folder, template_path, after_prefix, first, last, pending
        )
        launch = {
            **identity,
            **state,
            "pending_processes": pending,
            "inputs_base": str(Path(inputs).resolve()),
            "results_base": str(Path(results).resolve()),
            "bound_control_sha256": {str(p): digest for p, digest in controls.items()},
        }
        with launch_path.open("x", encoding="utf8") as stream:
            json.dump(launch, stream, indent=2)
        controls[launch_path] = queue.sha(launch_path.read_bytes())

        def check():
            queue.checkpoint(root)
            for path, digest in controls.items():
                require(queue.sha(path.read_bytes()) == digest, "bound_control_changed")

        try:
            while any(queue.alive(p) for p in pending):
                check()
                progress(
                    "waiting_for_verified_partial_batch", pending_processes=pending
                )
                sleep(20)
            check()
            predecessor = after_prefix
            verify_terminal(root, predecessor, plans[first - 1])
            family = after_prefix[:-4]
            for number in range(first, last + 1):
                check()
                prefix = family + f"{number:04}"
                progress("binding_next_unattempted_batch", number=number, prefix=prefix)
                queue.prepare(
                    root,
                    plans[number],
                    prefix,
                    template,
                    predecessor + "-validation.json",
                    inputs,
                    results,
                )
                progress(
                    "running_one_guarded_finite_batch", number=number, prefix=prefix
                )
                queue.guarded(
                    root,
                    [
                        sys.executable,
                        "-m",
                        "wechat_decrypt_tool.course_pdf_queue",
                        "--root",
                        str(root),
                        "worker",
                        "--prefix",
                        prefix,
                    ],
                    maximum_temperature=70,
                    maximum_power=200,
                    guard_name="extraction-thermal-guard",
                    sampler=lambda: checked_sample(root),
                )
                check()
                proof = verify_terminal(root, prefix, plans[number])
                check()
                receipt = queue.inside(root, prefix + "-validation.json")
                controls[receipt] = queue.sha(receipt.read_bytes())
                state["completed_batches"].append(
                    {
                        "number": number,
                        "prefix": prefix,
                        "page_occurrences": proof["page_occurrences"],
                        "manifest_sha256": proof["manifest_sha256"],
                    }
                )
                progress("finite_batch_terminal_backup_verified")
                predecessor = prefix
            progress("requested_finite_queue_finished")
        except BaseException as exc:
            progress(
                "paused"
                if isinstance(exc, InterruptedError) or (root / "pause.flag").exists()
                else "failed",
                error_type=type(exc).__name__,
                error_sha256=queue.sha(str(exc).encode()),
            )
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--queue", required=True, type=Path)
    parser.add_argument("--template", required=True, type=Path)
    parser.add_argument("--after-prefix", required=True)
    parser.add_argument("--first", required=True, type=int)
    parser.add_argument("--last", required=True, type=int)
    parser.add_argument("--inputs", required=True, type=Path)
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--predecessors", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        run(
            args.root,
            args.queue,
            args.template,
            args.after_prefix,
            args.first,
            args.last,
            args.inputs,
            args.results,
            queue.read(args.predecessors),
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "state": "stopped",
                    "error_type": type(exc).__name__,
                    "error_sha256": queue.sha(str(exc).encode()),
                }
            )
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
