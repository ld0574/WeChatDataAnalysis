"""Recheck an existing independent restore with an anchored companion bootstrap."""

import argparse
import json
import os
import subprocess
import time
from pathlib import Path

import psutil

from tools.course_extraction_environment_copy import file_sha
from tools.course_extraction_environment_restore import authority
from wechat_decrypt_tool.course_knowledge import atomic_json, schema_lease


def validate_probe(result, count, bootstrap_sha256):
    if (
        result.get("state") != "isolated_cpu_and_native_synthetic_probe_verified"
        or result.get("files_verified") != count
        or result.get("probe_bootstrap_sha256") != bootstrap_sha256
        or result.get("companion_bootstrap") is not True
        or result.get("gpu_context_initialized") is not False
        or result.get("gpu_inference_verified") is not False
        or any(result.get(key) != 0 for key in (
            "original_installation_access_attempts", "network_connection_attempts",
            "outside_module_count", "original_installation_native_mapping_count",
        ))
    ):
        raise ValueError("environment_reprobe_incomplete_or_not_isolated")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--bootstrap", required=True, type=Path)
    parser.add_argument("--bootstrap-sha256", required=True)
    parser.add_argument("--progress", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    args = parser.parse_args()
    bundle, bootstrap = args.bundle.resolve(), args.bootstrap.resolve()
    identity = {"pid": os.getpid(), "created": psutil.Process().create_time()}
    psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    psutil.Process().ionice(psutil.IOPRIO_LOW)

    def report(state, **fields):
        atomic_json(args.progress, {**identity, "state": state, "updated": time.time(),
                                   "source_deletion_allowed": False, **fields})

    try:
        with schema_lease(bundle / "environment-restore.lock", timeout=0):
            manifest = authority(bundle, args.manifest_sha256)
            if (bundle / "restore-input-manifest.sha256").read_text("ascii") != args.manifest_sha256:
                raise ValueError("environment_reprobe_restore_marker_changed")
            if file_sha(bootstrap) != args.bootstrap_sha256:
                raise ValueError("environment_reprobe_bootstrap_anchor_changed")
            command = [str(bundle / "python/python.exe"), "-I", "-B", str(bootstrap),
                       "--bundle", str(bundle), "--bootstrap-sha256", args.bootstrap_sha256, "probe"]
            stdout = args.progress.with_suffix(".stdout.log")
            stderr = args.progress.with_suffix(".stderr.log")
            with stdout.open("wb") as out, stderr.open("wb") as err:
                child = subprocess.Popen(command, stdout=out, stderr=err, creationflags=0x08000000)
                try:
                    created = psutil.Process(child.pid).create_time()
                    while child.poll() is None:
                        report("hashing_full_restored_payload_and_running_isolated_cpu_native_probe",
                               probe_pid=child.pid, probe_created=created)
                        time.sleep(5)
                except BaseException:
                    if child.poll() is None:
                        child.terminate()
                        child.wait(timeout=10)
                    raise
                if child.returncode:
                    raise ValueError("environment_reprobe_child_failed")
            if file_sha(bootstrap) != args.bootstrap_sha256:
                raise ValueError("environment_reprobe_bootstrap_changed_during_probe")
            authority(bundle, args.manifest_sha256)
            result = json.loads(stdout.read_bytes())
            validate_probe(result, len(manifest["files"]), args.bootstrap_sha256)
            receipt = {**identity,
                       "state": "independent_restore_cpu_native_probe_verified_gpu_unproven",
                       "destination": str(bundle), "manifest_sha256": args.manifest_sha256,
                       "probe_bootstrap": str(bootstrap), "probe_bootstrap_sha256": args.bootstrap_sha256,
                       "files": len(manifest["files"]), "bytes": sum(r["bytes"] for r in manifest["files"]),
                       "probe": result, "gpu_inference_verified": False,
                       "source_deletion_allowed": False, "updated": time.time()}
            atomic_json(args.receipt, receipt)
            report(receipt["state"], receipt=str(args.receipt))
    except BaseException as exc:
        report("failed", error_type=type(exc).__name__,
               error_code=str(exc) if isinstance(exc, ValueError) else type(exc).__name__)
        raise


if __name__ == "__main__":
    main()
