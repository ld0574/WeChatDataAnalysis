import io
import json
import sqlite3
import zipfile

from PIL import Image
import pytest

from wechat_decrypt_tool import course_textbook_whole_notebook_visuals as module


def test_rendered_images_follow_markdown_and_html_but_not_code():
    source = '''![inline](images/a.png "caption")
![reference][chart]

[chart]: <images/b%20c.png>

<img src="images/d.png" />

`![code](ignored.png)`

```python
![also-code](ignored2.png)
```
'''
    assert module.rendered_references(source) == ["images/a.png", "images/b%20c.png", "images/d.png"]


@pytest.mark.parametrize("reference, expected", [
    ("images/a.png", "course/notebooks/images/a.png"),
    ("../images/a%20b.png", "course/images/a b.png"),
    ("..\\images\\a.png", "course/images/a.png"),
    ("../../../outside.png", None),
    ("%2foutside.png", None),
    ("//server/image.png", None),
    ("https://example.org/image.png", None),
    ("data:image/png;base64,aaa", None),
    ("attachment:figure.png", None),
    ("C:/outside.png", None),
    ("image.png?version=1", None),
    ("image.png#part", None),
    ("bad%00.png", None),
])
def test_archive_members_are_relative_and_literal(reference, expected):
    assert module.member_for("course/notebooks/lesson.ipynb", reference) == expected


def test_unimplemented_embedded_and_runtime_visuals_stay_in_scope():
    notebook = {"nbformat": 4, "cells": [
        {"cell_type": "markdown", "source": ["![figure](attachment:x.png)"],
            "attachments": {"x.png": {"image/png": "private-pixels"}}},
        {"cell_type": "code", "source": ["![not-rendered](ignore.png)"], "outputs": [
            {"data": {"image/png": "private-pixels"}},
            {"data": {"application/vnd.jupyter.widget-view+json": {"model_id": "x"}}},
            {"data": {"text/html": '<img src="fig.png">'}},
        ]},
    ]}
    refs = module.dependencies(notebook)
    assert len(refs) == 5
    assert sum(r["reference"].startswith("attachment:") for r in refs) == 2
    assert sum(r["reference"].startswith("unsupported-output:") for r in refs) == 2
    assert refs[-1]["reference"] == "fig.png"


def ocr_fixture(tmp_path):
    source = io.BytesIO()
    Image.new("RGB", (10, 12), "white").save(source, format="PNG")
    raw = source.getvalue()
    raw_sha = module.whole.sha(raw)
    key = "image visual archive image.png"
    model, weights = "local-vision:test", "a" * 64
    extractor = "teaching-image-vision-v1-overlap64-depth3/" + model + "@" + weights
    revision = module.whole.sha((raw_sha + "\0" + extractor + "\0" + key).encode())
    image = f"document-visuals/teaching-images/{raw_sha}/image.png"
    path = tmp_path / image
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    metadata = {"model": model, "model_digest": weights, "parent_source_key": "archive image.png",
        "parent_raw_digest": raw_sha, "image": image, "image_digest": raw_sha,
        "review_required": True, "static_image_only": True, "raw_source_retained": True}
    policy = "image-vision-v1-overlap64-depth3/" + model + "@" + weights
    cache = tmp_path / "document-visuals/image-ocr-cache" / module.whole.sha((raw_sha + "\0" + policy).encode())
    cache.mkdir(parents=True)
    (cache / "0-12.json").write_text(json.dumps({"source_digest": raw_sha, "policy": policy, "text": "Synthetic diagram"}))
    (cache / "manifest.json").write_text(json.dumps({"source_digest": raw_sha, "model_digest": weights, "policy": policy,
        "width": 10, "height": 12, "raw_image_retained": image, "review_required": True,
        "regions": [{"top": 0, "bottom": 12}], "full_vertical_extent_covered": True}))
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE resource_revisions(path,source_key,raw_digest,extractor,revision,metadata)")
    db.execute("CREATE TABLE documents(path,source_key,active,locator,text,raw_digest,digest,extractor)")
    db.execute("CREATE TABLE document_version_metadata(path,source_key,revision,metadata)")
    db.execute("INSERT INTO resource_revisions VALUES(?,?,?,?,?,?)", ("synthetic.zip", key, raw_sha, extractor, revision, json.dumps(metadata)))
    body = "教学图片转录与图示解读（模型草稿，需对照原图复核）\nSynthetic diagram"
    db.execute("INSERT INTO documents VALUES(?,?,?,?,?,?,?,?)", ("synthetic.zip", key, 1, key + " chars 0", body, raw_sha, revision, extractor))
    db.execute("INSERT INTO document_version_metadata VALUES(?,?,?,?)", ("synthetic.zip", key, revision, json.dumps(metadata)))
    occurrence = {"path": "synthetic.zip", "source_key": "archive image.png", "sha256": raw_sha}
    return db, occurrence, raw, cache


def test_reuses_exact_existing_ocr_and_pixels_without_model(tmp_path):
    db, occurrence, raw, _ = ocr_fixture(tmp_path)
    files = {}
    value = module.existing_ocr(tmp_path, db, occurrence, raw, files)
    assert value["text"].endswith("Synthetic diagram")
    assert value["review_required"] is True
    assert len(value["document_rows"]) == 1
    assert len(files) == 3


