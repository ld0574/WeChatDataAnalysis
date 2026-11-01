"""Synthetic GPU recovery checks using only a restored extraction environment.

The caller must hold the archive GPU lease and supervise this process tree with
the thermal guard. This harness never reads course content or publishes text.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import io
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

PORT = 11437
MODEL = "qwen3.5:9b"
PHRASE = "the quick brown fox jumps over the lazy dog"


def loopback_endpoint(address):
    return (
        isinstance(address, tuple)
        and len(address) == 2
        and address == ("127.0.0.1", PORT)
    )


def validate_asr(text, *, all_cuda, all_fp32):
    if not all_cuda or not all_fp32:
        raise ValueError("environment_gpu_asr_parameters_not_cuda_fp32")
    if re.findall("[a-z]+", text.lower()) != PHRASE.split():
        raise ValueError("environment_gpu_asr_synthetic_phrase_mismatch")


def validate_sentinel(response, processes, expected, model_digest):
    value = json.loads(response["message"]["content"])
    if value != {"value": expected} or type(value.get("value")) is not int:
        raise ValueError("environment_gpu_sentinel_mismatch")
    if not any(
        p.get("digest") == model_digest and p.get("size_vram", 0) > 0
        for p in processes
    ):
        raise ValueError("environment_gpu_model_not_resident_on_gpu")


def isolated_runtime(bundle, expected_manifest):
    manifest_path = bundle / "extraction-environment.json"
    if hashlib.sha256(manifest_path.read_bytes()).hexdigest() != expected_manifest:
        raise ValueError("environment_gpu_manifest_anchor_changed")
    spec = importlib.util.spec_from_file_location(
        "extraction_bootstrap", bundle / "extraction_bootstrap.py"
    )
    bootstrap = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bootstrap)
    manifest, plan = bootstrap.verify()
    bootstrap.activate()
    roots = [Path(c["source_root"]).resolve() for c in plan["components"]]
    blocked = []

    def audit(event, args):
        if event == "socket.connect" and not loopback_endpoint(args[1]):
            blocked.append("network")
            raise RuntimeError("environment_gpu_external_network_forbidden")
        if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
            path = Path(os.fsdecode(args[0])).resolve()
            if any(path.is_relative_to(root) for root in roots):
                blocked.append("original_installation")
                raise RuntimeError("environment_gpu_original_installation_forbidden")

    sys.addaudithook(audit)
    from wechat_decrypt_tool.qwen_gpu_runtime import activate_for_worker, installed

    if not installed():
        raise ValueError("environment_gpu_runtime_not_installed")
    activate_for_worker()
    return bootstrap, manifest, roots, blocked


def check_isolation(bundle, bootstrap, roots, blocked, processes):
    import psutil

    outside = []
    for name, module in list(sys.modules.items()):
        origin = getattr(module, "__file__", None)
        if name != "__main__" and origin and not Path(origin).resolve().is_relative_to(bundle):
            outside.append(name)
    native = []
    for process in [psutil.Process(), *processes]:
        native.extend(bootstrap.original_native_mappings(
            [row.path for row in process.memory_maps()], roots
        ))
    if outside or native or blocked:
        raise ValueError("environment_gpu_probe_not_isolated")
    return {"outside_module_count": 0, "original_native_mapping_count": 0,
            "blocked_access_attempts": 0}


def asr_probe(bundle, work):
    import torch
    from wechat_decrypt_tool.asr_backends import QwenGpuBackend, read_audio

    audio = work / "synthetic-speech.wav"
    # Windows SAPI is a platform requirement. No sound is sent to speakers.
    command = (
        "$voice=New-Object -ComObject SAPI.SpVoice;"
        "$stream=New-Object -ComObject SAPI.SpFileStream;"
        "$stream.Open($env:COURSE_GPU_SYNTHETIC_WAV,3,$false);"
        "$voice.AudioOutputStream=$stream;"
        "$null=$voice.Speak('The quick brown fox jumps over the lazy dog');"
        "$stream.Close()"
    )
    subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                   env={**os.environ, "COURSE_GPU_SYNTHETIC_WAV": str(audio)},
                   check=True, capture_output=True, timeout=30, creationflags=0x08000000)
    model = QwenGpuBackend(bundle / "voice_models/qwen3-asr-06b-hf", 2)
    model.use_full_precision()
    parameters = list(model.model.parameters())
    all_cuda = bool(parameters) and all(p.device.type == "cuda" for p in parameters)
    all_fp32 = bool(parameters) and all(p.dtype == torch.float32 for p in parameters)
    torch.cuda.reset_peak_memory_stats()
    started = time.monotonic()
    text = model.transcribe(read_audio(str(audio)), "en")
    validate_asr(text, all_cuda=all_cuda, all_fp32=all_fp32)
    return {"actual_gpu_inference_completed": True, "all_parameters_cuda": all_cuda,
            "all_parameters_float32": all_fp32, "synthetic_phrase_matches": True,
            "transcript_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "peak_gpu_bytes": torch.cuda.max_memory_allocated(),
            "inference_seconds": time.monotonic() - started}, []


def ollama_probe(bundle, work, isolation):
    import httpx
    import psutil
    from PIL import Image, ImageDraw, ImageFont

    with socket.socket() as check:
        check.bind(("127.0.0.1", PORT))
    environment = {**os.environ, "OLLAMA_HOST": f"127.0.0.1:{PORT}",
                   "OLLAMA_MODELS": str(bundle / "ollama-models"), "OLLAMA_NO_CLOUD": "1",
                   "OLLAMA_KEEP_ALIVE": "0", "OLLAMA_NUM_PARALLEL": "1",
                   "OLLAMA_CONTEXT_LENGTH": "4096", "OLLAMA_FLASH_ATTENTION": "false"}
    with (work / "ollama.stdout.log").open("wb") as stdout, (work / "ollama.stderr.log").open("wb") as stderr:
        process = subprocess.Popen([str(bundle / "ollama/ollama.exe"), "serve"],
                                   cwd=work, env=environment, stdout=stdout, stderr=stderr,
                                   creationflags=0x08000000)
        owner = psutil.Process(process.pid)
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{PORT}", trust_env=False, timeout=240) as client:
                deadline = time.monotonic() + 90
                while True:
                    if process.poll() is not None:
                        raise ValueError("environment_gpu_ollama_server_exited")
                    try:
                        response = client.get("/api/tags", timeout=2)
                        response.raise_for_status()
                        tags = response.json()["models"]
                        break
                    except httpx.TransportError:
                        if time.monotonic() >= deadline:
                            raise ValueError("environment_gpu_ollama_startup_timeout") from None
                        time.sleep(0.5)
                selected = [r for r in tags if r["name"] == MODEL]
                candidates = list((bundle / "ollama-models").glob("manifests*/**/qwen3.5/9b"))
                expected = {hashlib.sha256(p.read_bytes()).hexdigest() for p in candidates}
                if len(selected) != 1 or selected[0]["digest"] not in expected:
                    raise ValueError("environment_gpu_ollama_restored_manifest_mismatch")
                model_digest = selected[0]["digest"]
                common = {"model": MODEL, "stream": False, "think": False, "keep_alive": "30s",
                          "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 128, "num_gpu": 99},
                          "format": {"type": "object", "properties": {"value": {"type": "integer"}},
                                     "required": ["value"], "additionalProperties": False}}

                def infer(message, expected_value):
                    reply = client.post("/api/chat", json={**common, "messages": [message]})
                    reply.raise_for_status()
                    resident = client.get("/api/ps")
                    resident.raise_for_status()
                    validate_sentinel(reply.json(), resident.json()["models"], expected_value, model_digest)

                infer({"role": "user", "content": "Return the integer 733 in the value field. Synthetic local recovery test."}, 733)
                image = Image.new("RGB", (640, 240), "white")
                ImageDraw.Draw(image).text((50, 45), "742", fill="black", font=ImageFont.load_default(size=120))
                buffer = io.BytesIO()
                image.save(buffer, format="PNG")
                raw = buffer.getvalue()
                infer({"role": "user", "content": "Read the three digits in this synthetic image. Return their integer in the value field.",
                       "images": [base64.b64encode(raw).decode()]}, 742)
                native_isolation = isolation([owner, *owner.children(recursive=True)])
                return {"restored_model_digest": model_digest, "text_gpu_sentinel_passed": True,
                        "vision_gpu_sentinel_passed": True,
                        "synthetic_image_sha256": hashlib.sha256(raw).hexdigest(),
                        **native_isolation}, []
        finally:
            owned = [(p, p.create_time()) for p in [*owner.children(recursive=True), owner]] if process.poll() is None else []
            for child, created in owned:
                try:
                    if child.create_time() == created:
                        child.terminate()
                except psutil.NoSuchProcess:
                    pass
            _, alive = psutil.wait_procs([p for p, _ in owned], timeout=10)
            for child in alive:
                if any(child.pid == p.pid and child.create_time() == created for p, created in owned):
                    child.kill()
            psutil.wait_procs(alive, timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--mode", choices=["asr", "ollama", "all"], required=True)
    args = parser.parse_args()
    bundle, work = args.bundle.resolve(), args.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    bootstrap, manifest, roots, blocked = isolated_runtime(bundle, args.manifest_sha256)
    def isolation(processes):
        return check_isolation(bundle, bootstrap, roots, blocked, processes)

    if args.mode == "all":
        result, _ = asr_probe(bundle, work)
        import gc
        import torch

        gc.collect()
        torch.cuda.empty_cache()
        vision, processes = ollama_probe(bundle, work, isolation)
        result.update(vision)
    else:
        result, processes = asr_probe(bundle, work) if args.mode == "asr" else ollama_probe(bundle, work, isolation)
    result.update(isolation(processes))
    receipt = {"state": "isolated_restored_gpu_synthetic_probe_verified", "mode": args.mode,
               "manifest_sha256": args.manifest_sha256, "files_verified": len(manifest["files"]),
               "bundle": str(bundle), "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               "course_content_used": False, "source_deletion_allowed": False,
               "updated": time.time(), **result}
    (work / (args.mode + "-validation.json")).write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps({"state": receipt["state"], "mode": args.mode}))


if __name__ == "__main__":
    main()
