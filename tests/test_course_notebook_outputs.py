import hashlib
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from wechat_decrypt_tool import course_notebook_outputs as module
from wechat_decrypt_tool.course_knowledge import KnowledgeStore, create_app


def notebook(outputs):
    return json.dumps(
        {
            "nbformat": 4,
            "cells": [
                {"cell_type": "code", "source": ["source_marker"], "outputs": outputs}
            ],
        }
    ).encode()


def test_recorded_output_representations_are_complete_inert_and_located():
    raw = notebook(
        [
            {
                "output_type": "stream",
                "name": "stdout",
                "text": ["line_one\n", "line_two\n"],
            },
            {
                "output_type": "error",
                "ename": "FixtureError",
                "evalue": "diagnostic_marker",
                "traceback": ["trace_one", "trace_two"],
            },
            {
                "output_type": "execute_result",
                "data": {
                    "text/plain": ["plain_marker"],
                    "text/html": "<script>inert_script_marker()</script>",
                    "application/json": {
                        "label": "json_marker",
                        "api_key": "secret_json_marker",
                    },
                    "application/javascript": "inert_js_marker()",
                    "image/png": "retained_base64_fixture",
                },
            },
            {
                "output_type": "stream",
                "name": "stderr",
                "text": "PASSWORD=secret_assignment_marker\nconfig_marker=local\n",
            },
            {"output_type": "new_unsupported_output"},
        ]
    )
    chunks, metadata = module.output_chunks(raw)
    indexed = dict(chunks)
    assert indexed["cell 1 output 1 stream stdout chars 0"] == "line_one\nline_two\n"
    assert indexed["cell 1 output 2 traceback 2 chars 0"] == "trace_two"
    assert (
        indexed["cell 1 output 3 mime text/html chars 0"]
        == "<script>inert_script_marker()</script>"
    )
    assert json.loads(indexed["cell 1 output 3 mime application/json chars 0"]) == {
        "label": "json_marker",
        "api_key": "[REDACTED]",
    }
    body = "".join(indexed.values())
    assert "secret_json_marker" not in body and "secret_assignment_marker" not in body
    assert (
        metadata["recorded_outputs"] == 5
        and metadata["textual_mime_representations"] == 4
    )
    assert metadata["credential_assignments_or_json_fields_redacted"] == 2
    assert len(metadata["pending_visual_or_unsupported_representations"]) == 4
    assert not metadata["code_executed"]


@pytest.mark.parametrize(
    "output",
    [
        {"output_type": "stream", "text": ["valid", 123]},
        {"output_type": "execute_result", "data": []},
        {"output_type": "error", "traceback": "invalid"},
    ],
)
def test_malformed_outputs_fail_instead_of_silently_omitting_recorded_text(output):
    with pytest.raises(ValueError, match="Malformed"):
        module.output_chunks(notebook([output]))


def test_long_output_chunks_cover_every_character_and_preserve_pause():
    raw = notebook(
        [{"output_type": "stream", "name": "stdout", "text": "fixture_line\n" * 1000}]
    )
    chunks, metadata = module.output_chunks(raw)
    assert "".join(value for _, value in chunks) == "fixture_line\n" * 1000
    assert metadata["characters_indexed"] == 13000
    assert max(len(value) for _, value in chunks) == 3000
    with pytest.raises(InterruptedError):
        module.output_chunks(
            raw, lambda: (_ for _ in ()).throw(InterruptedError("paused"))
        )


def test_training_parameters_and_ordinary_token_key_values_are_not_credentials():
    raw = notebook(
        [
            {
                "output_type": "execute_result",
                "data": {
                    "application/json": {
                        "max_tokens": 4096,
                        "token": "a_word",
                        "key": "training_label",
                        "monkey": "fixture",
                        "access_token": "secret_fixture",
                    }
                },
            }
        ]
    )
    chunks, _ = module.output_chunks(raw)
    value = json.loads(chunks[0][1])
    assert value == {
        "max_tokens": 4096,
        "token": "a_word",
        "key": "training_label",
        "monkey": "fixture",
        "access_token": "[REDACTED]",
    }
    source = json.loads(raw)
    source["cells"][0]["source"] = (
        'max_tokens=4096\ntokenizer="fixture"\nOPENAI_API_KEY="secret_fixture"\n'
    )
    cells, _ = module.source_chunks(json.dumps(source).encode())
    assert 'max_tokens=4096\ntokenizer="fixture"' in cells[0][1]
    assert "secret_fixture" not in cells[0][1]


