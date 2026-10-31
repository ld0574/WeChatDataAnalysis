import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from wechat_decrypt_tool import course_notebook_rich as module
from wechat_decrypt_tool.course_knowledge import KnowledgeStore, create_app


def notebook(html, *, views=(), states=None):
    outputs = [{"data": {"text/html": html}}]
    outputs += [
        {"data": {"application/vnd.jupyter.widget-view+json": {"model_id": key}}}
        for key in views
    ]
    return json.dumps(
        {
            "cells": [{"source": [], "outputs": outputs}],
            "metadata": {
                "widgets": {
                    "application/vnd.jupyter.widget-state+json": {"state": states}
                }
            }
            if states is not None
            else {},
        }
    ).encode()


def test_static_table_retains_source_cells_spans_and_headers_without_executing_markup():
    raw = notebook(
        '<style>body{display:none}</style><table><tr><th rowspan="2">Group</th><th colspan="2">Values</th></tr><tr><td>1 &amp; 2</td><td><b>three</b></td></tr></table><script>BAD_RUN()</script><a href="https://example.invalid/private">link</a><img src="https://example.invalid/p.png" onerror="BAD_RUN()" alt="chart">'
    )
    record = module.extract_rich(raw)[0]
    rows = record["tables"][0]["rows"]
    assert rows[0]["cells"] == [
        {"cell": 1, "header": True, "rowspan": 2, "colspan": 1, "text": "Group"},
        {"cell": 2, "header": True, "rowspan": 1, "colspan": 2, "text": "Values"},
    ]
    assert [c["text"] for c in rows[1]["cells"]] == ["1 & 2", "three"]
    assert record["omitted_runtime_elements"] == {
        "style": 1,
        "script": 1,
        "image_not_loaded": 1,
    }
    assert len(record["dependencies_not_loaded"]) == 2
    review = module.render_review(record)
    assert (
        "BAD_RUN" not in review and "<script" not in review and "onerror=" not in review
    )
    assert '<a href="https:' not in review and "<img src=" not in review
    assert 'rowspan="2"' in review and 'colspan="2"' in review
    assert "default-src 'none'" in review and record["runtime_executed"] is False
    assert (
        record["original_css_not_applied"] and not record["unclosed_source_structure"]
    )


def test_nested_table_does_not_silently_flatten_inner_cells_into_outer_rows():
    record = module.extract_rich(
        notebook(
            "<table><tr><td>outer<table><tr><td>inner</td></tr></table>tail</td></tr></table>"
        )
    )[0]
    assert len(record["tables"]) == 2
    assert record["tables"][0]["rows"][0]["cells"][0]["text"] == "outertail"
    assert record["tables"][1]["rows"][0]["cells"][0]["text"] == "inner"


def test_widget_state_keeps_reference_graph_and_missing_views_explicit():
    states = {
        "root": {
            "model_name": "BoxModel",
            "state": {"children": ["IPY_MODEL_value", "IPY_MODEL_missing"]},
        },
        "value": {
            "model_name": "ValueModel",
            "state": {
                "value": 0.75,
                "min": 0,
                "max": 1,
                "parent": "IPY_MODEL_root",
                "api_key": "synthetic_secret",
            },
        },
    }
    records = module.extract_rich(
        notebook("<p>fixture</p>", views=("root", "absent"), states=states)
    )
    root, missing = records[1:3]
    assert set(root["retained_models"]) == {"root", "value"}
    assert root["missing_model_ids"] == ["missing"] and root["root_model_resolved"]
    assert root["retained_models"]["value"]["state"]["value"] == 0.75
    assert root["retained_models"]["value"]["state"]["api_key"] == "[REDACTED]"
    assert (
        missing["missing_model_ids"] == ["absent"]
        and not missing["root_model_resolved"]
    )
    assert all(not r["runtime_executed"] for r in records)
    assert root["interactive_runtime_reproduced"] is False
    assert "synthetic_secret" not in module.render_review(records[-1])


@pytest.mark.parametrize(
    "markup",
    [
        "<table><td>orphan</td></table>",
        '<table><tr><td colspan="9999999">value</td></tr></table>',
    ],
)
def test_malformed_table_is_not_claimed_as_complete_structural_evidence(markup):
    with pytest.raises(ValueError, match="source row|span bound"):
        module.extract_rich(notebook(markup))


