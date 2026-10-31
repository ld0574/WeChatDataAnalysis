import contextlib
import hashlib
import io
import json

import pytest
from PIL import Image
from fastapi.testclient import TestClient

from wechat_decrypt_tool import course_animations as module
from wechat_decrypt_tool import course_tiled_ocr
from wechat_decrypt_tool.course_knowledge import KnowledgeStore, create_app


def animation_bytes(*, disposal=False):
    buffer = io.BytesIO()
    if disposal:
        palette = [0, 0, 0, 255, 0, 0, 0, 0, 255, 0, 255, 0] + [0] * 756
        images = [
            Image.new("P", (4, 4), 1),
            Image.new("P", (4, 4), 0),
            Image.new("P", (4, 4), 0),
        ]
        for image in images:
            image.putpalette(palette)
        images[1].putpixel((1, 1), 3)
        images[2].putpixel((3, 3), 2)
        images[0].save(
            buffer,
            format="GIF",
            save_all=True,
            append_images=images[1:],
            duration=[100, 200, 300],
            loop=2,
            disposal=[1, 3, 1],
            transparency=0,
            optimize=False,
        )
    else:
        images = [Image.new("RGB", (8, 6), color) for color in ("red", "green", "red")]
        images[0].save(
            buffer,
            format="GIF",
            save_all=True,
            append_images=images[1:],
            duration=[100, 200, 300],
            loop=0,
            disposal=1,
        )
    return buffer.getvalue()


def test_every_frame_original_clock_and_repeated_positions_are_preserved(tmp_path):
    raw = animation_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    store = KnowledgeStore(tmp_path / "kb")
    value = module.prepare_animation(store, raw, digest)
    assert value["complete_frame_decode"] and value["complete_recorded_timing"]
    assert value["declared_frames"] == 3 and len(value["frames"]) == 3
    assert [(f["start_ms"], f["end_ms"]) for f in value["frames"]] == [
        (0, 100),
        (100, 300),
        (300, 600),
    ]
    assert value["retained_duration_ms"] == 600 and value["loop"] == 0
    assert value["frames"][0]["image"] == value["frames"][2]["image"]
    assert len(list((store.root / "document-animations" / "frames").glob("*.png"))) == 2
    assert (store.root / value["original"]["file"]).read_bytes() == raw
    assert module.prepare_animation(store, raw, digest) == value


def test_gif_restore_previous_disposal_matches_independent_expected_canvas(tmp_path):
    raw = animation_bytes(disposal=True)
    store = KnowledgeStore(tmp_path / "kb")
    value = module.prepare_animation(store, raw, hashlib.sha256(raw).hexdigest())
    expected = [Image.new("RGBA", (4, 4), (255, 0, 0, 255)) for _ in range(3)]
    expected[1].putpixel((1, 1), (0, 255, 0, 255))
    expected[2].putpixel((3, 3), (0, 0, 255, 255))
    for frame, canvas in zip(value["frames"], expected):
        with Image.open(store.root / frame["image"]["file"]) as stored:
            assert stored.tobytes() == canvas.tobytes()
    assert value["frames"][1]["disposal"] == 3


def test_apng_default_poster_is_retained_outside_animation_clock(tmp_path):
    images = [Image.new("RGBA", (4, 4), color) for color in ("red", "green", "blue")]
    buffer = io.BytesIO()
    images[0].save(
        buffer,
        format="PNG",
        save_all=True,
        append_images=images[1:],
        default_image=True,
        duration=[100, 150],
        loop=2,
    )
    raw = buffer.getvalue()
    store = KnowledgeStore(tmp_path / "kb")
    value = module.prepare_animation(store, raw, hashlib.sha256(raw).hexdigest())
    assert value["default_image"] and value["frames"][0]["poster"]
    assert (
        value["frames"][0]["start_ms"] is None and value["frames"][0]["end_ms"] is None
    )
    assert [(f["start_ms"], f["end_ms"]) for f in value["frames"][1:]] == [
        (0, 100),
        (100, 250),
    ]
    assert value["retained_duration_ms"] == 250


def test_strict_decode_failure_retains_original_and_prefix_without_counting_tail_complete(
    tmp_path, monkeypatch
):
    raw = animation_bytes()
    store = KnowledgeStore(tmp_path / "kb")
    original = Image.Image.convert
    calls = 0

    def convert(image, *args, **kwargs):
        nonlocal calls
        if args and args[0] == "RGBA":
            calls += 1
            if calls == 2:
                raise OSError("synthetic damaged frame")
        return original(image, *args, **kwargs)

    with monkeypatch.context() as scope:
        scope.setattr(Image.Image, "convert", convert)
        value = module.prepare_animation(store, raw, hashlib.sha256(raw).hexdigest())
    assert len(value["frames"]) == 1 and value["declared_frames"] == 3
    assert value["errors"][0]["frame"] == 1
    assert not value["complete_frame_decode"] and not value["complete_recorded_timing"]
    assert (store.root / value["original"]["file"]).read_bytes() == raw


