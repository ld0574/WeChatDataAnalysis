import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from test_course_wiki_documents import archive as source_archive
from test_course_wiki_documents import courseware as source_courseware
from test_course_wiki_documents import draft, packet
from test_course_wiki_documents import published as source_published
from wechat_decrypt_tool import course_library as library
from wechat_decrypt_tool import course_wiki as wiki
from wechat_decrypt_tool import course_wiki_snapshot as snapshot
from wechat_decrypt_tool.course_knowledge import atomic_json
from wechat_decrypt_tool.course_video_visual_audit import ReadOnlyArchive

archive = source_archive
courseware = source_courseware
published = source_published


@pytest.fixture
def publication(courseware):
    store, key, *_ = courseware
    evidence = json.loads((store.root / "lessons" / key / "evidence.json").read_bytes())
    for row in evidence["frames"]:
        Image.new("RGB", (24, 18), "white").save(store.root / row["path"], "JPEG")
    library.publish(ReadOnlyArchive(store.root))
    p = packet(courseware)
    wiki.publish_draft(store, draft(p), p, "e" * 32, "fixture", "fixture-digest")
    return store.root


def test_restore_routes_without_original_archive(publication, tmp_path):
    backup = snapshot.create(publication, tmp_path / "backup", reserve_gib=0)
    manifest = json.loads((backup / snapshot.MANIFEST).read_bytes())
    assert not any(
        Path(row["path"]).suffix in {".sqlite3", ".sqlite", ".db", ".wal"}
        or "gpu.lock" in row["path"]
        for row in manifest["files"]
    )
    shutil.rmtree(publication)
    restored = snapshot.restore(backup, tmp_path / "restored", reserve_gib=0)
    result = snapshot.smoke(restored)
    assert result["article_versions"] == result["current_topics"] == 1
    assert result["article_image_routes_verified"] == 1
    assert not result["database_required"]
    client = TestClient(library.create_app(snapshot.PublishedStore(restored)))
    assert (
        client.get("/wiki/api/catalog").json()["published_snapshot"]["id"]
        == manifest["id"]
    )
    assert client.get("/wiki/api/coverage").status_code == 503
    source = client.get("/library-assets/course_library.js").text
    assert "snapshotNotice(versions)" in source and "snapshotNotice(wiki)" in source
    process = subprocess.run(
        [
            sys.executable,
            "-I",
            str(Path(__file__).with_name("wiki_snapshot_isolated_reader.py")),
            str(restored),
            str(publication),
            str(Path(snapshot.__file__).parent),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    isolated = json.loads(process.stdout)
    assert isolated["isolated_reader_verified"]
    assert isolated["original_root_access_attempts"] == 0


@pytest.mark.parametrize(
    "mutation",
    ["hash", "missing_manifest", "scope", "runtime", "escape", "missing_image"],
)
def test_rejects_corrupt_or_incomplete_snapshot(publication, tmp_path, mutation):
    backup = snapshot.create(publication, tmp_path / "backup", reserve_gib=0)
    path = backup / snapshot.MANIFEST
    value = json.loads(path.read_bytes())
    if mutation == "hash":
        (backup / "README.md").write_text("corrupt")
    elif mutation == "missing_manifest":
        value["files"] = [r for r in value["files"] if r["path"] != "wiki/catalog.json"]
    elif mutation == "scope":
        value["source_deletion_allowed"] = True
    elif mutation == "runtime":
        value["runtime_source_included"] = False
    elif mutation == "escape":
        value["files"][0]["path"] = "../escape"
    else:
        next((backup / "wiki/media").glob("*.*")).unlink()
    atomic_json(path, value)
    with pytest.raises((ValueError, OSError)):
        snapshot.verify(backup)


def test_refuses_existing_destination_and_overlapping_paths(publication, tmp_path):
    with pytest.raises(ValueError, match="overlaps"):
        snapshot.create(publication, publication / "backup", reserve_gib=0)
    backup = snapshot.create(publication, tmp_path / "backup", reserve_gib=0)
    target = tmp_path / "restored"
    target.mkdir()
    (target / "keep.txt").write_text("keep")
    with pytest.raises(ValueError, match="not_empty"):
        snapshot.restore(backup, target, reserve_gib=0)
    with pytest.raises(ValueError, match="overlaps"):
        snapshot.restore(backup, backup / "restore", reserve_gib=0)
    assert (target / "keep.txt").read_text() == "keep"


def test_refuses_insufficient_space_and_changed_publication(
    publication, tmp_path, monkeypatch
):
    with pytest.raises(InterruptedError, match="reserve"):
        snapshot.create(publication, tmp_path / "backup", reserve_gib=1e12)
    original = snapshot.shutil.copyfile
    changed = False

    def change_pointer(source, target):
        nonlocal changed
        result = original(source, target)
        if not changed:
            changed = True
            catalog = publication / "wiki/catalog.json"
            catalog.write_bytes(catalog.read_bytes() + b"\n")
        return result

    monkeypatch.setattr(snapshot.shutil, "copyfile", change_pointer)
    with pytest.raises(ValueError, match="changed"):
        snapshot.create(publication, tmp_path / "backup", reserve_gib=0)
    assert not list((tmp_path / "backup").rglob(snapshot.MANIFEST))
