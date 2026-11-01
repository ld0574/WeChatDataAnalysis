"""Resumable, hash-verified copy of the frozen local extraction environment."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import psutil

from wechat_decrypt_tool.course_knowledge import atomic_json, schema_lease
from wechat_decrypt_tool.course_lesson_exports import digest
from wechat_decrypt_tool.course_video_visual_audit import ReadOnlyArchive
from wechat_decrypt_tool.course_wiki_runtime import relative_file

WIKI = Path(__file__).resolve().parents[1] / "output/course-knowledge/wiki"
ROOT = WIKI.parent
PLAN = WIKI / "extraction-environment-20261102-plan.json"
INVENTORY = WIKI / "extraction-environment-20261102-inventory.json"
PROGRESS = WIKI / "extraction-environment-20261102-copy-progress.json"
POLICY = "offline-windows-course-extraction-environment-v1"
RESERVE = 32 * 1024**3

README = """# Windows 课程提取环境

本副本包含 Python、Python/GPU 运行库、本机 ASR 和两套 Ollama 模型、项目包、Poppler 和 FFmpeg。
课程资料与数据库另行备份；此包不包含它们。不要把本包当作课程内容验收或删除原视频的依据。
运行包仅支持 Windows x64，Windows 和兼容 NVIDIA 驱动仍由目标系统提供。
SVG Node/resvg 与显式字体尚未纳入；语义质量、完整提取和 GPU 推理恢复须分别验收。

在 PowerShell 中：

    .\\run-environment.ps1 verify
    .\\run-environment.ps1 probe
    .\\run-environment.ps1 ollama-serve
    .\\run-environment.ps1 course-cli --root 'X:\\课程归档目录' status

