"""Retained static-image repairs and notebook-image OCR with precise crop citations."""

from __future__ import annotations

import base64
import hashlib
import json
import time
from pathlib import Path

from .course_attachments import (
    normalized_original,
    resolve_reference,
    retained_reference,
)
from .course_image_ocr import retained_image_bytes
from .course_tiled_ocr import POLICY, describe_tile, local_model_digest, retain_tiles


def notebook_image_bytes(store, target, cache):
    pointer = (target["path"], target["source_key"])
    with store.connect() as db:
        parent = db.execute(
            "SELECT raw_digest FROM resource_revisions WHERE path=? AND source_key=?",
            pointer,
        ).fetchone()
    if parent is None or parent[0] != target["raw_digest"]:
        raise RuntimeError("Notebook image parent is missing or changed")
    identity = (*pointer, target["raw_digest"])
    if identity not in cache:
        result = resolve_reference(
            store, retained_reference(store, *pointer, target["raw_digest"])
        )
        path = result["path"]
        if path.stat().st_size > 16 * 1024**2:
            raise ValueError("Notebook image source byte bound exceeded")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != target["raw_digest"]:
            raise RuntimeError("Notebook image source hash differs")
        cache[identity] = json.loads(raw.decode("utf-8-sig"))
    image = target["image"]
    location = image["location"]
    if type(location["cell"]) is not int or location["cell"] < 1:
        raise ValueError("Invalid notebook image cell locator")
    cell = cache[identity]["cells"][location["cell"] - 1]
    if "output" in location:
        number = location["output"]
        if type(number) is not int or number < 1:
            raise ValueError("Invalid notebook image output locator")
        value = cell["outputs"][number - 1]["data"][image["mime"]]
    else:
        value = cell["attachments"][location["attachment"]][image["mime"]]
    if isinstance(value, list) and all(isinstance(part, str) for part in value):
        value = "".join(value)
    if not isinstance(value, str):
        raise ValueError("Malformed notebook image encoding")
    raw = base64.b64decode("".join(value.split()), validate=True)
    if hashlib.sha256(raw).hexdigest() != image["embedded_sha256"]:
        raise RuntimeError("Notebook image differs from audited embedded image")
    return raw


