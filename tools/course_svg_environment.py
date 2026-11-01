"""Preserve the exact local SVG renderer and fonts as a separate companion bundle."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

from tools.course_extraction_environment_copy import capacity, copy_one, file_sha
from wechat_decrypt_tool.course_knowledge import atomic_json
from wechat_decrypt_tool.course_svg_visuals import NODE_RENDER, renderer_configuration

POLICY = "local-svg-renderer-companion-v1"
FONT_NAMES = ("msyh.ttc", "msyhbd.ttc", "arial.ttf", "consola.ttf")
SYNTHETIC = '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="100"><rect width="400" height="100" fill="white"/><text x="12" y="40" font-family="Microsoft YaHei" font-size="24">本地环境 Synthetic 123</text><text x="12" y="80" font-family="Consolas" font-size="18">value = 42</text></svg>'
PROBE = r"""const fs=require('fs'),p=require('path'),c=require('crypto'),M=require('module');
const root=__dirname, plan=JSON.parse(fs.readFileSync(p.join(root,'svg-environment.json'),'utf8'));
const hash=b=>c.createHash('sha256').update(b).digest('hex');
const inside=s=>{if(typeof s!=='string'||s.includes(String.fromCharCode(92))||s.includes(':')||s.split('/').some(x=>!x||x==='.'||x==='..'))throw Error('unsafe_bundle_path');return p.join(root,...s.split('/'));};
const names=new Set();for(const r of plan.files){const f=inside(r.path);if(names.has(r.path.toLowerCase()))throw Error('duplicate_bundle_path');names.add(r.path.toLowerCase());if(fs.statSync(f).size!==r.bytes||hash(fs.readFileSync(f))!==r.sha256)throw Error('bundle_payload_changed');}
if(plan.policy!=='local-svg-renderer-companion-v1'||plan.system_font_loading!==false)throw Error('bundle_policy_changed');
if(p.resolve(process.execPath).toLowerCase()!==p.join(root,'node/node.exe').toLowerCase())throw Error('bundled_node_required');
const modules=[],resolve=M._resolveFilename;
M._resolveFilename=function(...args){const f=resolve.apply(this,args);if(p.isAbsolute(f)){const rel=p.relative(root,f);if(rel.startsWith('..')||p.isAbsolute(rel))throw Error('outside_bundle_module');modules.push(rel);}return f;};
const fonts=plan.fonts.map(f=>inside(f));
const {Resvg}=require(p.join(root,'runtime/node_modules/@resvg/resvg-js'));
const image=new Resvg(plan.synthetic_svg,{fitTo:{mode:'width',value:512},dpi:96,languages:['zh-CN','en'],logLevel:'warn',font:{loadSystemFonts:false,fontFiles:fonts,defaultFontFamily:'Microsoft YaHei',sansSerifFamily:'Microsoft YaHei',serifFamily:'Microsoft YaHei',monospaceFamily:'Consolas'}}).render();
const pixels=image.asPng(),digest=hash(pixels);
if(plan.expected_synthetic_png_sha256&&digest!==plan.expected_synthetic_png_sha256)throw Error('restored_svg_render_changed');
process.stdout.write(JSON.stringify({files_verified:plan.files.length,png_sha256:digest,width:image.width,height:image.height,resolved_bundle_modules:modules.length,system_font_loading:false,gpu_inference_verified:false,source_deletion_allowed:false}));
"""


def safe(root, relative):
    if (
        not isinstance(relative, str)
        or "\\" in relative
        or ":" in relative
        or any(p in {"", ".", ".."} for p in relative.split("/"))
    ):
        raise ValueError("svg_environment_unsafe_path")
    path = root.joinpath(*relative.split("/"))
    if not path.resolve().is_relative_to(root.resolve()) or path.is_symlink():
        raise ValueError("svg_environment_unsafe_path")
    return path


def verify(root):
    root = Path(root).resolve()
    value = json.loads((root / "svg-environment.json").read_bytes())
    if value["policy"] != POLICY or value["system_font_loading"] is not False:
        raise ValueError("svg_environment_policy_changed")
    seen = set()
    for row in value["files"]:
        path = safe(root, row["path"])
        key = row["path"].casefold()
        if key in seen:
            raise ValueError("svg_environment_duplicate_path")
        seen.add(key)
        if path.stat().st_size != row["bytes"] or file_sha(path) != row["sha256"]:
            raise ValueError("svg_environment_payload_changed")
    required = {
        "node/node.exe",
        "probe.js",
        "production-render.js",
        "runtime/node_modules/@resvg/resvg-js/index.js",
        "runtime/node_modules/@resvg/resvg-js/package.json",
        "runtime/node_modules/@resvg/resvg-js-win32-x64-msvc/resvgjs.win32-x64-msvc.node",
        *("fonts/" + name for name in FONT_NAMES),
    }
    if not required <= seen or value["fonts"] != ["fonts/" + n for n in FONT_NAMES]:
        raise ValueError("svg_environment_required_payload_missing")
    return value


def probe(root):
    root = Path(root).resolve()
    verify(root)
    result = subprocess.run(
        [str(root / "node/node.exe"), str(root / "probe.js")],
        cwd=root,
        env={**os.environ, "NODE_PATH": "", "NODE_OPTIONS": ""},
        capture_output=True,
        timeout=60,
    )
    if result.returncode:
        raise ValueError(
            "svg_environment_probe_failed_" + hashlib.sha256(result.stderr).hexdigest()
        )
    return json.loads(result.stdout)


def create(runtime, fonts_root, destination, reference, *, reserve=32 * 1024**3):
    runtime, fonts_root, destination = map(
        lambda p: Path(p).resolve(), (runtime, fonts_root, destination)
    )
    if destination.exists():
        raise ValueError("svg_environment_destination_exists")
    fonts = [fonts_root / n for n in FONT_NAMES]
    node, _, _, config = renderer_configuration(runtime, fonts, 2400)
    expected = reference["renderer"]
    if any(
        config[k] != expected[k]
        for k in (
            "engine",
            "node_sha256",
            "native_sha256",
            "renderer_script_sha256",
            "fonts",
        )
    ):
        raise ValueError("svg_environment_original_renderer_changed")
    sources = [(Path(node), "node/node.exe")]
    sources.extend(
        (p, "runtime/" + p.relative_to(runtime).as_posix())
        for p in sorted(runtime.rglob("*"))
        if p.is_file()
    )
    sources.extend((p, "fonts/" + p.name) for p in fonts)
    if any(
        destination.is_relative_to(p) or p.is_relative_to(destination)
        for p in (runtime, fonts_root)
    ) or any(p.is_relative_to(destination) or p == destination for p, _ in sources):
        raise ValueError("svg_environment_source_overlap")
    capacity(destination, sum(p.stat().st_size for p, _ in sources), reserve)
    destination.mkdir(parents=True)
    rows = []
    for source, relative in sources:
        st = source.stat()
        row = {"path": relative, "bytes": st.st_size, "mtime_ns": st.st_mtime_ns}
        rows.append(
            copy_one(
                source,
                safe(destination, relative),
                row,
                capacity_check=lambda p, n: capacity(p, n, reserve),
            )
        )
    for name, text in [("probe.js", PROBE), ("production-render.js", NODE_RENDER)]:
        path = destination / name
        path.write_text(text, encoding="utf-8")
        rows.append(
            {"path": name, "bytes": path.stat().st_size, "sha256": file_sha(path)}
        )
    manifest = {
        "policy": POLICY,
        "files": rows,
        "fonts": ["fonts/" + n for n in FONT_NAMES],
        "production_renderer": config,
        "system_font_loading": False,
        "synthetic_svg": SYNTHETIC,
        "expected_synthetic_png_sha256": None,
        "source_deletion_allowed": False,
    }
    atomic_json(destination / "svg-environment.json", manifest)
    result = probe(destination)
    manifest["expected_synthetic_png_sha256"] = result["png_sha256"]
    atomic_json(destination / "svg-environment.json", manifest)
    return probe(destination)


def restore(root, destination, *, reserve=32 * 1024**3):
    root, destination = Path(root).resolve(), Path(destination).resolve()
    manifest = verify(root)
    if (
        destination.exists()
        or destination.is_relative_to(root)
        or root.is_relative_to(destination)
    ):
        raise ValueError("svg_environment_restore_overlap_or_exists")
    capacity(destination, sum(r["bytes"] for r in manifest["files"]), reserve)
    destination.mkdir(parents=True)
    for row in manifest["files"]:
        target = safe(destination, row["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(safe(root, row["path"]), target)
    shutil.copyfile(root / "svg-environment.json", destination / "svg-environment.json")
    verify(destination)
    return probe(destination)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode", choices=["create", "verify", "probe", "restore"])
    p.add_argument("--root", required=True, type=Path)
    p.add_argument("--destination", type=Path)
    p.add_argument("--fonts", type=Path)
    p.add_argument("--reference", type=Path)
    a = p.parse_args()
    if a.mode == "create":
        if not all((a.destination, a.fonts, a.reference)):
            p.error("create requires --destination, --fonts, --reference")
        result = create(
            a.root, a.fonts, a.destination, json.loads(a.reference.read_bytes())
        )
    elif a.mode == "restore":
        if not a.destination:
            p.error("restore requires --destination")
        result = restore(a.root, a.destination)
    elif a.mode == "probe":
        result = probe(a.root)
    else:
        result = {"files_verified": len(verify(a.root)["files"])}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
