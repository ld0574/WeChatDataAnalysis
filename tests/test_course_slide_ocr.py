import hashlib
import json
from contextlib import contextmanager, nullcontext

from wechat_decrypt_tool import course_slide_ocr as module
from wechat_decrypt_tool.course_knowledge import GpuBusyError, KnowledgeStore


def fixture(tmp_path, monkeypatch):
    store = KnowledgeStore(tmp_path / "kb")
    monkeypatch.setattr(module, "local_model_digest", lambda _: "weights")
    image = store.root / "document-visuals" / "page.png"
    image.parent.mkdir()
    image.write_bytes(b"synthetic-render-bytes")
    row = {
        "path": str(tmp_path / "a.pptx"),
        "source_key": "",
        "slide": 1,
        "source_digest": "source",
        "renderer": "test",
        "image": "document-visuals/page.png",
        "image_digest": hashlib.sha256(image.read_bytes()).hexdigest(),
    }
    report = tmp_path / "render.json"
    return store, image, row, report


def test_visual_cache_reuses_identical_context_but_keeps_distinct_evidence(
    tmp_path, monkeypatch
):
    store, image, row, report = fixture(tmp_path, monkeypatch)
    report.write_text(
        json.dumps({"completed": [row, {**row, "path": "alias.pptx"}, row]})
    )
    calls = []

    def chat(*args):
        calls.append(1)
        return "synthetic_diagram"

    for _ in range(2):
        result = module.run_slide_ocr(
            store, report, "local", chat, lambda _: nullcontext()
        )
    assert len(calls) == 1 and len(result["completed"]) == 2 and not result["errors"]
    assert len(store.search("synthetic_diagram")) == 2
    image.write_bytes(b"changed")
    result = module.run_slide_ocr(store, report, "local", chat, lambda _: nullcontext())
    assert result["errors"] and not result["completed"] and len(calls) == 1


def test_busy_gpu_defers_without_consuming_inference_or_writing_evidence(
    tmp_path, monkeypatch
):
    store, image, row, report = fixture(tmp_path, monkeypatch)
    report.write_text(json.dumps({"completed": [row]}))

    @contextmanager
    def busy(_):
        raise GpuBusyError("synthetic busy")
        yield

    result = module.run_slide_ocr(store, report, "local", lambda *a: 1 / 0, busy)
    assert result["deferred_gpu"] and not result["completed"] and not result["errors"]
    with store.connect() as c:
        assert c.execute("SELECT count(*) FROM documents").fetchone()[0] == 0


def test_outside_evidence_root_is_rejected(tmp_path, monkeypatch):
    store, image, row, report = fixture(tmp_path, monkeypatch)
    report.write_text(json.dumps({"completed": [{**row, "image": "../outside.png"}]}))
    result = module.run_slide_ocr(
        store, report, "local", lambda *a: 1 / 0, lambda _: nullcontext()
    )
    assert "outside retained evidence root" in result["errors"][0]["error"]
