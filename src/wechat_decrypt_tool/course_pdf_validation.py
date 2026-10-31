"""Independent PDF source/page-pixel/cache/search/citation coverage validation."""

from __future__ import annotations

import hashlib
import io
import json
import re
import subprocess
import tempfile
import time
from pathlib import Path

TILED_POLICY = "original-resolution-tiles-v1/size1200-overlap64-split-long-axis-depth6"
BODY_PREFIX = "页面转录与图示解读（模型草稿，需对照页面复核）\n"


def require(condition, reason):
    if not condition:
        raise RuntimeError(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def coverage(width, height, boxes):
    require(
        boxes
        and all(
            len(b) == 4
            and all(type(v) is int for v in b)
            and 0 <= b[0] < b[2] <= width
            and 0 <= b[1] < b[3] <= height
            for b in boxes
        ),
        "PDF tile bounds differ",
    )
    boundaries = sorted({0, height, *[v for b in boxes for v in (b[1], b[3])]})
    for top, bottom in zip(boundaries, boundaries[1:]):
        end = 0
        for left, right in sorted(
            (left, right)
            for left, first, right, last in boxes
            if first <= top and last >= bottom
        ):
            require(left <= end, "PDF pixel coverage has a gap")
            end = max(end, right)
        require(end == width, "PDF pixel coverage has a gap")


def native_render(pdf, page, destination, poppler):
    result = subprocess.run(
        [
            str(poppler),
            "-f",
            str(page),
            "-l",
            str(page),
            "-singlefile",
            "-scale-to",
            "2000",
            "-r",
            "150",
            "-png",
            str(pdf),
            str(destination.with_suffix("")),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=120,
        creationflags=0x08000000 if __import__("os").name == "nt" else 0,
    )
    require(
        result.returncode == 0 and destination.is_file(),
        "Independent PDF rerender failed",
    )
    # Native messages may include source text/filenames. Only a digest and count leave memory.
    return {"stderr_bytes": len(result.stderr), "stderr_sha256": sha(result.stderr)}


def checked_asset(root, relative, namespace):
    path = (root / relative).resolve()
    require(
        path.is_relative_to((root / namespace).resolve()),
        "PDF asset is outside retained namespace",
    )
    return path


def cache_body(store, metadata, extractor, image):
    """Rebuild committed prose from accepted caches and all original-resolution crops."""
    from PIL import Image

    root = store.root
    image_path = root / metadata["image"]
    binding = metadata["model"] + "@" + metadata["model_digest"]
    renderer = extractor.split("/", 2)[1]
    candidates = [extractor, "pdf-vision-v2/" + renderer + "/" + binding]
    cache = None
    for policy in dict.fromkeys(candidates):
        candidate = image_path.with_name(
            image_path.stem + "-" + sha(policy.encode())[:20] + ".json"
        )
        if candidate.is_file():
            checked_asset(root, candidate.relative_to(root), "document-visuals")
            cache = json.loads(candidate.read_text("utf-8"))
            break
    require(cache is not None, "PDF accepted page cache is missing")
    require(
        cache.get("image_digest") == metadata["image_digest"]
        and cache.get("model") == metadata["model"]
        and cache.get("model_digest") == metadata["model_digest"]
        and isinstance(cache.get("text"), str)
        and cache["text"].strip(),
        "PDF page cache provenance differs",
    )
    tiles = metadata.get("tile_coverage")
    require(
        cache.get("tile_coverage") == tiles, "PDF cache/metadata tile coverage differs"
    )
    if not tiles:
        return BODY_PREFIX + cache["text"], 0
    width, height = image.size
    require(
        tiles.get("all_planned_tiles_completed")
        and tiles["source_width"] == width
        and tiles["source_height"] == height,
        "PDF tiled source dimensions differ",
    )
    coverage(width, height, [x["box"] for x in tiles["tiles"]])
    pieces = []
    regions_checked = 0
    for tile in tiles["tiles"]:
        box = tile["box"]
        crop_path = (
            root
            / "document-visuals/tiled-images"
            / metadata["image_digest"]
            / ("-".join(map(str, box)) + ".png")
        )
        checked_asset(
            root, crop_path.relative_to(root), "document-visuals/tiled-images"
        )
        raw = crop_path.read_bytes()
        with Image.open(io.BytesIO(raw)) as saved:
            rgba = image.crop(box).convert("RGBA")
            expected = Image.alpha_composite(
                Image.new("RGBA", rgba.size, "white"), rgba
            ).convert("RGB")
            require(
                saved.size == expected.size
                and saved.convert("RGB").tobytes() == expected.tobytes(),
                "PDF retained tile pixels differ",
            )
        policy = TILED_POLICY + "/" + binding
        identity = sha((sha(raw) + "\0" + policy).encode())
        accepted_root = root / "document-visuals/tile-ocr-cache" / identity
        regions = tile["inference_regions"]
        coverage(box[2] - box[0], box[3] - box[1], regions)
        texts = []
        for region in regions:
            accepted_file = accepted_root / ("-".join(map(str, region)) + ".json")
            checked_asset(
                root, accepted_file.relative_to(root), "document-visuals/tile-ocr-cache"
            )
            accepted = json.loads(accepted_file.read_text("utf-8"))
            require(
                accepted.get("policy") == policy
                and accepted.get("image_digest") == sha(raw)
                and isinstance(accepted.get("text"), str)
                and accepted["text"].strip()
                and not accepted.get("split"),
                "PDF accepted inference region differs",
            )
            texts.append(
                "裁切图内部区域 "
                + str(region)
                + "（重叠可能重复）\n"
                + accepted["text"]
            )
            regions_checked += 1
        pieces.append("页面原图区域 " + str(box) + "\n" + "\n\n".join(texts))
    content = (
        "页面按原渲染像素分块转录（重叠可能重复，需对照整页复核）\n"
        + "\n\n".join(pieces)
    )
    require(
        content == cache["text"], "PDF tiled page text differs from accepted regions"
    )
    return BODY_PREFIX + content, regions_checked


def verify_pdf_archive(
    store, plan_path, poppler, *, mode="final", limit=0, rerender=None
):
    """Final mode proves the entire attempted scope; committed mode is explicitly provisional."""
    from PIL import Image
    from pypdf import PdfReader
    from fastapi.testclient import TestClient
    from .course_attachments import resolve_reference, retained_reference
    from .course_knowledge import create_app, tokens

    require(
        mode in {"final", "committed"} and type(limit) is int and limit >= 0,
        "Invalid PDF validation scope",
    )
    require(
        mode != "final" or limit == 0, "Final PDF validation cannot use a page limit"
    )
    plan = json.loads(Path(plan_path).read_text("utf-8"))
    expected = {}
    for target in plan["pdf_sources_all_pages"]:
        require(
            type(target["pages"]) is int
            and 0 < target["pages"] <= 2000
            and re.fullmatch("[a-f0-9]{64}", target["parent_raw_digest"]),
            "Invalid PDF frozen source plan",
        )
        for page in range(1, target["pages"] + 1):
            identity = (target["path"], target["source_key"], page)
            require(
                identity not in expected,
                "PDF frozen plan has duplicate page occurrences",
            )
            expected[identity] = target
    require(
        len(expected) == plan["summary"]["page_occurrences"],
        "PDF frozen plan page count differs",
    )
    errors = []
    if mode == "final":
        report = json.loads((store.root / "pdf-ocr-report.json").read_text("utf-8"))
        require(
            report.get("limit") == 0
            and report.get("full_requested_scope_attempted")
            and not report.get("deferred_gpu"),
            "PDF full attempted batch has not finished",
        )
        completed = [
            (x["path"], x["source_key"], x["page"]) for x in report["completed"]
        ]
        errors = [(x["path"], x["source_key"], x.get("page")) for x in report["errors"]]
        require(
            len(set(completed)) == len(completed)
            and len(set(errors)) == len(errors)
            and not set(completed).intersection(errors)
            and set(completed).union(errors) == set(expected),
            "PDF complete/error partition differs from frozen full scope",
        )
        require(
            report.get("processed") == len(completed)
            and report.get("attempted_page_occurrences") == len(expected),
            "PDF attempted/completed counters differ",
        )
    else:
        completed = []
        with store.connect() as db:
            for identity in expected:
                path, key, page = identity
                row = db.execute(
                    "SELECT extractor FROM resource_revisions WHERE path=? AND source_key=?",
                    (path, "visual " + key + " page " + str(page)),
                ).fetchone()
                if row and row[0].startswith(
                    ("pdf-vision-v2/", "pdf-vision-v3-tiled-v1/")
                ):
                    completed.append(identity)
                    if limit and len(completed) == limit:
                        break
    require(completed, "No committed PDF page is available for validation")
    executable_digest = sha(Path(poppler).read_bytes())
    renderer = executable_digest[:16] + "-scale2000-dpi150"
    native = rerender or native_render
    originals, checked_pixels, warnings = {}, set(), {}
    source_downloads = set()
    chunks = regions = routes = cross_renderer_pages = 0
    original_renderers = set()
    result_name = (
        "pdf-full-validation.json"
        if mode == "final"
        else "pdf-committed-validation.json"
    )
    with (
        tempfile.TemporaryDirectory(
            prefix="pdf-validation-", dir=store.root
        ) as temporary,
        TestClient(create_app(store, "unused-local-validation")) as client,
    ):
        for number, identity in enumerate(completed):
            store.checkpoint()
            store.write_json(
                result_name.replace(".json", "-progress.json"),
                {
                    "stage": "verifying_source_pixels_cache_text_and_fts",
                    "pid": __import__("os").getpid(),
                    "updated": time.time(),
                    "completed": number,
                    "total": len(completed),
                    "requested_page_occurrences": len(expected),
                    "mode": mode,
                },
            )
            path, parent_key, page = identity
            target = expected[identity]
            raw_digest = target["parent_raw_digest"]
            if raw_digest not in originals:
                original = resolve_reference(
                    store, retained_reference(store, path, parent_key, raw_digest)
                )["path"]
                with original.open("rb") as source:
                    native_pages = len(PdfReader(source).pages)
                originals[raw_digest] = (original, native_pages)
            require(
                originals[raw_digest][1] == target["pages"],
                "PDF native page count differs from frozen source",
            )
            key = "visual " + parent_key + " page " + str(page)
            with store.connect() as db:
                db.execute("PRAGMA cache_size=-131072")
                revision = db.execute(
                    "SELECT * FROM resource_revisions WHERE path=? AND source_key=?",
                    (path, key),
                ).fetchone()
                require(
                    revision
                    and revision["extractor"].startswith(
                        ("pdf-vision-v2/", "pdf-vision-v3-tiled-v1/")
                    )
                    and revision["raw_digest"] == raw_digest,
                    "PDF committed revision binding differs",
                )
                meta = json.loads(revision["metadata"])
                require(
                    meta["parent_source_key"] == parent_key
                    and meta["parent_raw_digest"] == raw_digest
                    and meta["page"] == page
                    and meta["review_required"]
                    and meta.get("rendered_page_max_dimension", 2000) == 2000,
                    "PDF parent/page/review metadata differs",
                )
                binding = meta["model"] + "@" + meta["model_digest"]
                source_renderer = revision["extractor"].split("/", 2)[1]
                require(
                    re.fullmatch(r"[a-f0-9]{16}-scale2000-dpi150", source_renderer)
                    and revision["extractor"].split("/", 2)[2] == binding,
                    "PDF renderer/model provenance differs",
                )
                original_renderers.add(source_renderer)
                require(
                    revision["revision"]
                    == sha(
                        (
                            raw_digest + "\0" + revision["extractor"] + "\0" + key
                        ).encode()
                    ),
                    "PDF revision digest differs",
                )
                wanted = (
                    f"document-visuals/{source_renderer}/{raw_digest}/{page:05}.png"
                )
                require(
                    meta["image"].replace("\\", "/") == wanted,
                    "PDF page-image locator differs",
                )
                image_path = checked_asset(
                    store.root, meta["image"], "document-visuals"
                )
                raw = image_path.read_bytes()
                require(
                    sha(raw) == meta["image_digest"], "PDF retained image hash differs"
                )
                with Image.open(io.BytesIO(raw)) as image:
                    require(
                        max(image.size) <= 2000,
                        "PDF rendered dimensions exceed declared policy",
                    )
                    native_key = (raw_digest, page, source_renderer, renderer)
                    if native_key not in checked_pixels:
                        rendered = Path(temporary) / "page.png"
                        diagnostics = native(
                            originals[raw_digest][0], page, rendered, poppler
                        )
                        with Image.open(rendered) as independent:
                            require(
                                independent.size == image.size
                                and independent.convert("RGBA").tobytes()
                                == image.convert("RGBA").tobytes(),
                                "PDF retained pixels differ from independent original rerender",
                            )
                        checked_pixels.add(native_key)
                        if diagnostics.get("stderr_bytes"):
                            warnings[str(native_key)] = diagnostics
                    cross_renderer_pages += source_renderer != renderer
                    body, accepted_regions = cache_body(
                        store, meta, revision["extractor"], image
                    )
                rows = list(
                    db.execute(
                        "SELECT * FROM documents WHERE path=? AND source_key=? AND active=1",
                        (path, key),
                    )
                )
                require(
                    rows
                    and all(
                        r["digest"] == revision["revision"]
                        and r["raw_digest"] == raw_digest
                        and r["extractor"] == revision["extractor"]
                        for r in rows
                    ),
                    "PDF document revision differs",
                )
                require(
                    {r["locator"]: r["text"] for r in rows}
                    == {
                        key + " chars " + str(n): body[n : n + 3000]
                        for n in range(0, len(body), 3000)
                    },
                    "PDF complete cached text/chunk coverage differs",
                )
                history = db.execute(
                    "SELECT metadata FROM document_version_metadata WHERE path=? AND source_key=? AND revision=?",
                    (path, key, revision["revision"]),
                ).fetchone()
                require(
                    history and json.loads(history[0]) == meta,
                    "PDF historical metadata differs",
                )
                for row in rows:
                    require(
                        row["search_text"]
                        == " ".join(tokens(Path(path).name + " " + row["text"])),
                        "PDF searchable text differs",
                    )
                    term = max(tokens(row["text"]), key=len)
                    require(
                        db.execute(
                            "SELECT rowid FROM document_fts WHERE document_fts MATCH ? AND rowid=?",
                            ('"' + term.replace('"', '""') + '"', row["id"]),
                        ).fetchone(),
                        "PDF FTS posting is missing",
                    )
            doc = rows[0]["id"]
            source_identity = (path, parent_key)
            if source_identity not in source_downloads:
                source = client.get("/document-source/" + str(doc))
                require(
                    source.status_code == 200 and sha(source.content) == raw_digest,
                    "PDF original download differs",
                )
                source_downloads.add(source_identity)
                del source
            visual = client.get("/document-visual/" + str(doc))
            citation = client.get("/evidence/" + str(-1000000000 - doc))
            require(
                visual.status_code == 200
                and sha(visual.content) == meta["image_digest"]
                and citation.status_code == 200
                and "/document-source/" in citation.text
                and "/document-visual/" in citation.text,
                "PDF page/citation routes differ",
            )
            chunks += len(rows)
            regions += accepted_regions
            routes += 1
    accepted = mode == "final" and not errors and len(completed) == len(expected)
    result = {
        "all_requested_pdf_page_occurrences_verified": accepted,
        "full_requested_scope_attempted_verified": mode == "final",
        "mode": mode,
        "requested_page_occurrences": len(expected),
        "verified_completed_page_occurrences": len(completed),
        "failed_page_occurrences": len(errors),
        "unique_original_pdf_sources_verified": len(originals),
        "unique_original_pages_rerendered": len(checked_pixels),
        "active_chunks_verified": chunks,
        "accepted_tiled_inference_regions_verified": regions,
        "source_download_route_groups": len(source_downloads),
        "page_visual_and_citation_route_groups": routes,
        "native_warning_pages": len(warnings),
        "native_warning_diagnostics": warnings,
        "rendering_max_dimension": 2000,
        "validation_renderer_executable_sha256": executable_digest,
        "validation_renderer_policy": renderer,
        "original_renderer_policies": sorted(original_renderers),
        "cross_renderer_page_occurrences_with_exact_rgba_replay": cross_renderer_pages,
        "historical_renderer_executable_availability_not_claimed": True,
        "original_small_glyph_layout_and_model_semantics_independently_proven": False,
        "source_deletion_allowed": False,
        "no_course_content_emitted": True,
        "updated": time.time(),
    }
    store.write_json(result_name, result)
    store.write_json(
        result_name.replace(".json", "-progress.json"),
        {
            "stage": "batch_finished"
            if accepted or mode == "committed"
            else "finished_with_errors",
            "updated": time.time(),
            "pid": __import__("os").getpid(),
            "completed": len(completed),
            "errors": len(errors),
            "total": len(expected),
            "mode": mode,
        },
    )
    return result


def main():
    import argparse
    from .course_knowledge import KnowledgeStore, schema_lease

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--poppler", type=Path, required=True)
    parser.add_argument("--mode", choices=["final", "committed"], default="final")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    store = KnowledgeStore(args.root)
    with schema_lease(store.root / "pdf-independent-validation.lock", timeout=0):
        result = verify_pdf_archive(
            store, args.plan, args.poppler, mode=args.mode, limit=args.limit
        )
    print(
        json.dumps(
            {
                k: result[k]
                for k in (
                    "mode",
                    "all_requested_pdf_page_occurrences_verified",
                    "verified_completed_page_occurrences",
                    "failed_page_occurrences",
                    "active_chunks_verified",
                    "source_deletion_allowed",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
