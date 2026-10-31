"""Static SVG rendering and local tiled OCR with explicit derived-pixel provenance."""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from xml.etree import ElementTree

POLICY = "static-svg-resvg-v1/no-active-or-external-resources/derived-pixels"
RESVG_VERSION = "2.6.2"
RESERVE = 32 * 1024**3
BUDGET = 512 * 1024**2
MAX_PIXELS = 64_000_000
NODE_RENDER = r"""
const fs=require('fs');
try {
  const cfg=JSON.parse(fs.readFileSync(process.argv[4],'utf8'));
  const {Resvg}=require(process.argv[1]);
  const raw=fs.readFileSync(process.argv[2]);
  const svg=new Resvg(raw,{fitTo:{mode:'width',value:cfg.width},dpi:96,
    languages:['zh-CN','en'],logLevel:'warn',font:{loadSystemFonts:false,
    fontFiles:cfg.fonts,defaultFontFamily:'Microsoft YaHei',
    sansSerifFamily:'Microsoft YaHei',serifFamily:'Microsoft YaHei',
    monospaceFamily:'Consolas'}});
  if (!(svg.width>0 && svg.height>0 && Number.isFinite(svg.width) && Number.isFinite(svg.height))) throw 0;
  if (cfg.width*Math.ceil(svg.height*cfg.width/svg.width)>64000000) throw 0;
  if (svg.imagesToResolve().length) throw 0;
  const img=svg.render();
  if (!(img.width>0 && img.height>0 && img.width*img.height<=64000000)) throw 0;
  fs.writeFileSync(process.argv[3],img.asPng());
  process.stdout.write(JSON.stringify({sourceCanvas:[svg.width,svg.height],
    rasterSize:[img.width,img.height],unresolvedImages:0}));
} catch (_) { process.stderr.write('Static SVG renderer rejected input');process.exit(1); }
"""


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def warning_summary(raw):
    text = raw.decode("utf-8", "replace")
    normalized, clocks = re.subn(
        r"(?m)^(\s*(?:\x1b\[[0-9;]*m)*\[?)\d{4}-\d{2}-\d{2}[T ][0-9:.+\-]+Z?",
        r"\1[LOG_CLOCK]",
        text,
    )
    return {
        "warning_bytes": len(raw),
        "warnings_sha256": sha(normalized.encode()),
        "warning_digest_policy": "utf8-replacement-iso8601-log-clocks-stripped-v1",
        "log_clock_fields_removed": clocks,
        "warning_lines": len(text.splitlines()),
        "warning_technical_word_counts": {
            word: sum(word in line.lower() for line in text.splitlines())
            for word in (
                "font",
                "glyph",
                "attribute",
                "property",
                "parse",
                "style",
                "filter",
                "path",
                "text",
            )
        },
        "renderer_warnings_require_review": bool(raw),
    }


