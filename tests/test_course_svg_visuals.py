import base64
import contextlib
import hashlib
import io
import json
from pathlib import Path

import pytest
from PIL import Image
from fastapi.testclient import TestClient

from wechat_decrypt_tool import course_svg_visuals as module
from wechat_decrypt_tool import course_tiled_ocr
from wechat_decrypt_tool.course_knowledge import KnowledgeStore, create_app

RAW = b'<svg xmlns="http://www.w3.org/2000/svg" width="20" height="10" viewBox="0 0 20 10"><rect width="20" height="10" fill="red"/></svg>'


@pytest.mark.parametrize(
    "fragment",
    [
        "<script>throw 1</script>",
        "<foreignObject/>",
        '<animate attributeName="x"/>',
        '<animateColor attributeName="fill"/>',
        "<style>@keyframes spin { from { opacity:0; } to { opacity:1; } }</style>",
        '<rect style="animation:spin 1s infinite"/>',
        '<image href="file:///private.png"/>',
        '<image href="https://example.test/private.png"/>',
        '<rect style="fill:url(https://example.test/a)"/>',
        '<style>@import "https://example.test/a"</style>',
        '<rect onload="throw 1"/>',
        '<image href="data:image/svg+xml;base64,PHN2Zy8+"/>',
    ],
)
def test_active_external_and_nested_vector_resources_are_rejected(fragment):
    raw = ('<svg xmlns="http://www.w3.org/2000/svg">' + fragment + "</svg>").encode()
    with pytest.raises(ValueError):
        module.inspect_static_svg(raw)


def test_dtd_and_animated_embedded_raster_are_not_silently_rasterized():
    with pytest.raises(ValueError, match="declarations"):
        module.inspect_static_svg(b'<!DOCTYPE svg SYSTEM "file:///private"><svg/>')
    images = [Image.new("RGB", (2, 2), c) for c in ("red", "green")]
    buf = io.BytesIO()
    images[0].save(
        buf, format="GIF", save_all=True, append_images=images[1:], duration=100
    )
    payload = base64.b64encode(buf.getvalue()).decode()
    raw = (
        '<svg xmlns="http://www.w3.org/2000/svg"><image href="data:image/gif;base64,'
        + payload
        + '"/></svg>'
    ).encode()
    with pytest.raises(ValueError, match="animated"):
        module.inspect_static_svg(raw)


def test_warning_provenance_ignores_log_clock_but_detects_changed_diagnostics():
    first = module.warning_summary(
        b"2026-10-31T23:10:01Z WARN attribute parse diagnostic\n"
    )
    second = module.warning_summary(
        b"2026-11-01T00:11:02Z WARN attribute parse diagnostic\n"
    )
    changed = module.warning_summary(
        b"2026-11-01T00:11:02Z WARN different parse diagnostic\n"
    )
    assert first == second
    assert first["log_clock_fields_removed"] == 1
    assert changed["warnings_sha256"] != first["warnings_sha256"]
    message_date = module.warning_summary(
        b"2026-10-31T23:10:01Z WARN a source date 2030-01-01T01:01:01Z\n"
    )
    changed_message_date = module.warning_summary(
        b"2026-10-31T23:10:01Z WARN a source date 2031-01-01T01:01:01Z\n"
    )
    assert message_date["log_clock_fields_removed"] == 1
    assert message_date["warnings_sha256"] != changed_message_date["warnings_sha256"]


def test_embedded_static_jpeg_is_valid_without_animation_metadata():
    buffer = io.BytesIO()
    Image.new("RGB", (4, 3), "red").save(buffer, format="JPEG")
    payload = base64.b64encode(buffer.getvalue()).decode()
    raw = (
        '<svg xmlns="http://www.w3.org/2000/svg"><image href="data:image/jpeg;base64,'
        + payload
        + '"/></svg>'
    ).encode()
    info = module.inspect_static_svg(raw)
    assert info["embedded_rasters"][0]["format"] == "JPEG"
    assert info["embedded_rasters"][0]["size"] == [4, 3]


@pytest.fixture
def fake_renderer(monkeypatch):
    calls = []

    def render(raw, runtime, fonts, width):
        calls.append(hashlib.sha256(raw).hexdigest())
        buffer = io.BytesIO()
        Image.new("RGBA", (20, 10), "red").save(buffer, format="PNG")
        return buffer.getvalue(), {
            "width": width,
            "rasterSize": [20, 10],
            "engine": "test-only",
            "warning_bytes": 0,
        }

    monkeypatch.setattr(module, "render_svg", render)
    return calls


def test_native_svg_is_immutable_and_repeated_pixels_are_checked(
    tmp_path, fake_renderer
):
    store = KnowledgeStore(tmp_path / "kb")
    digest = hashlib.sha256(RAW).hexdigest()
    value = module.prepare_svg(store, RAW, digest, runtime="unused", fonts=[])
    assert module.prepare_svg(store, RAW, digest, runtime="unused", fonts=[]) == value
    assert len(fake_renderer) == 1
    assert module.verify_svg_render(store, digest, runtime="unused", fonts=[])[
        "all_repeated_raster_pixels_identical"
    ]
    assert len(fake_renderer) == 2
    render = store.root / value["render"]["file"]
    Image.new("RGBA", (20, 10), "blue").save(render, format="PNG")
    with pytest.raises(RuntimeError, match="hash differs"):
        module.svg_evidence(store, digest, rehash=True)


