"""Independent frame-pixel, timing, cache, text and citation checks for animations."""

from __future__ import annotations

import hashlib
import io
import json
import time
from pathlib import Path


def verify_animations(store, audit_path):
    from PIL import Image, ImageChops
    from fastapi.testclient import TestClient
    from .course_animations import animation_evidence
    from .course_knowledge import create_app, tokens

    def require(condition, reason):
        if not condition:
            raise RuntimeError(reason)

    def sha(raw):
        return hashlib.sha256(raw).hexdigest()

    def coverage(width, height, boxes):
        require(
            boxes
            and all(
                0 <= left < right <= width and 0 <= top < bottom <= height
                for left, top, right, bottom in boxes
            ),
            "Animation tile bounds differ",
        )
        cuts = sorted({0, height, *[v for box in boxes for v in (box[1], box[3])]})
        for top, bottom in zip(cuts, cuts[1:]):
            end = 0
            for left, right in sorted(
                (left, right)
                for left, first, right, last in boxes
                if first <= top and last >= bottom
            ):
                require(left <= end, "Animation pixel coverage has a gap")
                end = max(end, right)
            require(end == width, "Animation pixel coverage has a gap")

    root = store.root
    audit = json.loads(Path(audit_path).read_text("utf-8"))
    targets = {
        (x["path"], x["source_key"]): x
        for x in audit["image_occurrences"]
        if x["state"] == "animated_raw_retained_motion_audit_pending"
    }
    report = json.loads((root / "animation-ocr-report.json").read_text("utf-8"))
    require(report["requested"] == len(targets), "Animation requested scope differs")
    seen, manifests, pixels = set(), {}, set()
    chunks = tiles_checked = partial = routes_checked = 0
    with TestClient(create_app(store, "unused-local-validation")) as client:
        for item in report["completed"]:
            identity = (item["path"], item["source_key"])
            require(
                identity in targets and identity not in seen,
                "Animation source scope differs",
            )
            seen.add(identity)
            target = targets[identity]
            digest = target["sha256"]
            require(item["source_sha256"] == digest, "Animation parent hash differs")
            if digest not in manifests:
                manifests[digest] = animation_evidence(store, digest, rehash=True)
            manifest = manifests[digest]
            require(manifest is not None, "Native animation manifest is missing")
            groups = {}
            for frame in manifest["frames"]:
                groups.setdefault(frame["rgba_sha256"], []).append(frame)
            require(
                item["declared_frames"] == manifest["declared_frames"]
                and item["retained_frames"] == len(manifest["frames"])
                and item["unique_composited_frames"] == len(groups)
                and item["complete_frame_decode"] == manifest["complete_frame_decode"]
                and item["source_errors"] == manifest["errors"],
                "Animation report frame inventory differs",
            )
            partial += not manifest["complete_frame_decode"]
            manifest_file = "document-animations/" + digest + ".json"
            manifest_digest = sha((root / manifest_file).read_bytes())
            prefix = "animation visual " + target["source_key"] + " pixels "
            with store.connect() as db:
                revisions = list(
                    db.execute(
                        "SELECT * FROM resource_revisions WHERE path=? AND raw_digest=? "
                        "AND extractor LIKE 'course-animation-vision-v1/%'",
                        (target["path"], digest),
                    )
                )
            revisions = [
                r
                for r in revisions
                if json.loads(r["metadata"]).get("parent_source_key")
                == target["source_key"]
            ]
            grouped = {}
            for revision in revisions:
                meta = json.loads(revision["metadata"])
                key = revision["source_key"]
                require(key.startswith(prefix), "Animation source locator differs")
                frame_hash = key[len(prefix) :].split(" tile ")[0]
                require(
                    frame_hash in groups, "Animation OCR references an unknown frame"
                )
                grouped.setdefault(frame_hash, []).append((revision, meta))
            require(set(grouped) == set(groups), "Animation frame OCR coverage differs")
            source_tiles = 0
            first_doc = None
            first_crop_digest = None
            for frame_hash, frames in groups.items():
                original = frames[0]
                positions = [
                    {
                        k: f[k]
                        for k in (
                            "index",
                            "poster",
                            "start_ms",
                            "end_ms",
                            "duration_ms",
                            "disposal",
                            "blend",
                        )
                    }
                    for f in frames
                ]
                entries = grouped[frame_hash]
                coverage(*original["size"], [m["box"] for _, m in entries])
                for revision, meta in entries:
                    key = (
                        prefix + frame_hash + " tile " + "-".join(map(str, meta["box"]))
                    )
                    require(
                        revision["source_key"] == key, "Animation tile locator differs"
                    )
                    require(
                        meta["parent_raw_digest"] == digest
                        and meta["animation_source_sha256"] == digest
                        and meta["animation_manifest"] == manifest_file
                        and meta["animation_manifest_digest"] == manifest_digest
                        and meta["animation_frame_occurrences"] == positions
                        and meta["complete_source_frame_decode"]
                        == manifest["complete_frame_decode"]
                        and meta["media"] == manifest["original"]["file"]
                        and meta["media_digest"] == digest
                        and meta["source_image_digest"] == original["image"]["sha256"]
                        and [meta["source_width"], meta["source_height"]]
                        == original["size"]
                        and meta["review_required"]
                        and meta["all_requested_tile_regions_completed"],
                        "Animation metadata provenance or positions differ",
                    )
                    image_path = (root / meta["image"]).resolve()
                    require(
                        image_path.is_relative_to(
                            (root / "document-visuals").resolve()
                        ),
                        "Animation crop path is unsafe",
                    )
                    png = image_path.read_bytes()
                    require(
                        sha(png) == meta["image_digest"], "Animation crop hash differs"
                    )
                    pixel_key = (frame_hash, tuple(meta["box"]))
                    if pixel_key not in pixels:
                        with Image.open(root / original["image"]["file"]) as source:
                            rgba = source.crop(meta["box"]).convert("RGBA")
                            expected = Image.alpha_composite(
                                Image.new("RGBA", rgba.size, "white"), rgba
                            ).convert("RGB")
                        with Image.open(io.BytesIO(png)) as saved:
                            require(
                                saved.size == expected.size
                                and ImageChops.difference(
                                    saved.convert("RGB"), expected
                                ).getbbox()
                                is None,
                                "Animation retained crop pixels differ",
                            )
                        pixels.add(pixel_key)
                    policy = meta["inference_policy"]
                    require(
                        policy.endswith(
                            "/" + meta["model"] + "@" + meta["model_digest"]
                        ),
                        "Animation model cache policy differs",
                    )
                    cache = (
                        root
                        / "document-visuals"
                        / "tile-ocr-cache"
                        / sha((meta["image_digest"] + "\0" + policy).encode())
                    )
                    regions = meta["inference_regions"]
                    coverage(
                        meta["box"][2] - meta["box"][0],
                        meta["box"][3] - meta["box"][1],
                        regions,
                    )
                    texts = []
                    for box in regions:
                        accepted = json.loads(
                            (cache / ("-".join(map(str, box)) + ".json")).read_text(
                                "utf-8"
                            )
                        )
                        require(
                            accepted["policy"] == policy
                            and accepted["image_digest"] == meta["image_digest"]
                            and accepted.get("text")
                            and not accepted.get("split"),
                            "Animation accepted inference cache differs",
                        )
                        texts.append(
                            "裁切图内部区域 "
                            + str(box)
                            + "（重叠可能重复）\n"
                            + accepted["text"]
                        )
                    body = (
                        "动画逐帧画面转录与解读（模型草稿，须复核；未推断相邻帧间动作）\n"
                        + json.dumps(positions, ensure_ascii=False)
                        + "\n"
                        + "\n\n".join(texts)
                    )
                    with store.connect() as db:
                        rows = list(
                            db.execute(
                                "SELECT * FROM documents WHERE path=? AND source_key=? AND active=1",
                                (target["path"], key),
                            )
                        )
                        require(
                            rows
                            and all(
                                r["digest"] == revision["revision"]
                                and r["raw_digest"] == digest
                                for r in rows
                            ),
                            "Animation active revision differs",
                        )
                        require(
                            {r["locator"]: r["text"] for r in rows}
                            == {
                                key + " chars " + str(n): body[n : n + 3000]
                                for n in range(0, len(body), 3000)
                            },
                            "Animation indexed text differs from accepted cache",
                        )
                        history = db.execute(
                            "SELECT metadata FROM document_version_metadata WHERE path=? AND source_key=? AND revision=?",
                            (target["path"], key, revision["revision"]),
                        ).fetchone()
                        require(
                            history and json.loads(history[0]) == meta,
                            "Animation historical metadata differs",
                        )
                        for row in rows:
                            terms = tokens(row["text"])
                            require(
                                terms
                                and db.execute(
                                    "SELECT rowid FROM document_fts WHERE document_fts MATCH ? AND rowid=?",
                                    (
                                        '"' + terms[0].replace('"', '""') + '"',
                                        row["id"],
                                    ),
                                ).fetchone(),
                                "Animation FTS binding differs",
                            )
                    if first_doc is None:
                        first_doc = rows[0]["id"]
                        first_crop_digest = meta["image_digest"]
                    source_tiles += 1
                    chunks += len(rows)
            require(
                source_tiles == item["tile_occurrences"], "Animation tile count differs"
            )
            require(first_doc is not None, "Animation has no retained OCR citation")
            media = client.get("/animation-source/" + str(first_doc))
            crop = client.get("/document-visual/" + str(first_doc))
            source = client.get("/document-source/" + str(first_doc))
            response = client.get("/animation-manifest/" + str(first_doc))
            evidence = client.get("/evidence/" + str(-1000000000 - first_doc))
            require(
                media.status_code
                == source.status_code
                == response.status_code
                == crop.status_code
                == evidence.status_code
                == 200
                and sha(media.content) == sha(source.content) == digest
                and sha(crop.content) == first_crop_digest
                and response.json() == manifest
                and "/animation-source/" in evidence.text
                and "/document-source/" in evidence.text,
                "Animation source or evidence routes differ",
            )
            tiles_checked += source_tiles
            routes_checked += 1
    failed = {(x["path"], x["source_key"]) for x in report["errors"]}
    require(
        len(failed) == len(report["errors"])
        and not (failed & seen)
        and failed <= set(targets),
        "Animation error scope differs",
    )
    full = (
        not report["limit"]
        and report.get("full_requested_scope_attempted")
        and seen | failed == set(targets)
    )
    result = {
        "all_reported_completed_sources_verified": True,
        "full_requested_ocr_scope_verified": bool(full and not failed),
        "requested_source_occurrences": len(targets),
        "verified_source_occurrences": len(seen),
        "source_decode_partial_occurrences": partial,
        "ocr_errors": len(failed),
        "verified_tile_occurrences": tiles_checked,
        "verified_active_chunks": chunks,
        "unique_crops_every_pixel_verified": len(pixels),
        "source_route_groups_verified": routes_checked,
        "all_reported_cache_text_positions_revision_and_fts_bindings_verified": True,
        "model_outputs_remain_review_drafts": True,
        "source_deletion_allowed": False,
        "no_course_contents_emitted": True,
        "updated": time.time(),
    }
    store.write_json("animation-ocr-validation.json", result)
    return result


def main():
    import argparse
    from .course_knowledge import KnowledgeStore

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify_animations(KnowledgeStore(args.root), args.audit)))


if __name__ == "__main__":
    main()
