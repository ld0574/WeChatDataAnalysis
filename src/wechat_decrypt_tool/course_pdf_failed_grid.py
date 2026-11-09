"""One finite first-use finer grid for retained failed PDF pages.

All old caches and failures stay immutable. Only absent page occurrences may be
committed; cached success is independently checked and backed up, not declared
semantically accepted. A failed or paused dispatch cannot automatically restart.
"""
from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import shutil
import sys
import time

import httpx
from PIL import Image, ImageOps
import psutil

from . import course_pdf_queue as queue
from . import course_pdf_retry as retry
from . import course_textbook_whole_sources as whole
from . import course_tiled_ocr as tiled
from . import course_wiki_private_guard as guard
from .course_knowledge import ModelOutputTruncatedError, ModelRepetitionError, atomic_json, schema_lease
from .course_pdf_plan import frozen_pdf_scope

POLICY = "first-unused-640-grid-for-retained-failed-pdf-pages-v1"
MAX_CALLS = 48


def require(ok, code):
    if not ok:
        raise ValueError("requires_failed_pdf_grid_" + code)


def digest(value):
    return whole.sha(json.dumps(value, sort_keys=True, ensure_ascii=False).encode())


def code():
    names = {*queue.RUNTIME, "course_pdf_retry.py", "course_wiki_private_guard.py", Path(__file__).name}
    return {name: whole.sha(Path(__file__).with_name(name).read_bytes()) for name in sorted(names)}


def folder_for(root, key):
    require(len(key) == 32 and all(c in "0123456789abcdef" for c in key), "job_id")
    return root / "wiki/pdf-failed-grid-jobs" / key


def grid_folders(root, rows, model, model_digest):
    """Compute the actual old-policy cache identities without creating crops."""
    policy, result = tiled.POLICY + "/" + model + "@" + model_digest, set()
    for row in rows:
        with Image.open(queue.inside(root, row["image"])) as image:
            image = ImageOps.exif_transpose(image)
            for box in tiled.tile_boxes(*image.size, size=retry.RETRY_SIZE, overlap=retry.RETRY_OVERLAP):
                rgba = image.crop(box).convert("RGBA")
                rgb = Image.alpha_composite(Image.new("RGBA", rgba.size, "white"), rgba).convert("RGB")
                out = io.BytesIO()
                rgb.save(out, format="PNG")
                identity = whole.sha((whole.sha(out.getvalue()) + "\0" + policy).encode())
                result.add("document-visuals/tile-ocr-cache/" + identity)
    return sorted(result)


def unused(root, folders):
    require(all(not list((root / name).rglob("*.json")) for name in folders), "all_new_grid_model_roles_unused")


def absent(root, plan):
    with queue.ReadOnlyArchive(root).connect() as db:
        for path, parent, page in frozen_pdf_scope(plan):
            identity = path, "visual " + parent + " page " + str(page)
            for table in ("resource_revisions", "documents", "document_version_metadata"):
                require(db.execute("SELECT 1 FROM " + table + " WHERE path=? AND source_key=? LIMIT 1", identity).fetchone() is None,
                    "page_has_no_prior_committed_or_historical_revision")


