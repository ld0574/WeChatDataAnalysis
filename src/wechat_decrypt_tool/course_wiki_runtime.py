"""Offline Windows reader runtime, separate from Wiki data and GPU tooling."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import shutil
import sys
import time
from pathlib import Path, PurePosixPath

from packaging.requirements import Requirement

from .course_backup import file_hash
from .course_knowledge import atomic_json

POLICY = "offline-windows-wiki-reader-runtime-v1"
ROOTS = ("fastapi", "uvicorn", "psutil", "Pillow", "httpx", "jsonschema")
MANIFEST = "reader-runtime.json"

BOOTSTRAP = '''"""Launch with this bundle's Python, without a system installation."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

bundle = Path(__file__).resolve().parent
python = bundle / "python"
if Path(sys.executable).resolve() != python / "python.exe" or Path(sys.base_prefix).resolve() != python:
    raise RuntimeError("reader_runtime_requires_bundled_python")
manifest = json.loads((bundle / "reader-runtime.json").read_bytes())
if manifest["policy"] != "offline-windows-wiki-reader-runtime-v1":
    raise RuntimeError("reader_runtime_unknown_policy")
for row in manifest["files"]:
    path = bundle / row["path"]
    if not path.resolve().is_relative_to(bundle) or path.is_symlink():
        raise RuntimeError("reader_runtime_unsafe_file")
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024**2), b""):
            h.update(block)
    if path.stat().st_size != row["bytes"] or h.hexdigest() != row["sha256"]:
        raise RuntimeError("reader_runtime_file_changed")
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--snapshot", type=Path, required=True)
parser.add_argument("--port", type=int, default=3004)
parser.add_argument("--smoke", action="store_true")
args = parser.parse_args()
snapshot = args.snapshot.resolve()
source = snapshot / "runtime/src"
if not (snapshot / "wiki-snapshot.json").is_file() or not source.is_dir():
    raise RuntimeError("reader_runtime_requires_published_wiki_snapshot")
sys.path[:0] = [str(bundle / "site-packages"), str(source)]
from wechat_decrypt_tool.course_wiki_snapshot import PublishedStore, create_app, smoke, verify
if args.smoke:
    print(json.dumps(smoke(snapshot)))
else:
    import uvicorn
    verify(snapshot)
    uvicorn.run(create_app(PublishedStore(snapshot)), host="127.0.0.1", port=args.port)
