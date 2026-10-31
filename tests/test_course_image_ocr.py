import contextlib
import hashlib
import io
import json
import zipfile

import pytest
from PIL import Image

from wechat_decrypt_tool import course_frame_ocr, course_image_ocr as module
from wechat_decrypt_tool.course_image_audit import audit_images
from wechat_decrypt_tool.course_knowledge import KnowledgeStore, create_app


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    store = KnowledgeStore(tmp_path / "knowledge")
    source = tmp_path / "gone-originals"
    buffer = io.BytesIO()
    image = Image.new("RGBA", (400, 300), (0, 0, 0, 0))
    image.putpixel((200, 150), (0, 0, 0, 255))
    image.save(buffer, format="PNG")
    raw = buffer.getvalue()
    raw_hash = hashlib.sha256(raw).hexdigest()
    container = io.BytesIO()
    with zipfile.ZipFile(container, "w") as archive:
        archive.writestr("lesson/figure.png", raw)
        archive.writestr("lesson/data.zip", b"fixture retained nested archive bytes")
    value = container.getvalue()
    sha = hashlib.sha256(value).hexdigest()
    path = store.root / "resources" / "objects" / sha
    path.parent.mkdir(parents=True)
    path.write_bytes(value)
    resources = [
        {
            "relative_path": name,
            "sha256": sha,
            "bytes": len(value),
            "object": str(path.relative_to(store.root)),
        }
        for name in ("one.zip", "two.zip")
    ]
    store.write_json(
        "resources-manifest.json", {"source": str(source), "completed": resources}
    )
    rows = [
        {
            "path": str(source / resource["relative_path"]),
            "source_key": "archive lesson/figure.png",
            "container_sha256": sha,
            "sha256": raw_hash,
            "state": "raster_ocr_candidate",
        }
        for resource in resources
    ]
    audit = store.root / "image-audit.json"
    store.write_json(audit.name, {"image_occurrences": rows})
    monkeypatch.setattr(module, "local_model_digest", lambda _: "local-model-hash")
    monkeypatch.setattr(
        course_frame_ocr, "local_model_digest", lambda _: "local-model-hash"
    )
    return store, audit, path, raw


def test_duplicate_images_share_inference_but_keep_source_references_and_downloads(
    corpus,
):
    from fastapi.testclient import TestClient

    store, audit, container, raw = corpus
    calls = []

    def chat(model, messages):
        calls.append(messages)
        return "fixture diagram labels"

    def lease(_):
        return contextlib.nullcontext()

    first = module.run_image_ocr(store, audit, "local", chat, lease)
    second = module.run_image_ocr(store, audit, "local", chat, lease)
    assert len(first["completed"]) == len(second["completed"]) == 2
    assert not first["errors"] and len(calls) == 1
    with store.connect() as db:
        rows = db.execute(
            "SELECT id,path,raw_digest FROM documents WHERE active=1"
        ).fetchall()
    assert len(rows) == 2 and rows[0]["path"] != rows[1]["path"]
    assert {r["raw_digest"] for r in rows} == {hashlib.sha256(raw).hexdigest()}
    image_path = store.root / first["completed"][0]["image"]
    with Image.open(image_path) as image:
        assert image.getpixel((0, 0)) == (255, 255, 255)
        assert image.getpixel((200, 150)) == (0, 0, 0)
    with TestClient(create_app(store, "unused")) as client:
        for row in rows:
            assert client.get(f"/document-source/{row['id']}").content == raw
            assert (
                client.get(f"/document-source/{row['id']}?container=true").content
                == container.read_bytes()
            )


def test_changed_raw_container_is_never_sent_to_model(corpus):
    store, audit, container, raw = corpus
    value = bytearray(container.read_bytes())
    value[-1] ^= 1
    container.write_bytes(value)

    def no_chat(*args):
        pytest.fail("Corrupted teaching evidence must not reach inference")

    report = module.run_image_ocr(
        store, audit, "local", no_chat, lambda _: contextlib.nullcontext()
    )
    assert not report["completed"] and len(report["errors"]) == 2
    assert all("container changed" in r["error"] for r in report["errors"])


def test_existing_normalized_image_corruption_is_not_overwritten(corpus):
    store, audit, container, raw = corpus

    def lease(_):
        return contextlib.nullcontext()

    first = module.run_image_ocr(store, audit, "local", lambda *args: "fixture", lease)
    path = store.root / first["completed"][0]["image"]
    path.write_bytes(b"tampered")
    second = module.run_image_ocr(
        store, audit, "local", lambda *args: pytest.fail("No inference"), lease
    )
    assert not second["completed"] and len(second["errors"]) == 2
    assert path.read_bytes() == b"tampered"


def test_animation_and_unsafe_archive_member_are_excluded(corpus):
    store, audit, container, raw = corpus
    rows = json.loads(audit.read_text())["image_occurrences"]
    rows[0]["source_key"] = "archive ../figure.png"
    store.write_json(audit.name, {"image_occurrences": rows[:1]})
    report = module.run_image_ocr(
        store,
        audit,
        "local",
        lambda *args: pytest.fail("No inference"),
        lambda _: contextlib.nullcontext(),
    )
    assert len(report["errors"]) == 1 and "Unsafe" in report["errors"][0]["error"]
    buffer = io.BytesIO()
    a, b = Image.new("RGB", (300, 300), "black"), Image.new("RGB", (300, 300), "white")
    a.save(buffer, format="GIF", save_all=True, append_images=[b])
    with pytest.raises(ValueError, match="motion coverage"):
        module.prepare_image(store, {"sha256": "0" * 64}, buffer.getvalue(), 0)


def test_image_inventory_finds_retained_aliases_without_original_course(corpus):
    store, _, container, _ = corpus
    before = hashlib.sha256(container.read_bytes()).hexdigest()
    report = audit_images(store)
    assert report["counts"] == {"raster_ocr_candidate": 2}
    assert report["unique_raster_ocr_candidates"] == 1
    assert report["nested_archive_occurrences_not_expanded"] == 2
    assert hashlib.sha256(container.read_bytes()).hexdigest() == before
    rows = json.loads((store.root / "teaching-image-audit.json").read_text())[
        "image_occurrences"
    ]
    assert len(rows) == 2 and len({row["sha256"] for row in rows}) == 1