def inspect_static_svg(raw):
    if len(raw) > 32 * 1024**2:
        raise ValueError("SVG byte bound exceeded")
    text = raw.decode("utf-8-sig")
    if re.search(r"<!DOCTYPE|<!ENTITY|<\?(?!xml\s)", text, re.I) or "\0" in text:
        raise ValueError("SVG declarations or processing instructions are unsupported")
    root = ElementTree.fromstring(text)
    if root.tag != "{http://www.w3.org/2000/svg}svg":
        raise ValueError("Expected a namespaced SVG root")
    nodes = list(root.iter())
    if len(nodes) > 100000:
        raise ValueError("SVG node bound exceeded")
    active = {
        "script",
        "foreignobject",
        "animate",
        "animatemotion",
        "animatetransform",
        "animatecolor",
        "discard",
        "animation",
        "set",
        "iframe",
        "object",
        "embed",
        "audio",
        "video",
        "handler",
    }
    refs = embedded = 0
    embedded_images = []
    for node in nodes:
        tag = node.tag.rsplit("}", 1)[-1].lower()
        if tag in active:
            raise ValueError("Active or animated SVG elements are unsupported")
        styles = [node.text or ""] if tag == "style" else []
        for key, value in node.attrib.items():
            name = key.rsplit("}", 1)[-1].lower()
            if (
                name.startswith("on")
                or key == "{http://www.w3.org/XML/1998/namespace}base"
            ):
                raise ValueError("SVG event handlers or base URLs are unsupported")
            if name in {"href", "src"}:
                value = value.strip()
                if value.startswith("#"):
                    refs += 1
                elif re.fullmatch(
                    r"data:image/(?:png|jpeg|jpg|webp|gif);base64,[A-Za-z0-9+/=\s]+",
                    value,
                    re.I,
                ):
                    import base64
                    from PIL import Image

                    payload = base64.b64decode(
                        "".join(value.split(",", 1)[1].split()), validate=True
                    )
                    with Image.open(io.BytesIO(payload)) as image:
                        if (
                            getattr(image, "n_frames", 1) != 1
                            or image.width * image.height > MAX_PIXELS
                        ):
                            raise ValueError(
                                "SVG embedded raster is animated or exceeds pixel bound"
                            )
                        image.load()
                        embedded_images.append(
                            {
                                "sha256": sha(payload),
                                "size": list(image.size),
                                "format": image.format,
                            }
                        )
                    embedded += 1
                else:
                    raise ValueError(
                        "SVG external or nested-vector resource is unsupported"
                    )
            if name in {
                "style",
                "fill",
                "stroke",
                "filter",
                "mask",
                "clip-path",
                "cursor",
            }:
                styles.append(value)
        for style in styles:
            if "\\" in style or re.search(
                r"@import|@font-face|@(?:-webkit-)?keyframes|(?:^|[;{])\s*(?:-webkit-)?animation(?:-[a-z]+)?\s*:|expression\s*\(|/\*",
                style,
                re.I,
            ):
                raise ValueError("Unsupported SVG stylesheet resource syntax")
            for resource in re.findall(r"url\s*\((.*?)\)", style, re.I | re.S):
                if not resource.strip().strip("\"'").startswith("#"):
                    raise ValueError("SVG stylesheet external resource is unsupported")
    return {
        "nodes": len(nodes),
        "internal_references": refs,
        "embedded_raster_references": embedded,
        "embedded_rasters": embedded_images,
        "source_dimensions": {
            k: root.attrib[k]
            for k in ("width", "height", "viewBox", "preserveAspectRatio")
            if k in root.attrib
        },
    }


def renderer_configuration(runtime, fonts, width):
    runtime = Path(runtime).resolve()
    node = shutil.which("node")
    package = runtime / "node_modules/@resvg/resvg-js"
    if (
        not node
        or json.loads((package / "package.json").read_text("utf-8"))["version"]
        != RESVG_VERSION
    ):
        raise RuntimeError("Pinned local resvg runtime is unavailable")
    binaries = list((runtime / "node_modules/@resvg").glob("*/*.node"))
    if len(binaries) != 1:
        raise RuntimeError("Expected exactly one pinned local resvg native binary")
    font_paths = [Path(x).resolve() for x in fonts]
    if not font_paths or not all(p.is_file() for p in font_paths):
        raise ValueError("Explicit local SVG fonts are required")
    config = {
        "engine": "@resvg/resvg-js@" + RESVG_VERSION,
        "node_sha256": sha(Path(node).read_bytes()),
        "native_sha256": sha(binaries[0].read_bytes()),
        "renderer_script_sha256": sha(NODE_RENDER.encode()),
        "fonts": [{"name": p.name, "sha256": sha(p.read_bytes())} for p in font_paths],
        "width": width,
        "dpi": 96,
        "system_font_loading": False,
        "default_font_family": "Microsoft YaHei",
        "monospace_family": "Consolas",
    }
    return node, package, font_paths, config


def render_svg(raw, runtime, fonts, width):
    from PIL import Image

    if type(width) is not int or not 1 <= width <= 4096:
        raise ValueError("SVG raster width is out of bounds")
    node, package, fonts, config = renderer_configuration(runtime, fonts, width)
    with tempfile.TemporaryDirectory(prefix="course-svg-") as temporary:
        temporary = Path(temporary)
        source, output, options = (
            temporary / name for name in ("source.svg", "render.png", "options.json")
        )
        source.write_bytes(raw)
        options.write_text(
            json.dumps({"width": width, "fonts": list(map(str, fonts))}), "utf-8"
        )
        proc = subprocess.run(
            [
                node,
                "-e",
                NODE_RENDER,
                str(package),
                str(source),
                str(output),
                str(options),
            ],
            capture_output=True,
            timeout=90,
            creationflags=0x08000000 if __import__("os").name == "nt" else 0,
        )
        if proc.returncode or not output.is_file():
            raise RuntimeError("Local static SVG renderer failed; source retained")
        result = json.loads(proc.stdout)
        png = output.read_bytes()
        with Image.open(io.BytesIO(png)) as image:
            image.load()
            if (
                image.format != "PNG"
                or list(image.size) != result["rasterSize"]
                or image.width * image.height > MAX_PIXELS
            ):
                raise RuntimeError("SVG raster output dimensions differ")
        return png, {
            **config,
            **result,
            **warning_summary(proc.stderr),
            "renderer_fidelity_not_independently_proven": True,
        }


