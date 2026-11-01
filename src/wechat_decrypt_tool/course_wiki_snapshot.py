"""Portable snapshots of published Wiki data, distinct from full course backups."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import math
import re
import shutil
import time
import uuid
from pathlib import Path

from .course_backup import file_hash, inside
from .course_knowledge import atomic_json
from .course_library import Library, canonical, create_app
from .course_lesson_exports import digest
from .course_wiki import Wiki

POLICY = "published-course-wiki-snapshot-v1"
MANIFEST = "wiki-snapshot.json"


def _reserve(path, needed, reserve_gib):
    if not math.isfinite(reserve_gib) or reserve_gib < 0:
        raise ValueError("snapshot_invalid_disk_reserve")
    ancestor = path
    while not ancestor.exists():
        if ancestor.parent == ancestor:
            raise ValueError("snapshot_volume_unavailable")
        ancestor = ancestor.parent
    if shutil.disk_usage(ancestor).free < needed + reserve_gib * 1024**3:
        raise InterruptedError("snapshot_disk_reserve_reached")


def _images(value):
    if isinstance(value, dict):
        if value.get("image_sha256"):
            yield value
        for child in value.values():
            yield from _images(child)
    elif isinstance(value, list):
        for child in value:
            yield from _images(child)


def revision_dependencies(root, value):
    """A self-consistent file manifest must not hide missing bound reports."""
    revision = value["packet"].get("revision")
    if not revision:
        return
    try:
        parent = Wiki(root).article(revision["parent_article"])
        if digest(canonical(parent)) != revision["parent_sha256"]:
            raise ValueError("parent_changed")
        if revision.get("review_reports"):
            from .course_wiki_revision import source_crosschecks

            report, bindings = source_crosschecks(
                root, parent["id"], [b["job"] for b in revision["review_reports"]]
            )
            if (
                bindings != revision["review_reports"]
                or digest(canonical(report)) != revision["review_sha256"]
            ):
                raise ValueError("review_binding_changed")
        if revision.get("omission_reports"):
            from .course_wiki_completeness import published_report

            for binding in revision["omission_reports"]:
                report = published_report(root, parent["id"], job=binding["job"])
                if digest(canonical(report)) != binding["sha256"]:
                    raise ValueError("omission_binding_changed")
    except (OSError, KeyError, ValueError, TypeError):
        raise ValueError("snapshot_revision_dependency_invalid") from None


def writer_dependencies(root, publication):
    """Follow frozen writer recipes, including unpublished reused checkpoints."""
    primary = root / "wiki/jobs" / publication["id"]
    if not (primary / "manifest.json").exists():
        if publication["pipeline"] in {
            "local-topic-wiki-staged-v1",
            "wiki-chapter-scoped-synthesis-v1",
            "wiki-source-bound-revision-v1",
        }:
            raise ValueError("snapshot_writer_manifest_missing")
        return []
    folders, visiting = {}, set()

    def visit(key, parent=None):
        if not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{32}", key):
            raise ValueError("snapshot_writer_dependency_invalid_id")
        if key in visiting:
            raise ValueError("snapshot_writer_dependency_cycle")
        folder = inside(root, "wiki/jobs/" + key)
        try:
            manifest = json.loads((folder / "manifest.json").read_bytes())
            recipe = manifest["recipe"]
            sha = digest(canonical(recipe))
            if (
                manifest["id"] != key
                or manifest["recipe_sha256"] != sha
                or sha[:32] != key
                or recipe["packet"] != publication["packet"]
                or recipe["model_digest"] != publication["model_digest"]
                or recipe["policy"] != publication["pipeline"]
            ):
                raise ValueError("snapshot_writer_binding_changed")
            if parent is not None and any(
                recipe[k] != parent[k] for k in ("packet", "model_digest", "policy")
            ):
                raise ValueError("snapshot_writer_reused_recipe_changed")
            if key in folders:
                return
            visiting.add(key)
            for path in sorted(folder.glob("*.json")):
                saved = json.loads(path.read_bytes())
                checkpoint = re.fullmatch(
                    r"outline|exercises|introduction|(?:section|chapter|self-check)-\d+|repair-.+",
                    path.stem,
                )
                if checkpoint and (
                    digest(canonical(saved["data"])) != saved["data_sha256"]
                    or not re.fullmatch(r"[a-f0-9]{64}", saved["input_sha256"])
                ):
                    raise ValueError("snapshot_writer_checkpoint_changed")
            extra = recipe.get("extra", {})
            for field in ("unchanged_body_job", "unchanged_claim_job"):
                if field in extra:
                    visit(extra[field], recipe)
            if "unchanged_body_job" in extra:
                body = inside(root, "wiki/jobs/" + extra["unchanged_body_job"])
                stages = ["introduction"] + [
                    f"chapter-{n}"
                    for n in range(len(recipe["packet"]["chapter_scopes"]))
                ]
                if any(not (body / (stage + ".json")).is_file() for stage in stages):
                    raise ValueError("snapshot_writer_reused_body_incomplete")
            if "unchanged_claim_job" in extra:
                from .course_wiki_review import batches
                from .course_wiki_revision import omission_repairs, source_crosschecks

                revision = recipe["packet"]["revision"]
                parent_article = Wiki(root).article(revision["parent_article"])
                report, _ = source_crosschecks(
                    root,
                    parent_article["id"],
                    [b["job"] for b in revision.get("review_reports", [])],
                )
                targets = {
                    t["target"]: t
                    for _, group in batches(parent_article["article"])
                    for t in group
                }
                _, findings, _ = omission_repairs(
                    root,
                    parent_article,
                    [b["job"] for b in revision.get("omission_reports", [])],
                    targets,
                    source_scoped_additions=True,
                )
                required = {
                    r["target"]
                    for r in report["reviews"]
                    if r["verdict"] != "supported"
                } | set(findings)
                claims = inside(root, "wiki/jobs/" + extra["unchanged_claim_job"])
                if any(
                    not (claims / ("repair-" + target + ".json")).is_file()
                    for target in required
                    if "-append-" not in target
                ):
                    raise ValueError("snapshot_writer_reused_claims_incomplete")
            visiting.remove(key)
            folders[key] = folder
        except (OSError, KeyError, TypeError, json.JSONDecodeError):
            raise ValueError("snapshot_writer_dependency_unreadable") from None

    visit(publication["id"])
    return list(folders.values())


def assets(root):
    """Freeze publication pointers; never copy a live SQLite/WAL or GPU task."""
    root = Path(root).resolve()
    files, pointers = {}, {}

    def add(relative, expected=None, *, pointer=False):
        path = inside(root, relative)
        actual = file_hash(path)
        if expected and actual != expected:
            raise ValueError("snapshot_source_hash_mismatch")
        if relative in files and files[relative][1] != actual:
            raise ValueError("snapshot_source_changed_during_collection")
        files[relative] = (path, actual)
        if pointer:
            pointers[path] = actual

    wiki, library = Wiki(root), Library(root)
    add("wiki/catalog.json", pointer=True)
    add("library/catalog.json", pointer=True)
    add("inventory-unique.json", pointer=True)
    catalog, sources = wiki.catalog(), library.catalog()
    if sources["inventory_sha256"] != files["inventory-unique.json"][1]:
        raise ValueError("snapshot_source_inventory_mismatch")
    all_images = []
    for row in catalog["articles"]:
        value = wiki.article(row["id"])
        revision_dependencies(root, value)
        add("wiki/objects/" + row["object"] + ".json", row["file_sha256"])
        for key in value["packet"].get("evidence_partitions", {}):
            from .course_wiki_evidence import read as read_partition

            partition = read_partition(root, key, current=False)
            add(f"wiki/evidence-partitions/{key}/manifest.json")
            sha = partition["lesson_sha256"]
            add(f"library/objects/{sha}.json", sha)
            all_images.extend(_images(library.object(sha)))
        all_images.extend(_images(value["packet"]["sources"]))
        # Preserve the published writer's provenance, not unrelated live jobs.
        for folder in writer_dependencies(root, value):
            for path in sorted(folder.rglob("*.json")):
                add(path.relative_to(root).as_posix())
        for directory, reader in (
            ("review-jobs", "review"),
            ("completeness-jobs", "omissions"),
            ("visual-review-jobs", "visual"),
        ):
            for receipt in sorted((root / "wiki" / directory).glob("*/receipt.json")):
                saved = json.loads(receipt.read_bytes())
                if saved.get("article") != row["id"]:
                    continue
                if reader == "review":
                    from .course_wiki_review import published_review

                    published_review(root, row["id"], job=receipt.parent.name)
                elif reader == "omissions":
                    from .course_wiki_completeness import published_report

                    published_report(root, row["id"], job=receipt.parent.name)
                else:
                    from .course_wiki_visual_review import report

                    report(root, row["id"], job=receipt.parent.name)
                for path in sorted(receipt.parent.rglob("*.json")):
                    add(path.relative_to(root).as_posix())
        for directory in ("reviews", "completeness"):
            relative = f"wiki/{directory}/{row['id']}.json"
            if inside(root, relative).exists():
                if directory == "reviews":
                    from .course_wiki_review import published_review

                    published_review(root, row["id"])
                else:
                    from .course_wiki_completeness import published_report

                    published_report(root, row["id"])
                add(relative, pointer=True)
    for row in sources["lessons"]:
        if row["state"] != "draft_available":
            continue
        lesson = library.lesson(row["id"])
        add("library/objects/" + row["artifact"] + ".json", row["artifact"])
        all_images.extend(_images(lesson))
    library.terms()
    add(
        "library/objects/" + sources["terms_artifact"] + ".json",
        sources["terms_artifact"],
    )
    for image in all_images:
        sha = image["image_sha256"]
        if image.get("kind") == "document":
            from .course_wiki_documents import retained_image

            retained_image(root, sha, image["image_extension"])
            add("wiki/media/" + sha + "." + image["image_extension"], sha)
        else:
            relative = "library/media/" + sha + ".jpg"
            add(relative, sha)
            from .course_wiki_documents import image_type

            if image_type(inside(root, relative).read_bytes())[0] != "jpg":
                raise ValueError("snapshot_frame_not_jpeg")
    return files, pointers


def create(root, destination, *, reserve_gib=32):
    root, destination = Path(root).resolve(), Path(destination).resolve()
    if destination.is_relative_to(root) or root.is_relative_to(destination):
        raise ValueError("snapshot_destination_overlaps_working_archive")
    files, pointers = assets(root)
    package = Path(__file__).resolve().parent
    for path in sorted(package.rglob("*")):
        if (
            path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix in {".py", ".html", ".css", ".js"}
        ):
            if not path.resolve().is_relative_to(package):
                raise ValueError("snapshot_runtime_outside_package")
            relative = (
                "runtime/src/wechat_decrypt_tool/"
                + path.relative_to(package).as_posix()
            )
            files[relative] = (path, file_hash(path))
    needed = sum(path.stat().st_size for path, _ in files.values()) + 64 * 1024**2
    _reserve(destination, needed, reserve_gib)
    key = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    snapshot = destination / "snapshots" / key
    snapshot.mkdir(parents=True, exist_ok=False)
    rows = []
    for relative, (source, expected) in sorted(files.items()):
        _reserve(snapshot, source.stat().st_size, reserve_gib)
        target = inside(snapshot, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        if file_hash(target) != expected:
            raise ValueError("snapshot_copied_file_changed")
        rows.append(
            {"path": relative, "bytes": target.stat().st_size, "sha256": expected}
        )
    if any(file_hash(path) != expected for path, expected in pointers.items()):
        raise ValueError("snapshot_publication_changed_during_copy")
    requirements = (
        "\n".join(
            name + "==" + importlib.metadata.version(name)
            for name in ("fastapi", "uvicorn", "psutil", "Pillow", "httpx")
        )
        + "\n"
    )
    (snapshot / "reader-requirements.txt").write_text(requirements, encoding="utf-8")
    readme = """# 已发布课程 Wiki 快照