def prepare(root, audit_path, diagnostic_path):
    root = Path(root).resolve()
    guard.checkpoint(root)
    audit_path, diagnostic_path = queue.inside(root, audit_path), queue.inside(root, diagnostic_path)
    audit, diagnostic = whole.read(audit_path), whole.read(diagnostic_path)
    require(audit["state"] == "read_only_presence_inventory_complete" and audit["model_calls"] == 0
        and diagnostic["state"] == "actual_three_missing_pdf_pages_structure_inspected_without_inference"
        and diagnostic["audit_sha256"] == whole.sha(audit_path.read_bytes()) and diagnostic["model_calls"] == 0,
        "actual_readonly_missing_page_diagnostics")
    full_path = root / "pdf-all-pages-retained-plan.json"
    require(audit["input_sha256"][full_path.name] == whole.sha(full_path.read_bytes())
        and audit["old_reports_unchanged"] is True, "unchanged_full_inventory_at_actual_audit")
    sources = whole.read(full_path)["pdf_sources_all_pages"]
    by_identity = {(whole.sha(s["path"].encode()), whole.sha(s["source_key"].encode())): s for s in sources}
    selected = []
    for row in audit["rows"]:
        if row["missing_pages"]:
            original = by_identity[row["path_sha256"], row["source_key_sha256"]]
            require(original["parent_raw_digest"] == row["parent_raw_digest"] and original["pages"] == row["pages"], "same_original_source_scope")
            selected.append({**original, "selected_pages": row["missing_pages"]})
    plan = {"pdf_sources_selected_pages": selected, "summary": {"page_occurrences": sum(len(s["selected_pages"]) for s in selected)}}
    scope = frozen_pdf_scope(plan)
    rows = diagnostic["rows"]
    require(0 < len(scope) <= 64 and {(v["parent_raw_digest"], k[2]) for k, v in scope.items()}
        == {(r["raw_sha256"], r["page"]) for r in rows} and len(rows) <= 3, "complete_finite_missing_partition")
    template = whole.read(root / "pdf-queue-20261103-0027-binding.json")
    require(queue.local_model_digest(template["model"]) == template["model_digest"], "same_installed_local_vision_weights")
    bound = {p.relative_to(root).as_posix(): whole.sha(p.read_bytes()) for p in [audit_path, diagnostic_path, full_path]}
    for row in rows:
        for field, expected in [("source", row["raw_sha256"]), ("image", row["image_sha256"])]:
            p = queue.inside(root, row[field])
            require(whole.sha(p.read_bytes()) == expected, "exact_original_pdf_and_failed_page_image")
            bound[p.relative_to(root).as_posix()] = expected
        original = next(tree for tree in row["trees"] if tree["initial_size"] == 1200)
        require(bool(original["blocked"]), "actual_exhausted_original_subdivision")
        for name, checksum in original["cache_files"].items():
            p = queue.inside(root, name)
            require(whole.sha(p.read_bytes()) == checksum, "original_successes_and_failures_unchanged")
            for evidence in p.parent.glob("*.json"):
                bound[evidence.relative_to(root).as_posix()] = whole.sha(evidence.read_bytes())
    new_folders = grid_folders(root, rows, template["model"], template["model_digest"])
    unused(root, new_folders)
    absent(root, plan)
    renderer = Path(template["renderer"]).resolve()
    require(whole.sha(renderer.read_bytes()) == template["renderer_sha256"], "same_retained_renderer")
    contract = {"policy": POLICY, "code": code(), "old_files": bound, "plan": plan, "rows": rows,
        "grid_folders": new_folders, "model": template["model"], "model_digest": template["model_digest"],
        "renderer_sha256": template["renderer_sha256"], "maximum_wire_calls": MAX_CALLS,
        "original_budgets_reset": False, "automatic_retry_allowed": False, "automatic_thermal_resume_allowed": False,
        "semantic_accuracy_verified": False, "source_deletion_allowed": False}
    key = digest(contract)[:32]
    folder = folder_for(root, key)
    registry = root / "wiki/pdf-failed-grid-registrations"
    with schema_lease(root / "wiki/pdf-failed-grid-prepare.lock", timeout=0):
        registrations = [registry / (digest([row["raw_sha256"], row["page"], retry.RETRY_SIZE]) + ".json") for row in rows]
        require(not folder.exists() and all(not p.exists() for p in registrations), "one_finer_grid_budget_per_original_page")
        needed = sum(p.stat().st_size for p in renderer.parent.iterdir() if p.is_file())
        require(shutil.disk_usage(root).free > needed + 32 * 1024**3, "retained_renderer_disk_reserve")
        folder.mkdir(parents=True)
        for name in contract["code"]:
            whole.backup(Path(__file__).with_name(name), folder / "runtime" / name)
        for p in renderer.parent.iterdir():
            if p.is_file():
                target = folder / "renderer" / p.name
                whole.backup(p, target)
        atomic_json(folder / "contract.json", contract)
        atomic_json(folder / "plan.json", plan)
        atomic_json(folder / "renderer.json", {"file": renderer.name,
            "files": {p.name: whole.sha(p.read_bytes()) for p in (folder / "renderer").iterdir()}})
        registry.mkdir(exist_ok=True)
        for p in registrations:
            atomic_json(p, {"job": key, "policy": POLICY})
    return {"state": "one_unused_failed_pdf_grid_prepared", "job": key, "raw_pages": len(rows),
        "page_occurrences": len(scope), "unique_new_root_crops": len(new_folders), "maximum_wire_calls": MAX_CALLS, "model_calls": 0}


