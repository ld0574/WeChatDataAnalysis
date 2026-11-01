"""Restore a receipt-anchored extraction bundle, then probe its bundled runtime."""

import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path

import psutil

from tools.course_extraction_environment_copy import (
    POLICY,
    RESERVE,
    capacity,
    copy_one,
    file_sha,
)
from wechat_decrypt_tool.course_knowledge import atomic_json, schema_lease
from wechat_decrypt_tool.course_wiki_runtime import relative_file


def authority(source, expected):
    if not isinstance(expected, str) or not re.fullmatch("[a-f0-9]{64}", expected):
        raise ValueError("environment_restore_manifest_hash_required")
    path = source / "extraction-environment.json"
    if file_sha(path) != expected:
        raise ValueError("environment_restore_manifest_changed")
    value = json.loads(path.read_bytes())
    if value["policy"] != POLICY or value["source_deletion_allowed"] is not False:
        raise ValueError("environment_restore_policy_changed")
    plan = source / "environment-input-plan.json"
    if file_sha(plan) != value["input_plan_sha256"]:
        raise ValueError("environment_restore_input_plan_changed")
    planned = json.loads(plan.read_bytes())
    required = {r["path"] for r in planned["files"]} | {
        "environment-input-plan.json",
        "extraction_bootstrap.py",
        "run-environment.ps1",
        "README.md",
    }
    names = set()
    for row in value["files"]:
        relative_file(source, row["path"])
        folded = row["path"].casefold()
        if folded in names or not re.fullmatch("[a-f0-9]{64}", row["sha256"]):
            raise ValueError("environment_restore_duplicate_or_invalid_row")
        if type(row["bytes"]) is not int or row["bytes"] < 0:
            raise ValueError("environment_restore_invalid_file_size")
        names.add(folded)
    if {r["path"] for r in value["files"]} != required:
        raise ValueError("environment_restore_incomplete_input_plan")
    return value


