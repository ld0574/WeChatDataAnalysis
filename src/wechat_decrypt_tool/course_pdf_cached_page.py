"""Commit a proven accepted PDF cache without inference or asset mutation."""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

from PIL import Image
from pypdf import PdfReader

from .course_attachments import resolve_reference, retained_reference
from .course_knowledge import schema_lease
from .course_pdf_plan import frozen_pdf_scope
from .course_pdf_validation import cache_body, checked_asset, native_render, sha


def require(ok, reason):
    if not ok:
        raise ValueError("pdf_cached_page_" + reason)


def recover(
    store,
    plan,
    renderer,
    *,
    model,
    model_digest,
    excluded_plans,
    before_commit,
    prefix,
    rerender=None,
):
    """Use the writer lease only after proving a single page disjoint from producers.

    The caller backs up proof/assets in before_commit before any database write.
    Neither this path nor its callbacks receive an inference or GPU-lease API.
    A failed backup after commit requires a separate result-backup repair.
    """
    root = store.root.resolve()
    scope = frozen_pdf_scope(plan)
    require(len(scope) == 1, "requires_one_explicit_page_occurrence")
    require(re.fullmatch(r"[a-z0-9][a-z0-9-]{1,100}", prefix), "invalid_report_prefix")
    require(not list(root.glob(prefix + "-*.json")), "report_already_exists")
    require(excluded_plans, "active_queue_scopes_required")
    (path, parent_key, page), target = next(iter(scope.items()))
    raw_digest = target["parent_raw_digest"]
    for excluded in excluded_plans:
        require(
            not any(
                v["parent_raw_digest"] == raw_digest and k[2] == page
                for k, v in frozen_pdf_scope(excluded).items()
            ),
            "overlaps_active_queue",
        )
    store.checkpoint()
    renderer = Path(renderer).resolve()
    renderer_digest = sha(renderer.read_bytes())
    renderer_policy = renderer_digest[:16] + "-scale2000-dpi150"
    require(
        isinstance(model, str)
        and model
        and re.fullmatch(r"[a-f0-9]{64}", model_digest),
        "invalid_model_binding",
    )
    extractor = (
        "pdf-vision-v3-tiled-v1/" + renderer_policy + "/" + model + "@" + model_digest
    )
    key = "visual " + parent_key + " page " + str(page)
    with schema_lease(
        root / ("pdf-cached-page-" + raw_digest + "-" + str(page) + ".lock"), timeout=0
    ):
        with store.connect() as db:
            for table in (
                "documents",
                "resource_revisions",
                "document_version_metadata",
            ):
                require(
                    db.execute(
                        "SELECT 1 FROM "
                        + table
                        + " WHERE path=? AND source_key=? LIMIT 1",
                        (path, key),
                    ).fetchone()
                    is None,
                    "page_has_prior_history",
                )
        reference = retained_reference(store, path, parent_key, raw_digest)
        source = resolve_reference(store, reference)["path"].resolve()
        require(
            source.is_relative_to(root / "resources")
            and sha(source.read_bytes()) == raw_digest,
            "retained_source_changed",
        )
        with source.open("rb") as stream:
            native_pages = len(PdfReader(stream).pages)
        require(native_pages == target["pages"], "native_page_count_differs")
        image_path = (
            root / "document-visuals" / renderer_policy / raw_digest / f"{page:05}.png"
        )
        checked_asset(root, image_path.relative_to(root), "document-visuals")
        image_digest = sha(image_path.read_bytes())
        cache_path = image_path.with_name(
            image_path.stem + "-" + sha(extractor.encode())[:20] + ".json"
        )
        checked_asset(root, cache_path.relative_to(root), "document-visuals")
        cache = json.loads(cache_path.read_bytes())
        metadata = {
            "parent_source_key": parent_key,
            "parent_raw_digest": raw_digest,
            "image": str(image_path.relative_to(root)),
            "image_digest": image_digest,
            "page": page,
            "model": model,
            "model_digest": model_digest,
            "review_required": True,
            "rendered_page_max_dimension": 2000,
        }
        if cache.get("tile_coverage"):
            metadata["tile_coverage"] = cache["tile_coverage"]
        with Image.open(image_path) as image:
            require(max(image.size) <= 2000, "retained_dimensions_exceed_policy")
            with tempfile.TemporaryDirectory(
                prefix="cached-pdf-proof-", dir=root
            ) as temp:
                independent_path = Path(temp) / "page.png"
                diagnostics = (rerender or native_render)(
                    source, page, independent_path, renderer
                )
                with Image.open(independent_path) as independent:
                    require(
                        independent.size == image.size
                        and independent.convert("RGBA").tobytes()
                        == image.convert("RGBA").tobytes(),
                        "native_pixels_differ",
                    )
            body, accepted_regions = cache_body(store, metadata, extractor, image)
        assets = {source, image_path, cache_path}
        for tile in (metadata.get("tile_coverage") or {}).get("tiles", []):
            crop = (
                root
                / "document-visuals/tiled-images"
                / image_digest
                / ("-".join(map(str, tile["box"])) + ".png")
            )
            checked_asset(root, crop.relative_to(root), "document-visuals/tiled-images")
            assets.add(crop)
            policy = (
                "original-resolution-tiles-v1/size1200-overlap64-split-long-axis-depth6/"
                + model
                + "@"
                + model_digest
            )
            cache_folder = (
                root
                / "document-visuals/tile-ocr-cache"
                / sha((sha(crop.read_bytes()) + "\0" + policy).encode())
            )
            for region in tile["inference_regions"]:
                accepted = cache_folder / ("-".join(map(str, region)) + ".json")
                checked_asset(
                    root, accepted.relative_to(root), "document-visuals/tile-ocr-cache"
                )
                assets.add(accepted)
        hashes = {
            p.relative_to(root).as_posix(): {
                "sha256": sha(p.read_bytes()),
                "bytes": p.stat().st_size,
            }
            for p in sorted(assets)
        }
        proof = {
            "state": "accepted_page_cache_native_pixels_and_complete_body_verified",
            "page": page,
            "parent_raw_digest": raw_digest,
            "native_pages": native_pages,
            "renderer_sha256": renderer_digest,
            "image_digest": image_digest,
            "body_sha256": sha(body.encode()),
            "body_characters": len(body),
            "accepted_regions": accepted_regions,
            "assets": hashes,
            "model_calls": 0,
            "gpu_lease_calls": 0,
            "excluded_queue_overlap": False,
            "native_diagnostics": diagnostics,
            "source_deletion_allowed": False,
            "semantic_accuracy_verified": False,
        }
        before_commit(proof)
        for name, item in hashes.items():
            require(
                sha((root / name).read_bytes()) == item["sha256"],
                "proved_asset_changed_before_commit",
            )
        store.checkpoint()
        # The shared writer lease and require_absent guard reject duplicate commits.
        store.save_document_versions(
            [
                {
                    "path": path,
                    "source_key": key,
                    "raw_digest": raw_digest,
                    "extractor": extractor,
                    "require_absent": True,
                    "metadata": metadata,
                    "chunks": [
                        (key + " chars " + str(i), body[i : i + 3000])
                        for i in range(0, len(body), 3000)
                    ],
                }
            ]
        )
        report = {
            "processed": 1,
            "completed": [{"path": path, "source_key": parent_key, "page": page}],
            "errors": [],
            "limit": 0,
            "full_requested_scope_attempted": True,
            "attempted_page_occurrences": 1,
            "deferred_gpu": False,
            "model_calls": 0,
            "gpu_lease_calls": 0,
            "entire_pdf_scope_claimed": False,
        }
        store.write_json(prefix + "-ocr-report.json", report)
        return proof