@pytest.mark.parametrize("fault", ["cache_text", "chunk_text", "pixel_hash", "source_revision", "historical_metadata"])
def test_damaged_or_relabelled_ocr_is_rejected(tmp_path, fault):
    db, occurrence, raw, cache = ocr_fixture(tmp_path)
    if fault == "cache_text":
        saved = json.loads((cache / "0-12.json").read_text())
        saved["text"] = "Changed content"
        (cache / "0-12.json").write_text(json.dumps(saved))
    elif fault == "chunk_text":
        db.execute("UPDATE documents SET text='changed'")
    elif fault == "pixel_hash":
        (tmp_path / f"document-visuals/teaching-images/{occurrence['sha256']}/image.png").write_bytes(b"changed")
    elif fault == "source_revision":
        db.execute("UPDATE resource_revisions SET revision='changed'")
    else:
        db.execute("UPDATE document_version_metadata SET metadata='{}'")
    with pytest.raises(ValueError, match="requires_whole_notebook_visuals_"):
        module.existing_ocr(tmp_path, db, occurrence, raw, {})


def test_verify_requires_existing_sidecar_and_never_creates_one(tmp_path, monkeypatch):
    inventory = "b" * 64
    monkeypatch.setattr(module, "reconstruct", lambda *_: pytest.fail("must reject before reconstruction"))
    with pytest.raises(ValueError, match="existing_prepared_directory"):
        module.run(tmp_path, inventory, verify=True)
    assert not (tmp_path / "wiki").exists()


def test_verify_rejects_mutated_sidecar(tmp_path, monkeypatch):
    inventory = "b" * 64
    folder = tmp_path / "wiki/textbook-whole-notebook-visuals" / inventory
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text('{"changed": true}')
    monkeypatch.setattr(module, "reconstruct", lambda *_: {"proof": {}, "visuals": [], "unresolved": []})
    with pytest.raises(ValueError, match="immutable_recovered_sidecar"):
        module.run(tmp_path, inventory, verify=True)


def test_private_sidecar_binds_the_exact_notebook_archive_and_indexed_ocr(tmp_path, monkeypatch):
    db, occurrence, raw, _ = ocr_fixture(tmp_path)
    db.execute("CREATE TABLE videos(id,path,size,mtime_ns,duration)")
    db.commit()
    disk = sqlite3.connect(tmp_path / "knowledge.sqlite3")
    db.backup(disk)
    disk.close()
    notebook = {"nbformat": 4, "cells": [{"cell_type": "markdown", "source": ["![chart](../image.png)"]}]}
    notebook_raw = json.dumps(notebook).encode()
    notebook_sha = module.whole.sha(notebook_raw)
    archive = tmp_path / "resources/objects/course.zip"
    archive.parent.mkdir(parents=True)
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("notebooks/lesson.ipynb", notebook_raw)
        zipped.writestr("image.png", raw)
    archive_sha = module.whole.sha(archive.read_bytes())
    inventory_key = "b" * 64
    inventory = {"attachment_scope": {"selected_notebooks": [{"member_sha256": notebook_sha,
        "archive_sha256": archive_sha, "member": "notebooks/lesson.ipynb"}]},
        "sources": {"O00001": {"kind": "document", "text": notebook["cells"][0]["source"][0],
            "origin": {"member_sha256": notebook_sha, "cell": 0}}}}
    folder = tmp_path / "wiki/textbook-whole-record-evidence" / inventory_key
    folder.mkdir(parents=True)
    (folder / (notebook_sha + ".ipynb")).write_bytes(notebook_raw)
    (folder / "manifest.json").write_text(json.dumps(inventory))
    (tmp_path / "resources-manifest.json").write_text(json.dumps({"completed": [
        {"sha256": archive_sha, "object": "resources/objects/course.zip"}]}))
    (tmp_path / "teaching-image-audit.json").write_text(json.dumps({"image_occurrences": [
        {**occurrence, "container_sha256": archive_sha}]}))
    (tmp_path / "images-ocr-report.json").write_text(json.dumps({"completed": [occurrence], "errors": []}))
    monkeypatch.setattr(module.whole, "load", lambda *_: inventory)
    recovered = module.reconstruct(tmp_path, inventory_key)
    assert len(recovered["visuals"]) == 1
    image = recovered["visuals"][0]
    assert image["direct_text_source_ids"] == ["O00001"]
    assert image["semantic_coverage_verified"] is False
    assert image["existing_ocr"]["text"].endswith("Synthetic diagram")
    assert recovered["visual_dependency_recovery_complete"] is True
    assert recovered["proof"]["archive_database_read_only"] is True
    assert recovered["proof"]["model_calls"] == 0

    # A same-hash image somewhere else is not a substitute for the exact member.
    (tmp_path / "teaching-image-audit.json").write_text(json.dumps({"image_occurrences": [
        {**occurrence, "source_key": "archive different.png", "container_sha256": archive_sha}]}))
    with pytest.raises(ValueError, match="exact_same_archive_image_occurrence"):
        module.reconstruct(tmp_path, inventory_key)