def restore(
    source,
    destination,
    expected,
    *,
    reserve=RESERVE,
    checkpoint=lambda: None,
    progress=None,
    runner=None,
):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if destination.is_relative_to(source) or source.is_relative_to(destination):
        raise ValueError("environment_restore_source_destination_overlap")
    manifest = authority(source, expected)
    marker = destination / "restore-input-manifest.sha256"
    if destination.exists():
        if not marker.is_file() or marker.read_text("ascii") != expected:
            raise ValueError("environment_restore_existing_destination_not_owned")
    else:
        capacity(
            destination,
            sum(r["bytes"] for r in manifest["files"]) + len(manifest["files"]) * 4096,
            reserve,
        )
        destination.mkdir(parents=True)
        marker.write_text(expected, encoding="ascii")
    completed, reused, copied_bytes = 0, 0, 0
    identity = {"pid": os.getpid(), "created": psutil.Process().create_time()}

    def report(state, **extra):
        if progress:
            atomic_json(
                Path(progress),
                {
                    **identity,
                    "state": state,
                    "updated": time.time(),
                    "source": str(source),
                    "destination": str(destination),
                    "manifest_sha256": expected,
                    "completed_files": completed,
                    "reused_files": reused,
                    "completed_bytes": copied_bytes,
                    "total_files": len(manifest["files"]),
                    "source_deletion_allowed": False,
                    **extra,
                },
            )

    def check():
        checkpoint()

    with schema_lease(destination / "environment-restore.lock", timeout=0):
        try:
            report("copying_and_hashing_independent_restore")
            next_progress = 0
            for row in manifest["files"]:
                check()
                original = relative_file(source, row["path"])
                target = relative_file(destination, row["path"])
                if (
                    target.is_file()
                    and target.stat().st_size == row["bytes"]
                    and file_sha(target, check) == row["sha256"]
                ):
                    reused += 1
                else:
                    st = original.stat()
                    spec = {
                        **row,
                        "mtime_ns": st.st_mtime_ns,
                        "expected_sha256": row["sha256"],
                    }
                    copy_one(
                        original,
                        target,
                        spec,
                        checkpoint=check,
                        capacity_check=lambda p, n: capacity(p, n, reserve),
                    )
                completed += 1
                copied_bytes += row["bytes"]
                if time.monotonic() >= next_progress:
                    report("copying_and_hashing_independent_restore")
                    next_progress = time.monotonic() + 10
            authority(source, expected)
            raw = (source / "extraction-environment.json").read_bytes()
            if file_sha(source / "extraction-environment.json") != expected:
                raise ValueError("environment_restore_manifest_changed")
            (destination / "extraction-environment.json").write_bytes(raw)
            if file_sha(destination / "extraction-environment.json") != expected:
                raise ValueError("environment_restored_manifest_changed")
            report("isolated_cpu_native_probe_with_bundled_python")
            command = [
                str(destination / "python/python.exe"),
                "-I",
                "-B",
                str(destination / "extraction_bootstrap.py"),
                "probe",
            ]
            if runner:
                result = runner(command)
            else:
                log = Path(progress).parent if progress else destination
                with (
                    (log / "extraction-restored-probe.stdout.log").open("wb") as out,
                    (log / "extraction-restored-probe.stderr.log").open("wb") as err,
                ):
                    child = subprocess.Popen(
                        command,
                        stdout=out,
                        stderr=err,
                        creationflags=0x08000000 if os.name == "nt" else 0,
                    )
                    psutil.Process(child.pid).nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
                    psutil.Process(child.pid).ionice(psutil.IOPRIO_LOW)
                    try:
                        while child.poll() is None:
                            check()
                            report(
                                "isolated_cpu_native_probe_with_bundled_python",
                                probe_pid=child.pid,
                            )
                            time.sleep(5)
                    except BaseException:
                        if child.poll() is None:
                            child.terminate()
                            child.wait(timeout=10)
                        raise
                    if child.returncode:
                        raise ValueError("environment_restored_probe_failed")
                result = json.loads(
                    (log / "extraction-restored-probe.stdout.log").read_bytes()
                )
            if (
                result.get("state")
                != "isolated_cpu_and_native_synthetic_probe_verified"
                or result.get("files_verified") != len(manifest["files"])
                or any(
                    result.get(k) != 0
                    for k in [
                        "original_installation_access_attempts",
                        "network_connection_attempts",
                        "outside_module_count",
                        "original_installation_native_mapping_count",
                    ]
                )
                or result.get("gpu_context_initialized") is not False
                or result.get("gpu_inference_verified") is not False
            ):
                raise ValueError("environment_restored_probe_not_isolated")
            receipt = {
                **identity,
                "state": "independent_restore_cpu_native_probe_verified_gpu_unproven",
                "source": str(source),
                "destination": str(destination),
                "manifest_sha256": expected,
                "files": completed,
                "bytes": copied_bytes,
                "reused_files": reused,
                "probe": result,
                "gpu_inference_verified": False,
                "source_deletion_allowed": False,
                "updated": time.time(),
            }
            atomic_json(destination / "independent-restore-validation.json", receipt)
            report(
                receipt["state"],
                cpu_native_probe_verified=True,
                gpu_inference_verified=False,
            )
            return receipt
        except BaseException as exc:
            report(
                "paused" if isinstance(exc, InterruptedError) else "failed",
                error_type=type(exc).__name__,
                error_code=str(exc)
                if isinstance(exc, ValueError) and str(exc).startswith("environment_")
                else type(exc).__name__,
            )
            raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", required=True, type=Path)
    p.add_argument("--destination", required=True, type=Path)
    p.add_argument("--manifest-sha256", required=True)
    p.add_argument("--progress", required=True, type=Path)
    p.add_argument("--pause-flag", type=Path)
    a = p.parse_args()
    psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    psutil.Process().ionice(psutil.IOPRIO_LOW)

    def check():
        if a.pause_flag and a.pause_flag.exists():
            raise InterruptedError("environment_restore_pause_requested")

    result = restore(
        a.source,
        a.destination,
        a.manifest_sha256,
        progress=a.progress,
        checkpoint=check,
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
