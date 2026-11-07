"""One CPU-only recovery for a pilot preparation blocked by PS7 module paths.

Keep the original recipe, registrations and failure. The Windows OCR script runs
unchanged with only the Windows PowerShell built-in module directory in its
child environment. No GPU role, budget or worker is started or refreshed.
"""
from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import re
import subprocess
import time

from PIL import Image

from . import course_frame_assisted_pilot as pilot

POLICY = "original-unfinished-pilot-native-prepare-windows-modules-once-v1"


def windows_ocr(image, output, language, *, run=subprocess.run):
    executable = Path(os.environ.get("WINDIR", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    started = time.monotonic()
    result = run([str(executable), "-NoProfile", "-File", str(pilot.script_path()), "-ImagePath", str(image),
        "-OutputPath", str(output), "-Language", language], capture_output=True, timeout=120,
        env={**os.environ, "PSModulePath": str(executable.parent / "Modules")},
        creationflags=0x08000000 if os.name == "nt" else 0)
    prefix = output.with_suffix("")
    Path(str(prefix) + "-launcher.stdout.bin").write_bytes(result.stdout)
    Path(str(prefix) + "-launcher.stderr.bin").write_bytes(result.stderr)
    pilot.require(result.returncode == 0 and output.is_file(), "recovered_installed_windows_ocr_success")
    return time.monotonic() - started


def repair(root, key, *, recognize=windows_ocr):
    root = Path(root).resolve()
    pilot.require(re.fullmatch(r"[a-f0-9]{32}", key) is not None, "native_recovery_job_id")
    folder = root / "wiki/frame-assisted-pilots" / key
    pilot.shared.checkpoint(root)
    pilot.checked_sample(root)
    recipe = pilot.whole.read(folder / "recipe.json")
    failure_path = folder / "prepare-failure.json"
    failure = pilot.whole.read(failure_path)
    pilot.require(failure.get("reason_code") == "requires_assisted_frame_pilot_installed_windows_ocr_success",
        "original_native_launcher_failure")
    pilot.require(recipe["policy"] == pilot.POLICY and pilot.digest(recipe)[:32] == key
        and recipe["code_sha256"] == pilot.bindings(), "unchanged_failed_preparation_recipe")
    tokenizer = root / recipe["tokenizer"]
    pilot.require(pilot.whole.sha(tokenizer.read_bytes()) == recipe["tokenizer_sha256"], "same_native_recovery_tokenizer")
    pictures = {}
    for target, expected in recipe["targets"].items():
        registration = pilot.whole.read(root / "wiki/frame-assisted-registrations" / f"{recipe['video']}-{target}.json")
        source, frame = pilot.shared.rows(root, recipe["video"], int(target))
        pilot.require(registration == {"job": key, "frame": int(target), "video": recipe["video"]}
            and source == expected["source"] and frame == expected["frame"], "same_registered_unattempted_frame")
        raw = (root / frame["path"]).read_bytes()
        pilot.require(pilot.whole.sha(raw) == expected["raw_image_sha256"], "same_native_recovery_source_pixels")
        with Image.open(io.BytesIO(raw)) as native:
            stream = io.BytesIO()
            native.convert("RGB").save(stream, format="PNG")
        pictures[target] = stream.getvalue()
        pilot.require(pilot.whole.sha(pictures[target]) == expected["native_png_sha256"], "same_native_recovery_png")
        existing = folder / (target + ".png")
        pilot.require(not existing.exists() or existing.read_bytes() == pictures[target], "existing_native_png_unchanged")
    with pilot.knowledge.schema_lease(folder / "native-prepare-recovery.lock", timeout=0):
        marker = folder / "native-prepare-recovery-started.json"
        pilot.require(not marker.exists() and all(not (folder / name).exists()
            for name in ("contract.json", "contexts.json", "started.json", "guard-started.json"))
            and all(not (folder / f"{target}-{lang}.json").exists()
                for target in pictures for lang in ("zh-Hans-CN", "en-US")), "native_recovery_once_before_any_receipts_or_model_roles")
        original = {"policy": POLICY, "job": key, "recipe_sha256": pilot.digest(recipe),
            "original_failure_sha256": pilot.whole.sha(failure_path.read_bytes()),
            "recovery_code_sha256": pilot.whole.sha(Path(__file__).read_bytes()),
            "script_sha256": pilot.whole.sha(pilot.script_path().read_bytes()),
            "updated": time.time(), "gpu_calls": 0, "gpu_role_budget_refreshed": False,
            "child_module_path_policy": "windows_powershell_builtin_modules_only"}
        pilot.knowledge.atomic_json(marker, original)
        try:
            result = pilot.prepare_native_drafts(folder, recipe, pictures, tokenizer, recognize=recognize)
            pilot.load(root, key)
            pilot.require(pilot.whole.sha(failure_path.read_bytes()) == original["original_failure_sha256"], "original_native_failure_retained")
            result = {**result, "state": "original_native_preparation_recovered_without_gpu", "recovery_policy": POLICY,
                "original_failure_preserved": True, "gpu_role_budget_refreshed": False,
                "contract_sha256": pilot.whole.sha((folder / "contract.json").read_bytes())}
            pilot.knowledge.atomic_json(folder / "native-prepare-recovery-result.json", result)
            return result
        except BaseException as exc:
            pilot.knowledge.atomic_json(folder / "native-prepare-recovery-failure.json", pilot.whole.failure_status(exc))
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--job", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(repair(args.root, args.job)))
    except BaseException as exc:
        print(json.dumps(pilot.whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
