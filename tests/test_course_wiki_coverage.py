import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from test_course_wiki import archive as source_archive
from test_course_wiki import plan
from test_course_wiki import published as source_published
from test_course_wiki_writer import model
from wechat_decrypt_tool import course_wiki_coverage as coverage
from wechat_decrypt_tool import course_wiki_writer as writer
from wechat_decrypt_tool.course_knowledge import atomic_json
from wechat_decrypt_tool.course_library import create_app, publish
from wechat_decrypt_tool.course_video_visual_audit import ReadOnlyArchive

archive = source_archive
published = source_published


def build(store):
    return coverage.build(ReadOnlyArchive(store.root))


def test_full_inventory_includes_unregistered_courses_not_only_available_notes(
    published,
):
    store, key = published
    path = store.root / "inventory-unique.json"
    inv = json.loads(path.read_text("utf-8"))
    inv["videos"].append(
        {
            **inv["videos"][0],
            "path": str(store.root / "unregistered.mp4"),
            "relative_path": r"Collection\Lesson 10.mp4",
            "title": "Pending course",
        }
    )
    inv["unique_readable_videos"] = 2
    atomic_json(path, inv)
    publish(ReadOnlyArchive(store.root))
    result = build(store)
    assert result["scope"]["canonical_audio_courses"] == 2
    assert result["summary"]["source_notes_published"] == 1
    assert result["summary"]["courses_without_draft_citations"] == 2
    assert result["courses"][0]["id"] == key
    assert result["courses"][1]["database_state"] == "not_registered"
    assert result["summary"]["source_identity_mismatches"] == 0
    assert not result["full_source_coverage_verified"]


def test_selected_evidence_is_not_all_cited_and_a_citation_is_not_semantic_coverage(
    published, monkeypatch
):
    store, key = published
    model(monkeypatch, store, key)
    writer.generate(ReadOnlyArchive(store.root), plan(key))
    result = build(store)
    course = result["courses"][0]
    assert course["extracted_speech_segments"] == 7
    assert course["planned_reference_count"] == 7  # Six speech segments and one frame.
    assert course["draft_cited_speech_segments"] == 1
    assert course["draft_cited_frames"] == 1
    assert course["unreferenced_extracted_speech_segments"] == 6
    assert result["summary"]["courses_with_draft_citations"] == 1
    assert not course["semantic_coverage_verified"]
    assert (
        not result["semantic_accuracy_verified"]
        and not result["source_deletion_allowed"]
    )
    serialized = json.dumps(result)
    assert "synthetic segment 0" not in serialized
    assert "Synthetic explanation" not in serialized


def test_some_indexed_chunks_do_not_certify_whole_resource_or_wiki_document_support(
    published, tmp_path
):
    store, _ = published
    source = tmp_path / "materials"
    atomic_json(
        store.root / "resources-manifest.json",
        {
            "source": str(source),
            "completed": [
                {"relative_path": "notes.md", "sha256": "a" * 64},
                {"relative_path": "diagrams.pdf", "sha256": "b" * 64},
            ],
            "pending": [{"relative_path": "locked.zip", "reason": "unknown_password"}],
        },
    )
    atomic_json(
        store.root / "courseware-index-report.json",
        {
            "errors": [{"path": "locked.zip", "error": "encrypted"}],
            "skipped": [],
            "pdf_warnings": [],
        },
    )
    with store.write() as db:
        db.execute(
            "INSERT INTO documents(path,locator,digest,text,search_text,active,source_key,extractor,raw_digest) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                str(source / "notes.md"),
                "line 1",
                "digest",
                "private source prose",
                "search",
                1,
                "",
                "fixture",
                "a" * 64,
            ),
        )
    result = build(store)
    assert result["scope"]["retained_resource_paths"] == 2
    assert result["scope"]["pending_resource_paths"] == 1
    assert result["summary"]["active_document_chunks"] == 1
    assert result["summary"]["retained_paths_with_some_indexed_chunks"] == 1
    assert not any(r["wiki_document_citation_supported"] for r in result["attachments"])
    assert not any(
        r["complete_document_coverage_verified"] for r in result["attachments"]
    )
    assert result["recorded_index_findings"][
        "historical_findings_require_current_resolution_check"
    ]
    assert "private source prose" not in json.dumps(result)


def test_stale_inventory_or_tampered_article_never_replaces_previous_ledger(
    published, monkeypatch
):
    store, key = published
    model(monkeypatch, store, key)
    writer.generate(ReadOnlyArchive(store.root), plan(key))
    build(store)
    receipt = store.root / "wiki" / "coverage.json"
    original = receipt.read_bytes()
    catalog = json.loads((store.root / "wiki" / "catalog.json").read_text("utf-8"))
    object_path = (
        store.root / "wiki" / "objects" / (catalog["articles"][0]["object"] + ".json")
    )
    object_path.write_text("{}")
    with pytest.raises(ValueError, match="checksum"):
        build(store)
    assert receipt.read_bytes() == original


