"""Portable Windows course extraction environment; course data is separate."""

import argparse
import hashlib
import importlib
import json
import os
import runpy
import subprocess
import sys
import tempfile
import wave
from pathlib import Path, PurePosixPath

BUNDLE = Path(__file__).resolve().parent
POLICY = "offline-windows-course-extraction-environment-v1"


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024**2), b""):
            h.update(block)
    return h.hexdigest()


def inside(relative):
    if not isinstance(relative, str) or "\\" in relative:
        raise ValueError("environment_invalid_relative_path")
    value = PurePosixPath(relative)
    if (
        value.is_absolute()
        or not value.parts
        or value.as_posix() != relative
        or any(
            p in {".", ".."} or ":" in p or p.endswith((".", " ")) for p in value.parts
        )
    ):
        raise ValueError("environment_invalid_relative_path")
    path = BUNDLE.joinpath(*value.parts)
    if not path.resolve().is_relative_to(BUNDLE.resolve()) or path.is_symlink():
        raise ValueError("environment_file_outside_bundle")
    if any(p.is_symlink() for p in path.parents if p != BUNDLE):
        raise ValueError("environment_symlink_not_supported")
    return path


def verify():
    manifest = json.loads((BUNDLE / "extraction-environment.json").read_bytes())
    if manifest["policy"] != POLICY or manifest["source_deletion_allowed"]:
        raise ValueError("environment_manifest_policy_changed")
    plan_path = BUNDLE / "environment-input-plan.json"
    if sha(plan_path) != manifest["input_plan_sha256"]:
        raise ValueError("environment_input_plan_changed")
    plan = json.loads(plan_path.read_bytes())
    expected = {p["path"] for p in plan["files"]} | {
        "environment-input-plan.json",
        "extraction_bootstrap.py",
        "run-environment.ps1",
        "README.md",
    }
    paths, folded = set(), set()
    for row in manifest["files"]:
        path = inside(row["path"])
        if row["path"].casefold() in folded:
            raise ValueError("environment_duplicate_manifest_path")
        folded.add(row["path"].casefold())
        paths.add(row["path"])
        if path.stat().st_size != row["bytes"] or sha(path) != row["sha256"]:
            raise ValueError("environment_payload_changed")
    if paths != expected:
        raise ValueError("environment_incomplete_planned_payload")
    if (
        not {
            "python/python.exe",
            "python/Lib/os.py",
            "ollama/ollama.exe",
            "src/wechat_decrypt_tool/course_knowledge.py",
            "tools/ffmpeg/ffmpeg.exe",
            "tools/poppler/bin/pdftoppm.exe",
        }
        <= paths
    ):
        raise ValueError("environment_required_components_missing")
    return manifest, plan


def activate():
    if (
        Path(sys.executable).resolve() != BUNDLE / "python/python.exe"
        or Path(sys.base_prefix).resolve() != BUNDLE / "python"
    ):
        raise ValueError("environment_requires_bundled_interpreter")
    os.environ.update(
        WECHAT_TOOL_DATA_DIR=str(BUNDLE),
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        HF_HUB_DISABLE_TELEMETRY="1",
        HF_HUB_DISABLE_PROGRESS_BARS="1",
        OLLAMA_MODELS=str(BUNDLE / "ollama-models"),
        OLLAMA_HOST="127.0.0.1:11435",
    )
    native = [BUNDLE / "tools/poppler/bin", BUNDLE / "tools/ffmpeg"]
    os.environ["PATH"] = (
        os.pathsep.join(map(str, native)) + os.pathsep + os.environ.get("PATH", "")
    )
    sys.path[:0] = [str(BUNDLE / "site-packages"), str(BUNDLE / "src")]


