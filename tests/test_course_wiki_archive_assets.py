import hashlib
import json
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from wechat_decrypt_tool import course_wiki_archive_assets as assets
from wechat_decrypt_tool.course_library import create_app
from test_course_attachments import save, setup
from test_course_pdf_validation import fixture_archive


def pdf_fixture(tmp_path):
    store, _, _, _, image, _ = fixture_archive(tmp_path)
    client = TestClient(create_app(SimpleNamespace(root=store.root)))
    with store.connect() as db:
        row = dict(db.execute("SELECT * FROM documents ORDER BY id LIMIT 1").fetchone())
    params = {
        "revision": row["digest"],
        "text_sha256": hashlib.sha256(row["text"].encode()).hexdigest(),
    }
    url = "/wiki/api/courseware/document/" + str(row["id"])
    return store, client, row, params, url, image


def test_pdf_page_and_original_are_version_bound_exact_and_database_readonly(tmp_path):
    store, client, row, params, url, image = pdf_fixture(tmp_path)
    before = (store.root / "knowledge.sqlite3").read_bytes()
    page = client.get(url, params=params).json()
    assert page["assets"]["page"] == 1 and page["assets"]["source_is_pdf"]
    visual = client.get(page["assets"]["visual"])
    assert visual.status_code == 200 and visual.content == image.read_bytes()
    assert visual.headers["content-type"] == "image/png"
    original = client.get(page["assets"]["source"])
    assert original.status_code == 200 and original.content.startswith(b"%PDF-")
    assert hashlib.sha256(original.content).hexdigest() == row["raw_digest"]
    assert original.headers["content-type"] == "application/pdf"
    assert original.headers["content-disposition"].startswith("inline;")
    for response in [visual, original]:
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["cache-control"] == "no-store"
    assert (store.root / "knowledge.sqlite3").read_bytes() == before
    assert not (store.root / "library").exists()


@pytest.mark.parametrize("change", ["text", "inactive", "revision_metadata"])
def test_mutated_document_or_receipt_cannot_open_old_asset_links(tmp_path, change):
    store, client, row, params, url, _ = pdf_fixture(tmp_path)
    with store.write() as db:
        if change == "text":
            db.execute(
                "UPDATE documents SET text='changed fixture' WHERE id=?", (row["id"],)
            )
        elif change == "inactive":
            db.execute("UPDATE documents SET active=0 WHERE id=?", (row["id"],))
        else:
            db.execute("UPDATE document_version_metadata SET metadata='{}'")
    for suffix in ["/visual", "/source"]:
        assert client.get(url + suffix, params=params).status_code == 409


def test_changed_image_or_source_bytes_are_never_served_as_the_original(tmp_path):
    store, client, row, params, url, image = pdf_fixture(tmp_path)
    image.write_bytes(b"changed image fixture")
    assert client.get(url + "/visual", params=params).status_code == 409
    (store.root / "resources/objects" / row["raw_digest"]).write_bytes(
        b"changed PDF fixture"
    )
    assert client.get(url + "/source", params=params).status_code == 409


def test_image_path_cannot_escape_visual_archive_even_with_matching_metadata(tmp_path):
    store, client, row, params, url, _ = pdf_fixture(tmp_path)
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside-image-not-courseware")
    with store.write() as db:
        for version in list(db.execute("SELECT * FROM resource_revisions")):
            meta = json.loads(version["metadata"])
            meta["image"] = "../outside.png"
            meta["image_digest"] = hashlib.sha256(outside.read_bytes()).hexdigest()
            encoded = json.dumps(meta)
            db.execute(
                "UPDATE resource_revisions SET metadata=? WHERE path=? AND source_key=?",
                (encoded, version["path"], version["source_key"]),
            )
            db.execute(
                "UPDATE document_version_metadata SET metadata=? WHERE path=? AND source_key=?",
                (encoded, version["path"], version["source_key"]),
            )
    assert client.get(url, params=params).json()["assets"]["visual"] is None
    response = client.get(url + "/visual", params=params)
    assert (
        response.status_code == 404
        and b"outside-image-not-courseware" not in response.content
    )


