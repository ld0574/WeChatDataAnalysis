"""Continue a paused PDF batch using its exact retained renderer at a new path.

The original binding and frozen runtime remain unchanged. Only the executable
location is resolved in memory, with a separate immutable relocation receipt.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import copy
import json
from pathlib import Path
import re
import sys

from . import course_pdf_queue as queue
from . import course_pdf_queue_resume as resume
from .course_knowledge import atomic_json

POLICY = "retained-identical-pdf-renderer-location-resolution-v1"


def require(ok, reason):
    if not ok:
        raise ValueError("pdf_renderer_resume_" + reason)


def retained_renderer(environment, manifest_sha256, expected_executable):
    environment = Path(environment).resolve()
    manifest = environment / "extraction-environment.json"
    raw = manifest.read_bytes()
    require(queue.sha(raw) == manifest_sha256, "retained_environment_manifest_changed")
    entries = [row for row in json.loads(raw)["files"] if row["path"].replace("\\", "/").startswith("tools/poppler/")]
    require(bool(entries), "retained_renderer_bundle_missing")
    seen = set()
    for row in entries:
        target = environment / row["path"]
        require(not target.is_symlink() and target.resolve().is_relative_to(environment)
            and row["path"] not in seen, "retained_renderer_bundle_scope")
        seen.add(row["path"])
        require(target.stat().st_size == row["bytes"] and queue.sha(target.read_bytes()) == row["sha256"], "retained_renderer_bundle_changed")
    executable = environment / "tools/poppler/bin/pdftoppm.exe"
    require(any((environment / row["path"]).resolve() == executable.resolve() for row in entries), "listed_renderer_executable")
    require(queue.sha(executable.read_bytes()) == expected_executable, "exact_original_renderer_required")
    return executable, len(entries)


def relocation_path(root, prefix):
    resume.paths(root, prefix)
    return Path(root).resolve() / (prefix + "-renderer-relocation.json")


def prepare(root, prefix, environment, environment_receipt):
    root = Path(root).resolve()
    binding_path = root / (prefix + "-binding.json")
    binding = queue.read(binding_path)
    validation_raw = Path(environment_receipt).read_bytes()
    validation = json.loads(validation_raw)
    require(Path(validation["destination"]).resolve() == Path(environment).resolve(), "original_environment_destination")
    executable, count = retained_renderer(environment, validation["manifest_sha256"], binding["renderer_sha256"])
    source = Path(__file__).read_bytes()
    retained_source = root / (prefix + "-renderer-resolver.py")
    require(not retained_source.exists() or retained_source.read_bytes() == source, "immutable_resolver_source")
    if not retained_source.exists():
        retained_source.write_bytes(source)
    artifact = {"policy": POLICY, "prefix": prefix, "original_binding_sha256": queue.sha(binding_path.read_bytes()),
        "original_renderer_path": binding["renderer"], "renderer_sha256": binding["renderer_sha256"],
        "resolved_renderer_path": str(executable), "environment": str(Path(environment).resolve()),
        "environment_manifest_sha256": validation["manifest_sha256"], "verified_renderer_bundle_files": count,
        "environment_receipt_path": str(Path(environment_receipt).resolve()),
        "environment_receipt_sha256": queue.sha(validation_raw),
        "resolver_module_sha256": queue.sha(Path(__file__).read_bytes()),
        "retained_resolver_source": str(retained_source),
        "old_runtime_changed": False, "original_binding_changed": False,
        "new_model_or_renderer_revision": False, "model_calls": 0, "source_deletion_allowed": False}
    path = relocation_path(root, prefix)
    require(not path.exists() or queue.read(path) == artifact, "immutable_renderer_relocation")
    if not path.exists():
        atomic_json(path, artifact)
    return artifact


def verified_binding(root, prefix):
    root = Path(root).resolve()
    artifact = queue.read(relocation_path(root, prefix))
    require(artifact["policy"] == POLICY and artifact["prefix"] == prefix
        and artifact["resolver_module_sha256"] == queue.sha(Path(__file__).read_bytes()), "bound_resolver_revision")
    require(queue.sha(Path(artifact["retained_resolver_source"]).read_bytes()) == artifact["resolver_module_sha256"], "retained_resolver_source_changed")
    binding_path = root / (prefix + "-binding.json")
    require(queue.sha(binding_path.read_bytes()) == artifact["original_binding_sha256"], "original_binding_changed")
    require(queue.sha(Path(artifact["environment_receipt_path"]).read_bytes()) == artifact["environment_receipt_sha256"], "environment_receipt_changed")
    binding = queue.read(binding_path)
    require(binding["renderer"] == artifact["original_renderer_path"]
        and binding["renderer_sha256"] == artifact["renderer_sha256"], "original_renderer_identity")
    executable, count = retained_renderer(artifact["environment"], artifact["environment_manifest_sha256"], binding["renderer_sha256"])
    require(str(executable) == artifact["resolved_renderer_path"] and count == artifact["verified_renderer_bundle_files"], "original_relocated_bundle")
    return binding_path, {**binding, "renderer": str(executable)}


@contextmanager
def resolve_renderer(root, prefix):
    """Delegate every original check; resolve only this binding's file location."""
    root = Path(root).resolve()
    original_validate, original_read = queue.validate_inputs, queue.read

    def validate(request_root, request_prefix):
        require(Path(request_root).resolve() == root and request_prefix == prefix, "single_bound_batch_only")
        binding_path, resolved = verified_binding(root, prefix)

        def read(path):
            if Path(path).resolve() == binding_path:
                return copy.deepcopy(resolved)
            return original_read(path)

        queue.read = read
        try:
            # Backup bytes, source hashes, runtime hashes, plan, scope, old
            # reports, executable bytes and model digest are still checked.
            return original_validate(root, prefix)
        finally:
            queue.read = original_read

    queue.validate_inputs = validate
    try:
        yield
    finally:
        queue.read, queue.validate_inputs = original_read, original_validate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--environment", type=Path)
    parser.add_argument("--environment-receipt", type=Path)
    parser.add_argument("--backup", type=Path)
    parser.add_argument("command", choices=["prepare", "worker", "guard"])
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            require(args.environment is not None and args.environment_receipt is not None and args.backup is not None, "explicit_prepare_locations")
            prepare(args.root, args.prefix, args.environment, args.environment_receipt)
            with resolve_renderer(args.root, args.prefix):
                result = resume.prepare(args.root, args.prefix, args.backup)
            print(json.dumps(result))
        elif args.command == "worker":
            with resolve_renderer(args.root, args.prefix):
                resume.worker(args.root, args.prefix)
        else:
            from .course_archive_gpu import checked_sample
            from .course_gpu_guard import run as guarded

            root = args.root.resolve()
            with resolve_renderer(root, args.prefix):
                resume.inputs(root, args.prefix)
            previous = root / "extraction-thermal-guard.json"
            if previous.exists():
                status = queue.read(previous)
                require(not queue.alive(status), "previous_guard_exited")
                history = root / "wiki/pdf-renderer-resume-guard-history" / (queue.sha(previous.read_bytes()) + ".json")
                history.parent.mkdir(parents=True, exist_ok=True)
                if history.exists():
                    require(history.read_bytes() == previous.read_bytes(), "immutable_previous_guard")
                else:
                    history.write_bytes(previous.read_bytes())
            guarded(root, [sys.executable, "-m", __spec__.name, "--root", str(root), "--prefix", args.prefix, "worker"],
                maximum_power=200, maximum_temperature=70, guard_name="extraction-thermal-guard", sampler=lambda: checked_sample(root))
    except Exception as exc:
        # Public errors contain codes only; preserve diagnostic hashes privately.
        from .course_textbook_whole_sources import failure_status

        status = failure_status(exc)
        message = str(exc)
        if re.fullmatch(r"[A-Za-z0-9_-]{1,180}", message) and message.startswith("pdf_"):
            status["reason_code"] = message
        print(json.dumps(status))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
