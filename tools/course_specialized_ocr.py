"""Finite CPU-only OCR drafts in a separate RapidOCR environment.

Asset download and recognition are separate commands. Recognition denies socket
connections, uses only explicit hash-checked local weights, and never changes
the archive, GPU role budgets or publication state. Output contains counts only.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import socket
import time
import urllib.request

MODELS = {
    "det": ("ch_PP-OCRv5_det_server.onnx", "0f8846b1d4bba223a2a2f9d9b44022fbc22cc019051a602b41a7fda9667e4cad"),
    "rec": ("ch_PP-OCRv5_rec_server.onnx", "e09385400eaaaef34ceff54aeb7c4f0f1fe014c27fa8b9905d4709b65746562a"),
}
MODEL_BASE = "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.10.0/onnx/PP-OCRv5"
POLICY = "finite-offline-ppocrv5-server-private-drafts-cpu-v1"


def require(ok, code):
    if not ok:
        raise ValueError("requires_specialized_ocr_" + code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def save(path, value):
    raw = json.dumps(value, ensure_ascii=False, indent=2).encode()
    with Path(path).open("xb") as stream:
        stream.write(raw)


def failure_status(exc):
    message = str(exc)
    safe = re.fullmatch(r"requires_specialized_ocr_[a-z0-9_]{1,150}", message)
    return {"state": "paused" if isinstance(exc, InterruptedError) else "stopped",
        "error_type": type(exc).__name__, "reason_code": message if safe else None,
        "error_sha256": sha(message.encode())}


def reserve(folder):
    if os.name == "nt":
        for volume in {Path(folder).resolve().anchor, Path.home().anchor}:
            require(shutil.disk_usage(volume).free >= 32 * 1024**3, "32gib_each_volume")


def assets(folder):
    folder = Path(folder).resolve()
    reserve(folder)
    folder.mkdir(parents=True, exist_ok=True)
    result = {}
    for task, (name, expected) in MODELS.items():
        target = folder / name
        if not target.exists():
            partial = folder / (name + ".partial")
            require(not partial.exists(), "retained_partial_download_needs_explicit_recovery")
            with urllib.request.urlopen(f"{MODEL_BASE}/{task}/{name}", timeout=45) as response, partial.open("xb") as stream:
                total = 0
                while chunk := response.read(1024 * 1024):
                    reserve(folder)
                    total += len(chunk)
                    require(total <= 500 * 1024**2, "bounded_model_asset")
                    stream.write(chunk)
            require(sha(partial.read_bytes()) == expected, "download_matches_published_model_hash")
            partial.rename(target)
        require(sha(target.read_bytes()) == expected, "same_local_model_asset")
        result[task] = {"name": name, "sha256": expected, "bytes": target.stat().st_size}
    record = folder / "models.json"
    value = {"policy": POLICY, "models": result, "rapidocr": "3.10.0", "onnxruntime": "1.28.0"}
    if record.exists():
        require(json.loads(record.read_bytes()) == value, "same_model_manifest")
    else:
        save(record, value)
    return {"state": "local_specialized_ocr_assets_verified", "models": len(result),
        "bytes": sum(row["bytes"] for row in result.values()), "gpu_calls": 0}


@contextmanager
def network_denied():
    originals = socket.socket.connect, socket.socket.connect_ex

    def deny(*_args, **_kwargs):
        raise RuntimeError("requires_specialized_ocr_network_forbidden_during_recognition")

    socket.socket.connect = socket.socket.connect_ex = deny
    try:
        yield
    finally:
        socket.socket.connect, socket.socket.connect_ex = originals


def configuration(folder):
    folder = Path(folder).resolve()
    for name, expected in MODELS.values():
        require(sha((folder / name).read_bytes()) == expected, "same_offline_weights")
    return {"Global.use_cls": False, "Global.log_level": "critical", "Global.text_score": 0.0,
        "Global.max_side_len": 2560, "Global.model_root_dir": str(folder),
        "EngineConfig.onnxruntime.intra_op_num_threads": 2,
        "EngineConfig.onnxruntime.inter_op_num_threads": 1,
        "EngineConfig.onnxruntime.use_cuda": False, "EngineConfig.onnxruntime.use_dml": False,
        "EngineConfig.onnxruntime.use_cann": False, "EngineConfig.onnxruntime.use_coreml": False,
        "Det.ocr_version": "PP-OCRv5", "Det.model_type": "server", "Det.lang_type": "ch",
        "Det.model_path": str(folder / MODELS["det"][0]), "Det.limit_side_len": 1920, "Det.limit_type": "max",
        "Det.mean": [0.485, 0.456, 0.406], "Det.std": [0.229, 0.224, 0.225],
        "Rec.ocr_version": "PP-OCRv5", "Rec.model_type": "server", "Rec.lang_type": "ch",
        "Rec.model_path": str(folder / MODELS["rec"][0])}


def rows(result, width, height):
    boxes, texts, scores = result.boxes, result.txts, result.scores
    if boxes is None or texts is None or scores is None:
        require(boxes is None and texts is None and scores is None, "consistent_empty_recognition")
        return []
    require(len(boxes) == len(texts) == len(scores), "all_returned_lines_retained")
    output = []
    for box, text, score in zip(boxes, texts, scores, strict=True):
        points = [[float(x), float(y)] for x, y in box]
        require(len(points) == 4 and all(0 <= x <= width and 0 <= y <= height for x, y in points)
            and isinstance(text, str) and 0 <= float(score) <= 1, "bounded_original_coordinates_and_scores")
        output.append({"text": text, "polygon": points, "confidence": float(score)})
    return output


def recognize(folder, output, pictures, *, factory=None):
    from PIL import Image
    import onnxruntime

    require(importlib.metadata.version("rapidocr") == "3.10.0"
        and importlib.metadata.version("onnxruntime") == "1.28.0", "pinned_isolated_packages")
    output = Path(output).resolve()
    reserve(output)
    require(not output.exists() and 1 <= len(pictures) <= 6, "finite_once_only_draft_scope")
    params, inputs = configuration(folder), []
    for path in pictures:
        path = Path(path).resolve()
        require(path.is_file() and not path.is_symlink(), "regular_retained_image")
        with Image.open(path) as image:
            width, height = image.size
        require(0 < width <= 2560 and 0 < height <= 2560, "bounded_complete_native_frame")
        inputs.append({"path": str(path), "sha256": sha(path.read_bytes()), "width": width, "height": height})
    require(len({row["sha256"] for row in inputs}) == len(inputs), "distinct_finite_images")
    output.mkdir(parents=True)
    save(output / "started.json", {"policy": POLICY, "code_sha256": sha(Path(__file__).read_bytes()),
        "inputs": inputs, "parameters": params, "gpu_calls": 0, "network_connections_allowed": False})
    try:
        if factory is None:
            def factory(*, params):
                from rapidocr import RapidOCR, OCRVersion, ModelType

                converted = dict(params)
                for stage in ("Det", "Rec"):
                    converted[stage + ".ocr_version"] = OCRVersion(converted[stage + ".ocr_version"])
                    converted[stage + ".model_type"] = ModelType(converted[stage + ".model_type"])
                return RapidOCR(params=converted)
        onnxruntime.disable_telemetry_events()
        with network_denied():
            engine = factory(params=params)
            counts, elapsed = [], 0.0
            for index, image in enumerate(inputs):
                reserve(output)
                require(sha(Path(image["path"]).read_bytes()) == image["sha256"], "same_input_before_recognition")
                started = time.monotonic()
                result = engine(image["path"])
                duration = time.monotonic() - started
                require(sha(Path(image["path"]).read_bytes()) == image["sha256"], "same_input_after_recognition")
                recognized = rows(result, image["width"], image["height"])
                providers = {stage: getattr(engine, "text_" + stage).session.session.get_providers() for stage in ("det", "rec")}
                require(all(value == ["CPUExecutionProvider"] for value in providers.values()), "actual_cpu_only_providers")
                save(output / f"{index:02d}.json", {"policy": POLICY, "image": image, "lines": recognized,
                    "elapsed_seconds": duration, "providers": providers, "gpu_calls": 0,
                    "semantic_acceptance": False, "reader_publication_allowed": False})
                counts.append(len(recognized))
                elapsed += duration
        summary = {"state": "finite_private_specialized_ocr_drafts_saved", "images": len(inputs), "lines": counts,
            "elapsed_seconds": elapsed, "gpu_calls": 0, "semantic_acceptance": False,
            "reader_publication_allowed": False}
        save(output / "result.json", summary)
        return summary
    except BaseException as exc:
        save(output / "failure.json", failure_status(exc))
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("assets")
    p.add_argument("--folder", type=Path, required=True)
    p = commands.add_parser("recognize")
    p.add_argument("--folder", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--input", action="append", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(assets(args.folder) if args.command == "assets" else recognize(args.folder, args.output, args.input)))
    except BaseException as exc:
        print(json.dumps(failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
