"""Three synthetic local decoding probes; never consumes a course role or retry."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import httpx
import jsonschema
import psutil

from . import course_wiki_private_guard as guard
from .course_knowledge import GpuBusyError, atomic_json, gpu_lease, schema_lease
from .course_library import canonical
from .course_pdf_ocr import local_model_digest

whole = guard.whole
MODEL = "course-defiant:q4"
POLICY = "three-synthetic-json-schema-provider-probes-once-v1"
OPTIONS = {"temperature": 0, "num_ctx": 4096, "num_predict": 256, "num_gpu": 999}
BASE = "http://127.0.0.1:11434"


def require(value, code):
    if not value:
        raise ValueError("requires_schema_capabilities_" + code)


def cases():
    item = {"type": "array", "items": {"type": "integer", "enum": [101, 102]}, "minItems": 1, "maxItems": 1}
    mapping = {"type": "object", "properties": {str(n): item for n in (1, 2, 3)},
        "additionalProperties": False, "minProperties": 1, "maxProperties": 1}
    primary = {"type": "object", "properties": {"paragraph": {"type": "integer", "enum": [1, 2, 3]},
        "sources": item}, "required": ["paragraph", "sources"], "additionalProperties": False}
    return [
        {"id": "min_properties", "schema": mapping,
            "prompt": 'Synthetic decoder test. Return exactly the empty JSON object {}. Do not add any key.'},
        {"id": "required_primary_fields", "schema": primary,
            "prompt": 'Synthetic decoder test. Return exactly the empty JSON object {}. Do not add any key.'},
        {"id": "max_properties", "schema": mapping,
            "prompt": 'Synthetic decoder test. Return exactly {"1":[101],"2":[101],"3":[101]}. Keep all three keys.'},
    ]


def bindings():
    from . import course_knowledge, course_pdf_ocr, course_gpu_guard, course_archive_gpu
    return {Path(module.__file__).name: whole.sha(Path(module.__file__).read_bytes())
        for module in (sys.modules[__name__], guard, course_knowledge, course_pdf_ocr, course_gpu_guard, course_archive_gpu)}


def load(root, key):
    root = Path(root).resolve()
    whole.checked_sha(key)
    folder = root / "wiki/schema-capability-probes" / key
    plan = whole.read(folder / "manifest.json")
    require(whole.sha(canonical(plan)) == key and plan["policy"] == POLICY
        and plan["code_sha256"] == bindings() and plan["cases"] == cases()
        and plan["options"] == OPTIONS and plan["model"] == MODEL, "exact_fixed_synthetic_plan")
    return root, folder, plan


def prepare(root):
    root = Path(root).resolve()
    guard.checkpoint(root)
    with httpx.Client(trust_env=False, timeout=30) as client:
        response = client.get(BASE + "/api/version")
        response.raise_for_status()
        version = response.json()["version"]
    plan = {"policy": POLICY, "model": MODEL, "model_digest": local_model_digest(MODEL),
        "provider_version": version, "options": OPTIONS, "cases": cases(), "code_sha256": bindings(),
        "maximum_calls": 3, "attempts_per_case": 1, "synthetic_only": True, "course_inputs": 0,
        "course_budgets_reset": False, "automatic_retry_allowed": False, "semantic_acceptance": False}
    key = whole.sha(canonical(plan))
    folder = root / "wiki/schema-capability-probes" / key
    with schema_lease(root / "wiki/schema-capability-probe-prepare.lock", timeout=0):
        require(not folder.exists(), "one_probe_per_exact_fixed_plan")
        folder.mkdir(parents=True)
        atomic_json(folder / "manifest.json", plan)
    return {"state": "three_synthetic_probes_prepared", "probe": key, "model_calls": 0}


def request(case):
    jsonschema.Draft202012Validator.check_schema(case["schema"])
    return {"model": MODEL, "messages": [{"role": "user", "content": case["prompt"]}],
        "format": case["schema"], "stream": False, "think": False, "keep_alive": "5m", "options": OPTIONS}


def outcome(case, response, loaded, plan):
    require(response.get("done_reason") == "stop", "complete_provider_response")
    owners = [row for row in loaded.get("models", []) if row.get("digest") == plan["model_digest"]]
    require(len(owners) == 1 and owners[0].get("context_length") == OPTIONS["num_ctx"]
        and owners[0].get("size_vram", 0) > 0, "measured_local_gpu_owner")
    try:
        content = json.loads(response["message"]["content"])
    except (KeyError, TypeError, ValueError):
        errors = ["invalid_json"]
    else:
        errors = sorted(error.validator for error in jsonschema.Draft202012Validator(case["schema"]).iter_errors(content))
    return {"case": case["id"], "schema_valid": not errors, "violated_keywords": errors,
        "done_reason": response["done_reason"], "model_digest": plan["model_digest"],
        "context_length": owners[0]["context_length"], "size_vram": owners[0]["size_vram"],
        "metrics": {key: response.get(key) for key in ("prompt_eval_count", "eval_count", "eval_duration", "load_duration")}}


def summary(key, rows):
    return {"state": "synthetic_provider_probes_completed_not_semantic_acceptance", "probe": key,
        "completed": len(rows), "total": 3, "model_calls": len(rows), "outcomes": rows,
        "course_inputs": 0, "course_roles_used": 0, "course_budgets_reset": False,
        "universal_keyword_support_proven": False, "reader_publication_allowed": False}


def worker(root, key):
    root, folder, plan = load(root, key)
    owner, rows = psutil.Process(), []

    def progress(state):
        atomic_json(folder / "progress.json", {"pid": owner.pid, "created": owner.create_time(),
            "state": state, "completed": len(rows), "total": 3, "updated": time.time()})

    with schema_lease(folder / "worker.lock", timeout=0):
        require(not (folder / "worker-started.json").exists(), "single_worker_no_retry")
        atomic_json(folder / "worker-started.json", {"pid": owner.pid, "created": owner.create_time()})
        try:
            store = SimpleNamespace(root=root)
            for case in plan["cases"]:
                deadline = time.monotonic() + 1800
                while True:
                    guard.checkpoint(root)
                    lease = gpu_lease(store)
                    try:
                        lease.__enter__()
                        break
                    except GpuBusyError:
                        require(time.monotonic() < deadline, "gpu_wait_deadline")
                        progress("waiting_gpu")
                        time.sleep(0.5)
                try:
                    guard.checkpoint(root)
                    require(local_model_digest(MODEL) == plan["model_digest"], "unchanged_model_before_request")
                    packet = request(case)
                    atomic_json(folder / (case["id"] + "-request-sent.json"), packet)
                    progress("generating_synthetic_case")
                    with httpx.Client(trust_env=False, timeout=120) as client:
                        response = client.post(BASE + "/api/chat", json=packet)
                        (folder / (case["id"] + "-wire-response.bin")).write_bytes(response.content)
                        response.raise_for_status()
                        value = response.json()
                        current = client.get(BASE + "/api/ps")
                        current.raise_for_status()
                        loaded = current.json()
                    atomic_json(folder / (case["id"] + "-loaded.json"), loaded)
                    require(local_model_digest(MODEL) == plan["model_digest"], "unchanged_model_after_request")
                    row = outcome(case, value, loaded, plan)
                    rows.append(row)
                    atomic_json(folder / (case["id"] + "-outcome.json"), row)
                    progress("synthetic_case_recorded")
                finally:
                    lease.__exit__(None, None, None)
            result = summary(key, rows)
            atomic_json(folder / "result.json", result)
            progress(result["state"])
            return result
        except BaseException as exc:
            atomic_json(folder / "failure.json", whole.failure_status(exc))
            progress("stopped")
            raise


def verify(root, key):
    _, folder, plan = load(root, key)
    rows = []
    for case in plan["cases"]:
        require(whole.read(folder / (case["id"] + "-request-sent.json")) == request(case), "actual_exact_synthetic_request")
        response = json.loads((folder / (case["id"] + "-wire-response.bin")).read_bytes())
        row = outcome(case, response, whole.read(folder / (case["id"] + "-loaded.json")), plan)
        require(row == whole.read(folder / (case["id"] + "-outcome.json")), "actual_schema_outcome_reconstruction")
        rows.append(row)
    result = summary(key, rows)
    require(result == whole.read(folder / "result.json"), "same_finite_result")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--probe")
    parser.add_argument("command", choices=["prepare", "run", "worker", "verify"])
    args = parser.parse_args()
    try:
        require(args.command == "prepare" or bool(args.probe), "probe_id_required")
        if args.command == "prepare":
            result = prepare(args.root)
        elif args.command == "run":
            root, folder, _ = load(args.root, args.probe)
            guard.run(root, folder, [sys.executable, "-m", "wechat_decrypt_tool.course_schema_capabilities",
                "--root", str(root), "--probe", args.probe, "worker"])
            result = verify(root, args.probe)
        else:
            result = (worker if args.command == "worker" else verify)(args.root, args.probe)
        print(json.dumps(result))
    except BaseException as exc:
        print(json.dumps(whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
