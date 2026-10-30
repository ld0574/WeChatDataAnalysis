import json
import zipfile
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from wechat_decrypt_tool.course_knowledge import KnowledgeStore
from wechat_decrypt_tool import course_slide_render as module


def test_slide_queue_deduplicates_page_requests():
    rows = [
        {"path": "a.pptx", "location": " slide 3", "kind": "pptx"},
        {"path": "a.pptx", "location": " slide 1", "kind": "pptx"},
    ]
    targets = module.slide_targets([{"visual_pages_needing_ocr": rows + rows}])
    assert targets == [{"path": "a.pptx", "source_key": "", "pages": [1, 3]}]


def test_deck_with_external_data_is_rejected_before_office_open(tmp_path):
    path = tmp_path / "a.pptx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(
            "ppt/slides/_rels/slide1.xml.rels",
            '<Relationships><Relationship TargetMode="External" Type="test/image" Target="https://example.com/image.png"/></Relationships>',
        )
    with pytest.raises(ValueError, match="External linked"):
        module.validate_deck(path)


def test_render_reuses_verified_images_and_preserves_original(tmp_path, monkeypatch):
    from pathlib import Path
    from PIL import Image

    source = tmp_path / "source"
    source.mkdir()
    path = source / "a.pptx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("ppt/slides/slide1.xml", "<root/>")
    original = path.read_bytes()
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            {
                "visual_pages_needing_ocr": [
                    {"path": str(path), "kind": "pptx", "location": " slide 1"}
                ]
            }
        ),
        encoding="utf-8",
    )
    calls = []

    class Slide:
        def Export(self, path, kind, width, height):
            calls.append(1)
            assert Path(path).is_absolute()
            assert kind == "PNG"
            Image.new("RGB", (width, height), "white").save(path)

    slides = SimpleNamespace(Count=1, Item=lambda page: Slide())
    presentation = SimpleNamespace(
        Slides=slides, PageSetup=SimpleNamespace(SlideWidth=160, SlideHeight=90)
    )

    @contextmanager
    def open_deck(path):
        yield SimpleNamespace(Version="16.0"), presentation

    monkeypatch.chdir(tmp_path)
    store = KnowledgeStore("kb")
    for _ in range(2):
        result = module.render_slides(store, [report], source, open_deck=open_deck)
    assert result["errors"] == [] and len(calls) == 1 and len(result["completed"]) == 1
    assert path.read_bytes() == original and result["completed"][0]["width"] == 2000
    image = store.root / result["completed"][0]["image"]
    image.write_bytes(b"changed image")
    result = module.render_slides(store, [report], source, open_deck=open_deck)
    assert (
        "Committed slide image changed" in result["errors"][0]["error"]
        and len(calls) == 1
    )


def test_document_busy_retry_does_not_hide_other_sql_errors(tmp_path, monkeypatch):
    import sqlite3

    store = KnowledgeStore(tmp_path / "kb")
    attempts = []

    def operation(versions):
        attempts.append(1)
        if len(attempts) == 1:
            raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "_save_document_versions", operation)
    store.save_document_versions([])
    assert len(attempts) == 2

    def bad(versions):
        raise sqlite3.OperationalError("no such table: invalid")

    monkeypatch.setattr(store, "_save_document_versions", bad)
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        store.save_document_versions([])
