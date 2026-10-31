"""Collect only committed page/accepted tiled-cache trees for portable snapshots."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

POLICY = "original-resolution-tiles-v1/size1200-overlap64-split-long-axis-depth6"


def visual_cache_assets(root, metadata, *, extractor=None):
    from PIL import Image

    root = Path(root).resolve()
    assets = {}

    def checked(relative):
        path = (root / relative).resolve()
        if not path.is_relative_to((root / "document-visuals").resolve()):
            raise ValueError("Committed visual cache outside retained namespace")
        return path

    def sha(raw):
        return hashlib.sha256(raw).hexdigest()

    def add(path):
        raw = path.read_bytes()
        assets[path.relative_to(root).as_posix()] = sha(raw)
        return raw

    def tiled(image_relative, image_digest, policy, regions):
        if not policy.startswith(POLICY + "/"):
            raise ValueError("Unsupported committed tile-cache policy")
        image_path = checked(image_relative)
        raw = image_path.read_bytes()
        if sha(raw) != image_digest:
            raise RuntimeError("Committed tile image changed before backup")
        with Image.open(image_path) as image:
            width, height = image.size
        cache_root = (
            root
            / "document-visuals/tile-ocr-cache"
            / sha((image_digest + "\0" + policy).encode())
        )
        leaves = []

        def visit(box, depth):
            path = checked(
                (cache_root / ("-".join(map(str, box)) + ".json")).relative_to(root)
            )
            value = json.loads(add(path))
            if (
                value.get("policy") != policy
                or value.get("image_digest") != image_digest
            ):
                raise RuntimeError("Committed tile-cache binding differs")
            if (
                isinstance(value.get("text"), str)
                and value["text"].strip()
                and not value.get("split")
            ):
                leaves.append(list(box))
                return
            if value.get("text") or not value.get("split") or depth >= 6:
                raise RuntimeError("Committed tile-cache tree is incomplete")
            left, top, right, bottom = box
            if max(right - left, bottom - top) < 160:
                raise RuntimeError(
                    "Committed tile-cache tree exceeds subdivision bound"
                )
            if right - left >= bottom - top:
                middle = (left + right) // 2
                overlap = min(32, (right - left) // 8)
                children = (
                    (left, top, middle + overlap, bottom),
                    (middle - overlap, top, right, bottom),
                )
            else:
                middle = (top + bottom) // 2
                overlap = min(32, (bottom - top) // 8)
                children = (
                    (left, top, right, middle + overlap),
                    (left, middle - overlap, right, bottom),
                )
            for child in children:
                visit(child, depth + 1)

        visit((0, 0, width, height), 0)
        if leaves != regions:
            raise RuntimeError("Committed tile-cache leaves differ from metadata")

    image = metadata.get("image", "").replace("\\", "/")
    if metadata.get("inference_policy"):
        tiled(
            image,
            metadata["image_digest"],
            metadata["inference_policy"],
            metadata["inference_regions"],
        )
    match = re.fullmatch(
        r"document-visuals/([a-f0-9]{16}-scale2000-dpi150)/([a-f0-9]{64})/(\d{5})\.png",
        image,
    )
    if not match:
        return assets
    binding = metadata["model"] + "@" + metadata["model_digest"]
    image_path = checked(image)
    accepted = None
    candidates = [extractor] if extractor else []
    if extractor is None or extractor.startswith("pdf-vision-v3-tiled-v1/"):
        candidates.append("pdf-vision-v3-tiled-v1/" + match[1] + "/" + binding)
    candidates.append("pdf-vision-v2/" + match[1] + "/" + binding)
    for cache_extractor in dict.fromkeys(candidates):
        candidate = checked(
            image_path.with_name(
                image_path.stem + "-" + sha(cache_extractor.encode())[:20] + ".json"
            ).relative_to(root)
        )
        if not candidate.exists():
            continue
        value = json.loads(candidate.read_bytes())
        if (
            value.get("image_digest") == metadata["image_digest"]
            and value.get("model") == metadata["model"]
            and value.get("model_digest") == metadata["model_digest"]
            and value.get("tile_coverage") == metadata.get("tile_coverage")
            and isinstance(value.get("text"), str)
            and value["text"].strip()
        ):
            add(candidate)
            accepted = value
            break
    if accepted is None:
        raise RuntimeError("Committed PDF page cache is missing or differs")
    coverage = metadata.get("tile_coverage")
    if coverage:
        if not coverage.get("all_planned_tiles_completed"):
            raise RuntimeError("Committed PDF tile coverage is incomplete")
        for tile in coverage["tiles"]:
            relative = (
                "document-visuals/tiled-images/"
                + metadata["image_digest"]
                + "/"
                + "-".join(map(str, tile["box"]))
                + ".png"
            )
            path = checked(relative)
            digest = sha(add(path))
            tiled(relative, digest, POLICY + "/" + binding, tile["inference_regions"])
    return assets
