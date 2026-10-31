import hashlib
import io
import json
from contextlib import nullcontext

import pytest
from PIL import Image, ImageDraw
from pypdf import PdfWriter

from wechat_decrypt_tool import course_pdf_ocr as ocr
from wechat_decrypt_tool import course_pdf_retry as retry
from wechat_decrypt_tool import course_pdf_validation as validation
from wechat_decrypt_tool import course_tiled_ocr as tiled
from wechat_decrypt_tool.course_knowledge import KnowledgeStore


def report(completed, errors):
    return {
        "completed": completed,
        "errors": errors,
        "processed": len(completed),
        "limit": 0,
        "deferred_gpu": False,
        "full_requested_scope_attempted": True,
        "attempted_page_occurrences": len(completed) + len(errors),
    }


def row(page):
    return {"path": "fixture.pdf", "source_key": "", "page": page}


def test_reconciliation_preserves_primary_and_accepts_only_failed_page_partition():
    primary = report([row(1)], [row(2), row(3)])
    before = json.dumps(primary)
    result = retry.reconcile_pdf_reports(primary, report([row(2)], [row(3)]))
    assert json.dumps(primary) == before
    assert result["processed"] == 2
    assert result["errors"] == [row(3)]
    assert result["attempted_page_occurrences"] == 3
    assert not result["full_requested_scope_processed"]
    assert retry.reconcile_pdf_reports(primary, report([row(2), row(3)], []))[
        "full_requested_scope_processed"
    ]


@pytest.mark.parametrize(
    "bad",
    [
        report([row(1), row(2)], []),
        report([row(2)], []),
        report([row(2), row(2)], [row(3)]),
        report([row(2)], [row(2), row(3)]),
        {**report([row(2), row(3)], []), "deferred_gpu": True},
        {**report([row(2), row(3)], []), "attempted_page_occurrences": 1},
    ],
)
def test_retry_partition_cannot_add_skip_duplicate_or_defer_pages(bad):
    with pytest.raises(ValueError):
        retry.reconcile_pdf_reports(report([row(1)], [row(2), row(3)]), bad)


def synthetic_archive(tmp_path, monkeypatch):
    store = KnowledgeStore(tmp_path / "kb")
    source = tmp_path / "originals-not-present"
    path = str(source / "a.pdf")
    writer = PdfWriter()
    writer.add_blank_page(width=1000, height=900)
    buffer = io.BytesIO()
    writer.write(buffer)
    raw = buffer.getvalue()
    digest = hashlib.sha256(raw).hexdigest()
    obj = store.root / "resources/objects" / digest
    obj.parent.mkdir(parents=True)
    obj.write_bytes(raw)
    store.write_json(
        "resources-manifest.json",
        {
            "source": str(source),
            "completed": [
                {
                    "relative_path": "a.pdf",
                    "sha256": digest,
                    "bytes": len(raw),
                    "object": str(obj.relative_to(store.root)),
                },
            ],
        },
    )
    store.save_document_versions(
        [
            {
                "path": path,
                "source_key": "",
                "raw_digest": digest,
                "extractor": "fixture-retained-v1",
                "chunks": [("parent", "fixture_retained_parent")],
                "metadata": {},
            }
        ]
    )
    poppler = tmp_path / "synthetic-renderer.exe"
    poppler.write_bytes(b"synthetic renderer")
    for module in (retry, tiled, ocr):
        monkeypatch.setattr(module, "local_model_digest", lambda _: "a" * 64)
    monkeypatch.setattr(tiled.time, "sleep", lambda _: None)

    def render(pdf, page, destination, executable):
        assert hashlib.sha256(pdf.read_bytes()).hexdigest() == digest
        assert page == 1 and executable == poppler
        image = Image.new("RGB", (1000, 900), "white")
        draw = ImageDraw.Draw(image)
        for x in range(0, 1000, 91):
            for y in range(0, 900, 87):
                draw.rectangle(
                    (x, y, x + 7, y + 5), fill=(x % 255, y % 255, (x + y) % 255)
                )
        image.save(destination)
        return {"stderr_bytes": 0, "stderr_sha256": hashlib.sha256(b"").hexdigest()}

    monkeypatch.setattr(retry, "render_page", render)
    target = {"path": path, "source_key": "", "page": 1, "expected_digest": digest}
    plan = {
        "pdf_sources_all_pages": [
            {"path": path, "source_key": "", "pages": 1, "parent_raw_digest": digest}
        ],
        "summary": {"page_occurrences": 1},
    }
    store.write_json("plan.json", plan)
    input_report = {
        "pdf_pages_needing_ocr": [
            {"path": path, "location": " page 1", "parent_raw_digest": digest}
        ]
    }
    store.write_json("retry-input.json", input_report)
    return store, source, target, poppler, render


