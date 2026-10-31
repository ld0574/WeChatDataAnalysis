from wechat_decrypt_tool.course_knowledge import KnowledgeStore

import pytest


def test_revision_lookup_does_not_scan_all_other_members_of_large_course_zip(tmp_path):
    store = KnowledgeStore(tmp_path)
    with store.write() as db:
        db.executemany(
            "INSERT INTO documents(path,locator,digest,text,search_text) VALUES(?,?,?,?,?)",
            [
                ("course.zip", f"member-{n}.txt", f"revision-{n}", "fixture", "")
                for n in range(3000)
            ],
        )
    with store.connect() as db:
        # Bound VM work rather than wall time: a scan through all ZIP siblings
        # exceeds this allowance even on a fast developer machine.
        callbacks = []

        def budget():
            callbacks.append(1)
            return int(len(callbacks) > 20)

        db.set_progress_handler(budget, 100)
        rows = db.execute(
            "SELECT * FROM documents WHERE path=? AND digest=?",
            ("course.zip", "revision-2999"),
        ).fetchall()
        assert len(rows) == 1 and rows[0]["locator"] == "member-2999.txt"


def test_child_revision_lookup_does_not_parse_every_sibling_metadata_object(tmp_path):
    import json

    store = KnowledgeStore(tmp_path)
    with store.write() as db:
        db.executemany(
            "INSERT INTO resource_revisions VALUES(?,?,?,?,?,?,?)",
            [
                (
                    "course.zip",
                    f"member-{n}",
                    "raw",
                    "fixture",
                    "rev",
                    json.dumps(
                        {"parent_source_key": f"parent-{n}", "parent_raw_digest": "old"}
                    ),
                    0,
                )
                for n in range(3000)
            ],
        )
    with store.connect() as db:
        callbacks = []

        def budget():
            callbacks.append(1)
            return int(len(callbacks) > 20)

        db.set_progress_handler(budget, 100)
        rows = db.execute(
            "SELECT source_key FROM resource_revisions WHERE path=? AND json_extract(metadata,'$.parent_source_key')=? AND json_extract(metadata,'$.parent_raw_digest')!=?",
            ("course.zip", "parent-2999", "new"),
        ).fetchall()
        assert len(rows) == 1 and rows[0][0] == "member-2999"


def test_pause_during_document_batch_rolls_back_only_uncommitted_batch(
    tmp_path, monkeypatch
):
    store = KnowledgeStore(tmp_path)
    calls = []

    def pause():
        calls.append(1)
        if len(calls) == 4:
            raise InterruptedError("fixture local pause")

    monkeypatch.setattr(store, "checkpoint", pause)
    versions = [
        {
            "path": "fixture.zip",
            "source_key": f"archive {n}.txt",
            "raw_digest": str(n) * 64,
            "extractor": "fixture-v1",
            "chunks": [(f"fixture-{n}", "retained evidence")],
            "metadata": {},
        }
        for n in (1, 2)
    ]
    with pytest.raises(InterruptedError):
        store.save_document_versions(versions)
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM documents").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM resource_revisions").fetchone()[0] == 0
