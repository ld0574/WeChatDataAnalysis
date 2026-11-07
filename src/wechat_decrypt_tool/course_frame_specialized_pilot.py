"""Finite PP-OCRv5 drafts plus two separate local pixel checks, never publish.

The existing Windows pilot is immutable. Only its pure request/schema helpers
are reused; PP-OCR polygons and confidence remain explicit, without fake words.
Every selected frame must be genuinely unattempted across the old registries.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time

import httpx
import psutil
from PIL import Image
from tokenizers import Tokenizer

from . import course_frame_assisted_pilot as pixel

whole, knowledge, shared = pixel.whole, pixel.knowledge, pixel.shared
POLICY = "unattempted-ppocrv5-cpu-drafts-two-local-pixel-checks-once-v1"
DRAFT_POLICY = "finite-offline-ppocrv5-server-private-drafts-cpu-v1"
REGISTRIES = ("frame-assisted-registrations", "frame-ocr-recovery-registrations",
    "frame-context-recovery-registrations", "frame-specialized-registrations")
MODELS = {"det": "0f8846b1d4bba223a2a2f9d9b44022fbc22cc019051a602b41a7fda9667e4cad",
    "rec": "e09385400eaaaef34ceff54aeb7c4f0f1fe014c27fa8b9905d4709b65746562a"}


def require(ok, code):
    if not ok:
        raise ValueError("requires_specialized_frame_" + code)


def bindings():
    tool = Path(__file__).resolve().parents[2] / "tools/course_specialized_ocr.py"
    return {**pixel.bindings(), Path(__file__).name: whole.sha(Path(__file__).read_bytes()),
        tool.name: whole.sha(tool.read_bytes())}


def png(raw):
    with Image.open(io.BytesIO(raw)) as image:
        require(0 < image.width <= 2560 and 0 < image.height <= 2560, "bounded_complete_frame")
        output = io.BytesIO()
        image.convert("RGB").save(output, format="PNG")
    return output.getvalue()


def draft_context(receipt):
    image = receipt["image"]
    width, height = image["width"], image["height"]
    require(receipt["policy"] == DRAFT_POLICY and receipt["gpu_calls"] == 0
        and receipt["semantic_acceptance"] is False and receipt["reader_publication_allowed"] is False
        and receipt["providers"] == {"det": ["CPUExecutionProvider"], "rec": ["CPUExecutionProvider"]}
        and 0 < width <= 2560 and 0 < height <= 2560, "actual_specialized_cpu_receipt")
    lines = {}
    for index, row in enumerate(receipt["lines"]):
        points, score = row["polygon"], row["confidence"]
        require(isinstance(row["text"], str) and len(points) == 4 and math.isfinite(score) and 0 <= score <= 1
            and all(len(point) == 2 and all(math.isfinite(v) for v in point)
                and 0 <= point[0] <= width and 0 <= point[1] <= height for point in points), "literal_polygons_and_confidence")
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        require(min(xs) < max(xs) and min(ys) < max(ys), "positive_original_polygon")
        lines[f"L{index:04d}"] = {**row, "box": [round(1000 * min(xs) / width), round(1000 * min(ys) / height),
            round(1000 * max(xs) / width), round(1000 * max(ys) / height)]}
    require(bool(lines), "nonempty_draft_without_confidence_filter")
    return {"draft_engine": "PP-OCRv5-server/RapidOCR-3.10.0/ORT-1.28.0-CPU",
        "coordinate_policy": "polygon_original_pixels_box_normalized_1000",
        "primary_lines": lines, "secondary_lines": {}, "ocr_is_ground_truth": False,
        "full_image_checked_by_ocr_model": False, "all_low_confidence_lines_retained": True}


def exclusions(root, video):
    """Exclude prior roles, including aliases with exactly the same RGB pixels."""
    registrations, attempted = {}, set()
    for name in REGISTRIES:
        for path in sorted((root / "wiki" / name).glob(video + "-*.json")):
            registrations[path.relative_to(root).as_posix()] = whole.sha(path.read_bytes())
            attempted.add(int(path.stem.rsplit("-", 1)[1]))
    with shared.ReadOnlyArchive(root).connect() as db:
        attempted.update(row[0] for row in db.execute(
            "SELECT id FROM frames WHERE video=? AND (status!='pending' OR model!='')", (video,)))
    pixels = {}
    for key in sorted(attempted):
        _, frame = shared.rows(root, video, key)
        pixels[str(key)] = whole.sha(png((root / frame["path"]).read_bytes()))
    return {"registrations": registrations, "attempted_pixels": pixels}


def prepare(root, video, frames, drafts, tokenizer):
    root, drafts, tokenizer = [Path(p).resolve() for p in (root, drafts, tokenizer)]
    shared.checkpoint(root)
    pixel.checked_sample(root)
    require(re.fullmatch(r"[a-f0-9]{24}", video) and 1 <= len(frames) <= 3
        and len(set(frames)) == len(frames), "finite_distinct_scope")
    require(drafts.is_relative_to(root) and tokenizer.is_relative_to(root), "portable_private_inputs")
    started = whole.read(drafts / "started.json")
    summary = whole.read(drafts / "result.json")
    require(started["policy"] == DRAFT_POLICY and started["code_sha256"] == bindings()["course_specialized_ocr.py"]
        and started["network_connections_allowed"] is False and summary["gpu_calls"] == 0
        and summary["images"] == len(frames) == len(started["inputs"]), "complete_bound_specialized_drafts")
    for task, expected in MODELS.items():
        path = Path(started["parameters"][task.capitalize() + ".model_path"])
        require(whole.sha(path.read_bytes()) == expected, "same_local_specialized_weights")
    model, excluded = shared.local_model_digest(pixel.MODEL), exclusions(root, video)
    targets, contexts, pictures, files = {}, {}, {}, {}
    for index, key in enumerate(frames):
        source, frame = shared.rows(root, video, key)
        require(source["status"] == "transcribed" and frame["status"] == "pending" and not frame["model"]
            and str(key) not in excluded["attempted_pixels"], "new_frame_not_old_role_retry")
        image = (root / frame["path"]).resolve()
        require(image.is_relative_to(root / "lessons" / video / "frames") and not image.is_symlink(), "retained_original_frame")
        raw, target = image.read_bytes(), str(key)
        original_sha, native = whole.sha(raw), png(raw)
        policy = "frame-vision-v2-overlap64-depth3/" + pixel.MODEL + "@" + model
        cache = root / "lessons" / video / "frame-ocr-cache" / whole.sha((original_sha + "\0" + policy).encode())
        require(not cache.exists() and whole.sha(native) not in excluded["attempted_pixels"].values(), "no_cache_or_attempted_pixel_alias")
        path = drafts / f"{index:02d}.json"
        receipt = whole.read(path)
        require(receipt["image"] == started["inputs"][index] and receipt["image"]["sha256"] == original_sha
            and Path(receipt["image"]["path"]).resolve() == image, "actual_frame_to_cpu_receipt_binding")
        files[path.name] = whole.sha(path.read_bytes())
        contexts[target], pictures[target] = draft_context(receipt), native
        targets[target] = {"source": source, "frame": frame, "raw_image_sha256": original_sha,
            "native_png_sha256": whole.sha(native), "draft_receipt": path.name}
    require(len({row["native_png_sha256"] for row in targets.values()}) == len(targets), "distinct_full_frame_pixels")
    files.update({name: whole.sha((drafts / name).read_bytes()) for name in ("started.json", "result.json")})
    recipe = {"policy": POLICY, "video": video, "targets": targets, "model_digest": model,
        "code_sha256": bindings(), "tokenizer": tokenizer.relative_to(root).as_posix(),
        "tokenizer_sha256": whole.sha(tokenizer.read_bytes()), "draft_folder": drafts.relative_to(root).as_posix(),
        "draft_files_sha256": files, "excluded_at_prepare": excluded, "specialized_models_sha256": MODELS,
        "options": pixel.OPTIONS, "prompts_sha256": [pixel.digest(pixel.CORRECT), pixel.digest(pixel.REVIEW)],
        "attempts_per_role": 1, "automatic_retry_allowed": False, "different_model_pixel_review": False,
        "reader_publication_allowed": False, "database_write_allowed": False}
    key = pixel.digest(recipe)[:32]
    folder, registry = root / "wiki/frame-specialized-pilots" / key, root / "wiki/frame-specialized-registrations"
    registry.mkdir(parents=True, exist_ok=True)
    with knowledge.schema_lease(registry / "prepare.lock", timeout=0):
        require(not folder.exists() and exclusions(root, video) == excluded, "once_no_racing_registration")
        folder.mkdir(parents=True)
        knowledge.atomic_json(folder / "recipe.json", recipe)
        for target, raw in pictures.items():
            knowledge.atomic_json(registry / f"{video}-{target}.json", {"job": key, "frame": int(target), "video": video,
                "policy": POLICY, "native_png_sha256": whole.sha(raw)})
            (folder / (target + ".png")).write_bytes(raw)
        knowledge.atomic_json(folder / "contexts.json", contexts)
    return {"state": "finite_specialized_pixel_pilot_prepared", "job": key, "frames": len(targets), "model_calls": 0}


def load(root, key):
    root = Path(root).resolve()
    require(re.fullmatch(r"[a-f0-9]{32}", key), "job_id")
    folder = root / "wiki/frame-specialized-pilots" / key
    recipe, contexts = [whole.read(folder / name) for name in ("recipe.json", "contexts.json")]
    require(recipe["policy"] == POLICY and pixel.digest(recipe)[:32] == key and recipe["code_sha256"] == bindings()
        and whole.sha((root / recipe["tokenizer"]).read_bytes()) == recipe["tokenizer_sha256"], "same_code_recipe_and_tokenizer")
    drafts = root / recipe["draft_folder"]
    for name, expected in recipe["draft_files_sha256"].items():
        require(whole.sha((drafts / name).read_bytes()) == expected, "same_complete_cpu_receipt")
    for target, row in recipe["targets"].items():
        reg = whole.read(root / "wiki/frame-specialized-registrations" / f"{recipe['video']}-{target}.json")
        require(reg["job"] == key and whole.sha((folder / (target + ".png")).read_bytes()) == row["native_png_sha256"]
            and whole.sha((root / row["frame"]["path"]).read_bytes()) == row["raw_image_sha256"]
            and png((root / row["frame"]["path"]).read_bytes()) == (folder / (target + ".png")).read_bytes()
            and contexts[target] == draft_context(whole.read(drafts / row["draft_receipt"])), "same_original_pixels_and_all_draft_lines")
    for name, expected in recipe["excluded_at_prepare"]["registrations"].items():
        require(whole.sha((root / name).read_bytes()) == expected, "previous_negative_registration_retained")
    require(set(contexts) == set(recipe["targets"]), "all_and_only_planned_frames")
    return root, folder, recipe, contexts


def worker(root, key):
    root, folder, recipe, contexts = load(root, key)
    shared.checkpoint(root)
    owner, outcomes = psutil.Process(), {}
    tokenizer = Tokenizer.from_file(str(root / recipe["tokenizer"]))

    def progress(stage, **extra):
        knowledge.atomic_json(folder / "progress.json", {"pid": owner.pid, "created": owner.create_time(), "job": key,
            "stage": stage, "updated": time.time(), "total_frames": len(contexts), "finished_frames": len(outcomes), **extra})

    store = knowledge.KnowledgeStore(root)
    store.progress = progress

    def call(target, role, request):
        tokens = sum(len(tokenizer.encode(row["content"]).ids) for row in request["messages"])
        require(tokens <= pixel.MAX_TEXT_TOKENS, "complete_context_without_clipping")
        marker = folder / f"{target}-{role}-started.json"
        require(not marker.exists(), "role_once_without_retry")
        knowledge.atomic_json(marker, {"wire_request_sha256": pixel.digest(request), "updated": time.time()})
        progress("waiting_gpu", frame=target, role=role)
        with knowledge.wait_gpu_lease(store), httpx.Client(trust_env=False, timeout=600) as client:
            require(shared.local_model_digest(pixel.MODEL) == recipe["model_digest"], "same_installed_vision_weights")
            shared.checkpoint(root)
            progress("generating", frame=target, role=role)
            started = time.monotonic()
            response = client.post("http://127.0.0.1:11434/api/chat", json=request)
            if response.status_code >= 400:
                knowledge.atomic_json(folder / f"{target}-{role}-http-failure.json", {"http_status": response.status_code,
                    "private_body_base64": base64.b64encode(response.content).decode("ascii")})
            response.raise_for_status()
            response = response.json()
            loaded = client.get("http://127.0.0.1:11434/api/ps")
            loaded.raise_for_status()
            owners = [row for row in loaded.json().get("models", []) if row.get("digest") == recipe["model_digest"]]
            receipt = {"wire_request_sha256": pixel.digest(request), "response": response,
                "loaded_model": owners[0] if len(owners) == 1 else {}, "text_tokens": tokens,
                "service_seconds": time.monotonic() - started}
            path = folder / f"{target}-{role}-receipt.json"
            knowledge.atomic_json(path, receipt)
            value = pixel.checked_response(receipt, request, recipe, tokenizer)
        time.sleep(.35)
        return value, whole.sha(path.read_bytes())

    with knowledge.schema_lease(folder / "worker.lock", timeout=0):
        require(not (folder / "started.json").exists(), "worker_once_without_retry")
        knowledge.atomic_json(folder / "started.json", {"pid": owner.pid, "created": owner.create_time()})
        for target, context in contexts.items():
            shared.checkpoint(root)
            try:
                source, frame = shared.rows(root, recipe["video"], int(target))
                require(source == recipe["targets"][target]["source"] and frame == recipe["targets"][target]["frame"], "unchanged_unattempted_database_rows")
                correction, correct_sha = call(target, "correct", pixel.body(folder, target, pixel.CORRECT, context, pixel.schema_for(context)))
                rebuilt = pixel.reconstruction(context, correction)
                outcome = {"state": "unresolved", "correct_sha256": correct_sha, "model_calls": 1}
                if correction["complete_visible_text"] and correction["complete_visible_diagrams"]:
                    audit, audit_sha = call(target, "review", pixel.body(folder, target, pixel.REVIEW,
                        {**context, "correction": correction, "reconstructed_lines": rebuilt}, pixel.AUDIT_SCHEMA))
                    require(bool(audit["reason"].strip()), "nonempty_private_review_reason")
                    outcome = {**outcome, "state": "supported_same_model_pixel_review" if all(audit["checks"].values()) else "unresolved",
                        "review_sha256": audit_sha, "model_calls": 2}
                knowledge.atomic_json(folder / (target + "-outcome.json"), outcome)
                outcomes[target] = outcome
            except InterruptedError:
                raise
            except Exception as exc:
                failure = whole.failure_status(exc)
                knowledge.atomic_json(folder / (target + "-failure.json"), failure)
                outcomes[target] = {"state": "stopped", "failure": failure}
            progress("frame_finished", frame=target)
        knowledge.atomic_json(folder / "outcomes.json", outcomes)
        progress("finite_pilot_finished", supported=sum(o["state"] == "supported_same_model_pixel_review" for o in outcomes.values()))
    return {"state": "finite_specialized_pixel_pilot_finished", "job": key, "frames": len(outcomes)}


def verify(root, key):
    root, folder, recipe, contexts = load(root, key)
    status = whole.read(folder / "progress.json")
    require(status["stage"] == "finite_pilot_finished" and status["finished_frames"] == len(contexts)
        and not shared.live(status), "actual_exited_worker")
    outcomes, supported, calls, seconds = whole.read(folder / "outcomes.json"), 0, 0, 0.0
    require(set(outcomes) == set(contexts), "every_planned_frame_accounted")
    tokenizer = Tokenizer.from_file(str(root / recipe["tokenizer"]))
    for target, outcome in outcomes.items():
        for role in ("correct", "review"):
            path = folder / f"{target}-{role}-receipt.json"
            calls += int((folder / f"{target}-{role}-started.json").exists())
            if path.exists():
                if outcome["state"] != "stopped":
                    require(whole.sha(path.read_bytes()) == outcome.get(role + "_sha256"), "same_saved_role_receipt")
                seconds += whole.read(path)["service_seconds"]
        if outcome["state"] == "stopped":
            require(whole.read(folder / (target + "-failure.json")) == outcome["failure"], "structural_failure_retained")
            continue
        context = contexts[target]
        path = folder / f"{target}-correct-receipt.json"
        require(whole.sha(path.read_bytes()) == outcome["correct_sha256"], "same_correction")
        correction = pixel.checked_response(whole.read(path), pixel.body(folder, target, pixel.CORRECT, context,
            pixel.schema_for(context)), recipe, tokenizer)
        rebuilt, passed = pixel.reconstruction(context, correction), False
        if correction["complete_visible_text"] and correction["complete_visible_diagrams"]:
            path = folder / f"{target}-review-receipt.json"
            require(whole.sha(path.read_bytes()) == outcome["review_sha256"], "same_second_pixel_review")
            audit = pixel.checked_response(whole.read(path), pixel.body(folder, target, pixel.REVIEW,
                {**context, "correction": correction, "reconstructed_lines": rebuilt}, pixel.AUDIT_SCHEMA), recipe, tokenizer)
            require(bool(audit["reason"].strip()), "saved_private_review_reason")
            passed = all(audit["checks"].values())
        require(passed == (outcome["state"] == "supported_same_model_pixel_review"), "negative_never_promoted")
        supported += int(passed)
    for target, expected in recipe["targets"].items():
        source, frame = shared.rows(root, recipe["video"], int(target))
        require(source == expected["source"] and frame == expected["frame"], "archive_rows_unchanged")
    return {"state": "specialized_pixel_result_reconstructed_without_inference", "job": key, "frames": len(contexts),
        "supported": supported, "unresolved": len(contexts) - supported, "actual_role_starts": calls,
        "actual_service_seconds": seconds, "new_model_calls": 0, "database_writes": 0,
        "reader_publication_allowed": False, "different_model_pixel_review": False}


def guard(root, key, *, sample=None, launch=subprocess.Popen, stopper=pixel.thermal.stop_worker, sleep=time.sleep):
    root, folder, _, _ = load(root, key)
    owner, reading = psutil.Process(), None
    sampler = sample or (lambda: pixel.checked_sample(root))

    def progress(state, **extra):
        knowledge.atomic_json(folder / "guard-progress.json", {"pid": owner.pid, "created": owner.create_time(), "job": key,
            "state": state, "updated": time.time(), "reading": reading, "maximum_temperature_c": 70,
            "maximum_power_w": 200, "automatic_thermal_resume_allowed": False, **extra})

    def stop(process):
        retained = {name: (root / name).read_bytes() for name in ("pause.flag", "supervisor-stop.flag") if (root / name).exists()}
        for name, raw in retained.items():
            whole.backup(root / name, folder / "retained-control-markers" / (whole.sha(raw) + "-" + name))
        stopper(process, root)
        for name, raw in retained.items():
            (root / name).write_bytes(raw)

    with knowledge.schema_lease(folder / "guard.lock", timeout=0):
        require(not (folder / "guard-started.json").exists(), "guard_once_without_retry")
        knowledge.atomic_json(folder / "guard-started.json", {"pid": owner.pid, "created": owner.create_time()})
        process = None
        try:
            shared.checkpoint(root)
            reading = sampler()
            reason = pixel.thermal.violation(reading, 70, 200)
            if reason:
                progress("paused", reason_code=reason, worker_launched=False)
                return {"state": "paused_without_automatic_resume", "job": key}
            with (folder / "worker.stdout.log").open("ab") as stdout, (folder / "worker.stderr.log").open("ab") as stderr:
                process = launch([sys.executable, "-m", __spec__.name, "--root", str(root), "worker", "--job", key],
                    stdout=stdout, stderr=stderr, env={**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"},
                    creationflags=0x08000000 if os.name == "nt" else 0)
                while process.poll() is None:
                    reading = sampler()
                    reason = "control_marker_preserved" if any((root / name).exists() for name in ("pause.flag", "supervisor-stop.flag")) else pixel.thermal.violation(reading, 70, 200)
                    if reason:
                        stop(process)
                        progress("paused", reason_code=reason)
                        return {"state": "paused_without_automatic_resume", "job": key}
                    progress("running", worker_pid=process.pid)
                    sleep(5)
                progress("worker_exited", returncode=process.returncode)
                require(process.returncode == 0, "successful_worker_exit")
        except BaseException as exc:
            knowledge.atomic_json(folder / "guard-failure.json", whole.failure_status(exc))
            if process is not None and process.poll() is None:
                stop(process)
            progress("paused", reason_code="guard_exception", worker_launched=process is not None)
            raise
    result = verify(root, key)
    knowledge.atomic_json(folder / "checked-result.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--video", required=True)
    p.add_argument("--frame", type=int, action="append", required=True)
    p.add_argument("--drafts", type=Path, required=True)
    p.add_argument("--tokenizer", type=Path, required=True)
    for name in ("worker", "guard", "verify"):
        sub.add_parser(name).add_argument("--job", required=True)
    args = parser.parse_args()
    try:
        result = prepare(args.root, args.video, args.frame, args.drafts, args.tokenizer) if args.command == "prepare" else globals()[args.command](args.root, args.job)
        print(json.dumps(result))
    except BaseException as exc:
        print(json.dumps(whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
