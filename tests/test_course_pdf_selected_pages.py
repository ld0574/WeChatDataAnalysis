"""Selected scopes exercise the real producer, retained sources and independent audit."""

import base64
import hashlib
import io
import json
from contextlib import nullcontext

import pytest
from PIL import Image
from pypdf import PdfWriter

from wechat_decrypt_tool import course_pdf_ocr as producer
from wechat_decrypt_tool import course_pdf_validation as validator
from wechat_decrypt_tool.course_knowledge import KnowledgeStore
from wechat_decrypt_tool.course_pdf_plan import frozen_pdf_scope
from wechat_decrypt_tool.course_pdf_retry import RetryReportStore
from wechat_decrypt_tool.course_resource_archive import archive_resources


def make_plan(pages=(3, 5)):
    return {
        "pdf_sources_selected_pages": [
            {
                "path": "fixture.pdf",
                "source_key": "",
                "parent_raw_digest": "a" * 64,
                "pages": 5,
                "selected_pages": list(pages),
            }
        ],
        "summary": {"page_occurrences": len(pages)},
    }


@pytest.mark.parametrize(
    "pages", [[], [True], [1.0], ["1"], [0], [-1], [6], [3, 3], None]
)
def test_selected_page_list_is_strict_and_bounded(pages):
    plan = make_plan()
    plan["pdf_sources_selected_pages"][0]["selected_pages"] = pages
    with pytest.raises(ValueError, match="selected pages"):
        producer.pdf_targets([plan])


@pytest.mark.parametrize(
    "field,value",
    [
        ("pages", True),
        ("pages", 0),
        ("pages", 2.5),
        ("pages", 2001),
        ("parent_raw_digest", "bad"),
        ("parent_raw_digest", 123),
        ("path", ""),
        ("source_key", None),
    ],
)
def test_selected_source_binding_is_checked(field, value):
    plan = make_plan()
    plan["pdf_sources_selected_pages"][0][field] = value
    with pytest.raises(ValueError, match="frozen source plan"):
        producer.pdf_targets([plan])


def test_selected_plans_reject_overlaps_conflicts_and_implicit_widening():
    plan = make_plan()
    with pytest.raises(ValueError, match="duplicate page occurrences"):
        producer.pdf_targets([plan, plan])
    other = make_plan([2])
    other["pdf_sources_selected_pages"][0]["parent_raw_digest"] = "b" * 64
    with pytest.raises(ValueError, match="Conflicting"):
        producer.pdf_targets([plan, other])
    other["pdf_sources_selected_pages"][0]["parent_raw_digest"] = "a" * 64
    other["pdf_sources_selected_pages"][0]["pages"] = 6
    with pytest.raises(ValueError, match="Conflicting"):
        producer.pdf_targets([plan, other])
    other = make_plan([2])
    # Disjoint explicit selections for the same source are valid.
    assert len(producer.pdf_targets([plan, other])) == 2
    plan["pdf_sources_all_pages"] = [
        {
            k: v
            for k, v in plan["pdf_sources_selected_pages"][0].items()
            if k != "selected_pages"
        }
    ]
    plan["summary"]["page_occurrences"] = 7
    with pytest.raises(ValueError, match="duplicate page occurrences"):
        producer.pdf_targets([plan])
    plan = make_plan()
    plan["pdf_warnings"] = [{"warning": "skipped"}]
    with pytest.raises(ValueError, match="mix legacy"):
        producer.pdf_targets([plan])
    plan = make_plan()
    plan["summary"]["page_occurrences"] = 5
    with pytest.raises(ValueError, match="page count differs"):
        producer.pdf_targets([plan])
    plan["summary"]["page_occurrences"] = True
    with pytest.raises(ValueError, match="page count differs"):
        producer.pdf_targets([plan])