def test_svg_disk_budget_does_not_publish_an_unretained_render(
    tmp_path, fake_renderer, monkeypatch
):
    store = KnowledgeStore(tmp_path / "kb")
    digest = hashlib.sha256(RAW).hexdigest()
    monkeypatch.setattr(module, "BUDGET", 1)
    with pytest.raises(InterruptedError, match="budget"):
        module.prepare_svg(store, RAW, digest, runtime="unused", fonts=[])
    assert not (store.root / "document-vectors" / (digest + ".json")).exists()


def test_repeat_render_detects_rgb_change_even_when_alpha_is_identical(
    tmp_path, fake_renderer, monkeypatch
):
    store = KnowledgeStore(tmp_path / "kb")
    digest = hashlib.sha256(RAW).hexdigest()
    value = module.prepare_svg(store, RAW, digest, runtime="unused", fonts=[])
    buffer = io.BytesIO()
    Image.new("RGBA", (20, 10), "blue").save(buffer, format="PNG")

    def changed(*args):
        return buffer.getvalue(), value["renderer"]

    monkeypatch.setattr(module, "render_svg", changed)
    with pytest.raises(RuntimeError, match="pixels differ"):
        module.verify_svg_render(store, digest, runtime="unused", fonts=[])


def test_svg_ocr_uses_derived_coordinates_and_restores_original_vector_source(
    tmp_path, fake_renderer, monkeypatch
):
    from wechat_decrypt_tool.course_backup import create_snapshot, restore_snapshot

    store = KnowledgeStore(tmp_path / "kb")
    digest = hashlib.sha256(RAW).hexdigest()
    module.prepare_svg(store, RAW, digest, runtime="unused", fonts=[])
    source = tmp_path / "removed-originals"
    obj = store.root / "resources/objects" / digest
    obj.parent.mkdir(parents=True)
    obj.write_bytes(RAW)
    targets = [
        {
            "path": str(source / name),
            "source_key": "",
            "sha256": digest,
            "container_sha256": digest,
            "state": "vector_raw_retained_render_audit_pending",
        }
        for name in ("a.svg", "b.svg")
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
                    "bytes": len(RAW),
                    "object": str(obj.relative_to(store.root)),
                }
                for name in ("a.svg", "b.svg")
            ],
        },
    )
    monkeypatch.setattr(course_tiled_ocr, "local_model_digest", lambda _: "test-model")
    calls = []

    def chat(*args):
        calls.append(True)
        return "svg_rendered_fixture"

    def lease(_):
        return contextlib.nullcontext()

    result = module.index_svgs(
        store, store.root / "audit.json", "local", chat, lease, wait=False
    )
    assert len(result["completed"]) == 2 and not result["errors"] and len(calls) == 1
    hits = store.search("svg_rendered_fixture")
    assert len(hits) == 2 and all("派生渲染像素" in h["text"] for h in hits)
    with store.connect() as db:
        meta = json.loads(
            db.execute("SELECT metadata FROM resource_revisions LIMIT 1").fetchone()[0]
        )
    assert meta["parent_raw_digest"] == digest
    assert meta["source_image_digest"] != digest
    assert meta["coordinate_space"].startswith("Derived SVG raster pixels")
    again = module.index_svgs(
        store, store.root / "audit.json", "local", chat, lease, wait=False
    )
    assert not again["errors"] and len(calls) == 1
    snapshot = create_snapshot(store, tmp_path / "backup", reserve_gib=0)
    restored = KnowledgeStore(
        restore_snapshot(snapshot["snapshot"], tmp_path / "restored", reserve_gib=0)
    )
    obj.unlink()
    assert module.svg_evidence(restored, digest, rehash=True)["source_sha256"] == digest
    with TestClient(create_app(restored, "unused")) as client:
        for hit in hits:
            doc = -hit["id"] - 1000000000
            original = client.get("/document-source/" + str(doc))
            assert original.status_code == 200 and original.content == RAW
            assert original.headers["content-type"] == "application/octet-stream"
            assert client.get("/document-visual/" + str(doc)).status_code == 200


def test_native_pinned_resvg_renders_synthetic_use_mask_and_embedded_bitmap():
    runtime = (
        Path(__file__).resolve().parents[1] / "output/course-knowledge/render-runtime"
    )
    font = Path("C:/Windows/Fonts/msyh.ttc")
    if (
        not (runtime / "node_modules/@resvg/resvg-js/package.json").exists()
        or not font.exists()
    ):
        pytest.skip(
            "Pinned local render runtime is a separately installed optional tool"
        )
    buffer = io.BytesIO()
    Image.new("RGB", (20, 10), (0, 255, 0)).save(buffer, format="PNG")
    payload = base64.b64encode(buffer.getvalue()).decode()
    raw = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="10"><defs><rect id="r" width="20" height="10" fill="white"/><mask id="m"><use href="#r"/></mask></defs><image mask="url(#m)" width="20" height="10" href="data:image/png;base64,'
        + payload
        + '"/></svg>'
    ).encode()
    info = module.inspect_static_svg(raw)
    assert info["embedded_raster_references"] == 1 and info["internal_references"] == 1
    png, renderer = module.render_svg(raw, runtime, [font], 20)
    with Image.open(io.BytesIO(png)) as image:
        assert image.size == (20, 10)
        assert image.convert("RGBA").tobytes() == bytes((0, 255, 0, 255)) * 200
    assert renderer["engine"] == "@resvg/resvg-js@2.6.2"
    assert renderer["unresolvedImages"] == 0