def svg_evidence(store, digest, *, rehash=False):
    from PIL import Image

    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        return None
    directory = store.root / "document-vectors"
    path = directory / (digest + ".json")
    if not path.exists():
        return None
    value = json.loads(path.read_text("utf-8"))
    if value.get("policy") != POLICY or value.get("source_sha256") != digest:
        raise RuntimeError("SVG rendering provenance policy differs")
    for key, suffix in (("original", ".svg"), ("render", ".png")):
        asset = value[key]
        asset_path = (store.root / asset["file"]).resolve()
        expected = (
            directory
            / ("sources" if key == "original" else "renders")
            / (digest + suffix)
        )
        if (
            asset_path != expected.resolve()
            or not asset_path.is_file()
            or asset_path.stat().st_size != asset["bytes"]
        ):
            raise RuntimeError("SVG retained asset is missing or unsafe")
        if rehash and sha(asset_path.read_bytes()) != asset["sha256"]:
            raise RuntimeError("SVG retained asset hash differs")
    if value["original"]["sha256"] != digest:
        raise RuntimeError("SVG retained source hash differs")
    if rehash:
        if (
            inspect_static_svg((store.root / value["original"]["file"]).read_bytes())
            != value["structure"]
        ):
            raise RuntimeError("SVG source structure differs")
        with Image.open(store.root / value["render"]["file"]) as image:
            rgba = image.convert("RGBA")
            if (
                list(image.size) != value["renderer"]["rasterSize"]
                or sha(str(rgba.size).encode() + rgba.tobytes()) != value["rgba_sha256"]
            ):
                raise RuntimeError("SVG rendered pixels differ")
    return value