def load(root, key):
    root = Path(root).resolve()
    folder = folder_for(root, key)
    contract = whole.read(folder / "contract.json")
    require(contract["policy"] == POLICY and digest(contract)[:32] == key and contract["code"] == code(), "same_frozen_first_grid_budget")
    require(whole.read(folder / "plan.json") == contract["plan"], "same_complete_selected_scope")
    for name, checksum in contract["code"].items():
        require(whole.sha(queue.inside(root, folder / "runtime" / name).read_bytes()) == checksum, "same_retained_runtime_code")
    for name, checksum in contract["old_files"].items():
        require(whole.sha(queue.inside(root, name).read_bytes()) == checksum, "all_original_evidence_retained")
    for row in contract["rows"]:
        registration = root / "wiki/pdf-failed-grid-registrations" / (digest([row["raw_sha256"], row["page"], retry.RETRY_SIZE]) + ".json")
        require(whole.read(registration) == {"job": key, "policy": POLICY}, "same_first_use_registration")
    renderer = whole.read(folder / "renderer.json")
    for name, checksum in renderer["files"].items():
        require(whole.sha(queue.inside(root, folder / "renderer" / name).read_bytes()) == checksum, "portable_renderer_dependencies_unchanged")
    executable = folder / "renderer" / renderer["file"]
    require(whole.sha(executable.read_bytes()) == contract["renderer_sha256"], "same_native_renderer_bytes")
    return root, folder, contract, executable


class LimitedChat:
    """Persist every attempted wire request; never retry HTTP or refresh calls."""
    def __init__(self, root, folder, contract, *, client=None):
        self.root, self.folder, self.contract, self.client = root, folder, contract, client

    def __call__(self, model, messages):
        guard.checkpoint(self.root)
        calls = self.folder / "calls"
        calls.mkdir(exist_ok=True)
        number = len(list(calls.glob("*-request.json")))
        require(number < self.contract["maximum_wire_calls"] and model == self.contract["model"]
            and any(m.get("images") for m in messages), "finite_first_grid_wire_budget")
        require(queue.local_model_digest(model) == self.contract["model_digest"], "same_model_before_wire")
        body = {"model": model, "messages": messages, "stream": False, "think": False, "keep_alive": "5m",
            "options": {"temperature": 0, "presence_penalty": 0, "repeat_penalty": 1.15,
                "repeat_last_n": 256, "num_ctx": 8192, "num_predict": 2500, "num_gpu": 999}}
        path = calls / f"{number:03d}-request.json"
        with path.open("x", encoding="utf-8") as stream:
            json.dump(body, stream, ensure_ascii=False)
        if self.client is None:
            with httpx.Client(trust_env=False, timeout=600) as client:
                response = client.post("http://127.0.0.1:11434/api/chat", json=body)
        else:
            response = self.client.post("http://127.0.0.1:11434/api/chat", json=body)
        value = response.json()
        atomic_json(calls / f"{number:03d}-response.json", {"request_sha256": whole.sha(path.read_bytes()),
            "status": response.status_code, "response": value})
        if response.status_code >= 400 and value.get("error") == "prediction aborted, token repeat limit reached":
            raise ModelRepetitionError()
        response.raise_for_status()
        if value.get("done_reason") != "stop":
            raise ModelOutputTruncatedError(value)
        require(queue.local_model_digest(model) == self.contract["model_digest"], "same_model_after_wire")
        return value["message"]["content"]