def retained_fixture(tmp_path, monkeypatch):
    source = tmp_path / "originals"
    source.mkdir()
    original = source / "fixture.pdf"
    writer = PdfWriter()
    for _ in range(5):
        writer.add_blank_page(width=64, height=32)
    with original.open("wb") as f:
        writer.write(f)
    digest = hashlib.sha256(original.read_bytes()).hexdigest()
    store = KnowledgeStore(tmp_path / "kb")
    store.save_document_versions(
        [
            {
                "path": str(original),
                "source_key": "",
                "raw_digest": digest,
                "extractor": "fixture-parent",
                "chunks": [],
                "metadata": {"pages": 5},
            }
        ]
    )
    inventory = tmp_path / "inventory.json"
    inventory.write_text("{}")
    archive_resources(store, source, inventory, reserve_gib=0)
    original.unlink()
    poppler = tmp_path / "renderer.exe"
    poppler.write_bytes(b"selected-page-renderer-fixture")
    monkeypatch.setattr(producer, "local_model_digest", lambda _: "c" * 64)
    renders, calls = [], []

    def render(pdf, page, destination, executable):
        assert executable == poppler
        assert hashlib.sha256(pdf.read_bytes()).hexdigest() == digest
        renders.append(page)
        Image.new("RGB", (64, 32), (page, 251, 252)).save(destination)

    monkeypatch.setattr(producer, "render_page", render)

    def chat(model, messages):
        with Image.open(io.BytesIO(base64.b64decode(messages[-1]["images"][0]))) as im:
            page = im.getpixel((0, 0))[0]
        calls.append(page)
        return "selected_pdf_fixture_page_" + str(page)

    def independent(pdf, page, destination, executable):
        assert executable == poppler
        assert hashlib.sha256(pdf.read_bytes()).hexdigest() == digest
        Image.new("RGB", (64, 32), (page, 251, 252)).save(destination)
        return {"stderr_bytes": 0, "stderr_sha256": hashlib.sha256(b"").hexdigest()}

    def plan(pages, name="plan.json"):
        value = make_plan(pages)
        target = value["pdf_sources_selected_pages"][0]
        target.update(path=str(original), parent_raw_digest=digest)
        store.write_json(name, value)
        return store.root / name

    def run(path, prefix, chat_fn=chat):
        return producer.run_pdf_ocr(
            RetryReportStore(store, prefix),
            [path],
            source,
            "local-fixture",
            chat_fn,
            lambda _: nullcontext(),
            poppler=poppler,
        )

    return store, poppler, independent, plan, run, renders, calls, chat


def page_history(store, page):
    key = "visual  page " + str(page)
    with store.connect() as db:
        return {
            table: [
                tuple(r)
                for r in db.execute(
                    "SELECT * FROM " + table + " WHERE source_key=? ORDER BY rowid",
                    (key,),
                )
            ]
            for table in (
                "documents",
                "resource_revisions",
                "document_version_metadata",
            )
        }


def test_subset_preserves_old_pages_and_reports_without_claiming_missing_pages(
    tmp_path, monkeypatch
):
    store, poppler, independent, plan, run, renders, calls, _ = retained_fixture(
        tmp_path, monkeypatch
    )
    run(plan([1], "prior-plan.json"), "prior")
    prior = page_history(store, 1)
    store.write_json("old-failure.json", {"failed_page": 2, "preserved": True})
    old_reports = {p.name: p.read_bytes() for p in store.root.glob("prior-*.json")}
    old_reports["old-failure.json"] = (store.root / "old-failure.json").read_bytes()
    selected = plan([3, 5])
    result = run(selected, "selected")
    assert renders == calls == [1, 3, 5]
    assert [r["page"] for r in result["completed"]] == [3, 5]
    assert result["full_requested_scope_processed"]
    verified = validator.verify_pdf_archive(
        store,
        selected,
        poppler,
        rerender=independent,
        report_path=store.root / "selected-ocr-report.json",
        result_name="selected-validation.json",
    )
    assert verified["all_requested_pdf_page_occurrences_verified"]
    assert verified["requested_page_occurrences"] == 2
    assert verified["unique_original_pages_rerendered"] == 2
    assert verified["page_visual_and_citation_route_groups"] == 2
    assert verified["page_scope"] == "selected_pages"
    assert not verified["entire_pdf_scope_verified"]
    assert not verified["source_deletion_allowed"]
    for page in (2, 4):
        assert not any(page_history(store, page).values())
    run(selected, "selected-again")
    assert renders == calls == [1, 3, 5]
    assert page_history(store, 1) == prior
    assert all(
        (store.root / name).read_bytes() == raw for name, raw in old_reports.items()
    )


