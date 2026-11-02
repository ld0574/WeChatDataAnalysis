"""Publish completed lessons while observing one exact existing worker.

This queue never starts processing/model jobs or clears pause flags. Publication
intents survive interruption so HTTP checks resume without republishing data.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import time
from urllib.parse import urlsplit
import uuid

import httpx
import psutil

from .course_knowledge import atomic_json, schema_lease
from .course_library import Library, canonical, publish
from .course_lesson_exports import ExportMismatch, digest, validate_lesson
from .course_video_visual_audit import ReadOnlyArchive


def reader_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
            or parsed.username is not None or parsed.password is not None
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment
            or parsed.port is None or not 0 < parsed.port < 65536):
        raise ValueError("library_queue_requires_loopback_reader")
    return value.rstrip("/")


def identity_alive(pid, created):
    if type(pid) is not int or pid <= 0 or not math.isfinite(created) or created <= 0:
        raise ValueError("library_queue_invalid_worker_identity")
    try:
        return psutil.Process(pid).create_time() == created
    except psutil.NoSuchProcess:
        return False


def checkpoint(store):
    if (store.root / "pause.flag").exists():
        raise InterruptedError("library_queue_pause_requested")
    if shutil.disk_usage(store.root).free < 32 * 1024**3:
        raise InterruptedError("library_queue_disk_reserve_reached")
    store.checkpoint()


def available(catalog):
    return {r["id"] for r in catalog["lessons"] if r["state"] == "draft_available"}


def ready_sources(store, catalog):
    if digest((store.root / "inventory-unique.json").read_bytes()) != catalog["inventory_sha256"]:
        raise ValueError("library_queue_inventory_changed")
    unpublished = {r["id"] for r in catalog["lessons"]} - available(catalog)
    with store.connect() as db:
        candidates = [r["id"] for r in db.execute(
            "SELECT id FROM videos WHERE status IN ('transcribed','transcribed_with_gaps') AND error=''"
        ) if r["id"] in unpublished]
    ready, pending = [], {}
    for key in sorted(candidates):
        checkpoint(store)
        try:
            validate_lesson(store, key)
        except ExportMismatch as exc:
            pending[key] = str(exc)
        except (OSError, ValueError, KeyError, TypeError):
            pending[key] = "unreadable_or_invalid_export_structure"
        else:
            ready.append(key)
    return ready, pending


def verify_publication(store, client, before, catalog):
    """Compare frozen objects, search destinations and every newly added image."""
    checkpoint(store)
    if canonical(Library(store.root).catalog()) != canonical(catalog):
        raise ValueError("library_queue_catalog_changed_before_verification")
    response = client.get("/library/api/catalog")
    response.raise_for_status()
    if canonical(response.json()) != canonical(catalog):
        raise ValueError("library_queue_http_catalog_changed")
    after = available(catalog)
    if not set(before) <= after:
        raise ValueError("library_queue_previous_sources_lost")
    library, checks = Library(store.root), []
    for row in catalog["lessons"]:
        key = row["id"]
        if key not in after - set(before):
            continue
        checkpoint(store)
        lesson = library.object(row["artifact"])
        response = client.get("/library/api/lesson/" + key)
        response.raise_for_status()
        if canonical(response.json()) != canonical(lesson):
            raise ValueError("library_queue_http_lesson_changed")
        matched = 0
        for section in lesson["sections"]:
            if not section.get("concepts"):
                continue
            offset, seen = 0, set()
            while True:
                checkpoint(store)
                if offset in seen:
                    raise ValueError("library_queue_search_pagination_cycle")
                seen.add(offset)
                response = client.get("/library/api/search", params={
                    "q": section["concepts"][0]["name"], "video": key,
                    "limit": 100, "offset": offset})
                response.raise_for_status()
                page = response.json()
                if any(r.get("section") == section["id"] for r in page["items"]):
                    break
                offset = page["next_offset"]
                if offset is None:
                    raise ValueError("library_queue_new_section_not_searchable")
            matched += 1
            if matched == 6:
                break
        images = {r["image_sha256"] for r in lesson["evidence"].values() if r.get("image_sha256")}
        for sha in sorted(images):
            checkpoint(store)
            response = client.get("/library/frame/" + sha)
            response.raise_for_status()
            if digest(response.content) != sha:
                raise ValueError("library_queue_http_image_changed")
        checks.append({"video": key, "sections": len(lesson["sections"]),
            "evidence": len(lesson["evidence"]), "keyword_target_sections_verified": matched,
            "image_routes_verified": len(images), "actual_http_equal": True})
    return {"state": "new_complete_source_notes_published_and_http_checked",
        "published": catalog["published"], "requested": catalog["requested"],
        "term_count": catalog["term_count"], "added_sources": checks,
        "catalog_sha256": digest(canonical(catalog)),
        "all_prior_published_sources_preserved": True, "semantic_accuracy_verified": False,
        "source_deletion_allowed": False, "updated": time.time()}


def publish_once(store, client):
    checkpoint(store)
    (store.root / "wiki").mkdir(exist_ok=True)
    folder = store.root / "wiki/library-publication-queue"
    with ExitStack() as stack:
        try:
            stack.enter_context(schema_lease(store.root / "wiki/next-source-publication.lock", timeout=0))
        except RuntimeError as exc:
            if str(exc) == "Another course archive operation still holds next-source-publication.lock":
                return {"state": "waiting_for_existing_publisher"}
            raise
        pending = folder / "pending.json"
        recovering = pending.exists()
        if recovering:
            intent = json.loads(pending.read_bytes())
            if (intent.get("policy") != "completed-source-publication-intent-v1"
                    or not re.fullmatch(r"[a-f0-9]{32}", intent["id"])):
                raise ValueError("library_queue_invalid_retained_intent")
        else:
            before = Library(store.root).catalog()
            ready, deferred = ready_sources(store, before)
            if not ready:
                return {"state": "waiting_for_complete_source_exports", "pending_sources": deferred}
            folder.mkdir(parents=True, exist_ok=True)
            intent = {"policy": "completed-source-publication-intent-v1", "id": uuid.uuid4().hex,
                "before_ids": sorted(available(before)), "ready_sources": ready,
                "inventory_sha256": before["inventory_sha256"], "created": time.time()}
            atomic_json(pending, intent)
            result = publish(store)
            intent = {**intent, "catalog": result}
            atomic_json(pending, intent)
        if digest((store.root / "inventory-unique.json").read_bytes()) != intent["inventory_sha256"]:
            raise ValueError("library_queue_inventory_changed")
        # A crash after publishing but before saving the result is reconciled
        # against the current pointer; it never causes an immediate republish.
        catalog = intent.get("catalog") or Library(store.root).catalog()
        result = verify_publication(store, client, intent["before_ids"], catalog)
        result.update(intent=intent["id"], verification_recovered_without_republishing=recovering)
        destination = folder / "receipts" / intent["id"]
        destination.mkdir(parents=True, exist_ok=True)
        receipt = destination / "validation.json"
        if receipt.exists():
            receipt = destination / ("verification-recovered-" + uuid.uuid4().hex + ".json")
        atomic_json(receipt, result)
        pending.replace(destination / "intent.json")
        return {**result, "receipt": str(receipt.relative_to(store.root))}


def watch(store, client, worker_pid, worker_created, *, interval=30, alive=None, sleep=time.sleep):
    if not math.isfinite(interval) or not 1 <= interval <= 60:
        raise ValueError("library_queue_invalid_poll_interval")
    alive = alive or (lambda: identity_alive(worker_pid, worker_created))
    identity = {"pid": os.getpid(), "created": psutil.Process().create_time(),
        "worker_pid": worker_pid, "worker_created": worker_created}

    def progress(state, **extra):
        atomic_json(store.root / "wiki/library-publication-queue-progress.json", {
            **identity, "state": state, "updated": time.time(), "source_deletion_allowed": False,
            "automatic_worker_restart_allowed": False, **extra})

    (store.root / "wiki").mkdir(exist_ok=True)
    with schema_lease(store.root / "wiki/library-publication-queue.lock", timeout=0):
        if not alive():
            progress("worker_identity_not_live")
            return 1
        progress("watching_exact_worker")
        last_receipt = None
        try:
            while True:
                checkpoint(store)
                result = publish_once(store, client)
                last_receipt = result.get("receipt", last_receipt)
                progress(result["state"], result=result, last_receipt=last_receipt)
                if not alive():
                    progress("worker_exited", final_result=result)
                    return 0
                sleep(interval)
        except InterruptedError as exc:
            reason = str(exc) if str(exc).startswith("library_queue_") else "archive_pause_or_reserve_reached"
            progress("paused", reason=reason, error_sha256=hashlib.sha256(str(exc).encode()).hexdigest())
            return 0
        except Exception as exc:
            failure = {"error_type": type(exc).__name__,
                "error_sha256": hashlib.sha256(str(exc).encode()).hexdigest(), "updated": time.time()}
            if isinstance(exc, httpx.HTTPStatusError):
                failure["http_status"] = exc.response.status_code
            if type(exc) is ValueError and str(exc).startswith(("library_queue_", "library_publication_")):
                failure["error_code"] = str(exc)
            failure_path = store.root / "wiki/library-publication-queue/failures" / (uuid.uuid4().hex + ".json")
            failure_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_json(failure_path, failure)
            progress("failed", **failure, failure_receipt=str(failure_path.relative_to(store.root)))
            return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--worker-pid", type=int, required=True)
    parser.add_argument("--worker-created", type=float, required=True)
    parser.add_argument("--reader-url", type=reader_url, default="http://127.0.0.1:3003")
    args = parser.parse_args(argv)
    with httpx.Client(base_url=args.reader_url, trust_env=False, timeout=90) as client:
        return watch(ReadOnlyArchive(args.root), client, args.worker_pid, args.worker_created)


if __name__ == "__main__":
    raise SystemExit(main())
