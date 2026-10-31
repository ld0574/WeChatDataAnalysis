import hashlib
import json
import zipfile
from contextlib import nullcontext

import pytest
from fastapi.testclient import TestClient

from wechat_decrypt_tool.course_knowledge import KnowledgeStore, create_app
from wechat_decrypt_tool import course_pdf_ocr as module


def test_pdf_queue_deduplicates_pages_and_preserves_truncation_requests():
    r = {
        "pdf_pages_needing_ocr": [
            {"path": "a.zip", "location": "archive inner.pdf page 6"}
        ],
        "pdf_warnings": [
            {
                "path": "a.pdf",
                "location": "",
                "warning": "further form content is skipped",
            }
        ],
    }
    targets = module.pdf_targets([r, r])
    assert len(targets) == 2 and targets[0]["page"] == 6 and targets[1]["page"] is None


def test_pdf_member_is_read_only_and_path_traversal_is_rejected(tmp_path):
    path = tmp_path / "a.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("inner.pdf", b"pdf bytes")
        z.writestr("../escape.pdf", b"bad")
    before = path.read_bytes()
    temp = tmp_path / "temp"
    temp.mkdir()
    with module.pdf_source(
        tmp_path, {"path": str(path), "source_key": "archive inner.pdf"}, temp
    ) as (p, digest):
        assert (
            p.read_bytes() == b"pdf bytes"
            and digest == hashlib.sha256(b"pdf bytes").hexdigest()
        )
    with pytest.raises(ValueError, match="Unsafe"):
        with module.pdf_source(
            tmp_path, {"path": str(path), "source_key": "archive ../escape.pdf"}, temp
        ):
            pass
    assert path.read_bytes() == before and not (tmp_path / "escape.pdf").exists()


def test_ocr_caches_gpu_output_and_serves_immutable_page_evidence(
    tmp_path, monkeypatch
):
    from PIL import Image

    source = tmp_path / "source"
    source.mkdir()
    pdf = source / "a.pdf"
    pdf.write_bytes(b"test-pdf")
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            {"pdf_pages_needing_ocr": [{"path": str(pdf), "location": " page 1"}]}
        ),
        encoding="utf-8",
    )
    poppler = tmp_path / "poppler.exe"
    poppler.write_bytes(b"test-renderer")
    monkeypatch.setattr(module, "local_model_digest", lambda _: "local-weights-digest")

    def render(pdf, page, destination, program):
        Image.new("RGB", (8, 8), "white").save(destination)

    monkeypatch.setattr(module, "render_page", render)
    calls = []

    def chat(*args, **kwargs):
        calls.append(1)
        return "visible_ocr_marker"

    store = KnowledgeStore(tmp_path / "kb")
    for _ in range(2):
        result = module.run_pdf_ocr(
            store,
            [report],
            source,
            "local",
            chat,
            lambda _: nullcontext(),
            poppler=poppler,
        )
    assert (
        result["errors"] == []
        and len(calls) == 1
        and store.search("visible_ocr_marker")
    )
    evidence_id = store.search("visible_ocr_marker")[0]["id"]
    document_id = -evidence_id - 1_000_000_000
    with TestClient(create_app(store, "unused")) as client:
        assert "/document-visual/" in client.get("/evidence/" + str(evidence_id)).text
        image = client.get("/document-visual/" + str(document_id))
        assert image.status_code == 200 and image.headers["content-type"] == "image/png"


def test_changed_pdf_invalidates_old_ocr_but_keeps_old_evidence(tmp_path):
    store = KnowledgeStore(tmp_path / "kb")
    path = tmp_path / "a.pdf"

    def base(raw, text):
        return {
            "path": str(path),
            "source_key": "",
            "raw_digest": raw,
            "extractor": "v1",
            "chunks": [(" text chars 0", text)],
            "metadata": {},
        }

    store.save_document_versions([base("old", "base_old")])
    visual = {
        "path": str(path),
        "source_key": "visual  page 1",
        "raw_digest": "old",
        "extractor": "ocr-v1",
        "chunks": [("visual  page 1 chars 0", "ocr_old")],
        "metadata": {"parent_source_key": "", "parent_raw_digest": "old"},
    }
    store.save_document_versions([visual])
    assert store.search("ocr_old")
    store.save_document_versions([base("new", "base_new")])
    assert not store.search("ocr_old")
    with pytest.raises(RuntimeError, match="source revision changed"):
        store.save_document_versions([visual])
    with store.connect() as c:
        assert (
            c.execute("SELECT text FROM documents WHERE text='ocr_old'").fetchone()[0]
            == "ocr_old"
        )


def test_page_gpu_waits_without_losing_pause_control(tmp_path, monkeypatch):
    from wechat_decrypt_tool.course_knowledge import gpu_lease

    store = KnowledgeStore(tmp_path / "kb")
    holder = gpu_lease(store)
    holder.__enter__()
    stages = []
    monkeypatch.setattr(
        module.time, "sleep", lambda _: holder.__exit__(None, None, None)
    )
    with module.page_gpu_lease(store, gpu_lease, stages.append, True):
        assert stages == ["waiting_gpu"]
    with gpu_lease(store):
        pass
    (store.root / "pause.flag").write_text("pause")
    with pytest.raises(InterruptedError):
        with module.page_gpu_lease(store, gpu_lease, stages.append, True):
            pass


