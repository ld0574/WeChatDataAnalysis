import base64
import contextlib
import hashlib
import io
import json

from PIL import Image
from fastapi.testclient import TestClient

from wechat_decrypt_tool import course_tiled_ocr, course_visual_sources as module
from wechat_decrypt_tool.course_knowledge import KnowledgeStore, create_app


def test_notebook_image_aliases_share_inference_and_keep_exact_source_and_crop_links(
    tmp_path, monkeypatch
):
    store = KnowledgeStore(tmp_path / "kb")
    buffer = io.BytesIO()
    Image.new("RGB", (64, 40), "white").save(buffer, format="PNG")
    image_bytes = buffer.getvalue()
    image_digest = hashlib.sha256(image_bytes).hexdigest()
    raw = json.dumps(
        {
            "cells": [
                {
                    "source": [],
                    "outputs": [
                        {"data": {"image/png": base64.b64encode(image_bytes).decode()}}
                    ],
                }
            ]
        }
    ).encode()
    sha = hashlib.sha256(raw).hexdigest()
    obj = store.root / "resources" / "objects" / sha
    obj.parent.mkdir(parents=True)
    obj.write_bytes(raw)
    source = tmp_path / "originals_gone"
    targets, resources = [], []
    for name in ("a.ipynb", "b.ipynb"):
        path = str(source / name)
        store.save_document_versions(
            [
                {
                    "path": path,
                    "source_key": "",
                    "raw_digest": sha,
                    "extractor": "fixture",
                    "chunks": [],
                    "metadata": {},
                }
            ]
        )
        resources.append(
            {
                "relative_path": name,
                "sha256": sha,
                "bytes": len(raw),
                "object": str(obj.relative_to(store.root)),
            }
        )
        targets.append(
            {
                "path": path,
                "source_key": "",
                "raw_digest": sha,
                "image": {
                    "embedded_sha256": image_digest,
                    "location": {"cell": 1, "output": 1},
                    "mime": "image/png",
                    "state": "raster_ocr_candidate",
                },
            }
        )
    store.write_json(
        "resources-manifest.json", {"source": str(source), "completed": resources}
    )
    store.write_json("audit.json", {"targets": targets, "errors": []})
    for imported in (module, course_tiled_ocr):
        monkeypatch.setattr(imported, "local_model_digest", lambda _: "local-fixture")
    calls = []

    def chat(*args):
        calls.append(1)
        return "notebook_figure_marker"

    for _ in range(2):
        result = module.index_visual_sources(
            store,
            store.root / "audit.json",
            "local",
            chat,
            lambda _: contextlib.nullcontext(),
            wait=False,
        )
    assert result["full_requested_scope_processed"] and len(result["completed"]) == 2
    assert len(calls) == 1
    hits = store.search("notebook_figure_marker")
    assert len(hits) == 2
    with TestClient(create_app(store, "unused")) as client:
        for hit in hits:
            doc_id = -hit["id"] - 1000000000
            assert client.get(f"/document-source/{doc_id}").content == raw
            assert client.get(f"/document-visual/{doc_id}").content == image_bytes
    with store.connect() as db:
        metadata = [
            json.loads(x[0])
            for x in db.execute(
                "SELECT metadata FROM resource_revisions WHERE source_key LIKE 'notebook image %'"
            )
        ]
    assert all(
        x["parent_raw_digest"] == sha and x["source_image_digest"] == image_digest
        for x in metadata
    )
    targets[0]["image"]["embedded_sha256"] = "0" * 64
    store.write_json("changed-audit.json", {"targets": targets[:1], "errors": []})
    changed = module.index_visual_sources(
        store,
        store.root / "changed-audit.json",
        "local",
        chat,
        lambda _: contextlib.nullcontext(),
        wait=False,
    )
    assert len(changed["errors"]) == 1 and len(calls) == 1


def test_static_repair_uses_retained_image_and_completes_every_planned_tile(
    tmp_path, monkeypatch
):
    store = KnowledgeStore(tmp_path / "kb")
    buffer = io.BytesIO()
    Image.new("RGB", (1300, 25), "white").save(buffer, format="PNG")
    raw = buffer.getvalue()
    sha = hashlib.sha256(raw).hexdigest()
    obj = store.root / "resources" / "objects" / sha
    obj.parent.mkdir(parents=True)
    obj.write_bytes(raw)
    source = tmp_path / "gone"
    path = str(source / "long.png")
    store.write_json(
        "resources-manifest.json",
        {
            "source": str(source),
            "completed": [
                {
                    "relative_path": "long.png",
                    "sha256": sha,
                    "bytes": len(raw),
                    "object": str(obj.relative_to(store.root)),
                }
            ],
        },
    )
    store.write_json(
        "audit.json",
        {
            "image_occurrences": [
                {"path": path, "source_key": "", "sha256": sha, "container_sha256": sha}
            ]
        },
    )
    store.write_json(
        "errors.json",
        {
            "errors": [
                {"path": path, "source_key": "", "error": "fixture previous failure"}
            ]
        },
    )
    for imported in (module, course_tiled_ocr):
        monkeypatch.setattr(imported, "local_model_digest", lambda _: "local-fixture")
    result = module.index_visual_sources(
        store,
        store.root / "audit.json",
        "local",
        lambda *a: "recovered_crop_marker",
        lambda _: contextlib.nullcontext(),
        failures_path=store.root / "errors.json",
        wait=False,
    )
    assert (
        result["full_requested_scope_processed"]
        and len(result["completed"][0]["tiles"]) == 2
    )
    assert len(store.search("recovered_crop_marker")) == 2