本快照只包括创建时已发布的主题文章、保留版本、来源笔记、原样代码、引用图片和完整检查报告。
不包含提取数据库、全部课程音频/视频、全部附件、模型权重、Python 环境或浏览器内的学习记录。
它不证明课程覆盖、语义正确、备份全部素材或可以删除原视频。未发布课程仍显示未发布。

在已安装 Python 3.11+ 和 reader-requirements.txt 所列依赖的本机，用 PowerShell 进入本快照目录：

    $env:PYTHONPATH = (Join-Path (Get-Location) 'runtime/src')
    python -m wechat_decrypt_tool.course_wiki_snapshot serve --root . --port 3004

浏览器打开 http://127.0.0.1:3004/ 。阅读与搜索无需提取数据库、Ollama 或原课程所在目录。
阅读位置和自测作答保存在当前浏览器；更换浏览器或端口不会自动带入原有学习记录。
迁移到新电脑前需另行准备 Python 和依赖；本快照未打包离线安装轮子，不能据此称为环境完整备份。
"""
    (snapshot / "README.md").write_text(readme, encoding="utf-8")
    for name in ("reader-requirements.txt", "README.md"):
        path = snapshot / name
        rows.append(
            {"path": name, "bytes": path.stat().st_size, "sha256": file_hash(path)}
        )
    value = {
        "policy": POLICY,
        "id": key,
        "created": time.time(),
        "files": rows,
        "scope": "published_wiki_and_source_notes_only",
        "runtime_source_included": True,
        "python_environment_included": False,
        "browser_learning_records_included": False,
        "full_course_archive": False,
        "semantic_accuracy_verified": False,
        "source_deletion_allowed": False,
    }
    atomic_json(snapshot / MANIFEST, value)
    verify(snapshot)
    return snapshot


class PublishedStore:
    """Reader-only root with no extraction database dependency or fallback."""

    def __init__(self, root):
        self.root = Path(root).resolve()
        value = json.loads((self.root / MANIFEST).read_bytes())
        if value["policy"] != POLICY:
            raise ValueError("snapshot_unknown_policy")
        self.snapshot_info = {k: value[k] for k in ("id", "created", "scope")}


def verify(root):
    root = Path(root).resolve()
    manifest = json.loads((root / MANIFEST).read_bytes())
    if (
        manifest["policy"] != POLICY
        or manifest.get("runtime_source_included") is not True
        or manifest["scope"] != "published_wiki_and_source_notes_only"
        or any(
            manifest[k] is not False
            for k in (
                "python_environment_included",
                "browser_learning_records_included",
                "full_course_archive",
                "semantic_accuracy_verified",
                "source_deletion_allowed",
            )
        )
    ):
        raise ValueError("snapshot_invalid_scope_or_acceptance")
    paths = set()
    for row in manifest["files"]:
        if row["path"] in paths:
            raise ValueError("snapshot_duplicate_file")
        paths.add(row["path"])
        path = inside(root, row["path"])
        if path.stat().st_size != row["bytes"] or file_hash(path) != row["sha256"]:
            raise ValueError("snapshot_file_integrity_failure")
    wiki, library = Wiki(root), Library(root)
    catalog, source_catalog = wiki.catalog(), library.catalog()
    required, _ = assets(root)
    runtime = "runtime/src/wechat_decrypt_tool/"
    essential = {
        runtime + name
        for name in (
            "__init__.py",
            "course_wiki_snapshot.py",
            "course_library.py",
            "resources/course_library.html",
            "resources/course_library.js",
            "resources/course_library.css",
        )
    }
    html = inside(root, runtime + "resources/course_library.html").read_text("utf-8")
    if "/library-assets/course_learning.js" in html:
        essential.add(runtime + "resources/course_learning.js")
    if (
        not (set(required) | essential | {"README.md", "reader-requirements.txt"})
        <= paths
    ):
        raise ValueError("snapshot_manifest_missing_dependency")
    if source_catalog["inventory_sha256"] != file_hash(root / "inventory-unique.json"):
        raise ValueError("snapshot_inventory_integrity_failure")
    for row in catalog["articles"]:
        value = wiki.article(row["id"])
        if not row.get("superseded_by") and not any(
            result["id"] == row["id"] for result in wiki.search(row["title"])
        ):
            raise ValueError("snapshot_article_search_failure")
        for image in _images(value["packet"]["sources"]):
            if image.get("kind") == "document":
                from .course_wiki_documents import retained_image

                retained_image(root, image["image_sha256"], image["image_extension"])
    ready = [r for r in source_catalog["lessons"] if r["state"] == "draft_available"]
    for row in ready:
        library.lesson(row["id"])
    library.terms()
    reports = 0
    for folder, reader in (
        ("review-jobs", "review"),
        ("completeness-jobs", "omissions"),
        ("visual-review-jobs", "visual"),
    ):
        for path in (root / "wiki" / folder).glob("*/receipt.json"):
            value = json.loads(path.read_bytes())
            if reader == "review":
                from .course_wiki_review import published_review

                published_review(root, value["article"], job=path.parent.name)
            elif reader == "omissions":
                from .course_wiki_completeness import published_report

                published_report(root, value["article"], job=path.parent.name)
            else:
                from .course_wiki_visual_review import report

                report(root, value["article"], job=path.parent.name)
            reports += 1
    return {
        "policy": POLICY,
        "snapshot": manifest["id"],
        "files_verified": len(paths),
        "article_versions": len(catalog["articles"]),
        "current_topics": sum(not r.get("superseded_by") for r in catalog["articles"]),
        "source_notes": len(ready),
        "complete_model_reports": reports,
        "database_required": False,
        "full_course_archive": False,
        "semantic_accuracy_verified": False,
        "source_deletion_allowed": False,
    }


def restore(snapshot, destination, *, reserve_gib=32):
    snapshot, destination = Path(snapshot).resolve(), Path(destination).resolve()
    if destination.is_relative_to(snapshot.parent.parent) or snapshot.is_relative_to(
        destination
    ):
        raise ValueError("snapshot_restore_overlaps_backup")
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("snapshot_restore_destination_not_empty")
    verify(snapshot)
    manifest = json.loads((snapshot / MANIFEST).read_bytes())
    _reserve(
        destination,
        sum(row["bytes"] for row in manifest["files"]) + 64 * 1024**2,
        reserve_gib,
    )
    destination.mkdir(parents=True, exist_ok=True)
    for row in manifest["files"]:
        _reserve(destination, row["bytes"], reserve_gib)
        target = inside(destination, row["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(inside(snapshot, row["path"]), target)
    shutil.copyfile(snapshot / MANIFEST, destination / MANIFEST)
    verify(destination)
    return destination


def smoke(root):
    """Exercise actual reader routes without a database, GPU or original paths."""
    from fastapi.testclient import TestClient

    result = verify(root)
    root = Path(root).resolve()
    client = TestClient(create_app(PublishedStore(root)))
    for url in ("/", "/library/api/catalog", "/wiki/api/catalog"):
        if client.get(url).status_code != 200:
            raise ValueError("snapshot_reader_route_failure")
    for row in Library(root).catalog()["lessons"]:
        if row["state"] == "draft_available":
            if client.get("/library/api/lesson/" + row["id"]).status_code != 200:
                raise ValueError("snapshot_source_note_route_failure")
    images = set()
    for row in Wiki(root).catalog()["articles"]:
        response = client.get("/wiki/api/article/" + row["id"])
        if response.status_code != 200:
            raise ValueError("snapshot_article_route_failure")
        value = response.json()
        if not row.get("superseded_by"):
            hits = client.get("/wiki/api/search", params={"q": row["title"]}).json()[
                "items"
            ]
            if not any(r["id"] == row["id"] for r in hits):
                raise ValueError("snapshot_search_route_failure")
        for image in _images(value["packet"]["sources"]):
            sha = image["image_sha256"]
            images.add(
                f"/wiki/image/{sha}.{image['image_extension']}"
                if image.get("kind") == "document"
                else "/library/frame/" + sha
            )
    for url in images:
        if client.get(url).status_code != 200:
            raise ValueError("snapshot_image_route_failure")
    result.update(
        reader_routes_verified=True, article_image_routes_verified=len(images)
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("create")
    build.add_argument("--root", type=Path, required=True)
    build.add_argument("--destination", type=Path, required=True)
    check = sub.add_parser("verify")
    check.add_argument("--root", type=Path, required=True)
    check.add_argument("--smoke", action="store_true")
    recover = sub.add_parser("restore")
    recover.add_argument("--root", type=Path, required=True)
    recover.add_argument("--destination", type=Path, required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--root", type=Path, required=True)
    serve.add_argument("--port", type=int, default=3004)
    args = parser.parse_args()
    if args.command == "create":
        print(json.dumps({"snapshot": str(create(args.root, args.destination))}))
    elif args.command == "restore":
        print(json.dumps({"restored": str(restore(args.root, args.destination))}))
    elif args.command == "verify":
        print(json.dumps(smoke(args.root) if args.smoke else verify(args.root)))
    else:
        import uvicorn

        verify(args.root)
        uvicorn.run(
            create_app(PublishedStore(args.root)), host="127.0.0.1", port=args.port
        )


if __name__ == "__main__":
    main()