def index_visual_sources(
    store, audit_path, model, chat, lease, *, failures_path=None, limit=0, wait=True
):
    audit = json.loads(Path(audit_path).read_text("utf-8"))
    notebook = failures_path is None
    kind = "notebook-images" if notebook else "image-repairs"
    if notebook:
        if audit.get("errors"):
            raise ValueError("Notebook image source audit contains unresolved errors")
        targets = [
            row
            for row in audit["targets"]
            if row["image"]["state"] == "raster_ocr_candidate"
        ]
    else:
        failures = json.loads(Path(failures_path).read_text("utf-8"))
        failed = {(x["path"], x["source_key"]) for x in failures["errors"]}
        targets = [
            row
            for row in audit["image_occurrences"]
            if (row["path"], row["source_key"]) in failed
        ]
        if {(row["path"], row["source_key"]) for row in targets} != failed:
            raise ValueError("Image repair failures do not match audited sources")
    manifest = json.loads((store.root / "resources-manifest.json").read_text("utf-8"))
    resources = {
        normalized_original(manifest["source"] + "/" + r["relative_path"]): r
        for r in manifest["completed"]
    }
    model_digest = local_model_digest(model)
    extractor = "course-tiled-visual-v1/" + POLICY + "/" + model + "@" + model_digest
    completed, errors, seen, checked, notebooks = [], [], set(), set(), {}
    committed_tiles = 0

    def progress(stage, **extra):
        store.write_json(
            kind + "-progress.json",
            {
                "stage": stage,
                "pid": __import__("os").getpid(),
                "updated": time.time(),
                "processed": len(completed),
                "errors": len(errors),
                "total": len(targets),
                "committed_tile_occurrences": committed_tiles,
                **extra,
            },
        )

    for target in targets:
        store.checkpoint()
        if limit and len(completed) + len(errors) >= limit:
            break
        if notebook:
            image = target["image"]
            digest = image["embedded_sha256"]
            locator = (
                json.dumps(
                    image["location"],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + " "
                + image["mime"]
            )
            source_key = "notebook image " + target["source_key"] + " " + locator
            parent_digest = target["raw_digest"]
        else:
            digest = target["sha256"]
            source_key = "image tiled visual " + target["source_key"]
            parent_digest = digest
        identity = (target["path"], source_key)
        if identity in seen:
            raise ValueError("Duplicate tiled image source identity")
        seen.add(identity)
        progress("preparing_tiles", source_image_digest=digest)
        image_tiles = []
        done = 0
        try:
            raw = (
                notebook_image_bytes(store, target, notebooks)
                if notebook
                else retained_image_bytes(store, target, resources, checked)
            )
            image_tiles = retain_tiles(store, raw, digest)
            for tile in image_tiles:
                store.checkpoint()
                key = source_key + " tile " + "-".join(map(str, tile["box"]))
                if (
                    store.document_revision(
                        target["path"], key, parent_digest, extractor
                    )
                    is None
                ):
                    content, inference = describe_tile(
                        store,
                        tile,
                        model,
                        chat,
                        lease,
                        model_digest=model_digest,
                        progress=progress,
                        wait=wait,
                    )
                    body = (
                        "原分辨率裁切图转录与图示解读（模型草稿，需对照原图复核）\n原图区域 "
                        + str(tile["box"])
                        + "\n"
                        + content
                    )
                    metadata = {
                        **tile,
                        **inference,
                        "parent_source_key": target["source_key"],
                        "parent_raw_digest": parent_digest,
                        "model": model,
                        "model_digest": model_digest,
                        "review_required": True,
                        "static_image_only": True,
                        "raw_source_retained": True,
                    }
                    if notebook:
                        metadata.update(
                            notebook_image_location=image["location"],
                            notebook_image_mime=image["mime"],
                        )
                    progress("saving", source_image_digest=digest, tile_box=tile["box"])
                    store.save_document_versions(
                        [
                            {
                                "path": target["path"],
                                "source_key": key,
                                "raw_digest": parent_digest,
                                "extractor": extractor,
                                "chunks": [
                                    (key + " chars " + str(n), body[n : n + 3000])
                                    for n in range(0, len(body), 3000)
                                ],
                                "metadata": metadata,
                            }
                        ]
                    )
                committed_tiles += 1
                done += 1
            completed.append(
                {
                    "path": target["path"],
                    "parent_source_key": target["source_key"],
                    "source_key": source_key,
                    "parent_raw_digest": parent_digest,
                    "source_image_digest": digest,
                    "tiles": image_tiles,
                    "all_planned_tiles_completed": True,
                }
            )
        except InterruptedError:
            progress("paused")
            raise
        except Exception as exc:
            errors.append(
                {
                    "path": target["path"],
                    "source_key": source_key,
                    "source_image_digest": digest,
                    "completed_tiles": done,
                    "planned_tiles": len(image_tiles),
                    "error": type(exc).__name__ + ": " + str(exc),
                }
            )
        store.write_json(
            kind + "-report.json",
            {
                "completed": completed,
                "errors": errors,
                "requested": len(targets),
                "limit": limit,
            },
        )
    result = {
        "completed": completed,
        "errors": errors,
        "requested": len(targets),
        "limit": limit,
        "extractor": extractor,
        "originals_retained": True,
        "review_required": True,
        "full_requested_scope_processed": not limit
        and not errors
        and len(completed) == len(targets),
    }
    store.write_json(kind + "-report.json", result)
    progress("finished_with_errors" if errors else "batch_finished", limit=limit)
    return result


def main():
    import argparse
    from .course_knowledge import KnowledgeStore, gpu_lease, local_chat, schema_lease

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--failures", type=Path)
    parser.add_argument("--model", default="qwen3.5:9b")
    args = parser.parse_args()
    store = KnowledgeStore(args.root)
    kind = "image-repairs" if args.failures else "notebook-images"
    with schema_lease(store.root / (kind + ".lock"), timeout=0):
        result = index_visual_sources(
            store,
            args.audit,
            args.model,
            local_chat,
            gpu_lease,
            failures_path=args.failures,
        )
    print(
        json.dumps(
            {"completed": len(result["completed"]), "errors": len(result["errors"])}
        )
    )


if __name__ == "__main__":
    main()