def test_finer_grid_real_pixel_cache_fts_citation_and_portable_restore(
    tmp_path, monkeypatch
):
    from wechat_decrypt_tool.course_backup import create_snapshot, restore_snapshot

    store, source, target, poppler, render = synthetic_archive(tmp_path, monkeypatch)
    primary = report(
        [], [{**target, "error": "synthetic previous subdivision failure"}]
    )
    store.write_json("pdf-ocr-report.json", primary)
    original = (store.root / "pdf-ocr-report.json").read_bytes()
    calls = []

    def chat(*args, **kwargs):
        calls.append(1)
        return "synthetic_retry_visible_text"

    def lease(_):
        return nullcontext()

    proxy = retry.RetryReportStore(store)
    prepared = retry.prepare_retry_page(
        proxy, target, source, "local", chat, lease, poppler
    )
    assert prepared["tiles"] == 4 and len(calls) == 4
    assert retry.prepare_retry_page(
        proxy, target, source, "local", chat, lease, poppler
    )["cached_page_result"]
    assert len(calls) == 4
    result = ocr.run_pdf_ocr(
        proxy,
        [store.root / "retry-input.json"],
        source,
        "local",
        chat,
        lease,
        poppler=poppler,
        wait=True,
    )
    assert not result["errors"] and len(calls) == 4
    assert retry.prepare_retry_page(
        proxy, target, source, "local", chat, lease, poppler
    )["cached_committed_page"]
    assert (store.root / "pdf-ocr-report.json").read_bytes() == original
    merged = retry.reconcile_pdf_reports(primary, result)
    store.write_json("pdf-reconciled-report.json", merged)
    proof = validation.verify_pdf_archive(
        store,
        store.root / "plan.json",
        poppler,
        rerender=render,
        report_path=store.root / "pdf-reconciled-report.json",
        result_name="pdf-reconciled-validation.json",
    )
    assert proof["all_requested_pdf_page_occurrences_verified"]
    assert proof["accepted_tiled_inference_regions_verified"] == 4
    assert store.search("synthetic_retry_visible_text")
    snapshot = create_snapshot(store, tmp_path / "backup", reserve_gib=0)
    restored = KnowledgeStore(
        restore_snapshot(snapshot["snapshot"], tmp_path / "restored", reserve_gib=0)
    )
    for name in ("plan.json", "pdf-reconciled-report.json"):
        restored.write_json(name, json.loads((store.root / name).read_text("utf-8")))
    assert validation.verify_pdf_archive(
        restored,
        restored.root / "plan.json",
        poppler,
        rerender=render,
        report_path=restored.root / "pdf-reconciled-report.json",
    )["all_requested_pdf_page_occurrences_verified"]
    assert (store.root / "pdf-ocr-report.json").read_bytes() == original


def test_incomplete_retry_never_publishes_a_page_cache(tmp_path, monkeypatch):
    store, source, target, poppler, _ = synthetic_archive(tmp_path, monkeypatch)

    calls = []

    def fail(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            return "accepted_first_tile"
        raise RuntimeError("synthetic native inference failure")

    with pytest.raises(RuntimeError, match="native inference failure"):
        retry.prepare_retry_page(
            store, target, source, "local", fail, lambda _: nullcontext(), poppler
        )
    assert not list((store.root / "document-visuals").glob("*/*/00001-*.json"))
    assert list((store.root / "document-visuals/tile-ocr-cache").glob("*/*.json"))
    with store.connect() as db:
        assert not db.execute(
            "SELECT id FROM documents WHERE source_key LIKE 'visual %'"
        ).fetchall()


def test_failed_partition_runner_resumes_commits_and_backs_up_separate_reports(
    tmp_path, monkeypatch
):
    from pathlib import Path
    from wechat_decrypt_tool.course_backup import create_snapshot, restore_snapshot

    store, source, target, poppler, _ = synthetic_archive(tmp_path, monkeypatch)
    primary = report([], [{**target, "error": "previous subdivision failure"}])
    store.write_json("pdf-ocr-report.json", primary)
    original = (store.root / "pdf-ocr-report.json").read_bytes()
    calls = []

    def chat(*args, **kwargs):
        calls.append(1)
        return "synthetic_queued_retry"

    def lease(_):
        return nullcontext()

    merged = retry.run_failed_pages(
        store, primary, source, "local", chat, lease, poppler
    )
    assert merged["recovered_primary_errors"] == 1 and not merged["errors"]
    assert len(calls) == 4
    assert (store.root / "pdf-ocr-report.json").read_bytes() == original
    with store.connect() as db:
        original_ids = db.execute("SELECT id FROM documents ORDER BY id").fetchall()
    resumed = retry.run_failed_pages(
        store, primary, source, "local", chat, lease, poppler
    )
    assert resumed["recovered_primary_errors"] == 1 and not resumed["errors"]
    assert resumed["completed"][0]["cached"]
    with store.connect() as db:
        assert (
            db.execute("SELECT id FROM documents ORDER BY id").fetchall()
            == original_ids
        )
    assert len(calls) == 4
    result = validation.verify_pdf_archive(
        store,
        store.root / "plan.json",
        poppler,
        rerender=retry.render_page,
        report_path=store.root / "pdf-reconciled-report.json",
        result_name="pdf-reconciled-validation.json",
    )
    assert result["all_requested_pdf_page_occurrences_verified"]
    snapshot = create_snapshot(store, tmp_path / "backup", reserve_gib=0)
    restored = Path(
        restore_snapshot(snapshot["snapshot"], tmp_path / "restore", reserve_gib=0)
    )
    for name in (
        "pdf-ocr-report.json",
        "pdf-retry-ocr-report.json",
        "pdf-reconciled-report.json",
        "pdf-reconciled-validation.json",
    ):
        assert (restored / name).read_bytes() == (store.root / name).read_bytes()


def test_failed_partition_runner_retains_errors_and_does_not_publish_incomplete_page(
    tmp_path, monkeypatch
):
    store, source, target, poppler, _ = synthetic_archive(tmp_path, monkeypatch)
    primary = report([], [{**target, "error": "previous failure"}])

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic inference unavailable")

    result = retry.run_failed_pages(
        store, primary, source, "local", fail, lambda _: nullcontext(), poppler
    )
    assert result["processed"] == 0 and len(result["errors"]) == 1
    assert not result["full_requested_scope_processed"]
    assert "synthetic inference unavailable" in result["errors"][0]["error"]
    assert not list((store.root / "document-visuals").glob("*/*/00001-*.json"))
