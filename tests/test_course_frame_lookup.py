"""Publishing one lesson must not scan every course's frame descriptions."""

import sqlite3

from wechat_decrypt_tool.course_knowledge import KnowledgeStore


QUERY = "SELECT * FROM frames WHERE video=? ORDER BY time,id"


def plan(connection, video):
    return [
        row[3] for row in connection.execute("EXPLAIN QUERY PLAN " + QUERY, (video,))
    ]


def test_existing_frame_lookup_migrates_without_changing_rows_or_search(tmp_path):
    store = KnowledgeStore(tmp_path)
    with store.connect() as db:
        db.execute("DROP INDEX frame_video_time")
        db.executemany(
            "INSERT INTO frames(video,time,path,description,search_text) VALUES(?,?,?,?,?)",
            [
                (video, (i % 7) * 30, f"{video}/{i}.jpg", "synthetic" * 200, "fixture")
                for video in ("course-a", "course-b", "course-c")
                for i in range(80)
            ],
        )
        before = [tuple(row) for row in db.execute(QUERY, ("course-b",))]
        assert any("SCAN frames" in row for row in plan(db, "course-b"))
        search_before = [
            tuple(row)
            for row in db.execute(
                "SELECT rowid FROM visual_fts WHERE visual_fts MATCH 'fixture' ORDER BY rowid"
            )
        ]
    migrated = KnowledgeStore(tmp_path)
    with migrated.connect() as db:
        assert [tuple(row) for row in db.execute(QUERY, ("course-b",))] == before
        assert [
            tuple(row)
            for row in db.execute(
                "SELECT rowid FROM visual_fts WHERE visual_fts MATCH 'fixture' ORDER BY rowid"
            )
        ] == search_before
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        for video in ("course-b", "not-registered"):
            steps = plan(db, video)
            assert any("SEARCH frames" in step and "video=?" in step for step in steps)
            assert not any(
                "SCAN frames" in step or "TEMP B-TREE" in step for step in steps
            )


def test_empty_archive_index_is_idempotent_and_connection_replans(tmp_path):
    store = KnowledgeStore(tmp_path)
    with sqlite3.connect(store.path) as db:
        db.execute("DROP INDEX frame_video_time")
        assert db.execute(QUERY, ("missing",)).fetchall() == []
        KnowledgeStore(tmp_path)
        assert db.execute(QUERY, ("missing",)).fetchall() == []
        # The live publisher can keep its connection: SQLite notices the schema
        # change when it prepares subsequent parameterized lesson lookups.
        assert any("SEARCH frames" in row for row in plan(db, "missing"))
    KnowledgeStore(tmp_path)