Ollama 默认只监听本机 127.0.0.1:11435，使用此包模型，不覆盖原 Ollama 服务。
不要在原服务占用 GPU 时加载模型；GPU 测试和课程处理仍必须遵守共享锁与温控守护。
probe 只做离线 CPU/工具合成检查，不加载 GPU 模型，不证明 GPU 推理已恢复。
启动前逐文件重新核对清单。environment-input-plan.json 保留来源路径，仅供溯源；运行不读取这些原路径。
"""
LAUNCHER = """$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'python/python.exe') -I -B (Join-Path $PSScriptRoot 'extraction_bootstrap.py') @args
exit $LASTEXITCODE
"""


def file_sha(path, checkpoint=lambda: None):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024**2), b""):
            checkpoint()
            h.update(block)
    return h.hexdigest()


def capacity(path, needed, reserve=RESERVE):
    ancestor = path.resolve()
    while not ancestor.exists():
        ancestor = ancestor.parent
    if shutil.disk_usage(ancestor).free < needed + reserve:
        raise InterruptedError("environment_copy_disk_reserve_reached")


def copy_one(
    source, destination, row, *, checkpoint=lambda: None, capacity_check=capacity
):
    if source.resolve() == destination.resolve() or destination.is_symlink():
        raise ValueError("environment_copy_target_overlaps_source_or_is_link")
    before = source.stat()
    if (before.st_size, before.st_mtime_ns) != (row["bytes"], row["mtime_ns"]):
        raise ValueError("environment_frozen_source_changed")
    if source.is_symlink() or not source.is_file():
        raise ValueError("environment_source_not_regular_file")
    capacity_check(destination, row["bytes"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.with_name(destination.name + ".environment-copy-part")
    if staging.is_symlink():
        raise ValueError("environment_staging_symlink")
    h = hashlib.sha256()
    with source.open("rb") as stream, staging.open("wb") as output:
        for block in iter(lambda: stream.read(8 * 1024**2), b""):
            checkpoint()
            capacity_check(destination, len(block))
            output.write(block)
            h.update(block)
    copied = h.hexdigest()
    after = source.stat()
    if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
        raise ValueError("environment_source_changed_during_copy")
    if row.get("expected_sha256") and row["expected_sha256"] != copied:
        raise ValueError("environment_expected_model_hash_mismatch")
    if (
        staging.stat().st_size != row["bytes"]
        or file_sha(staging, checkpoint) != copied
    ):
        raise ValueError("environment_copied_file_hash_mismatch")
    staging.replace(destination)
    return {"path": row["path"], "bytes": row["bytes"], "sha256": copied}


def source_file(row, components):
    component = components[row["component"]]
    root = Path(component["source_root"]).resolve()
    source = Path(row["source"])
    if not source.resolve().is_relative_to(root) or source.is_symlink():
        raise ValueError("environment_source_outside_component")
    if (
        row["path"]
        != component["target_prefix"]
        + "/"
        + source.resolve().relative_to(root).as_posix()
    ):
        raise ValueError("environment_source_target_mapping_changed")
    return source


def main():
    psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    psutil.Process().ionice(psutil.IOPRIO_VERYLOW)
    store = ReadOnlyArchive(ROOT)
    raw = PLAN.read_bytes()
    plan = json.loads(raw)
    expected = json.loads(INVENTORY.read_bytes())["plan_sha256"]
    if digest(raw) != expected:
        raise ValueError("environment_frozen_plan_changed")
    destination = Path(plan["destination"]).resolve()
    if not Path(plan["destination"]).is_absolute() or destination.parent == destination:
        raise ValueError("environment_unexpected_destination")
    components = {c["name"]: c for c in plan["components"]}
    for component in components.values():
        source = Path(component["source_root"]).resolve()
        if destination.is_relative_to(source) or source.is_relative_to(destination):
            raise ValueError("environment_copy_source_destination_overlap")
    rows = []
    copied_bytes = 0
    next_progress = 0
    current = None

    def progress(state, **extra):
        atomic_json(
            PROGRESS,
            {
                "state": state,
                "pid": os.getpid(),
                "created": psutil.Process().create_time(),
                "updated": time.time(),
                "destination": str(destination),
                "plan_sha256": expected,
                "completed_files": min(len(rows), len(plan["files"])),
                "total_planned_files": len(plan["files"]),
                "completed_bytes": copied_bytes,
                "total_planned_bytes": plan["bytes"],
                "component": current,
                "model_calls": 0,
                "gpu_inference_verified": False,
                "environment_recovery_verified": False,
                "source_deletion_allowed": False,
                **extra,
            },
        )

    def checkpoint():
        nonlocal next_progress
        store.checkpoint()
        if time.monotonic() >= next_progress:
            progress("copying_and_hashing_frozen_environment")
            next_progress = time.monotonic() + 10

    with schema_lease(WIKI / "extraction-environment-copy.lock", timeout=0):
        try:
            marker = destination / "environment-input-plan.json"
            if destination.exists():
                if not marker.is_file() or marker.read_bytes() != raw:
                    raise ValueError(
                        "environment_existing_destination_not_owned_by_plan"
                    )
            else:
                capacity(destination, plan["required_allocated_bytes_estimate"])
                destination.mkdir(parents=True)
                marker.write_bytes(raw)
            saved_path = destination / "copy-checkpoint.json"
            saved = (
                json.loads(saved_path.read_bytes())
                if saved_path.exists()
                else {"plan_sha256": expected, "files": []}
            )
            if saved["plan_sha256"] != expected:
                raise ValueError("environment_resume_plan_changed")
            prior = {r["path"]: r for r in saved["files"]}
            if len(prior) != len(saved["files"]) or not set(prior) <= {
                r["path"] for r in plan["files"]
            }:
                raise ValueError("environment_resume_contains_invalid_files")
            for spec in plan["files"]:
                current = spec["component"]
                checkpoint()
                source = source_file(spec, components)
                target = relative_file(destination, spec["path"])
                if spec["path"] in prior:
                    record = prior[spec["path"]]
                    stat = source.stat()
                    if (stat.st_size, stat.st_mtime_ns) != (
                        spec["bytes"],
                        spec["mtime_ns"],
                    ):
                        raise ValueError("environment_resume_frozen_source_changed")
                    if (
                        record["bytes"] != spec["bytes"]
                        or target.stat().st_size != spec["bytes"]
                        or file_sha(target, checkpoint) != record["sha256"]
                    ):
                        raise ValueError("environment_resume_copy_changed")
                    if (
                        spec.get("expected_sha256")
                        and record["sha256"] != spec["expected_sha256"]
                    ):
                        raise ValueError("environment_resume_model_hash_changed")
                else:
                    record = copy_one(source, target, spec, checkpoint=checkpoint)
                rows.append(record)
                copied_bytes += record["bytes"]
                if len(rows) % 100 == 0 or record["bytes"] > 128 * 1024**2:
                    atomic_json(saved_path, {"plan_sha256": expected, "files": rows})
            atomic_json(saved_path, {"plan_sha256": expected, "files": rows})
            if PLAN.read_bytes() != raw:
                raise ValueError("environment_plan_changed_during_copy")
            generated = {
                "extraction_bootstrap.py": Path(__file__)
                .with_name("course_extraction_environment_bootstrap.py")
                .read_bytes(),
                "run-environment.ps1": LAUNCHER.encode(),
                "README.md": README.encode(),
            }
            for name, payload in generated.items():
                path = relative_file(destination, name)
                path.write_bytes(payload)
                rows.append(
                    {"path": name, "bytes": len(payload), "sha256": digest(payload)}
                )
            rows.append(
                {
                    "path": "environment-input-plan.json",
                    "bytes": len(raw),
                    "sha256": expected,
                }
            )
            manifest = {
                "policy": POLICY,
                "created": time.time(),
                "input_plan_sha256": expected,
                "files": rows,
                "components": plan["components"],
                "python_version": plan["python_version"],
                "course_data_included": False,
                "gpu_inference_verified": False,
                "independent_restore_verified": False,
                "source_deletion_allowed": False,
            }
            atomic_json(destination / "extraction-environment.json", manifest)
            progress("verifying_with_bundled_interpreter")
            output = WIKI / "extraction-environment-20261102-verify.stdout.log"
            error = WIKI / "extraction-environment-20261102-verify.stderr.log"
            with output.open("wb") as stdout, error.open("wb") as stderr:
                child = subprocess.Popen(
                    [
                        str(destination / "python/python.exe"),
                        "-I",
                        "-B",
                        str(destination / "extraction_bootstrap.py"),
                        "verify",
                    ],
                    stdout=stdout,
                    stderr=stderr,
                    creationflags=0x08000000,
                )
                psutil.Process(child.pid).nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
                psutil.Process(child.pid).ionice(psutil.IOPRIO_VERYLOW)
                while child.poll() is None:
                    progress(
                        "verifying_with_bundled_interpreter", verification_pid=child.pid
                    )
                    time.sleep(5)
            if child.returncode != 0:
                raise ValueError("environment_bundled_verification_failed")
            checked = json.loads(output.read_bytes())
            assert checked["state"] == "environment_files_verified" and checked[
                "files"
            ] == len(rows)
            progress(
                "environment_file_backup_verified_not_recovery_accepted",
                backup_files=len(rows),
            )
            atomic_json(
                WIKI / "extraction-environment-20261102-backup-validation.json",
                {
                    "state": "environment_file_backup_verified_not_recovery_accepted",
                    "updated": time.time(),
                    "destination": str(destination),
                    "manifest_sha256": file_sha(
                        destination / "extraction-environment.json"
                    ),
                    "plan_sha256": expected,
                    "files": len(rows),
                    "bytes": sum(row["bytes"] for row in rows),
                    "bundled_interpreter_verification": checked,
                    "model_calls": 0,
                    "independent_restore_verified": False,
                    "gpu_inference_verified": False,
                    "environment_recovery_verified": False,
                    "source_deletion_allowed": False,
                },
            )
        except BaseException as exc:
            progress(
                "paused" if isinstance(exc, InterruptedError) else "failed",
                error_type=type(exc).__name__,
                error_sha256=digest(str(exc).encode()),
            )
            raise SystemExit(1) from None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--progress", type=Path)
    args = parser.parse_args()
    ROOT = args.root.resolve()
    WIKI = ROOT / "wiki"
    PLAN = args.plan.resolve()
    INVENTORY = args.inventory.resolve()
    PROGRESS = (
        args.progress.resolve()
        if args.progress
        else WIKI / "extraction-environment-copy-progress.json"
    )
    main()