def test_source_size_and_missing_retained_object_have_explicit_errors(
    tmp_path, monkeypatch
):
    store, client, row, params, url, _ = pdf_fixture(tmp_path)
    monkeypatch.setattr(assets, "MAX_SOURCE_BYTES", 1)
    assert client.get(url + "/source", params=params).status_code == 413
    (store.root / "resources/objects" / row["raw_digest"]).unlink()
    original = __import__("pathlib").Path(row["path"])
    original.parent.mkdir(exist_ok=True)
    original.write_bytes(b"must-not-read-original-path")
    response = client.get(url + "/source", params=params)
    assert (
        response.status_code == 404
        and b"must-not-read-original-path" not in response.content
    )


def test_archive_member_download_uses_retained_container_and_safe_unicode_filename(
    tmp_path,
):
    import zipfile
    from wechat_decrypt_tool.course_resource_archive import archive_resources
    from wechat_decrypt_tool import course_attachments

    source, inventory, store = setup(tmp_path)
    original = source / "course.zip"
    raw = b"synthetic safe HTML <script>not executed</script>"
    filename = "\u8bf4\u660e.html"
    with zipfile.ZipFile(original, "w") as z:
        z.writestr(filename, raw)
    key = save(store, original, raw, member=filename)
    archive_resources(store, source, inventory, reserve_gib=0)
    original.unlink()
    with store.connect() as db:
        row = db.execute("SELECT * FROM documents WHERE id=?", (key,)).fetchone()
    params = {
        "revision": row["digest"],
        "text_sha256": hashlib.sha256(row["text"].encode()).hexdigest(),
    }
    client = TestClient(create_app(SimpleNamespace(root=store.root)))
    url = "/wiki/api/courseware/document/" + str(key)
    assert client.get(url, params=params).json()["assets"]["source"]
    response = client.get(url + "/source", params=params)
    assert response.status_code == 200 and response.content == raw
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers[
        "content-disposition"
    ] == "attachment; filename*=UTF-8''" + quote(filename)
    derived = course_attachments.resolve_source(store, key)["path"]
    derived.write_bytes(b"changed derived fixture")
    assert client.get(url + "/source", params=params).status_code == 409
    assert derived.read_bytes() == b"changed derived fixture"


def test_plain_retained_source_does_not_claim_a_page_preview(tmp_path):
    from wechat_decrypt_tool.course_resource_archive import archive_resources

    source, inventory, store = setup(tmp_path)
    original = source / "note.txt"
    original.write_bytes(b"synthetic direct source")
    key = save(store, original, original.read_bytes())
    archive_resources(store, source, inventory, reserve_gib=0)
    original.unlink()
    with store.connect() as db:
        row = db.execute("SELECT * FROM documents WHERE id=?", (key,)).fetchone()
    params = {
        "revision": row["digest"],
        "text_sha256": hashlib.sha256(row["text"].encode()).hexdigest(),
    }
    client = TestClient(create_app(SimpleNamespace(root=store.root)))
    url = "/wiki/api/courseware/document/" + str(key)
    page = client.get(url, params=params).json()
    assert page["assets"]["visual"] is None and page["assets"]["source"]
    assert client.get(url + "/visual", params=params).status_code == 404


def test_snapshot_and_invalid_fingerprints_remain_explicit(tmp_path):
    _, client, _, params, url, _ = pdf_fixture(tmp_path)
    assert (
        client.get(url + "/visual", params={**params, "text_sha256": "bad"}).status_code
        == 400
    )
    snapshot = TestClient(
        create_app(SimpleNamespace(root=tmp_path, snapshot_info={"policy": "fixture"}))
    )
    for suffix in ["/visual", "/source"]:
        assert snapshot.get(url + suffix, params=params).status_code == 503