@pytest.fixture
def retained(tmp_path):
    store = KnowledgeStore(tmp_path / "kb")
    raw = notebook(
        "<table><tr><th>label</th><th>amount</th></tr><tr><td>rich_table_fixture</td><td>42</td></tr></table><script>never_execute()</script>",
        views=("missing",),
    )
    sha = hashlib.sha256(raw).hexdigest()
    obj = store.root / "resources" / "objects" / sha
    obj.parent.mkdir(parents=True)
    obj.write_bytes(raw)
    source = tmp_path / "source_removed"
    sources, resources = [], []
    for name in ("a.ipynb", "b.ipynb"):
        path = str(source / name)
        sources.append({"path": path, "source_key": "", "raw_digest": sha})
        resources.append(
            {
                "relative_path": name,
                "sha256": sha,
                "bytes": len(raw),
                "object": str(obj.relative_to(store.root)),
            }
        )
        store.save_document_versions(
            [
                {
                    "path": path,
                    "source_key": "",
                    "raw_digest": sha,
                    "extractor": "fixture",
                    "chunks": [],
                    "metadata": {},
                }
            ]
        )
    store.write_json(
        "resources-manifest.json", {"source": str(source), "completed": resources}
    )
    store.write_json("audit.json", {"completed": sources})
    return store, raw


def test_retained_aliases_dedupe_assets_preserve_citations_and_are_resumable(retained):
    store, raw = retained
    result = module.index_rich_notebooks(store, store.root / "audit.json")
    assert len(result["completed"]) == 2 and not result["errors"]
    assert (
        result["counts"]["html_dom"] == 2
        and result["counts"]["widget_views_missing_saved_state"] == 2
    )
    assert len(list((store.root / "document-reviews").glob("*.html"))) == 2
    hits = store.search("rich_table_fixture")
    assert len(hits) == 2
    ids = {h["id"] for h in hits}
    again = module.index_rich_notebooks(store, store.root / "audit.json")
    assert (
        not again["errors"]
        and {h["id"] for h in store.search("rich_table_fixture")} == ids
    )
    with TestClient(create_app(store, "unused")) as client:
        for hit in hits:
            doc = -hit["id"] - 1000000000
            evidence = client.get("/evidence/" + str(hit["id"]))
            assert (
                evidence.status_code == 200 and f"/notebook-rich/{doc}" in evidence.text
            )
            source = client.get(f"/document-source/{doc}")
            assert source.status_code == 200 and source.content == raw
            page = client.get(f"/notebook-rich/{doc}")
            assert page.status_code == 200 and "rich_table_fixture" in page.text
            assert "never_execute" not in page.text
            assert "default-src 'none'" in page.headers["content-security-policy"]
        assert client.get("/notebook-rich/99999").status_code == 404


def test_changed_immutable_page_fails_index_and_download(retained):
    store, _ = retained
    module.index_rich_notebooks(store, store.root / "audit.json")
    hit = store.search("rich_table_fixture")[0]
    doc = -hit["id"] - 1000000000
    with store.connect() as db:
        metadata = json.loads(
            db.execute(
                "SELECT metadata FROM document_version_metadata LIMIT 1"
            ).fetchone()[0]
        )
        all_meta = [
            json.loads(r[0])
            for r in db.execute("SELECT metadata FROM document_version_metadata")
        ]
    metadata = next(
        m for m in all_meta if m.get("review_page") and m["rich_kind"] == "html_dom"
    )
    (store.root / metadata["review_page"]).write_text("tampered", "utf-8")
    result = module.index_rich_notebooks(store, store.root / "audit.json")
    assert len(result["errors"]) == 2
    with TestClient(create_app(store, "unused")) as client:
        assert client.get(f"/notebook-rich/{doc}").status_code == 404


def test_rich_static_review_and_source_survive_independent_restore(tmp_path, retained):
    from wechat_decrypt_tool.course_backup import create_snapshot, restore_snapshot

    store, raw = retained
    module.index_rich_notebooks(store, store.root / "audit.json")
    snapshot = create_snapshot(store, tmp_path / "backup", reserve_gib=0)
    restored = KnowledgeStore(
        restore_snapshot(snapshot["snapshot"], tmp_path / "restored", reserve_gib=0)
    )
    for item in (store.root / "document-reviews").iterdir():
        item.unlink()
    for item in (store.root / "resources" / "objects").iterdir():
        item.unlink()
    hits = restored.search("rich_table_fixture")
    with TestClient(create_app(restored, "unused")) as client:
        for hit in hits:
            doc = -hit["id"] - 1000000000
            assert client.get(f"/document-source/{doc}").content == raw
            assert "rich_table_fixture" in client.get(f"/notebook-rich/{doc}").text
    assert len(list((restored.root / "document-reviews").glob("*.json"))) == 2


def test_distinct_inert_sources_with_same_static_page_do_not_share_wrong_provenance():
    a, b = [
        module.extract_rich(notebook(f"<p>same</p><script>{value}</script>"))[0]
        for value in ("first()", "second()")
    ]
    assert a["safe_markup"] == b["safe_markup"]
    assert a["representation_sha256"] != b["representation_sha256"]
    assert module.digest(module.canonical(a).encode()) != module.digest(
        module.canonical(b).encode()
    )