def run(root, key):
    root, folder, contract, renderer = load(root, key)
    guard.checkpoint(root)
    with schema_lease(root / "pdf-ocr.lock", timeout=0):
        require(not (folder / "worker-started.json").exists(), "worker_once_without_automatic_retry")
        require((folder / "independent-input-restore.json").is_file(), "actual_input_restore_before_worker")
        unused(root, contract["grid_folders"])
        absent(root, contract["plan"])
        owner = psutil.Process()
        atomic_json(folder / "worker-started.json", {"pid": owner.pid, "created": owner.create_time()})
        store = queue.KnowledgeStore(root)
        store.checkpoint = lambda: guard.checkpoint(root)

        def progress(state, **extra):
            atomic_json(folder / "progress.json", {"pid": owner.pid, "created": owner.create_time(),
                "state": state, "updated": time.time(), "job": key,
                **{k: v for k, v in extra.items() if k in {"tile_box", "local_region"}}})

        store.progress = progress
        proxy = retry.RetryReportStore(store, prefix=folder.relative_to(root).as_posix() + "/prepared")
        original = Path(whole.read(root / "resources-manifest.json")["source"]).resolve()

        def audit(event, args):
            if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
                require(not Path(os.fsdecode(args[0])).resolve().is_relative_to(original), "original_course_reads_forbidden")

        sys.addaudithook(audit)
        try:
            chat = LimitedChat(root, folder, contract)
            for row in contract["rows"]:
                source = next(s for s in contract["plan"]["pdf_sources_selected_pages"] if s["parent_raw_digest"] == row["raw_sha256"])
                target = {"path": source["path"], "source_key": source["source_key"], "page": row["page"],
                    "expected_digest": row["raw_sha256"], "expected_pages": source["pages"]}
                progress("preparing_first_unused_finer_grid")
                result = retry.prepare_retry_page(proxy, target, original, contract["model"], chat, queue.gpu_lease,
                    renderer, progress=progress)
                atomic_json(folder / (row["raw_sha256"] + "-prepared.json"), result)

            def forbid_inference(*args, **kwargs):
                require(False, "commit_uses_only_completed_grid_caches")

            progress("committing_only_complete_page_caches")
            report = queue.run_pdf_ocr(proxy, [folder / "plan.json"], original, contract["model"],
                forbid_inference, queue.gpu_lease, poppler=renderer, wait=True)
            require(report["processed"] == len(frozen_pdf_scope(contract["plan"])) and not report["errors"], "complete_exact_committed_partition")
            progress("independently_verifying_and_backing_up_exact_pages")
            prefix = "pdf-failed-grid-" + key
            destination = Path.home() / "Documents/CourseKnowledgeBackups/pdf-failed-grid-results" / key
            extra = [p for p in folder.rglob("*") if p.is_file() and p.suffix != ".log"
                and p.name not in {"progress.json", "guard-progress.json"}]
            result = queue.backup_batch(store, folder / "plan.json", folder / "prepared-ocr-report.json", renderer,
                destination, prefix=prefix, extra_files=extra)
            queue.verify_backup(result)
            load(root, key)
            atomic_json(folder / "result.json", result)
            progress("finite_failed_grid_committed_verified_and_backed_up")
            return {"state": "finite_failed_grid_committed_verified_and_backed_up", "job": key,
                "page_occurrences": report["processed"], "semantic_accuracy_verified": False, "source_deletion_allowed": False}
        except BaseException as exc:
            failure = whole.failure_status(exc)
            atomic_json(folder / ("failure-" + str(time.time_ns()) + ".json"), failure)
            progress(failure["state"])
            raise


def guarded(root, key):
    root, folder, _, _ = load(root, key)
    require((folder / "independent-input-restore.json").is_file(), "independent_input_restore_before_dispatch")
    return guard.run(root, folder, [sys.executable, "-m", __spec__.name, "--root", str(root), "--key", key, "worker"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--key")
    parser.add_argument("--audit", type=Path)
    parser.add_argument("--diagnostic", type=Path)
    parser.add_argument("command", choices=["prepare", "load", "guard", "worker"])
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = prepare(args.root, args.audit, args.diagnostic)
        elif args.command == "load":
            _, _, contract, _ = load(args.root, args.key)
            result = {"state": "frozen_failed_pdf_grid_inputs_loaded", "job": args.key,
                "page_occurrences": len(frozen_pdf_scope(contract["plan"])), "model_calls": 0}
        else:
            result = {"guard": guarded, "worker": run}[args.command](args.root, args.key)
        print(json.dumps(result))
    except BaseException as exc:
        print(json.dumps(whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
