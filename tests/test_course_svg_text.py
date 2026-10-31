import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from wechat_decrypt_tool import course_svg_text as module
from wechat_decrypt_tool.course_knowledge import KnowledgeStore, create_app


def svg(body):
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 200">'
        + body
        + "</svg>"
    ).encode()


def test_literal_text_runs_whitespace_and_unresolved_source_coordinates():
    raw = svg(
        '<g transform="translate(10,20)" style="font-family:fixture"><title>diagram title</title><text x="2 4" y="7">A <tspan dx="3">中文</tspan> tail</text></g>'
    )
    records, meta = module.xml_records(raw)
    assert meta["text_containers"] == 2
    record = records[1]
    assert record["text"] == "A 中文 tail"
    assert record["xml_path"] == [0, 1]
    assert record["runs"] == [
        {
            "xml_path": [0, 1],
            "tag": "text",
            "attributes": {"x": "2 4", "y": "7"},
            "text": "A ",
            "tail": "",
        },
        {
            "xml_path": [0, 1, 0],
            "tag": "tspan",
            "attributes": {"dx": "3"},
            "text": "中文",
            "tail": " tail",
        },
    ]
    assert record["ancestors"][1]["attributes"]["transform"] == "translate(10,20)"
    assert (
        record["source_visibility_not_inferred"]
        and meta["positioning_and_style_not_resolved"]
    )


def test_credential_split_across_tspans_is_not_leaked_in_literal_runs():
    raw = svg(
        "<text>api_key=<tspan>fixture-secret-value</tspan></text><text>tokenizer=training_fixture</text>"
    )
    records, meta = module.xml_records(raw)
    indexed = json.dumps(records)
    assert "fixture-secret-value" not in indexed
    assert "[REDACTED" in indexed and "training_fixture" in indexed
    assert meta["credential_bearing_containers_masked"] == 1


def test_paths_and_embedded_text_are_not_falsely_claimed_as_literal_unicode():
    records, meta = module.xml_records(svg('<path d="M0,0 L1,1"/>'))
    assert records == [module.NO_TEXT] and meta["text_containers"] == 0


def test_long_record_chunk_boundaries_preserve_complete_literal_source():
    text = "literal_fixture_" * 900
    raw = svg('<text x="1">' + text + "</text>")
    chunks, meta = module.svg_text_chunks(raw)
    assert len(chunks) > 2 and all(len(value) <= 3000 for _, value in chunks)
    result = json.loads("".join(value for _, value in chunks))
    assert result["text"] == text and result["runs"][0]["text"] == text
    assert meta["recorded_source_text_characters"] == len(text)


@pytest.mark.parametrize(
    "body", ["<script/>", '<image href="https://example.test/x.png"/>', "<animate/>"]
)
def test_source_text_parser_never_executes_active_or_external_svg(body):
    with pytest.raises(ValueError):
        module.svg_text_chunks(svg(body))


def test_source_aliases_resume_and_restore_without_renderer_or_original_drive(tmp_path):
    from wechat_decrypt_tool.course_backup import create_snapshot, restore_snapshot

    raw = svg('<text x="1">literal_svg_fixture</text>')
    digest = hashlib.sha256(raw).hexdigest()
    store = KnowledgeStore(tmp_path / "kb")
    source = tmp_path / "removed-originals"
    obj = store.root / "resources/objects" / digest
    obj.parent.mkdir(parents=True)
    obj.write_bytes(raw)
    rows = [
        {
            "path": str(source / name),
            "source_key": "",
            "sha256": digest,
            "state": "vector_raw_retained_render_audit_pending",
        }
        for name in ("a.svg", "b.svg")
    ]
    store.write_json("audit.json", {"image_occurrences": rows})
    store.write_json(
        "resources-manifest.json",
        {
            "source": str(source),
            "completed": [
                {
                    "relative_path": name,
                    "sha256": digest,
                    "bytes": len(raw),
                    "object": str(obj.relative_to(store.root)),
                }
                for name in ("a.svg", "b.svg")
            ],
        },
    )
    result = module.index_svg_texts(store, store.root / "audit.json")
    assert len(result["completed"]) == 2 and not result["errors"]
    hits = store.search("literal_svg_fixture")
    assert len(hits) == 2
    ids = {x["id"] for x in hits}
    assert all(
        x.get("cached")
        for x in module.index_svg_texts(store, store.root / "audit.json")["completed"]
    )
    assert {x["id"] for x in store.search("literal_svg_fixture")} == ids
    snapshot = create_snapshot(store, tmp_path / "backup", reserve_gib=0)
    restored = KnowledgeStore(
        restore_snapshot(snapshot["snapshot"], tmp_path / "restored", reserve_gib=0)
    )
    obj.unlink()
    with TestClient(create_app(restored, "unused")) as client:
        for hit in hits:
            doc = -hit["id"] - 1000000000
            response = client.get("/document-source/" + str(doc))
            assert response.status_code == 200 and response.content == raw
