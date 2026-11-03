"""Resume the existing course worker with GPU and both-volume disk protection."""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

from . import course_gpu_guard
from .course_knowledge import atomic_json


def checked_sample(root, *, sampler=None, usage=None, volumes=None):
    root = Path(root).resolve()
    usage = usage or shutil.disk_usage
    volumes = volumes or {root.anchor, Path.home().anchor}
    free = {volume: usage(volume).free / 1024**3 for volume in sorted(volumes)}
    record = {
        "updated": time.time(),
        "minimum_free_gib": 32,
        "free_gib": free,
        "below_reserve": [volume for volume, gib in free.items() if gib < 32],
        "automatic_resume_allowed": False,
    }
    atomic_json(root / "archive-disk-reserve-last-check.json", record)
    if record["below_reserve"]:
        raise RuntimeError("course_archive_32gib_reserve_reached")
    return {**(sampler or course_gpu_guard.sample)(), "free_gib": free}


def run(root, model):
    root, model = Path(root).resolve(), Path(model).resolve()
    if (
        not (model / "config.json").is_file()
        or not (model / "model.safetensors").is_file()
    ):
        raise ValueError("course_archive_local_asr_model_missing")
    command = [
        sys.executable,
        "-m",
        "wechat_decrypt_tool.course_knowledge",
        "--root",
        str(root),
        "--model",
        "qwen3.5:9b",
        "run",
        "--inventory",
        str(root / "inventory-unique.json"),
        "--asr-model",
        str(model),
        "--limit",
        "0",
        "--enrich",
        "--asr-batch-size",
        "4",
    ]
    course_gpu_guard.run(
        root,
        command,
        maximum_power=200,
        maximum_temperature=70,
        sampler=lambda: checked_sample(root),
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--asr-model", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        run(args.root, args.asr_model)
    except Exception as exc:
        print(
            __import__("json").dumps(
                {"state": "stopped", "error_type": type(exc).__name__}
            )
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
