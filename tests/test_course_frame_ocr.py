import base64
import io
import json

import pytest
from PIL import Image

from wechat_decrypt_tool.course_knowledge import (
    KnowledgeStore,
    ModelOutputTruncatedError,
    ModelRepetitionError,
)
from wechat_decrypt_tool import course_frame_ocr as module


def fixture(tmp_path, monkeypatch):
    store = KnowledgeStore(tmp_path / "kb")
    key = "a" * 24
    image = store.root / "lessons" / key / "frames" / "0.jpg"
    image.parent.mkdir(parents=True)
    Image.new("RGB", (64, 400), "white").save(image)
    frame = {"path": str(image.relative_to(store.root)), "video": key}
    monkeypatch.setattr(module, "local_model_digest", lambda _: "synthetic-model")
    return store, frame


@pytest.mark.parametrize("failure", ["truncated", "server_repeat"])
def test_truncation_splits_with_overlap_and_reuses_complete_regions(
    tmp_path, monkeypatch, failure
):
    store, frame = fixture(tmp_path, monkeypatch)
    calls = []

    def chat(model, messages):
        with Image.open(
            io.BytesIO(base64.b64decode(messages[-1]["images"][0]))
        ) as image:
            height = image.height
        calls.append(height)
        if height > 250:
            if failure == "server_repeat":
                raise ModelRepetitionError()
            raise ModelOutputTruncatedError(
                {
                    "done_reason": "length",
                    "message": {"content": "synthetic discarded partial"},
                    "eval_count": 2500,
                }
            )
        return "synthetic_complete_region"

    for _ in range(2):
        text = module.describe_frame(store, frame, "local", chat)
    assert calls == [400, 232, 232] and "synthetic_complete_region" in text
    manifests = list(
        (store.root / "lessons" / frame["video"] / "frame-ocr-cache").glob(
            "*/manifest.json"
        )
    )
    value = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert value["full_vertical_extent_covered"] and len(value["regions"]) == 2
    assert value["regions"][0]["bottom"] > value["regions"][1]["top"]
    rejected = json.loads(
        next(manifests[0].parent.glob("*.truncated.json")).read_text(encoding="utf-8")
    )
    assert rejected["accepted_as_evidence"] is False and "discarded partial" not in text


def test_persistent_truncation_keeps_failure_explicit_without_accepting_partial(
    tmp_path, monkeypatch
):
    store, frame = fixture(tmp_path, monkeypatch)

    def chat(*args):
        raise RuntimeError("Text model output was truncated")

    with pytest.raises(RuntimeError, match="subdivision limit"):
        module.describe_frame(store, frame, "local", chat)
    assert not list(
        (store.root / "lessons" / frame["video"] / "frame-ocr-cache").glob(
            "*/manifest.json"
        )
    )
