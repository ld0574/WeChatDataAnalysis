"""Read-only acceptance of every successful primary static-image occurrence.

This verifies retained pixels and committed evidence, not model semantics. Failed
primary occurrences remain an exact, separately reported repair scope. No model,
original course file, schema migration or archive write is needed for this audit.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import time
from pathlib import Path


def require(value, reason):
    if not value:
        raise RuntimeError(reason)


def sha(value):
    return hashlib.sha256(value).hexdigest()


def asset(root, relative, namespace):
    path = (root / relative).resolve()
    require(
        path.is_relative_to((root / namespace).resolve()),
        "Static image evidence escaped retained namespace",
    )
    return path


def identities(rows):
    result = {(row["path"], row["source_key"]) for row in rows}
    require(len(result) == len(rows), "Duplicate static image source identity")
    return result


def accepted_body(root, metadata, size):
    """Replay the accepted region tree without trusting its coverage boolean."""
    width, height = size
    binding = metadata["model"] + "@" + metadata["model_digest"]
    policy = "image-vision-v1-overlap64-depth3/" + binding
    relative = "document-visuals/image-ocr-cache/" + sha(
        (metadata["image_digest"] + "\0" + policy).encode()
    )
    cache = asset(root, relative, "document-visuals/image-ocr-cache")
    manifest = json.loads(
        asset(root, cache / "manifest.json", relative).read_text("utf-8")
    )
    require(
        manifest.get("source_digest") == metadata["image_digest"]
        and manifest.get("model_digest") == metadata["model_digest"]
        and manifest.get("policy") == policy
        and manifest.get("width") == width
        and manifest.get("height") == height
        and manifest.get("raw_image_retained") == metadata["image"]
        and manifest.get("review_required") is True
        and manifest.get("full_vertical_extent_covered") is True,
        "Static image cache manifest binding differs",
    )

    def visit(top, bottom, depth):
        path = asset(root, cache / f"{top}-{bottom}.json", relative)
        value = json.loads(path.read_text("utf-8"))
        require(
            value.get("source_digest") == metadata["image_digest"]
            and value.get("policy") == policy,
            "Static image region identity differs",
        )
        if isinstance(value.get("text"), str) and value["text"].strip():
            require(not value.get("split"), "Split region accepted as final OCR")
            return [(top, bottom, value["text"])]
        require(
            value.get("split") is True and depth < 3 and bottom - top >= 160,
            "Static image region has no complete accepted OCR",
        )
        middle = (top + bottom) // 2
        overlap = min(32, (bottom - top) // 8)
        return visit(top, middle + overlap, depth + 1) + visit(
            middle - overlap, bottom, depth + 1
        )

    regions = visit(0, height, 0)
    require(
        manifest.get("regions")
        == [{"top": top, "bottom": bottom} for top, bottom, _ in regions],
        "Static image cache region list differs from accepted tree",
    )
    if len(regions) == 1:
        text = regions[0][2]
    else:
        text = (
            "原图分区转录（重叠区域可能重复；请对照完整原图复核跨区域内容）\n"
            + "\n\n".join(
                f"区域 y={top}–{bottom}（原图高度 {height}）\n{text}"
                for top, bottom, text in regions
            )
        )
    return "教学图片转录与图示解读（模型草稿，需对照原图复核）\n" + text


def validate_primary_images(
    store, *, audit_name="teaching-image-audit.json", report_name="images-ocr-report.json",
    progress=lambda **_: None
):
    from PIL import Image, ImageChops, ImageOps

    from .course_attachments import normalized_original, safe_member
    from .course_image_ocr import retained_image_bytes
    from .course_knowledge import tokens

    started = time.monotonic()
    names = [audit_name, report_name, "resources-manifest.json"]
    frozen = {name: (store.root / name).read_bytes() for name in names}
    audit, report, manifest = [json.loads(frozen[name]) for name in names]
    rows = [
        row
        for row in audit["image_occurrences"]
        if row["state"] == "raster_ocr_candidate"
    ]
    targets = identities(rows)
    completed, failed = identities(report["completed"]), identities(report["errors"])
    require(
        targets
        and report.get("limit") == 0
        and report.get("deferred_gpu") is False
        and report.get("requested") == len(targets)
        and not completed & failed
        and completed | failed == targets,
        "Primary static image report does not partition the full requested scope",
    )
    resources = {}
    for row in manifest["completed"]:
        key = normalized_original(
            manifest["source"] + "/" + safe_member(row["relative_path"])
        )
        require(key not in resources, "Ambiguous retained resource identity")
        resources[key] = row
    checked, pixels, bodies = set(), set(), {}
    verified = chunks = 0
    with store.connect() as db:
        db.execute("BEGIN")  # Stable WAL read view; never obtains the writer lease.
        try:
            for row in rows:
                store.checkpoint()
                identity = (row["path"], row["source_key"])
                if identity in failed:
                    continue
                progress(
                    stage="checking_primary_static_evidence",
                    verified=verified,
                    requested_successful=len(completed),
                    original_failures=len(failed),
                )
                raw = retained_image_bytes(store, row, resources, checked)
                key = "image visual " + row["source_key"]
                revision = db.execute(
                    "SELECT * FROM resource_revisions WHERE path=? AND source_key=?",
                    (row["path"], key),
                ).fetchone()
                require(revision is not None, "Static image committed revision missing")
                metadata = json.loads(revision["metadata"])
                binding = metadata["model"] + "@" + metadata["model_digest"]
                extractor = "teaching-image-vision-v1-overlap64-depth3/" + binding
                digest = sha((row["sha256"] + "\0" + extractor + "\0" + key).encode())
                require(
                    revision["raw_digest"] == row["sha256"]
                    and revision["extractor"] == extractor
                    and revision["revision"] == digest
                    and metadata.get("parent_source_key") == row["source_key"]
                    and metadata.get("parent_raw_digest") == row["sha256"]
                    and all(
                        metadata.get(k) is True
                        for k in (
                            "review_required",
                            "static_image_only",
                            "raw_source_retained",
                        )
                    ),
                    "Static image source/revision binding differs",
                )
                expected_image = (
                    "document-visuals/teaching-images/" + row["sha256"] + "/image.png"
                )
                require(
                    metadata["image"].replace("\\", "/") == expected_image,
                    "Static image locator differs",
                )
                normalized = asset(
                    store.root, metadata["image"], "document-visuals/teaching-images"
                )
                pixel_key = (row["sha256"], metadata["image_digest"])
                if pixel_key not in pixels:
                    png = normalized.read_bytes()
                    require(
                        sha(png) == metadata["image_digest"],
                        "Static image bytes changed",
                    )
                    with (
                        Image.open(io.BytesIO(raw)) as original,
                        Image.open(io.BytesIO(png)) as saved,
                    ):
                        require(
                            getattr(original, "n_frames", 1) == 1,
                            "Static source is animated",
                        )
                        original = ImageOps.exif_transpose(original).convert("RGBA")
                        expected = Image.alpha_composite(
                            Image.new("RGBA", original.size, "white"), original
                        ).convert("RGB")
                        require(
                            saved.mode == "RGB"
                            and saved.size == expected.size
                            and ImageChops.difference(saved, expected).getbbox()
                            is None,
                            "Normalized static image pixels differ from retained original",
                        )
                        size = saved.size
                    pixels.add(pixel_key)
                else:
                    with Image.open(normalized) as saved:
                        size = saved.size
                cache_key = (row["sha256"], metadata["image_digest"], binding)
                if cache_key not in bodies:
                    bodies[cache_key] = accepted_body(store.root, metadata, size)
                body = bodies[cache_key]
                expected_chunks = {
                    key + " chars " + str(n): body[n : n + 3000]
                    for n in range(0, len(body), 3000)
                }
                documents = db.execute(
                    "SELECT * FROM documents WHERE path=? AND source_key=? AND active=1",
                    (row["path"], key),
                ).fetchall()
                require(
                    len(documents) == len(expected_chunks)
                    and {doc["locator"]: doc["text"] for doc in documents}
                    == expected_chunks,
                    "Static image chunks differ from accepted OCR cache",
                )
                historical = db.execute(
                    "SELECT metadata FROM document_version_metadata WHERE path=? AND source_key=? AND revision=?",
                    (row["path"], key, digest),
                ).fetchone()
                require(
                    historical and json.loads(historical[0]) == metadata,
                    "Static image immutable metadata differs",
                )
                for doc in documents:
                    require(
                        doc["raw_digest"] == row["sha256"]
                        and doc["digest"] == digest
                        and doc["extractor"] == extractor
                        and doc["search_text"]
                        == " ".join(tokens(Path(row["path"]).name + " " + doc["text"])),
                        "Static image search/source binding differs",
                    )
                    probe = max(
                        (
                            word
                            for word in set(tokens(doc["search_text"]))
                            if word.strip("_")
                        ),
                        key=lambda x: (len(x), x),
                    )
                    require(
                        db.execute(
                            "SELECT rowid FROM document_fts WHERE document_fts MATCH ? AND rowid=?",
                            ('"' + probe + '"', doc["id"]),
                        ).fetchone()
                        is not None,
                        "Static image FTS probe missing",
                    )
                chunks += len(documents)
                verified += 1
        finally:
            db.rollback()
    require(
        all((store.root / n).read_bytes() == frozen[n] for n in names),
        "Static image validation inputs changed during audit",
    )
    return {
        "primary_requested_occurrences": len(targets),
        "primary_successful_occurrences_verified": verified,
        "primary_failed_occurrences_requiring_separate_repair_acceptance": len(failed),
        "unique_normalized_images_every_pixel_verified": len(pixels),
        "accepted_cache_trees_reconstructed": len(bodies),
        "active_chunks_and_individual_fts_probes_verified": chunks,
        "all_primary_successful_occurrences_verified": verified == len(completed),
        "all_static_occurrences_accepted": verified == len(targets),
        "scope": "Every primary success; original failures require separate repair acceptance. Per-chunk FTS probes do not replace full native postings validation. OCR semantics remain review drafts.",
        "evidence_sha256": {n: sha(value) for n, value in frozen.items()},
        "archive_database_read_only": True,
        "models_invoked": False,
        "source_deletion_allowed": False,
        "seconds": time.monotonic() - started,
        "updated": time.time(),
    }


def main():
    import os

    from .course_knowledge import atomic_json, schema_lease
    from .course_video_visual_audit import ReadOnlyArchive

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    if os.name == "nt":
        import psutil

        process = psutil.Process()
        process.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
        process.ionice(psutil.IOPRIO_LOW)
    store = ReadOnlyArchive(args.root)
    with schema_lease(store.root / "primary-static-image-validation.lock", timeout=0):

        def progress(**fields):
            atomic_json(
                store.root / "primary-static-image-validation-progress.json",
                {**fields, "pid": __import__("os").getpid(), "updated": time.time()},
            )

        try:
            result = validate_primary_images(store, progress=progress)
            atomic_json(store.root / "primary-static-image-validation.json", result)
            progress(
                stage="batch_finished",
                verified=result["primary_successful_occurrences_verified"],
            )
            print(json.dumps(result))
        except BaseException as exc:
            progress(
                stage="stopped_with_error",
                error_type=type(exc).__name__,
                error_sha256=sha(str(exc).encode()),
            )
            raise


if __name__ == "__main__":
    main()