def test_unindexed_retained_checkpoint_gets_source_and_output_evidence(tmp_path):
    store = KnowledgeStore(tmp_path / "kb")
    raw = notebook([{"output_type": "stream", "text": "recovered_output_marker"}])
    sha = hashlib.sha256(raw).hexdigest()
    obj = store.root / "resources" / "objects" / sha
    obj.parent.mkdir(parents=True)
    obj.write_bytes(raw)
    source = tmp_path / "gone"
    relative = ".ipynb_checkpoints/lesson.ipynb"
    path = str(source / relative)
    store.write_json(
        "resources-manifest.json",
        {
            "source": str(source),
            "completed": [
                {
                    "relative_path": relative,
                    "sha256": sha,
                    "bytes": len(raw),
                    "object": str(obj.relative_to(store.root)),
                }
            ],
        },
    )
    store.write_json(
        "audit.json",
        {"completed": [{"path": path, "source_key": "", "raw_digest": sha}]},
    )
    report = module.index_notebook_outputs(store, store.root / "audit.json")
    assert not report["errors"] and len(store.search("recovered_output_marker")) == 1
    assert len(store.search("source_marker")) == 1
    with store.connect() as db:
        parent = db.execute(
            "SELECT metadata FROM resource_revisions WHERE path=? AND source_key=''",
            (path,),
        ).fetchone()
    assert json.loads(parent[0])["historical_checkpoint"]


def test_index_outputs_preserves_source_cells_aliases_and_raw_downloads(
    tmp_path, monkeypatch
):
    store = KnowledgeStore(tmp_path / "kb")
    source = tmp_path / "originals_gone"
    raw = notebook(
        [{"output_type": "stream", "name": "stdout", "text": "output_search_marker\n"}]
    )
    sha = hashlib.sha256(raw).hexdigest()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("lesson.ipynb", raw)
    container = buffer.getvalue()
    container_sha = hashlib.sha256(container).hexdigest()
    obj = store.root / "resources" / "objects" / container_sha
    obj.parent.mkdir(parents=True)
    obj.write_bytes(container)
    targets, resources = [], []
    for name in ("one.zip", "two.zip"):
        path = str(source / name)
        key = "archive lesson.ipynb"
        store.save_document_versions(
            [
                {
                    "path": path,
                    "source_key": key,
                    "raw_digest": sha,
                    "extractor": "synthetic_base",
                    "chunks": [(key + " cell 1 chars 0", "source_marker")],
                    "metadata": {},
                }
            ]
        )
        targets.append({"path": path, "source_key": key, "raw_digest": sha})
        resources.append(
            {
                "relative_path": name,
                "sha256": container_sha,
                "bytes": len(container),
                "object": str(obj.relative_to(store.root)),
            }
        )
    store.write_json(
        "resources-manifest.json", {"source": str(source), "completed": resources}
    )
    store.write_json("audit.json", {"completed": targets})
    calls = []
    original = module.output_chunks

    def parser(*args):
        calls.append(1)
        return original(*args)

    monkeypatch.setattr(module, "output_chunks", parser)
    first = module.index_notebook_outputs(store, store.root / "audit.json")
    second = module.index_notebook_outputs(store, store.root / "audit.json")
    assert not first["errors"] and len(first["completed"]) == 2
    assert len(calls) == 1 and all(row["cached"] for row in second["completed"])
    assert len(store.search("source_marker")) == 2
    hits = store.search("output_search_marker")
    assert len(hits) == 2
    with TestClient(create_app(store, "unused")) as client:
        for hit in hits:
            document_id = -hit["id"] - 1000000000
            assert client.get(f"/document-source/{document_id}").content == raw
            assert (
                "output_search_marker" in client.get("/evidence/" + str(hit["id"])).text
            )
    store.save_document_versions(
        [
            {
                "path": targets[0]["path"],
                "source_key": targets[0]["source_key"],
                "raw_digest": "b" * 64,
                "extractor": "synthetic_changed",
                "chunks": [],
                "metadata": {},
            }
        ]
    )
    assert len(store.search("output_search_marker")) == 1
    changed = module.index_notebook_outputs(store, store.root / "audit.json")
    assert len(changed["errors"]) == 1
    assert len(store.search("output_search_marker")) == 1