def test_inventory_change_requires_republishing_source_catalog(published):
    store, _ = published
    build(store)
    receipt = store.root / "wiki" / "coverage.json"
    original = receipt.read_bytes()
    path = store.root / "inventory-unique.json"
    value = json.loads(path.read_text("utf-8"))
    value["new_inventory_revision"] = True
    atomic_json(path, value)
    with pytest.raises(ValueError, match="republish_sources_first"):
        build(store)
    assert receipt.read_bytes() == original


def test_report_is_read_only_and_does_not_create_schema_writer_or_gpu_leases(
    published, monkeypatch
):
    store, _ = published
    from wechat_decrypt_tool.course_knowledge import KnowledgeStore

    monkeypatch.setattr(
        KnowledgeStore,
        "__init__",
        lambda *args, **kwargs: pytest.fail("must use read-only archive"),
    )
    with store.connect() as db:
        before = db.execute("SELECT count(*) FROM documents").fetchone()[0]
    result = build(store)
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM documents").fetchone()[0] == before
    assert not (store.root / "gpu.lock").exists()
    assert result["scope"]["canonical_audio_courses"] == 1
    assert result["gaps"]


def test_no_audio_sources_remain_explicitly_in_scope(published):
    store, _ = published
    path = store.root / "inventory-unique.json"
    inv = json.loads(path.read_text("utf-8"))
    inv["excluded_without_audio"] = [
        {"relative_path": "silent.mp4", "path": str(Path(store.root) / "silent.mp4")}
    ]
    atomic_json(path, inv)
    publish(ReadOnlyArchive(store.root))
    result = build(store)
    assert result["scope"]["excluded_no_audio_sources"] == 1
    assert len(result["no_audio_sources"]) == 1


def test_http_reads_snapshot_without_opening_database_and_detects_new_catalog(
    published, monkeypatch
):
    store, key = published
    result = build(store)
    assert "wiki/catalog.json" in result["observed_absent_inputs"]
    ro = ReadOnlyArchive(store.root)
    client = TestClient(create_app(ro))
    monkeypatch.setattr(ro, "connect", lambda: pytest.fail("HTTP must not scan DB"))
    before = client.get("/wiki/api/coverage")
    assert before.status_code == 200
    assert before.json()["stale_inputs"] == []
    assert before.json()["snapshot_only"]
    model(monkeypatch, store, key)
    writer.generate(ReadOnlyArchive(store.root), plan(key))
    assert client.get("/wiki/api/coverage").json()["stale_inputs"] == [
        "wiki/catalog.json"
    ]
    # HTTP does not silently build a new receipt or promote the old counts.
    assert client.get("/wiki/api/coverage").json()["summary"]["draft_articles"] == 0
    assert client.get("/wiki/api/catalog").json()["articles"]


def test_http_missing_invalid_and_unsafe_receipts_fail_without_breaking_reading(
    published,
):
    store, _ = published
    client = TestClient(create_app(ReadOnlyArchive(store.root)))
    assert client.get("/wiki/api/coverage").status_code == 503
    result = build(store)
    path = store.root / "wiki" / "coverage.json"
    result["input_sha256"] = {"../private.json": "a" * 64}
    atomic_json(path, result)
    assert client.get("/wiki/api/coverage").status_code == 503
    result["input_sha256"] = {}
    result["observed_absent_inputs"] = [str(store.root.parent / "elsewhere.json")]
    atomic_json(path, result)
    assert client.get("/wiki/api/coverage").status_code == 503
    path.write_text("{}")
    assert client.get("/wiki/api/coverage").status_code == 503
    assert client.get("/library/api/catalog").status_code == 200


def test_http_detects_changed_inputs_without_replacing_receipt(published):
    store, _ = published
    build(store)
    receipt = store.root / "wiki" / "coverage.json"
    before = receipt.read_bytes()
    path = store.root / "inventory-unique.json"
    value = json.loads(path.read_bytes())
    value["revision"] = 2
    atomic_json(path, value)
    client = TestClient(create_app(ReadOnlyArchive(store.root)))
    assert client.get("/wiki/api/coverage").json()["stale_inputs"] == [
        "inventory-unique.json"
    ]
    assert receipt.read_bytes() == before


def test_optional_input_appearing_midbuild_keeps_previous_snapshot(
    published, monkeypatch
):
    store, _ = published
    build(store)
    path = store.root / "wiki" / "coverage.json"
    before = path.read_bytes()
    ro = ReadOnlyArchive(store.root)
    checks = []

    def publish_at_final_checkpoint():
        checks.append(True)
        if len(checks) == 2:
            atomic_json(store.root / "wiki" / "catalog.json", {"articles": []})

    monkeypatch.setattr(ro, "checkpoint", publish_at_final_checkpoint)
    with pytest.raises(ValueError, match="inputs_changed"):
        coverage.build(ro)
    assert path.read_bytes() == before