def prepare_svg(store, raw, digest, *, runtime, fonts, width=2400):
    from PIL import Image
    from .course_knowledge import atomic_json, schema_lease

    if sha(raw) != digest:
        raise ValueError("SVG input hash differs from frozen source")
    structure = inspect_static_svg(raw)
    with schema_lease(store.root / "svg-native-assets.lock", timeout=60):
        existing = svg_evidence(store, digest, rehash=True)
        if existing:
            if existing["renderer"]["width"] != width:
                raise RuntimeError("Existing immutable SVG render width differs")
            return existing
        store.checkpoint()
        png, renderer = render_svg(raw, runtime, fonts, width)
        directory = store.root / "document-vectors"
        used = (
            sum(p.stat().st_size for p in directory.rglob("*") if p.is_file())
            if directory.exists()
            else 0
        )
        if used + len(raw) + len(png) > BUDGET or shutil.disk_usage(
            store.root
        ).free < RESERVE + len(raw) + len(png):
            raise InterruptedError("SVG asset budget or disk reserve reached")
        assets = {}
        for name, suffix, data in (("original", ".svg", raw), ("render", ".png", png)):
            path = (
                directory
                / ("sources" if name == "original" else "renders")
                / (digest + suffix)
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and sha(path.read_bytes()) != sha(data):
                raise RuntimeError(
                    "Uncommitted SVG asset differs; preserve for inspection"
                )
            if not path.exists():
                with tempfile.NamedTemporaryFile(
                    dir=path.parent, delete=False
                ) as stream:
                    part = Path(stream.name)
                    stream.write(data)
                part.replace(path)
            assets[name] = {
                "file": str(path.relative_to(store.root)).replace("\\", "/"),
                "sha256": sha(data),
                "bytes": len(data),
            }
        with Image.open(io.BytesIO(png)) as image:
            rgba = image.convert("RGBA")
            pixels = sha(str(rgba.size).encode() + rgba.tobytes())
        value = {
            "policy": POLICY,
            "source_sha256": digest,
            "structure": structure,
            "renderer": renderer,
            **assets,
            "rgba_sha256": pixels,
            "coordinate_space": "Derived SVG raster pixels at recorded render size; original vector bytes retained; no native SVG pixel resolution claimed",
            "source_deletion_allowed": False,
        }
        atomic_json(directory / (digest + ".json"), value)
        return svg_evidence(store, digest, rehash=True)


def index_svgs(store, audit_path, model, chat, lease, *, wait=True, limit=0):
    from .course_attachments import normalized_original
    from .course_image_ocr import retained_image_bytes
    from .course_tiled_ocr import (
        POLICY as TILE_POLICY,
        describe_tile,
        local_model_digest,
        retain_tiles,
    )

    audit = json.loads(Path(audit_path).read_text("utf-8"))
    targets = [
        x
        for x in audit["image_occurrences"]
        if x["state"] == "vector_raw_retained_render_audit_pending"
    ]
    manifest = json.loads((store.root / "resources-manifest.json").read_text("utf-8"))
    resources = {
        normalized_original(manifest["source"] + "/" + x["relative_path"]): x
        for x in manifest["completed"]
    }
    model_digest = local_model_digest(model)
    completed, errors, seen, checked = [], [], set(), set()

    def progress(stage, **extra):
        store.write_json(
            "svg-images-progress.json",
            {
                "stage": stage,
                "pid": __import__("os").getpid(),
                "updated": time.time(),
                "processed": len(completed),
                "errors": len(errors),
                "total": len(targets),
                **extra,
            },
        )

    for target in targets:
        if limit and len(completed) + len(errors) >= limit:
            break
        store.checkpoint()
        identity = (target["path"], target["source_key"])
        if identity in seen:
            raise ValueError("Duplicate SVG source identity")
        seen.add(identity)
        tiles = []
        try:
            progress("checking_native_svg_provenance")
            raw = retained_image_bytes(store, target, resources, checked)
            native = svg_evidence(store, sha(raw), rehash=True)
            if native is None:
                raise RuntimeError(
                    "Native SVG render must be prepared and verified before GPU OCR"
                )
            manifest_file = "document-vectors/" + target["sha256"] + ".json"
            manifest_digest = sha((store.root / manifest_file).read_bytes())
            extractor = (
                "course-svg-visual-v1/"
                + POLICY
                + "/"
                + manifest_digest
                + "/"
                + TILE_POLICY
                + "/"
                + model
                + "@"
                + model_digest
            )
            raster = (store.root / native["render"]["file"]).read_bytes()
            tiles = retain_tiles(store, raster, native["render"]["sha256"])
            for tile in tiles:
                tile["coordinate_space"] = native["coordinate_space"]
            source_key = "svg rendered visual " + target["source_key"]
            for tile in tiles:
                key = source_key + " tile " + "-".join(map(str, tile["box"]))
                if (
                    store.document_revision(
                        target["path"], key, target["sha256"], extractor
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
                        "SVG 渲染图裁切转录与解读（模型草稿，需复核；坐标为派生渲染像素，原始矢量文件另存）\n渲染图区域 "
                        + str(tile["box"])
                        + "\n"
                        + content
                    )
                    metadata = {
                        **tile,
                        **inference,
                        "parent_source_key": target["source_key"],
                        "parent_raw_digest": target["sha256"],
                        "model": model,
                        "model_digest": model_digest,
                        "review_required": True,
                        "raw_source_retained": True,
                        "svg_manifest": manifest_file,
                        "svg_manifest_digest": manifest_digest,
                        "svg_coordinate_space": native["coordinate_space"],
                        "svg_renderer": native["renderer"],
                        "coordinate_space": native["coordinate_space"],
                    }
                    store.save_document_versions(
                        [
                            {
                                "path": target["path"],
                                "source_key": key,
                                "raw_digest": target["sha256"],
                                "extractor": extractor,
                                "chunks": [
                                    (key + " chars " + str(n), body[n : n + 3000])
                                    for n in range(0, len(body), 3000)
                                ],
                                "metadata": metadata,
                            }
                        ]
                    )
            completed.append(
                {
                    "path": target["path"],
                    "parent_source_key": target["source_key"],
                    "source_key": source_key,
                    "parent_raw_digest": target["sha256"],
                    "source_image_digest": native["render"]["sha256"],
                    "tiles": tiles,
                    "svg_manifest": manifest_file,
                    "svg_manifest_digest": manifest_digest,
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
                    "source_key": target["source_key"],
                    "error_type": type(exc).__name__,
                    "reason": str(exc),
                }
            )
        store.write_json(
            "svg-images-report.json",
            {
                "completed": completed,
                "errors": errors,
                "requested": len(targets),
                "limit": limit,
                "full_requested_scope_processed": not limit
                and not errors
                and len(completed) == len(targets),
            },
        )
    progress("finished_with_errors" if errors else "batch_finished")
    return {"completed": completed, "errors": errors, "requested": len(targets)}


def verify_svg_render(store, digest, *, runtime, fonts):
    """Re-render original bytes and compare all RGBA pixels, without source-drive reads."""
    from PIL import Image

    native = svg_evidence(store, digest, rehash=True)
    if native is None:
        raise RuntimeError("Retained SVG manifest is missing")
    raw = (store.root / native["original"]["file"]).read_bytes()
    png, renderer = render_svg(raw, runtime, fonts, native["renderer"]["width"])
    if renderer != native["renderer"]:
        raise RuntimeError(
            "SVG renderer, fonts, dimensions or warning provenance differs"
        )
    with (
        Image.open(io.BytesIO(png)) as repeated,
        Image.open(store.root / native["render"]["file"]) as archived,
    ):
        if (
            repeated.size != archived.size
            or repeated.convert("RGBA").tobytes() != archived.convert("RGBA").tobytes()
        ):
            raise RuntimeError("SVG repeated rendering pixels differ")
    return {
        "source_sha256": digest,
        "raw_hash_and_structure_verified": True,
        "all_repeated_raster_pixels_identical": True,
        "raster_size": renderer["rasterSize"],
        "font_and_renderer_hashes_verified": True,
        "renderer_warning_bytes": renderer["warning_bytes"],
        "semantic_renderer_fidelity_not_claimed": True,
    }


def main():
    import argparse
    from .course_attachments import normalized_original
    from .course_image_ocr import retained_image_bytes
    from .course_knowledge import KnowledgeStore, gpu_lease, local_chat, schema_lease

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--font", type=Path, action="append", required=True)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--model", default="qwen3.5:9b")
    args = parser.parse_args()
    store = KnowledgeStore(args.root)
    targets = [
        x
        for x in json.loads(args.audit.read_text("utf-8"))["image_occurrences"]
        if x["state"] == "vector_raw_retained_render_audit_pending"
    ]
    manifest = json.loads((store.root / "resources-manifest.json").read_text("utf-8"))
    resources = {
        normalized_original(manifest["source"] + "/" + x["relative_path"]): x
        for x in manifest["completed"]
    }
    completed, seen, checked = [], set(), set()
    with schema_lease(store.root / "svg-preparation.lock", timeout=0):
        for target in targets:
            digest = target["sha256"]
            if digest in seen:
                continue
            store.checkpoint()
            raw = retained_image_bytes(store, target, resources, checked)
            prepare_svg(store, raw, digest, runtime=args.runtime, fonts=args.font)
            completed.append(
                verify_svg_render(store, digest, runtime=args.runtime, fonts=args.font)
            )
            seen.add(digest)
            store.write_json(
                "svg-native-validation.json",
                {
                    "requested_sources": len({x["sha256"] for x in targets}),
                    "source_occurrences": len(targets),
                    "completed": completed,
                    "all_requested_source_renders_verified": len(seen)
                    == len({x["sha256"] for x in targets}),
                    "source_deletion_allowed": False,
                    "updated": time.time(),
                },
            )
    if not args.prepare_only:
        with schema_lease(store.root / "svg-images.lock", timeout=0):
            result = index_svgs(store, args.audit, args.model, local_chat, gpu_lease)
        print(
            json.dumps(
                {"completed": len(result["completed"]), "errors": len(result["errors"])}
            )
        )
    else:
        print(
            json.dumps(
                {
                    "native_source_renders_verified": len(completed),
                    "source_occurrences": len(targets),
                    "gpu_ocr_pending": True,
                }
            )
        )


if __name__ == "__main__":
    main()
