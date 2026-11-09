"""Double-copy and independently reload a finite ready-course input batch.

Includes the exact code and all files actually opened by the readonly input
loader. Excludes videos, models, the full environment and future predecessor
results. Restoring input files does not certify semantic quality.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from . import course_textbook_ready_batch as batch

whole = batch.whole


def require(ok, code):
    if not ok:
        raise ValueError("requires_ready_input_backup_" + code)


def file_hash(path):
    import hashlib

    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for raw in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(raw)
    return value.hexdigest()


def verify(root, key, original_root, manifest):
    root, original_root, manifest = Path(root).resolve(), Path(original_root).resolve(), Path(manifest).resolve()
    workspace = root.parents[1]
    require(not root.is_relative_to(original_root) and not original_root.is_relative_to(root), "distinct_actual_restore_root")
    for module in (sys.modules[__name__], batch, batch.original, batch.previous, batch.recovered, whole):
        require(Path(module.__file__).resolve().is_relative_to(workspace / "src"), "copied_code_without_original_module_fallback")
    old_reads = []

    def audit(event, args):
        if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
            path = Path(os.fsdecode(args[0])).resolve()
            if path.is_relative_to(original_root):
                old_reads.append(str(path))
                require(False, "original_archive_fallback_forbidden")

    sys.addaudithook(audit)
    index = whole.read(manifest)
    for name, expected in index["files"].items():
        path = (workspace / name).resolve()
        require(path.is_relative_to(workspace) and path.is_file()
            and path.stat().st_size == expected["bytes"] and file_hash(path) == expected["sha256"], "all_restored_bytes_match_manifest")
    _, _, plan, _, flights = batch.load(root, key)
    batch.unused(root, batch.folder_for(root, key), flights)
    return {"state": "ready_course_inputs_independently_restored", "batch": key,
        "courses": len(plan["items"]), "records": sum(r["records"] for r in plan["items"]),
        "characters": sum(r["characters"] for r in plan["items"]), "original_root_reads": len(old_reads),
        "model_calls": 0, "original_roles_still_unused": True, "full_predecessor_results_restored": False,
        "audio_video_models_or_full_environment_restored": False, "semantic_acceptance": False,
        "reader_publication_allowed": False, "source_deletion_allowed": False}


def backup(root, key):
    root = Path(root).resolve()
    workspace = root.parents[1]
    folder = batch.folder_for(root, key)
    batch.guard.checkpoint(root)
    require(not (folder / "independent-input-restore.json").exists(), "one_completed_input_restore_receipt")
    opened, capture = set(), [True]

    def audit(event, args):
        if capture[0] and event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
            path = Path(os.fsdecode(args[0])).resolve()
            if path.is_relative_to(root):
                opened.add(path)

    sys.addaudithook(audit)
    _, _, _, _, flights = batch.load(root, key)
    batch.unused(root, folder, flights)
    capture[0] = False
    # Include metadata whose existence/glob was checked without opening its
    # contents. These are real preflight receipts, never placeholder ledgers.
    opened.update([folder / "plan.json", folder / "preflights.json"])
    for flight in flights:
        path = root / "wiki/textbook-keyed-jobs" / flight["job"]
        opened.update(path.glob("*.json"))
    selected = {p.relative_to(workspace).as_posix(): p for p in opened}
    selected.update({p.relative_to(workspace).as_posix(): p for p in (workspace / "src").rglob("*.py")})
    for p in selected.values():
        require(p.is_file() and not p.is_symlink() and p.stat().st_size <= 32 * 1024**2, "finite_actual_input_dependency")
    needed = sum(p.stat().st_size for p in selected.values())
    destination = Path.home() / "Documents/CourseKnowledgeBackups" / ("ready-course-inputs-" + time.strftime("%Y%m%d-%H%M%S"))
    require(shutil.disk_usage(Path.home()).free > 2 * needed + 32 * 1024**3, "both_copies_preserve_disk_reserve")
    saved, restored = destination / "workspace", destination / "restored/workspace"
    entries = {}
    for name, source in sorted(selected.items()):
        batch.guard.checkpoint(root)
        sha, size = file_hash(source), source.stat().st_size
        first, second = saved / name, restored / name
        first.parent.mkdir(parents=True, exist_ok=True)
        second.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, first)
        shutil.copyfile(first, second)
        require(file_hash(first) == sha == file_hash(second) and source.stat().st_size == size, "both_exact_input_copies")
        entries[name] = {"sha256": sha, "bytes": size}
    manifest = destination / "manifest.json"
    with manifest.open("x", encoding="utf-8") as stream:
        json.dump({"files": entries, "scope": "finite_complete_next_course_originals_code_tokenizer_and_exact_predecessor_execution_metadata"}, stream, indent=2)
    process = subprocess.run([sys.executable, "-m", __spec__.name, "--root", "output/course-knowledge", "--batch", key,
        "--original-root", str(root), "--manifest", str(manifest), "verify"], cwd=restored, capture_output=True,
        text=True, encoding="utf-8", env={**os.environ, "PYTHONPATH": str(restored / "src"), "PYTHONUTF8": "1",
            "PYTHONUNBUFFERED": "1", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"},
        creationflags=0x08000000 if os.name == "nt" else 0)
    (destination / "restore.stdout.log").write_text(process.stdout, encoding="utf-8")
    (destination / "restore.stderr.log").write_text(process.stderr, encoding="utf-8")
    require(process.returncode == 0, "actual_copied_code_input_restore")
    result = {**json.loads(process.stdout), "backup": str(destination), "files": len(entries), "bytes": needed,
        "manifest_sha256": file_hash(manifest)}
    for path in [folder / "independent-input-restore.json", destination / "restore-receipt.json"]:
        with path.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, indent=2)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--batch", required=True)
    parser.add_argument("--original-root", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("command", choices=["backup", "verify"])
    args = parser.parse_args()
    try:
        result = verify(args.root, args.batch, args.original_root, args.manifest) if args.command == "verify" else backup(args.root, args.batch)
        print(json.dumps(result))
    except BaseException as exc:
        failure = whole.failure_status(exc)
        if args.command == "backup":
            folder = batch.folder_for(args.root, args.batch)
            if folder.exists():
                atomic = batch.atomic_json
                atomic(folder / ("input-backup-failure-" + str(time.time_ns()) + ".json"), failure)
        print(json.dumps(failure))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