@pytest.mark.parametrize("tamper", ["pixels", "clock", "coverage", "unsafe"])
def test_animation_validation_rejects_changed_pixels_clock_coverage_and_paths(
    tmp_path, tamper
):
    raw = animation_bytes()
    store = KnowledgeStore(tmp_path / "kb")
    digest = hashlib.sha256(raw).hexdigest()
    value = module.prepare_animation(store, raw, digest)
    if tamper == "pixels":
        path = store.root / value["frames"][0]["image"]["file"]
        data = bytearray(path.read_bytes())
        data[-8] ^= 1
        path.write_bytes(data)
    elif tamper == "clock":
        value["frames"][1]["start_ms"] += 1
    elif tamper == "coverage":
        value["frames"].pop(1)
    else:
        value["frames"][0]["image"]["file"] = "../outside.png"
    (store.root / "document-animations" / (digest + ".json")).write_text(
        json.dumps(value), "utf-8"
    )
    with pytest.raises(RuntimeError, match="hash|timeline|coverage|unsafe"):
        module.animation_evidence(store, digest, rehash=True)


def test_asset_budget_stops_without_publishing_manifest(tmp_path, monkeypatch):
    store = KnowledgeStore(tmp_path / "kb")
    raw = animation_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    monkeypatch.setattr(module, "BUDGET", 10)
    with pytest.raises(InterruptedError, match="budget"):
        module.prepare_animation(store, raw, digest)
    assert not (store.root / "document-animations" / (digest + ".json")).exists()


def test_all_frame_ocr_shares_inference_keeps_positions_and_restores_without_originals(
    tmp_path, monkeypatch
):
    from wechat_decrypt_tool.course_backup import create_snapshot, restore_snapshot

    raw = animation_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    store = KnowledgeStore(tmp_path / "kb")
    source = tmp_path / "originals_removed"
    obj = store.root / "resources" / "objects" / digest
    obj.parent.mkdir(parents=True)
    obj.write_bytes(raw)
    targets = [
        {
            "path": str(source / name),
            "source_key": "",
            "sha256": digest,
            "container_sha256": digest,
            "state": "animated_raw_retained_motion_audit_pending",
        }
        for name in ("a.gif", "b.gif")
    ]
    store.write_json("audit.json", {"image_occurrences": targets})
    store.write_json(
        "resources-manifest.json",
        {
            "source": str(source),
            "completed": [
                {
                    "relative_path": name,
                    "sha256": digest,
                    "bytes": len(raw),
                    "object": str(obj.relative_to(store.root)),
                }
                for name in ("a.gif", "b.gif")
            ],
        },
    )
    monkeypatch.setattr(
        course_tiled_ocr, "local_model_digest", lambda _: "local-fixture"
    )
    calls = []

    def chat(*args):
        calls.append(True)
        return "animation_frame_fixture"

    result = module.index_animations(
        store,
        store.root / "audit.json",
        "local",
        chat,
        lambda _: contextlib.nullcontext(),
        wait=False,
    )
    assert len(result["completed"]) == 2 and not result["errors"] and len(calls) == 2
    hits = store.search("animation_frame_fixture")
    assert len(hits) == 4
    ids = {h["id"] for h in hits}
    again = module.index_animations(
        store,
        store.root / "audit.json",
        "local",
        chat,
        lambda _: contextlib.nullcontext(),
        wait=False,
    )
    assert (
        not again["errors"]
        and len(calls) == 2
        and {h["id"] for h in store.search("animation_frame_fixture")} == ids
    )
    from wechat_decrypt_tool.course_animation_validation import verify_animations

    verified = verify_animations(store, store.root / "audit.json")
    assert verified["full_requested_ocr_scope_verified"]
    assert verified["verified_source_occurrences"] == 2
    assert verified["verified_tile_occurrences"] == 4
    assert verified["unique_crops_every_pixel_verified"] == 2
    report = json.loads((store.root / "animation-ocr-report.json").read_text("utf-8"))
    store.write_json(
        "animation-ocr-report.json", {**report, "completed": report["completed"][:1]}
    )
    assert not verify_animations(store, store.root / "audit.json")[
        "full_requested_ocr_scope_verified"
    ]
    store.write_json("animation-ocr-report.json", report)
    with store.write() as db:
        original = db.execute(
            "SELECT id,text FROM documents WHERE active=1 LIMIT 1"
        ).fetchone()
        db.execute(
            "UPDATE documents SET text='changed cache binding' WHERE id=?",
            (original["id"],),
        )
    with pytest.raises(RuntimeError, match="indexed text differs"):
        verify_animations(store, store.root / "audit.json")
    with store.write() as db:
        db.execute(
            "UPDATE documents SET text=? WHERE id=?", (original["text"], original["id"])
        )
    verify_animations(store, store.root / "audit.json")
    snapshot = create_snapshot(store, tmp_path / "backup", reserve_gib=0)
    restored = KnowledgeStore(
        restore_snapshot(snapshot["snapshot"], tmp_path / "restored", reserve_gib=0)
    )
    obj.unlink()
    with TestClient(create_app(restored, "unused")) as client:
        for hit in hits:
            doc = -hit["id"] - 1000000000
            page = client.get("/evidence/" + str(hit["id"]))
            assert page.status_code == 200 and "/animation-source/" in page.text
            response = client.get("/animation-source/" + str(doc))
            assert response.status_code == 200 and response.content == raw
            assert response.headers["content-type"] == "image/gif"
            manifest = client.get("/animation-manifest/" + str(doc))
            assert manifest.status_code == 200 and len(manifest.json()["frames"]) == 3
            assert client.get("/document-visual/" + str(doc)).status_code == 200
        assert client.get("/animation-source/99999").status_code == 404
    assert module.animation_evidence(restored, digest, rehash=True)[
        "complete_frame_decode"
    ]
