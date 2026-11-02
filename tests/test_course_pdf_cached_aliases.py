import json

import pytest

from wechat_decrypt_tool import course_pdf_cached_aliases as aliases
from wechat_decrypt_tool.course_knowledge import KnowledgeStore


@pytest.fixture
def cache_case(tmp_path, monkeypatch):
    store = KnowledgeStore(tmp_path / "archive")
    root = store.root
    (root / "resources-manifest.json").write_text("{}")
    parent = root / "retained.pdf"
    parent.write_bytes(b"a retained PDF identity for a cache-only test")
    raw = aliases.sha(parent.read_bytes())
    image = root / "image.png"
    image.write_bytes(b"retained rendered page bytes")
    value = {
        "text": "cached transcription " * 300,
        "model": "local-test",
        "model_digest": "f" * 64,
        "image_digest": aliases.sha(image.read_bytes()),
    }
    cache = root / "page.json"
    cache.write_text(json.dumps(value))
    metadata = {
        "parent_source_key": "",
        "parent_raw_digest": raw,
        "page": 1,
        "image": image.name,
        "image_digest": value["image_digest"],
        "model": value["model"],
        "model_digest": value["model_digest"],
        "review_required": True,
    }
    key = "visual  page 1"
    text = aliases.PREFIX + value["text"]
    store.save_document_versions(
        [
            {
                "path": "donor.pdf",
                "source_key": key,
                "raw_digest": raw,
                "extractor": "pdf-vision-v2/renderer/local-test@"
                + value["model_digest"],
                "metadata": metadata,
                "chunks": [
                    (key + " chars " + str(i), text[i : i + 3000])
                    for i in range(0, len(text), 3000)
                ],
            }
        ]
    )
    with store.connect() as db:
        row = dict(
            db.execute(
                "SELECT * FROM resource_revisions WHERE path='donor.pdf'"
            ).fetchone()
        )
    donor = {
        "donor_path_sha256": aliases.sha(b"donor.pdf"),
        "donor_source_key_sha256": aliases.sha(key.encode()),
        "donor_revision": row["revision"],
        "extractor": row["extractor"],
        "metadata_sha256": aliases.sha(row["metadata"].encode()),
        "image": image.name,
        "image_sha256": value["image_digest"],
        "cache": cache.name,
        "cache_sha256": aliases.sha(cache.read_bytes()),
        "cache_text_sha256": aliases.sha(value["text"].encode()),
        "committed_text_sha256": aliases.sha(text.encode()),
    }
    proof = {
        "state": "existing_pdf_ocr_cached_alias_donors_byte_and_committed_text_verified",
        "rows": [
            {
                "path": "alias.pdf",
                "parent_source_key": "",
                "parent_raw_digest": raw,
                "page": 1,
                "donor": donor,
            }
        ],
    }
    monkeypatch.setattr(
        aliases, "resolve_retained_version", lambda *_: {"path": parent}
    )
    pause = root / "pdf-full-driver-progress.json"
    pause.write_text('{"stage":"paused","attempts":3}')
    return store, proof, parent, image, cache, pause


def count(store):
    with store.connect() as db:
        return db.execute("SELECT COUNT(*) FROM documents").fetchone()[0]


def test_prepare_is_read_only_and_apply_is_idempotent(cache_case):
    store, proof, _, _, _, pause = cache_case
    before, paused = count(store), pause.read_bytes()
    prepared = aliases.prepare(store, proof)
    assert count(store) == before
    assert prepared["donors_verified"] == 1
    assert len(prepared["versions"]) == 1
    result = aliases.apply(store, prepared)
    assert result["page_occurrences"] == 1 and result["model_calls"] == 0
    with store.connect() as db:
        original = list(
            db.execute(
                "SELECT locator,text FROM documents WHERE path='donor.pdf' ORDER BY locator"
            )
        )
        added = list(
            db.execute(
                "SELECT locator,text FROM documents WHERE path='alias.pdf' ORDER BY locator"
            )
        )
        assert [tuple(x) for x in original] == [tuple(x) for x in added]
        assert (
            db.execute("SELECT COUNT(*) FROM documents WHERE active=0").fetchone()[0]
            == 0
        )
    assert count(store) == before * 2
    again = aliases.prepare(store, proof)
    assert again["skipped_existing"] == 1 and not again["versions"]
    aliases.apply(store, again)
    assert count(store) == before * 2 and pause.read_bytes() == paused


@pytest.mark.parametrize("index", [2, 3, 4])
def test_changed_parent_image_or_cache_rejected_before_any_write(cache_case, index):
    store, proof, *_ = cache_case
    before = count(store)
    cache_case[index].write_bytes(b"changed")
    with pytest.raises(ValueError, match="pdf_alias_retained_bytes_changed"):
        aliases.prepare(store, proof)
    assert count(store) == before


def test_inactive_donor_rejected(cache_case):
    store, proof, *_ = cache_case
    with store.write() as db:
        db.execute("UPDATE documents SET active=0")
    with pytest.raises(ValueError, match="pdf_alias_committed_text_changed"):
        aliases.prepare(store, proof)
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM resource_revisions").fetchone()[0] == 1


def test_prepared_file_mutation_and_pause_prevent_commit(cache_case):
    store, proof, _, image, *_ = cache_case
    prepared = aliases.prepare(store, proof)
    before = count(store)
    image.write_bytes(b"mutation after preparation")
    with pytest.raises(ValueError, match="pdf_alias_prepared_file_changed"):
        aliases.apply(store, prepared)
    (store.root / "pause.flag").write_text("user pause")
    with pytest.raises(InterruptedError):
        aliases.apply(store, prepared)
    assert count(store) == before


def test_concurrent_target_creation_is_never_overwritten(cache_case):
    store, proof, *_ = cache_case
    prepared = aliases.prepare(store, proof)
    newer = {
        **prepared["versions"][0],
        "require_absent": False,
        "extractor": "newer-independent",
        "chunks": [("newer locator", "newer text")],
    }
    store.save_document_versions([newer])
    before = count(store)
    with pytest.raises(RuntimeError, match="no longer absent"):
        aliases.apply(store, prepared)
    with store.connect() as db:
        row = db.execute(
            "SELECT extractor FROM resource_revisions WHERE path='alias.pdf'"
        ).fetchone()
        assert row[0] == "newer-independent"
        assert (
            db.execute(
                "SELECT text FROM documents WHERE path='alias.pdf' AND active=1"
            ).fetchone()[0]
            == "newer text"
        )
    assert count(store) == before


def test_atomic_batch_rolls_back_when_later_target_is_no_longer_absent(cache_case):
    store, proof, *_ = cache_case
    prepared = aliases.prepare(store, proof)
    existing = {
        **prepared["versions"][0],
        "path": "second.pdf",
        "require_absent": False,
    }
    store.save_document_versions([existing])
    prepared["versions"].append({**prepared["versions"][0], "path": "second.pdf"})
    before = count(store)
    with pytest.raises(RuntimeError, match="no longer absent"):
        aliases.apply(store, prepared)
    with store.connect() as db:
        assert not db.execute(
            "SELECT 1 FROM documents WHERE path='alias.pdf'"
        ).fetchone()
    assert count(store) == before


def test_duplicate_target_is_not_double_counted(cache_case):
    store, proof, *_ = cache_case
    proof["rows"].append(proof["rows"][0])
    before = count(store)
    with pytest.raises(ValueError, match="pdf_alias_duplicate_target"):
        aliases.prepare(store, proof)
    assert count(store) == before