def probe(manifest, plan):
    # Block reads of every original component and all Python network connections.
    original_roots = [Path(c["source_root"]).resolve() for c in plan["components"]]
    original_access = []
    network_access = []

    def audit(event, args):
        if event == "socket.connect":
            network_access.append(event)
            raise RuntimeError("environment_probe_network_forbidden")
        if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
            value = Path(os.fsdecode(args[0])).resolve()
            if any(value.is_relative_to(root) for root in original_roots):
                original_access.append(str(value))
                raise RuntimeError("environment_probe_original_installation_forbidden")

    sys.addaudithook(audit)
    from wechat_decrypt_tool.qwen_gpu_runtime import activate_for_worker, installed

    if not installed():
        raise ValueError("environment_gpu_runtime_manifest_not_installed")
    activate_for_worker()
    packages = [
        "torch",
        "transformers",
        "av",
        "numpy",
        "pypdf",
        "openpyxl",
        "fontTools",
        "PIL",
        "fastapi",
        "psutil",
    ]
    versions = {}
    for name in packages:
        module = importlib.import_module(name)
        if not Path(module.__file__).resolve().is_relative_to(BUNDLE):
            raise ValueError("environment_import_outside_bundle")
        versions[name] = getattr(module, "__version__", None)
    import torch

    assert not torch.cuda.is_initialized()
    assert (torch.tensor([[1.0, 2.0]]) @ torch.tensor([[3.0], [4.0]])).item() == 11.0
    from transformers import AutoProcessor

    processor = AutoProcessor.from_pretrained(
        BUNDLE / "voice_models/qwen3-asr-06b-hf", local_files_only=True
    )
    from wechat_decrypt_tool.asr_backends import read_audio
    from pypdf import PdfWriter
    from PIL import Image

    with tempfile.TemporaryDirectory(prefix="course-env-synthetic-") as temporary:
        temporary = Path(temporary)
        audio = temporary / "silence.wav"
        with wave.open(str(audio), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(16000)
            stream.writeframes(b"\0\0" * 16000)
        assert len(read_audio(str(audio))) == 16000
        pdf = temporary / "blank.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        with pdf.open("wb") as stream:
            writer.write(stream)
        rendered = temporary / "page"
        result = subprocess.run(
            [
                str(BUNDLE / "tools/poppler/bin/pdftoppm.exe"),
                "-f",
                "1",
                "-l",
                "1",
                "-scale-to",
                "128",
                "-singlefile",
                "-png",
                str(pdf),
                str(rendered),
            ],
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise ValueError("environment_poppler_synthetic_failed")
        with Image.open(rendered.with_suffix(".png")) as image:
            image.load()
            assert image.size == (128, 128)
    for relative, args in [
        ("tools/ffmpeg/ffmpeg.exe", ["-version"]),
        ("ollama/ollama.exe", ["--version"]),
    ]:
        result = subprocess.run(
            [str(BUNDLE / relative), *args], capture_output=True, timeout=20
        )
        if result.returncode != 0:
            raise ValueError("environment_native_tool_version_failed")
    outside = []
    for name, module in list(sys.modules.items()):
        origin = getattr(module, "__file__", None)
        if (
            origin
            and origin not in {"built-in", "frozen"}
            and not Path(origin).resolve().is_relative_to(BUNDLE)
        ):
            outside.append(name)
    if outside or original_access or network_access:
        raise ValueError("environment_probe_not_isolated")
    assert not torch.cuda.is_initialized()
    return {
        "state": "isolated_cpu_and_native_synthetic_probe_verified",
        "files_verified": len(manifest["files"]),
        "dependencies": versions,
        "processor_class": type(processor).__name__,
        "synthetic_audio_samples": 16000,
        "synthetic_pdf_page_rendered": True,
        "native_programs_started": 3,
        "original_installation_access_attempts": len(original_access),
        "network_connection_attempts": len(network_access),
        "outside_module_count": len(outside),
        "gpu_context_initialized": False,
        "gpu_inference_verified": False,
        "course_data_included": False,
        "source_deletion_allowed": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode", choices=["verify", "probe", "course-cli", "ollama-serve"]
    )
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    manifest, plan = verify()
    activate()
    if args.mode == "verify":
        print(
            json.dumps(
                {
                    "state": "environment_files_verified",
                    "files": len(manifest["files"]),
                    "source_deletion_allowed": False,
                }
            )
        )
    elif args.mode == "probe":
        print(json.dumps(probe(manifest, plan)))
    elif args.mode == "course-cli":
        sys.argv = ["course_knowledge", *args.arguments]
        runpy.run_module("wechat_decrypt_tool.course_knowledge", run_name="__main__")
    else:
        raise SystemExit(subprocess.call([str(BUNDLE / "ollama/ollama.exe"), "serve"]))


if __name__ == "__main__":
    main()