'''

LAUNCHER = """param(
    [Parameter(Mandatory=$true)][string]$Snapshot,
    [int]$Port = 3004
)
$ErrorActionPreference = 'Stop'
$readerPython = Join-Path $PSScriptRoot 'python/python.exe'
$readerBootstrap = Join-Path $PSScriptRoot 'reader_bootstrap.py'
& $readerPython -I -B $readerBootstrap --snapshot $Snapshot --port $Port
exit $LASTEXITCODE
"""

README = r"""# Windows 离线 Wiki 阅读环境

本目录包含 CPython、读取已发布 Wiki 所需的依赖及逐文件哈希清单。
不需要在新电脑安装 Python、pip、Ollama，也不读取提取数据库或原课程目录。
仅支持 Windows x64；仍依赖 Windows 系统组件。GPU 转写、生成模型及渲染器不在本包内。
资料与运行环境分别保留，迁移时需同时复制本目录和一份完整的已发布 Wiki 快照。

在 PowerShell 运行：

    .\run-wiki.ps1 -Snapshot 'X:\完整Wiki快照目录' -Port 3004

浏览器打开 http://127.0.0.1:3004/ 。启动时重新核对运行文件及 Wiki 清单。
学习记录需另外从原浏览器导出后导入；沿用相同端口也不会自动迁移浏览器记录。
运行环境可用不证明课程覆盖、语义质量或原视频删除资格。
"""


def relative_file(root, relative):
    if not isinstance(relative, str) or "\\" in relative:
        raise ValueError("reader_runtime_invalid_relative_path")
    parts = PurePosixPath(relative)
    if (
        parts.is_absolute()
        or not parts.parts
        or parts.as_posix() != relative
        or any(
            p in (".", "..") or ":" in p or p.endswith((".", " ")) for p in parts.parts
        )
    ):
        raise ValueError("reader_runtime_invalid_relative_path")
    root = root.resolve()
    path = root.joinpath(*parts.parts)
    if not path.resolve().is_relative_to(root):
        raise ValueError("reader_runtime_path_outside_bundle")
    if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != root):
        raise ValueError("reader_runtime_symlink_not_supported")
    return path


def dependency_closure():
    pending, selected = list(ROOTS), {}
    while pending:
        req = Requirement(pending.pop())
        if req.marker and not req.marker.evaluate({"extra": ""}):
            continue
        dist = importlib.metadata.distribution(req.name)
        name = dist.metadata["Name"]
        if not req.specifier.contains(dist.version, prereleases=True):
            raise ValueError("reader_runtime_installed_dependency_version_mismatch")
        key = name.casefold().replace("_", "-")
        if key not in selected:
            selected[key] = dist
            pending.extend(dist.requires or [])
    return selected


def _capacity(path, required, reserve_gib):
    ancestor = path.resolve()
    while not ancestor.exists():
        ancestor = ancestor.parent
    if shutil.disk_usage(ancestor).free < required + reserve_gib * 1024**3:
        raise InterruptedError("reader_runtime_disk_reserve_reached")


def create(destination, *, reserve_gib=64, checkpoint=lambda: None):
    if sys.platform != "win32" or platform.machine().upper() not in {"AMD64", "X86_64"}:
        raise ValueError("reader_runtime_requires_windows_x64")
    if type(reserve_gib) is not int or reserve_gib < 0:
        raise ValueError("reader_runtime_invalid_reserve")
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError("reader_runtime_destination_already_exists")
    base = Path(sys.base_prefix).resolve()
    sources = {}
    for directory, names, files in os.walk(base, followlinks=False):
        current = Path(directory)
        names[:] = [
            name
            for name in names
            if name not in {"__pycache__", "site-packages"}
            and not (current == base and name == "Scripts")
        ]
        if current.is_symlink() or any((current / name).is_symlink() for name in names):
            raise ValueError("reader_runtime_interpreter_contains_symlink")
        for name in files:
            source = current / name
            if source.is_symlink() or not source.resolve().is_relative_to(base):
                raise ValueError("reader_runtime_interpreter_file_outside_root")
            sources["python/" + source.relative_to(base).as_posix()] = source
    if "python/python.exe" not in sources or "python/Lib/os.py" not in sources:
        raise ValueError("reader_runtime_incomplete_interpreter")
    distributions = dependency_closure()
    for dist in distributions.values():
        site = Path(dist.locate_file("")).resolve()
        if not dist.files:
            raise ValueError("reader_runtime_dependency_file_list_missing")
        for member in dist.files:
            source = Path(dist.locate_file(member))
            if "__pycache__" in member.parts or member.name == "direct_url.json":
                continue
            if not source.resolve().is_relative_to(site):
                # Generated console entry points are unnecessary for -m startup.
                if "Scripts" in member.parts or "bin" in member.parts:
                    continue
                raise ValueError("reader_runtime_dependency_file_outside_site")
            relative = source.resolve().relative_to(site)
            if source.is_symlink() or not source.is_file():
                raise ValueError("reader_runtime_dependency_file_missing_or_symlink")
            target = "site-packages/" + relative.as_posix()
            if target in sources and sources[target].resolve() != source.resolve():
                raise ValueError("reader_runtime_dependency_path_collision")
            sources[target] = source
    needed = sum(p.stat().st_size for p in sources.values()) + 8 * 1024**2
    _capacity(destination, needed, reserve_gib)
    destination.mkdir(parents=True)
    rows = []
    for relative, source in sorted(sources.items()):
        checkpoint()
        _capacity(destination, source.stat().st_size, reserve_gib)
        before = source.stat()
        target = relative_file(destination, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        original = file_hash(source, checkpoint)
        if file_hash(target, checkpoint) != original or (
            source.stat().st_size,
            source.stat().st_mtime_ns,
        ) != (before.st_size, before.st_mtime_ns):
            raise ValueError("reader_runtime_source_changed_during_copy")
        rows.append(
            {"path": relative, "bytes": target.stat().st_size, "sha256": original}
        )
    for name, content in {
        "reader_bootstrap.py": BOOTSTRAP,
        "run-wiki.ps1": LAUNCHER,
        "README.md": README,
    }.items():
        path = destination / name
        path.write_text(content, "utf-8")
        rows.append(
            {"path": name, "bytes": path.stat().st_size, "sha256": file_hash(path)}
        )
    manifest = {
        "policy": POLICY,
        "created": time.time(),
        "python_version": platform.python_version(),
        "platform": "windows-x64",
        "dependencies": {d.metadata["Name"]: d.version for d in distributions.values()},
        "files": rows,
        "gpu_and_rendering_runtime_included": False,
        "course_data_included": False,
        "source_deletion_allowed": False,
    }
    atomic_json(destination / MANIFEST, manifest)
    return verify(destination, checkpoint=checkpoint)


def verify(root, *, checkpoint=lambda: None):
    root = Path(root).resolve()
    manifest = json.loads((root / MANIFEST).read_bytes())
    if (
        manifest["policy"] != POLICY
        or manifest["platform"] != "windows-x64"
        or manifest["source_deletion_allowed"]
    ):
        raise ValueError("reader_runtime_manifest_policy_changed")
    paths, folded_paths = set(), set()
    for row in manifest["files"]:
        checkpoint()
        folded = row["path"].casefold()
        if folded in folded_paths:
            raise ValueError("reader_runtime_duplicate_manifest_path")
        paths.add(row["path"])
        folded_paths.add(folded)
        path = relative_file(root, row["path"])
        if (
            path.stat().st_size != row["bytes"]
            or file_hash(path, checkpoint) != row["sha256"]
        ):
            raise ValueError("reader_runtime_file_changed")
    if not {
        "python/python.exe",
        "python/Lib/os.py",
        "reader_bootstrap.py",
        "run-wiki.ps1",
        "README.md",
    } <= paths or not any(p.startswith("site-packages/") for p in paths):
        raise ValueError("reader_runtime_required_files_missing")
    return {
        "state": "reader_runtime_files_verified",
        "files": len(paths),
        "bytes": sum(r["bytes"] for r in manifest["files"]),
        "python_version": manifest["python_version"],
        "dependencies": len(manifest["dependencies"]),
        "independent_execution_verified": False,
        "gpu_and_rendering_runtime_included": False,
        "source_deletion_allowed": False,
    }


def restore(root, destination, *, reserve_gib=64, checkpoint=lambda: None):
    if type(reserve_gib) is not int or reserve_gib < 0:
        raise ValueError("reader_runtime_invalid_reserve")
    root, destination = Path(root).resolve(), Path(destination).resolve()
    if (
        destination.exists()
        or destination.is_relative_to(root)
        or root.is_relative_to(destination)
    ):
        raise ValueError("reader_runtime_restore_target_must_be_new_and_separate")
    report = verify(root, checkpoint=checkpoint)
    _capacity(destination, report["bytes"] + 8 * 1024**2, reserve_gib)
    manifest = json.loads((root / MANIFEST).read_bytes())
    destination.mkdir(parents=True)
    for row in manifest["files"]:
        checkpoint()
        _capacity(destination, row["bytes"], reserve_gib)
        target = relative_file(destination, row["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(relative_file(root, row["path"]), target)
    shutil.copyfile(root / MANIFEST, destination / MANIFEST)
    return verify(destination, checkpoint=checkpoint)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("create", "verify", "restore"))
    parser.add_argument("--root", type=Path)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--reserve-gib", type=int, default=64)
    args = parser.parse_args()
    if args.command == "create":
        result = create(args.destination, reserve_gib=args.reserve_gib)
    elif args.command == "restore":
        result = restore(args.root, args.destination, reserve_gib=args.reserve_gib)
    else:
        result = verify(args.root)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
