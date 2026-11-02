"""Resume one archive worker under a measured GPU power and temperature ceiling."""

from __future__ import annotations

import argparse
import math
import os
import subprocess
import sys
import time
from pathlib import Path

import psutil

from .course_knowledge import atomic_json, schema_lease


def sample():
    result = subprocess.run(
        [
            "nvidia-smi",
            "-i",
            "0",
            "--query-gpu=temperature.gpu,power.draw,power.limit",
            "--format=csv,noheader,nounits",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    values = [float(v.strip()) for v in result.stdout.strip().split(",")]
    if len(values) != 3 or not all(math.isfinite(v) for v in values):
        raise ValueError("gpu_sensor_invalid")
    temperature, power, limit = values
    if not (0 <= temperature <= 125 and 0 <= power <= 1000 and 0 < limit <= 1000):
        raise ValueError("gpu_sensor_out_of_range")
    return {"temperature_c": temperature, "power_w": power, "power_limit_w": limit}


def violation(reading, maximum_temperature, maximum_power):
    if reading["temperature_c"] >= maximum_temperature:
        return "temperature_limit_reached"
    if reading["power_limit_w"] > maximum_power + 0.5:
        return "power_cap_changed_or_lost"
    return None


def stop_worker(worker, root):
    """Keep checkpoints; allow an in-flight batch to exit, then stop only our tree."""
    (root / "pause.flag").write_text("gpu_guard_pause", encoding="utf-8")
    (root / "supervisor-stop.flag").write_text("gpu_guard_pause", encoding="utf-8")
    try:
        owner = psutil.Process(worker.pid)
        owned = [(p, p.create_time()) for p in [*owner.children(recursive=True), owner]]
    except psutil.NoSuchProcess:
        owned = []
    # Model inference runs in Ollama's server, outside our worker process tree.
    # Both local course models can own the shared GPU slot during a review.
    for model in ("qwen3.5:9b", "course-defiant:q4"):
        try:
            subprocess.run(["ollama", "stop", model], capture_output=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            pass
    try:
        worker.wait(timeout=15)
    except subprocess.TimeoutExpired:
        for process, created in owned:
            try:
                if process.create_time() == created:
                    process.terminate()
            except psutil.NoSuchProcess:
                pass
        _, alive = psutil.wait_procs([p for p, _ in owned], timeout=5)
        for process in alive:
            try:
                process.kill()
            except psutil.NoSuchProcess:
                pass


def run(
    root,
    command,
    *,
    resume=False,
    maximum_temperature=70,
    maximum_power=120,
    interval=5,
    guard_name="gpu-thermal-guard",
    sampler=sample,
    launch=subprocess.Popen,
    stopper=stop_worker,
    sleep=time.sleep,
):
    root = Path(root).resolve()
    log_names = {
        "gpu-thermal-guard": "thermal-worker",
        "wiki-revision-thermal-guard": "thermal-wiki-revision",
        "extraction-thermal-guard": "thermal-extraction",
        "review-experiment-thermal-guard": "thermal-review-experiment",
    }
    if guard_name not in log_names:
        raise ValueError("gpu_guard_unknown_role")
    if not (root / "knowledge.sqlite3").is_file():
        raise ValueError("gpu_guard_archive_missing")
    if not (
        40 <= maximum_temperature <= 85
        and 50 <= maximum_power <= 350
        and 1 <= interval <= 30
    ):
        raise ValueError("gpu_guard_invalid_limits")
    with schema_lease(root / (guard_name + ".lock"), timeout=0):
        reading = sampler()
        reason = violation(reading, maximum_temperature, maximum_power)
        if reason:
            raise ValueError("gpu_guard_preflight_" + reason)
        pause = root / "pause.flag"
        if pause.exists():
            if not resume:
                raise ValueError("gpu_guard_resume_required")
            pause.rename(
                root / ("pause-before-resume-" + str(time.time_ns()) + ".flag")
            )
        environment = {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
        log_name = log_names[guard_name]
        with (
            (root / (log_name + ".stdout.log")).open("ab") as stdout,
            (root / (log_name + ".stderr.log")).open("ab") as stderr,
        ):
            try:
                worker = launch(
                    command,
                    stdout=stdout,
                    stderr=stderr,
                    env=environment,
                    creationflags=0x08000000 if os.name == "nt" else 0,
                )
            except BaseException:
                pause.write_text("gpu_guard_worker_launch_failed", encoding="utf-8")
                raise

            def progress(state, **extra):
                atomic_json(
                    root / (guard_name + ".json"),
                    {
                        "state": state,
                        "updated": time.time(),
                        "pid": os.getpid(),
                        "created": psutil.Process().create_time(),
                        "worker_pid": worker.pid,
                        "maximum_temperature_c": maximum_temperature,
                        "maximum_power_limit_w": maximum_power,
                        "reading": reading,
                        "automatic_restart_allowed": False,
                        **extra,
                    },
                )

            try:
                while worker.poll() is None:
                    if pause.exists():
                        reason = "pause_requested"
                    else:
                        try:
                            reading = sampler()
                            reason = violation(
                                reading, maximum_temperature, maximum_power
                            )
                        except Exception as exc:
                            reason = "gpu_sensor_failed_" + type(exc).__name__
                    if reason:
                        progress("pausing", reason=reason)
                        stopper(worker, root)
                        progress("paused", reason=reason)
                        return
                    progress("running")
                    sleep(interval)
                progress("worker_exited", returncode=worker.returncode)
            except BaseException:
                stopper(worker, root)
                progress("paused", reason="guard_interrupted")
                raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    role = parser.add_mutually_exclusive_group(required=True)
    role.add_argument("--asr-model", type=Path)
    role.add_argument("--revision-article")
    parser.add_argument("--review-job", action="append", default=[])
    parser.add_argument("--omission-job", action="append", default=[])
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--maximum-power", type=float, default=120)
    parser.add_argument("--maximum-temperature", type=float, default=70)
    args = parser.parse_args()
    if args.omission_job and not args.revision_article:
        parser.error("--omission-job requires --revision-article")
    if args.review_job and not args.revision_article:
        parser.error("--review-job requires --revision-article")
    command = [
        sys.executable,
        "-m",
        "wechat_decrypt_tool.course_knowledge",
        "--root",
        str(args.root.resolve()),
        "--model",
        "qwen3.5:9b",
        "run",
        "--inventory",
        str(args.root.resolve() / "inventory-unique.json"),
        "--asr-model",
        str(args.asr_model.resolve()) if args.asr_model else "",
        "--limit",
        "0",
        "--enrich",
        "--asr-batch-size",
        "4",
    ]
    guard_name = "gpu-thermal-guard"
    if args.revision_article:
        guard_name = "wiki-revision-thermal-guard"
        command = [
            sys.executable,
            "-m",
            "wechat_decrypt_tool.course_wiki_revision",
            "--root",
            str(args.root.resolve()),
            "--article",
            args.revision_article,
            "--wait-gpu-seconds",
            "1800",
        ]
        for job in args.omission_job:
            command.extend(["--omission-job", job])
        for job in args.review_job:
            command.extend(["--review-job", job])
    run(
        args.root,
        command,
        resume=args.resume,
        maximum_power=args.maximum_power,
        maximum_temperature=args.maximum_temperature,
        guard_name=guard_name,
    )


if __name__ == "__main__":
    main()
