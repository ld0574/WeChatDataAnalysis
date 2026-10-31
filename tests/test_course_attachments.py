import hashlib
import zipfile

from fastapi.testclient import TestClient

from wechat_decrypt_tool import course_attachments as module
from wechat_decrypt_tool.course_knowledge import (
    KnowledgeStore,
    create_app,
    DOCUMENT_ID_OFFSET,
)
from wechat_decrypt_tool.course_resource_archive import archive_resources


def save(store, path, raw, *, member="", extractor="test"):
    key = "archive " + member if member else ""
    store.save_document_versions(
        [
            {
                "path": str(path),
                "source_key": key,
                "raw_digest": hashlib.sha256(raw).hexdigest(),
                "extractor": extractor,
                "chunks": [(key + " chars 0", "synthetic_source_marker")],
                "metadata": {},
            }
        ]
    )
    with store.connect() as db:
        return db.execute(
            "SELECT id FROM documents WHERE path=? AND source_key=? AND active=1",
            (str(path), key),
        ).fetchone()[0]


def setup(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    inventory = tmp_path / "inventory.json"
    inventory.write_text("{}")
    return source, inventory, KnowledgeStore(tmp_path / "kb")


def test_direct_source_download_survives_missing_original_and_uses_safe_headers(
    tmp_path,
):
    source, inventory, store = setup(tmp_path)
    path = source / "原始说明.html"
    raw = b"<script>synthetic source only</script>"
    path.write_bytes(raw)
    key = save(store, path, raw)
    archive_resources(store, source, inventory, reserve_gib=0)
    path.unlink()
    with TestClient(create_app(store, "unused")) as client:
        page = client.get("/evidence/" + str(-DOCUMENT_ID_OFFSET - key))
        assert "/document-source/" + str(key) in page.text
        response = client.get("/document-source/" + str(key))
        assert response.status_code == 200 and response.content == raw
        assert response.headers["content-type"] == "application/octet-stream"
        assert response.headers["content-disposition"].startswith("attachment;")
        assert response.headers["x-content-type-options"] == "nosniff"


def test_member_and_parent_zip_downloads_are_exact_and_cache_corruption_fails(tmp_path):
    source, inventory, store = setup(tmp_path)
    path = source / "course.zip"
    raw = b'print("synthetic source")\n'
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("pkg/main.py", raw)
    original = path.read_bytes()
    key = save(store, path, raw, member="pkg/main.py")
    archive_resources(store, source, inventory, reserve_gib=0)
    path.unlink()
    with TestClient(create_app(store, "unused")) as client:
        assert client.get("/document-source/" + str(key)).content == raw
        assert (
            client.get("/document-source/" + str(key) + "?container=true").content
            == original
        )
        assert (
            "下载所在压缩包"
            in client.get("/evidence/" + str(-DOCUMENT_ID_OFFSET - key)).text
        )
        derived = module.resolve_source(store, key)["path"]
        derived.write_bytes(b"tampered")
        assert client.get("/document-source/" + str(key)).status_code == 503
        assert (
            client.get("/document-source/" + str(key) + "?container=true").content
            == original
        )
        assert derived.read_bytes() == b"tampered"


def test_history_never_downloads_a_different_current_member(tmp_path):
    source, inventory, store = setup(tmp_path)
    path = source / "course.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("note.txt", b"old source")
    old = save(store, path, b"old source", member="note.txt")
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("note.txt", b"new source")
    new = save(store, path, b"new source", member="note.txt", extractor="v2")
    archive_resources(store, source, inventory, reserve_gib=0)
    with TestClient(create_app(store, "unused")) as client:
        assert client.get("/document-source/" + str(old)).status_code == 409
        assert (
            client.get("/evidence/" + str(-DOCUMENT_ID_OFFSET - old)).status_code == 200
        )
        assert client.get("/document-source/" + str(new)).content == b"new source"


def test_unsafe_member_and_modified_container_are_not_served(tmp_path):
    source, inventory, store = setup(tmp_path)
    path = source / "course.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("../escape.py", b"synthetic")
        archive.writestr("safe.txt", b"synthetic")
    unsafe = save(store, path, b"synthetic", member="../escape.py")
    safe = save(store, path, b"synthetic", member="safe.txt")
    archive_resources(store, source, inventory, reserve_gib=0)
    with TestClient(create_app(store, "unused")) as client:
        assert client.get("/document-source/" + str(unsafe)).status_code == 503
        reference = module.source_reference(store, safe)
        reference["path"].write_bytes(b"changed container")
        assert client.get("/document-source/" + str(safe)).status_code == 503
    assert not (store.root.parent / "escape.py").exists()


def test_member_cache_budget_fails_without_discarding_source(tmp_path, monkeypatch):
    source, inventory, store = setup(tmp_path)
    path = source / "course.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("note.txt", b"synthetic content")
    key = save(store, path, b"synthetic content", member="note.txt")
    archive_resources(store, source, inventory, reserve_gib=0)
    monkeypatch.setattr(module, "CACHE_BUDGET", 1)
    with TestClient(create_app(store, "unused")) as client:
        assert client.get("/document-source/" + str(key)).status_code == 507
        assert (
            client.get("/document-source/" + str(key) + "?container=true").status_code
            == 200
        )
    assert path.exists() and not list(
        (store.root / "resources" / "derived").glob("*.part")
    )