@pytest.mark.parametrize(
    "tamper", ["missing", "extra", "duplicate", "overlap", "boolean"]
)
def test_independent_validator_requires_exact_subset_partition(
    tmp_path, monkeypatch, tamper
):
    store, poppler, independent, plan, run, _, _, _ = retained_fixture(
        tmp_path, monkeypatch
    )
    selected = plan([3, 5])
    report = run(selected, "selected")
    if tamper == "missing":
        report["completed"].pop()
    elif tamper == "extra":
        report["completed"].append({**report["completed"][0], "page": 2})
    elif tamper == "duplicate":
        report["completed"].append(report["completed"][0])
    elif tamper == "overlap":
        report["errors"].append(report["completed"][0])
    else:
        report["completed"][0]["page"] = True
    store.write_json("tampered-report.json", report)
    with pytest.raises(RuntimeError, match="partition differs"):
        validator.verify_pdf_archive(
            store,
            selected,
            poppler,
            rerender=independent,
            report_path=store.root / "tampered-report.json",
        )


def test_selected_page_errors_are_accounted_without_widening_scope(
    tmp_path, monkeypatch
):
    store, poppler, independent, plan, run, _, _, chat = retained_fixture(
        tmp_path, monkeypatch
    )

    def fail_first(model, messages):
        text = chat(model, messages)
        if text.endswith("3"):
            raise RuntimeError("fixture page unavailable")
        return text

    selected = plan([3, 5])
    report = run(selected, "failed", fail_first)
    assert report["full_requested_scope_attempted"]
    assert not report["full_requested_scope_processed"]
    assert report["attempted_page_occurrences"] == 2
    result = validator.verify_pdf_archive(
        store,
        selected,
        poppler,
        rerender=independent,
        report_path=store.root / "failed-ocr-report.json",
    )
    assert result["failed_page_occurrences"] == 1
    assert result["verified_completed_page_occurrences"] == 1
    assert not result["all_requested_pdf_page_occurrences_verified"]
    assert not any(page_history(store, 2).values())


def test_full_native_count_is_checked_by_both_producer_and_validator(
    tmp_path, monkeypatch
):
    store, poppler, independent, plan, run, renders, _, _ = retained_fixture(
        tmp_path, monkeypatch
    )
    selected = plan([3, 5])
    run(selected, "selected")
    value = json.loads(selected.read_text("utf-8"))
    value["pdf_sources_selected_pages"][0]["pages"] = 6
    store.write_json("wrong-native-count.json", value)
    wrong = store.root / "wrong-native-count.json"
    report = run(wrong, "wrong")
    assert not report["full_requested_scope_attempted"]
    assert "native page count differs" in report["errors"][0]["error"]
    assert renders == [3, 5]
    with pytest.raises(RuntimeError, match="native page count differs"):
        validator.verify_pdf_archive(
            store,
            wrong,
            poppler,
            rerender=independent,
            report_path=store.root / "selected-ocr-report.json",
        )


def test_legacy_all_pages_plan_still_completes_and_validates(tmp_path, monkeypatch):
    store, poppler, independent, plan, run, renders, _, _ = retained_fixture(
        tmp_path, monkeypatch
    )
    selected = plan([3, 5])
    run(selected, "selected")
    value = json.loads(selected.read_text("utf-8"))
    target = value.pop("pdf_sources_selected_pages")[0]
    del target["selected_pages"]
    value.update(pdf_sources_all_pages=[target], summary={"page_occurrences": 5})
    store.write_json("legacy-plan.json", value)
    legacy = store.root / "legacy-plan.json"
    assert len(frozen_pdf_scope(value)) == 5
    report = run(legacy, "legacy")
    assert report["processed"] == 5 and renders == [3, 5, 1, 2, 4]
    verified = validator.verify_pdf_archive(
        store,
        legacy,
        poppler,
        rerender=independent,
        report_path=store.root / "legacy-ocr-report.json",
    )
    assert verified["all_requested_pdf_page_occurrences_verified"]
    assert verified["entire_pdf_scope_verified"]