@pytest.mark.parametrize("archived", [False, True])
def test_pdf_uses_retained_source_and_rejects_stale_plan(tmp_path, archived):
    from wechat_decrypt_tool.course_resource_archive import archive_resources

    source = tmp_path / "source"
    source.mkdir()
    path = source / ("course.zip" if archived else "course.pdf")
    raw = b"synthetic retained PDF"
    if archived:
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("slides/course.pdf", raw)
    else:
        path.write_bytes(raw)
    key = "archive slides/course.pdf" if archived else ""
    digest = hashlib.sha256(raw).hexdigest()
    store = KnowledgeStore(tmp_path / "kb")
    store.save_document_versions(
        [
            {
                "path": str(path),
                "source_key": key,
                "raw_digest": digest,
                "extractor": "synthetic",
                "chunks": [],
                "metadata": {"pages": 1},
            }
        ]
    )
    inventory = tmp_path / "inventory.json"
    inventory.write_text("{}")
    archive_resources(store, source, inventory, reserve_gib=0)
    path.unlink()
    target = {"path": str(path), "source_key": key, "expected_digest": digest}
    with module.pdf_source(source, target, tmp_path, store=store) as (pdf, sha):
        assert pdf.read_bytes() == raw and sha == digest
    with pytest.raises(RuntimeError, match="revision changed"):
        with module.pdf_source(
            source, {**target, "expected_digest": "0" * 64}, tmp_path, store=store
        ):
            pytest.fail("stale plan must never reach rendering")


def test_all_page_plan_preserves_digest_and_rejects_conflicts():
    row = {"path": "source.pdf", "source_key": "", "parent_raw_digest": "a" * 64}
    plan = {"pdf_sources_all_pages": [row]}
    assert module.pdf_targets([plan, plan]) == [
        {
            "path": "source.pdf",
            "source_key": "",
            "page": None,
            "expected_digest": "a" * 64,
        }
    ]
    with pytest.raises(ValueError, match="Conflicting"):
        module.pdf_targets(
            [plan, {"pdf_sources_all_pages": [{**row, "parent_raw_digest": "b" * 64}]}]
        )


def test_all_pages_ocr_reuses_hash_cache_across_portable_aliases(tmp_path, monkeypatch):
    from io import BytesIO
    from PIL import Image
    from pypdf import PdfWriter
    from wechat_decrypt_tool.course_resource_archive import archive_resources

    source = tmp_path / "source"
    source.mkdir()
    writer = PdfWriter()
    for _ in range(2):
        writer.add_blank_page(width=100, height=100)
    buffer = BytesIO()
    writer.write(buffer)
    raw = buffer.getvalue()
    sha = hashlib.sha256(raw).hexdigest()
    store = KnowledgeStore(tmp_path / "kb")
    parents = []
    for name in ("original.pdf", "alias.pdf"):
        path = source / name
        path.write_bytes(raw)
        store.save_document_versions(
            [
                {
                    "path": str(path),
                    "source_key": "",
                    "raw_digest": sha,
                    "extractor": "synthetic",
                    "chunks": [],
                    "metadata": {"pages": 2},
                }
            ]
        )
        parents.append({"path": str(path), "source_key": "", "parent_raw_digest": sha})
    inventory = tmp_path / "inventory.json"
    inventory.write_text("{}")
    archive_resources(store, source, inventory, reserve_gib=0)
    for path in source.iterdir():
        path.unlink()
    report = tmp_path / "plan.json"
    report.write_text(json.dumps({"pdf_sources_all_pages": parents}))
    renderer = tmp_path / "renderer.exe"
    renderer.write_bytes(b"synthetic renderer")
    monkeypatch.setattr(module, "local_model_digest", lambda _: "local-weights")
    renders, calls = [], []

    def render(pdf, page, destination, program):
        renders.append(page)
        Image.new("RGB", (20, 20), "white").save(destination)

    def chat(*args):
        calls.append(1)
        return "synthetic_page_marker"

    monkeypatch.setattr(module, "render_page", render)
    result = module.run_pdf_ocr(
        store,
        [report],
        source,
        "local",
        chat,
        lambda _: nullcontext(),
        poppler=renderer,
    )
    assert result["full_requested_scope_processed"] and result["processed"] == 4
    assert renders == [1, 2] and len(calls) == 2
    with store.connect() as db:
        metadata = [
            json.loads(r[0])
            for r in db.execute(
                "SELECT metadata FROM resource_revisions WHERE source_key LIKE 'visual %'"
            )
        ]
        assert len(metadata) == 4
        assert all(
            m["parent_raw_digest"] == sha and m["review_required"] for m in metadata
        )
