"""Offline, hash-verified ASR/Ollama weights for a portable course archive.

Only explicitly selected model files are read. Ollama account keys, history,
download caches and course data are never bundled. Python, Ollama executables,
GPU drivers and renderer runtimes remain separate installation requirements.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import sys
import uuid
from pathlib import Path, PurePosixPath

from .asr_models import SPECS

ASR_MODEL = "qwen3-asr-06b-hf"
MAX_BLOB_BYTES = 32 * 1024**3
SHA = re.compile(r"[a-f0-9]{64}")


def safe_relative(value):
    if not isinstance(value, str) or "\\" in value or ":" in value:
        raise ValueError("Invalid model bundle relative path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or any(x in (".", "..") for x in path.parts)
    ):
        raise ValueError("Model bundle path escapes its root")
    if path.as_posix() != value:
        raise ValueError("Noncanonical model bundle path")
    return value


def inside(root, relative):
    root = Path(root).resolve()
    path = root / safe_relative(relative)
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError("Model bundle asset escapes its declared root")
    return path


def digest_file(path, checkpoint=lambda: None):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while block := stream.read(4 * 1024**2):
            checkpoint()
            hasher.update(block)
    return hasher.hexdigest()


def ollama_layout(model, expected_manifest, raw):
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*:[a-z0-9][a-z0-9._-]*", model):
        raise ValueError("Use an explicit local Ollama library model and tag")
    if not isinstance(expected_manifest, str) or not SHA.fullmatch(expected_manifest):
        raise ValueError("Expected local Ollama manifest SHA-256 is required")
    if len(raw) > 1024**2 or hashlib.sha256(raw).hexdigest() != expected_manifest:
        raise ValueError("Local Ollama manifest differs from the selected model")
    try:
        manifest = json.loads(raw)
    except (ValueError, UnicodeError):
        raise ValueError("Invalid local Ollama model manifest") from None
    if not isinstance(manifest, dict):
        raise ValueError("Invalid local Ollama model manifest structure")
    layers = manifest.get("layers")
    if not isinstance(layers, list) or not 0 < len(layers) <= 64:
        raise ValueError("Ollama layer count exceeds bounded local scope")
    name, tag = model.split(":")
    expected = {
        f"ollama/manifests/registry.ollama.ai/library/{name}/{tag}": (
            len(raw),
            expected_manifest,
        )
    }
    for layer in [manifest.get("config"), *layers]:
        if not isinstance(layer, dict) or not re.fullmatch(
            r"sha256:[a-f0-9]{64}", str(layer.get("digest", ""))
        ):
            raise ValueError("Ollama layer digest is not a local SHA-256 reference")
        sha, size = layer["digest"][7:], layer.get("size")
        if type(size) is not int or not 0 < size <= MAX_BLOB_BYTES:
            raise ValueError("Invalid Ollama blob byte count")
        target = "ollama/blobs/sha256-" + sha
        if target in expected and expected[target] != (size, sha):
            raise ValueError("Duplicate Ollama blob sizes disagree")
        expected[target] = (size, sha)
    return expected


def model_plan(asr_root, ollama_root, model, expected_manifest):
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*:[a-z0-9][a-z0-9._-]*", model):
        raise ValueError("Use an explicit local Ollama library model and tag")
    if not isinstance(expected_manifest, str) or not SHA.fullmatch(expected_manifest):
        raise ValueError("Expected local Ollama manifest SHA-256 is required")
    asr_root, ollama_root = Path(asr_root).resolve(), Path(ollama_root).resolve()
    assets = []

    def asset(source, target, size, sha):
        safe_relative(target)
        if (
            type(size) is not int
            or not 0 < size <= MAX_BLOB_BYTES
            or not isinstance(sha, str)
            or not SHA.fullmatch(sha)
            or not source.is_file()
            or source.stat().st_size != size
        ):
            raise ValueError("Model asset size, digest or availability differs")
        assets.append(
            {"source": source, "target": target, "bytes": size, "sha256": sha}
        )

    for name, expected in SPECS[ASR_MODEL]["files"].items():
        asset(
            inside(asr_root, name),
            f"asr/{ASR_MODEL}/{name}",
            expected["size"],
            expected["sha256"],
        )
    name, tag = model.split(":")
    relative = f"manifests/registry.ollama.ai/library/{name}/{tag}"
    manifest_path = inside(ollama_root, relative)
    if not manifest_path.is_file() or manifest_path.stat().st_size > 1024**2:
        raise ValueError("Local Ollama model manifest is absent or exceeds limit")
    native_source = manifest_path
    raw = manifest_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_manifest:
        # New Ollama tags can wrap a platform-selected immutable manifest.
        # Export that exact selected manifest into a normal legacy tag location,
        # rather than downloading or promising other advertised platform variants.
        try:
            wrapper = json.loads(raw)
            entries = wrapper.get("layers", []) if isinstance(wrapper, dict) else []
            if not isinstance(entries, list):
                raise ValueError("Invalid wrapped model layer scope")
            matches = [
                x
                for x in entries
                if isinstance(x, dict)
                and x.get("digest") == "sha256:" + expected_manifest
                and x.get("mediaType")
                == "application/vnd.docker.distribution.manifest.v2+json"
            ]
        except (ValueError, UnicodeError):
            matches = []
        native_source = inside(ollama_root, "blobs/sha256-" + expected_manifest)
        if (
            len(matches) != 1
            or not native_source.is_file()
            or native_source.stat().st_size > 1024**2
            or native_source.stat().st_size != matches[0].get("size")
        ):
            raise ValueError("Local Ollama manifest differs from the selected model")
        raw = native_source.read_bytes()
    for target, (size, sha) in ollama_layout(model, expected_manifest, raw).items():
        source = (
            native_source
            if target.startswith("ollama/manifests/")
            else inside(ollama_root, target.removeprefix("ollama/"))
        )
        asset(source, target, size, sha)
    return assets


def python_environment():
    def packages(path=None):
        return sorted(
            {
                (str(d.metadata["Name"]), str(d.version))
                for d in importlib.metadata.distributions(
                    **({"path": path} if path is not None else {})
                )
                if d.metadata["Name"]
            }
        )

    from .qwen_gpu_runtime import runtime_root

    overlay = runtime_root()
    return {
        "python": platform.python_version(),
        "platform": sys.platform,
        "machine": platform.machine(),
        "base_packages": packages(),
        "gpu_overlay_packages": packages([str(overlay)]) if overlay.is_dir() else [],
        "gpu_overlay_precedes_base_packages_in_asr_worker": True,
        "executables_drivers_packages_and_renderers_included": False,
        "asr_device": "cuda",
        "asr_precision": "float32",
    }


def _copy(source, target, expected, *, checkpoint, reserve, progress_root):
    before = source.stat()
    if before.st_size != expected["bytes"]:
        raise RuntimeError("Model source size changed before copy")
    if shutil.disk_usage(progress_root).free < reserve + before.st_size:
        raise InterruptedError("Model bundle disk reserve would be exceeded")
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + "." + uuid.uuid4().hex + ".part")
    hasher = hashlib.sha256()
    try:
        with source.open("rb") as src, part.open("xb") as dst:
            while block := src.read(4 * 1024**2):
                checkpoint()
                if shutil.disk_usage(progress_root).free < reserve + len(block):
                    raise InterruptedError("Model copy reached reserved disk capacity")
                dst.write(block)
                hasher.update(block)
        after = source.stat()
        if (before.st_size, before.st_mtime_ns) != (
            after.st_size,
            after.st_mtime_ns,
        ) or hasher.hexdigest() != expected["sha256"]:
            raise RuntimeError("Model bytes changed or differ from frozen digest")
        os.replace(part, target)
    finally:
        if part.exists():
            part.unlink()


def create_bundle(
    destination,
    asr_root,
    ollama_root,
    model,
    expected_manifest,
    *,
    reserve_gib=32,
    checkpoint=lambda: None,
    progress=lambda **fields: None,
):
    from .course_knowledge import atomic_json, schema_lease

    destination = Path(destination).resolve()
    for source in (Path(asr_root).resolve(), Path(ollama_root).resolve()):
        if destination.is_relative_to(source) or source.is_relative_to(destination):
            raise ValueError("Model bundle must be separate from installed models")
    plan = model_plan(asr_root, ollama_root, model, expected_manifest)
    required = sum(
        {
            x["sha256"]: x["bytes"]
            for x in plan
            if not inside(
                destination, f"objects/{x['sha256'][:2]}/{x['sha256']}"
            ).exists()
        }.values()
    )
    existing_parent = destination
    while not existing_parent.exists():
        existing_parent = existing_parent.parent
    reserve = int(reserve_gib * 1024**3)
    if reserve < 0 or shutil.disk_usage(existing_parent).free < reserve + required:
        raise InterruptedError("Insufficient model bundle capacity beyond reserve")
    destination.mkdir(parents=True, exist_ok=True)
    with schema_lease(destination / "bundle.lock", timeout=0):
        manifest_path = destination / "manifest.json"
        if manifest_path.exists():
            result = verify_bundle(destination, checkpoint=checkpoint)
            current = json.loads(manifest_path.read_text("utf-8"))
            if (
                current["model"] != model
                or current["ollama_manifest_sha256"] != expected_manifest
            ):
                raise ValueError(
                    "Existing bundle belongs to a different model revision"
                )
            expected = {(x["target"], x["bytes"], x["sha256"]) for x in plan}
            actual = {(x["target"], x["bytes"], x["sha256"]) for x in current["assets"]}
            if actual != expected:
                raise ValueError(
                    "Existing model bundle differs from current frozen scope"
                )
            return {**result, "cached": True}
        assets = []
        for index, entry in enumerate(plan):
            checkpoint()
            relative = "objects/" + entry["sha256"][:2] + "/" + entry["sha256"]
            target = inside(destination, relative)
            if target.exists():
                if (
                    target.stat().st_size != entry["bytes"]
                    or digest_file(target, checkpoint) != entry["sha256"]
                ):
                    raise RuntimeError("Existing immutable model bundle object changed")
            else:
                _copy(
                    entry["source"],
                    target,
                    entry,
                    checkpoint=checkpoint,
                    reserve=reserve,
                    progress_root=destination,
                )
            assets.append({k: entry[k] for k in ("target", "bytes", "sha256")})
            assets[-1]["object"] = relative
            progress(
                completed=index + 1,
                total=len(plan),
                copied_unique_bytes=sum(
                    {x["sha256"]: x["bytes"] for x in assets}.values()
                ),
            )
        atomic_json(
            manifest_path,
            {
                "version": 1,
                "model": model,
                "asr_model": ASR_MODEL,
                "asr_asset_revision": SPECS[ASR_MODEL].get("revision"),
                "ollama_manifest_sha256": expected_manifest,
                "ollama_storage_policy": "Exact selected native manifest exported under a legacy tag; only its config and layers. Other advertised platform variants are not included.",
                "assets": assets,
                "environment": python_environment(),
                "model_weights_only": True,
                "course_archive_included": False,
                "source_deletion_allowed": False,
            },
        )
        return {**verify_bundle(destination, checkpoint=checkpoint), "cached": False}


def verify_bundle(bundle, *, checkpoint=lambda: None):
    bundle = Path(bundle).resolve()
    path = inside(bundle, "manifest.json")
    if path.stat().st_size > 4 * 1024**2:
        raise ValueError("Model bundle manifest exceeds limit")
    manifest = json.loads(path.read_text("utf-8"))
    if manifest.get("version") != 1 or manifest.get("model_weights_only") is not True:
        raise ValueError("Unsupported model bundle")
    assets = manifest.get("assets")
    if not isinstance(assets, list) or not 0 < len(assets) <= 256:
        raise ValueError("Invalid model bundle asset scope")
    seen, objects = set(), {}
    for entry in assets:
        target = safe_relative(entry["target"])
        if (
            not target.startswith(
                (f"asr/{ASR_MODEL}/", "ollama/blobs/", "ollama/manifests/")
            )
            or target in seen
        ):
            raise ValueError("Unexpected or duplicated model restoration path")
        seen.add(target)
        sha = entry["sha256"]
        size = entry["bytes"]
        if (
            not isinstance(sha, str)
            or not SHA.fullmatch(sha)
            or type(size) is not int
            or not 0 < size <= MAX_BLOB_BYTES
        ):
            raise ValueError("Invalid model bundle object metadata")
        if entry["object"] != f"objects/{sha[:2]}/{sha}":
            raise ValueError("Model object does not match its digest")
        source = inside(bundle, entry["object"])
        if sha in objects and objects[sha] != size:
            raise ValueError("Conflicting model object byte counts")
        if sha not in objects:
            if source.stat().st_size != size:
                raise RuntimeError("Model bundle object hash or size differs")
            objects[sha] = size
    if manifest.get("asr_model") != ASR_MODEL:
        raise ValueError("Model bundle ASR scope differs")
    model = manifest.get("model", "")
    if not isinstance(model, str) or not re.fullmatch(
        r"[a-z0-9][a-z0-9._-]*:[a-z0-9][a-z0-9._-]*", model
    ):
        raise ValueError("Invalid bundled Ollama model identity")
    name, tag = model.split(":")
    native_target = f"ollama/manifests/registry.ollama.ai/library/{name}/{tag}"
    native = next((x for x in assets if x["target"] == native_target), None)
    if native is None or native["bytes"] > 1024**2:
        raise ValueError("Bundled native Ollama manifest is missing")
    expected = {
        f"asr/{ASR_MODEL}/{name}": (entry["size"], entry["sha256"])
        for name, entry in SPECS[ASR_MODEL]["files"].items()
    }
    expected.update(
        ollama_layout(
            model,
            manifest.get("ollama_manifest_sha256"),
            inside(bundle, native["object"]).read_bytes(),
        )
    )
    actual = {x["target"]: (x["bytes"], x["sha256"]) for x in assets}
    if actual != expected:
        raise ValueError("Model bundle omits or alters required ASR/Ollama files")
    for sha, size in objects.items():
        source = inside(bundle, f"objects/{sha[:2]}/{sha}")
        if source.stat().st_size != size or digest_file(source, checkpoint) != sha:
            raise RuntimeError("Model bundle object hash or size differs")
    return {
        "bundle": str(bundle),
        "manifest_sha256": digest_file(path, checkpoint),
        "verified_assets": len(assets),
        "verified_unique_bytes": sum(objects.values()),
        "model_weights_only": True,
        "source_deletion_allowed": False,
    }


def restore_bundle(bundle, destination, *, reserve_gib=32, checkpoint=lambda: None):
    bundle, destination = Path(bundle).resolve(), Path(destination).resolve()
    if destination.is_relative_to(bundle) or bundle.is_relative_to(destination):
        raise ValueError("Restore must be separate from bundle")
    if destination.exists() and (
        not destination.is_dir() or any(destination.iterdir())
    ):
        raise ValueError("Model restore destination must be empty")
    verified = verify_bundle(bundle, checkpoint=checkpoint)
    manifest = json.loads((bundle / "manifest.json").read_text("utf-8"))
    required = sum(x["bytes"] for x in manifest["assets"])
    existing = destination
    while not existing.exists():
        existing = existing.parent
    reserve = int(reserve_gib * 1024**3)
    if reserve < 0 or shutil.disk_usage(existing).free < required + reserve:
        raise InterruptedError("Insufficient independent model restore capacity")
    destination.mkdir(parents=True, exist_ok=True)
    for entry in manifest["assets"]:
        checkpoint()
        target = inside(destination, entry["target"])
        _copy(
            inside(bundle, entry["object"]),
            target,
            entry,
            checkpoint=checkpoint,
            reserve=reserve,
            progress_root=destination,
        )
        if digest_file(target, checkpoint) != entry["sha256"]:
            raise RuntimeError("Independent restored model hash differs")
    return {
        **verified,
        "restored": str(destination),
        "restored_files": len(manifest["assets"]),
        "executables_and_drivers_still_required": True,
    }


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("create")
    build.add_argument("--destination", type=Path, required=True)
    build.add_argument("--asr-root", type=Path, required=True)
    build.add_argument("--ollama-root", type=Path, required=True)
    build.add_argument("--model", default="qwen3.5:9b")
    build.add_argument("--expected-manifest", required=True)
    build.add_argument("--reserve-gib", type=float, default=32)
    for name in ("verify", "restore"):
        command = commands.add_parser(name)
        command.add_argument("--bundle", type=Path, required=True)
        if name == "restore":
            command.add_argument("--destination", type=Path, required=True)
            command.add_argument("--reserve-gib", type=float, default=32)
    args = parser.parse_args()
    if args.command == "create":
        result = create_bundle(
            args.destination,
            args.asr_root,
            args.ollama_root,
            args.model,
            args.expected_manifest,
            reserve_gib=args.reserve_gib,
        )
    elif args.command == "restore":
        result = restore_bundle(
            args.bundle, args.destination, reserve_gib=args.reserve_gib
        )
    else:
        result = verify_bundle(args.bundle)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
