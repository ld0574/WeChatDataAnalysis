import base64
import contextlib
import hashlib
import io
import json

import pytest
from PIL import Image

from wechat_decrypt_tool import course_tiled_ocr as module
from wechat_decrypt_tool.course_knowledge import (
    KnowledgeStore,
    ModelOutputTruncatedError,
)


def raw_image(size=(1230, 12)):
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    for x in range(size[0]):
        image.putpixel((x, 0), (x % 256, 20, 30, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def assert_coverage(boxes, width, height):
    ys = sorted({0, height, *(b[1] for b in boxes), *(b[3] for b in boxes)})
    for top, bottom in zip(ys, ys[1:]):
        intervals = sorted(
            (left, right)
            for left, y_top, right, y_bottom in boxes
            if y_top <= top and y_bottom >= bottom
        )
        end = 0
        for left, right in intervals:
            assert left <= end
            end = max(end, right)
        assert end == width


@pytest.mark.parametrize("size", [(2481, 2501), (668, 73721), (1, 1), (1200, 1200)])
def test_tile_plan_covers_every_pixel_without_downscaling(size):
    boxes = module.tile_boxes(*size)
    assert_coverage(boxes, *size)
    assert all(
        0 < right - left <= 1200 and 0 < bottom - top <= 1200
        for left, top, right, bottom in boxes
    )
    assert len(set(boxes)) == len(boxes)
    with pytest.raises(ValueError, match="tile count"):
        module.tile_boxes(1, 1000000)


def test_retained_tiles_preserve_pixels_overlap_and_corruption(tmp_path):
    store = KnowledgeStore(tmp_path / "kb")
    raw = raw_image()
    sha = hashlib.sha256(raw).hexdigest()
    tiles = module.retain_tiles(store, raw, sha)
    assert len(tiles) == 2
    for tile in tiles:
        with Image.open(store.root / tile["image"]) as image:
            left, top, right, bottom = tile["box"]
            assert image.size == (right - left, bottom - top)
            for x in range(image.width):
                assert image.getpixel((x, 0)) == ((left + x) % 256, 20, 30)
                assert image.getpixel((x, 1)) == (255, 255, 255)
    path = store.root / tiles[0]["image"]
    path.write_bytes(b"changed")
    with pytest.raises(RuntimeError, match="tile changed"):
        module.retain_tiles(store, raw, sha)
    assert path.read_bytes() == b"changed"


def test_dense_wide_tile_splits_horizontally_reuses_cache_and_rejects_partial(
    tmp_path, monkeypatch
):
    store = KnowledgeStore(tmp_path / "kb")
    raw = raw_image((240, 100))
    tile = module.retain_tiles(store, raw, hashlib.sha256(raw).hexdigest())[0]
    monkeypatch.setattr(module, "local_model_digest", lambda _: "local-fixture")
    calls = []

    def chat(model, messages):
        with Image.open(
            io.BytesIO(base64.b64decode(messages[-1]["images"][0]))
        ) as image:
            width = image.width
        calls.append(width)
        if width > 170:
            raise ModelOutputTruncatedError(
                {
                    "done_reason": "length",
                    "message": {"content": "rejected_partial_marker"},
                }
            )
        return "complete_region_marker"

    for _ in range(2):
        text, metadata = module.describe_tile(
            store,
            tile,
            "local",
            chat,
            lambda _: contextlib.nullcontext(),
            model_digest="local-fixture",
            wait=False,
        )
    assert calls[0] == 240 and len(calls) == 3 and max(calls[1:]) <= 170
    assert "complete_region_marker" in text and "rejected_partial_marker" not in text
    assert_coverage(metadata["inference_regions"], 240, 100)
    rejected = next(
        (store.root / "document-visuals" / "tile-ocr-cache").glob("*/*.rejected.json")
    )
    assert json.loads(rejected.read_text())["accepted_as_evidence"] is False


def test_incomplete_or_changed_model_does_not_produce_success(tmp_path, monkeypatch):
    store = KnowledgeStore(tmp_path / "kb")
    raw = raw_image((240, 100))
    tile = module.retain_tiles(store, raw, hashlib.sha256(raw).hexdigest())[0]
    monkeypatch.setattr(module, "local_model_digest", lambda _: "changed-model")
    with pytest.raises(RuntimeError, match="model changed"):
        module.describe_tile(
            store,
            tile,
            "local",
            lambda *a: pytest.fail("No inference"),
            lambda _: contextlib.nullcontext(),
            model_digest="expected-model",
            wait=False,
        )
    monkeypatch.setattr(module, "local_model_digest", lambda _: "expected-model")

    def incomplete(*a):
        raise RuntimeError("Text model output was truncated")

    with pytest.raises(RuntimeError, match="subdivision limit"):
        module.describe_tile(
            store,
            tile,
            "local",
            incomplete,
            lambda _: contextlib.nullcontext(),
            model_digest="expected-model",
            wait=False,
        )


def test_waiting_alias_reuses_result_published_before_gpu_lease_is_acquired(tmp_path):
    store = KnowledgeStore(tmp_path / "kb")
    raw = raw_image((240, 100))
    tile = module.retain_tiles(store, raw, hashlib.sha256(raw).hexdigest())[0]

    @contextlib.contextmanager
    def lease(_):
        folder = next((store.root / "document-visuals" / "tile-ocr-cache").iterdir())
        store.write_json(
            str((folder / "0-0-240-100.json").relative_to(store.root)),
            {
                "policy": module.POLICY + "/local@fixture",
                "image_digest": tile["image_digest"],
                "text": "completed_by_other_source_alias",
            },
        )
        yield

    text, _ = module.describe_tile(
        store,
        tile,
        "local",
        lambda *a: pytest.fail("Cached alias must not repeat inference"),
        lease,
        model_digest="fixture",
        wait=False,
    )
    assert "completed_by_other_source_alias" in text
