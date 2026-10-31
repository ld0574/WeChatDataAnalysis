"""Exact transactional document totals without rescanning long source-path indexes."""

from __future__ import annotations


def install_document_counts(db):
    """Install delta tracking under the caller's short schema/writer lease.

    A savepoint binds initialization and trigger installation to one transaction.
    Existing producer processes pick up SQLite triggers without a Python reload.
    Counts roll back with their documents, including hidden CSV staging rows.
    """
    db.execute("SAVEPOINT course_document_count_migration")
    try:
        db.execute("""CREATE TABLE IF NOT EXISTS course_document_counts(
            id INTEGER PRIMARY KEY CHECK(id=1),
            active INTEGER NOT NULL,
            historical INTEGER NOT NULL,
            initialized INTEGER NOT NULL CHECK(initialized IN (0,1)),
            CHECK(initialized=0 OR (active>=0 AND historical>=0)))""")
        if not db.execute("SELECT 1 FROM course_document_counts WHERE id=1").fetchone():
            db.execute("INSERT INTO course_document_counts VALUES(1,0,0,0)")
        statements = (
            """CREATE TRIGGER IF NOT EXISTS course_document_count_insert
                AFTER INSERT ON documents BEGIN
                UPDATE course_document_counts SET active=active+(new.active IS 1),
                    historical=historical+(new.active IS 0) WHERE id=1;
                END""",
            """CREATE TRIGGER IF NOT EXISTS course_document_count_delete
                AFTER DELETE ON documents BEGIN
                UPDATE course_document_counts SET active=active-(old.active IS 1),
                    historical=historical-(old.active IS 0) WHERE id=1;
                END""",
            """CREATE TRIGGER IF NOT EXISTS course_document_count_update
                AFTER UPDATE OF active ON documents WHEN old.active IS NOT new.active BEGIN
                UPDATE course_document_counts SET
                    active=active+(new.active IS 1)-(old.active IS 1),
                    historical=historical+(new.active IS 0)-(old.active IS 0) WHERE id=1;
                END""",
        )
        for statement in statements:
            db.execute(statement)
    except BaseException:
        db.execute("ROLLBACK TO course_document_count_migration")
        db.execute("RELEASE course_document_count_migration")
        raise
    db.execute("RELEASE course_document_count_migration")


def initialize_document_counts(store):
    """Seed from a WAL snapshot without holding the producer's writer lease.

    Until publication, the counter row contains signed deltas since installation.
    Capture that row and full document totals in the same read snapshot, then add
    (snapshot totals - snapshot deltas) to the current deltas atomically. Writes
    during the scan are therefore included exactly once, including deletions and
    active/history transitions. A separate seeding lease avoids redundant scans.
    """
    from .course_knowledge import schema_lease

    with store.connect() as db:
        if db.execute(
            "SELECT initialized FROM course_document_counts WHERE id=1"
        ).fetchone()[0]:
            return
    with schema_lease(store.root / "document-count-seed.lock", timeout=3600):
        with store.connect() as reader:
            reader.execute("BEGIN")
            try:
                delta = reader.execute(
                    "SELECT active,historical,initialized FROM course_document_counts WHERE id=1"
                ).fetchone()
                if delta[2]:
                    return
                actual = reader.execute(
                    "SELECT coalesce(sum(active IS 1),0),coalesce(sum(active IS 0),0) FROM documents"
                ).fetchone()
            finally:
                reader.rollback()
        with schema_lease(store.root / "writer.lock"):
            with store.connect() as writer:
                writer.execute("BEGIN IMMEDIATE")
                writer.execute(
                    """UPDATE course_document_counts SET active=active+?,
                    historical=historical+?,initialized=1 WHERE id=1 AND initialized=0""",
                    (actual[0] - delta[0], actual[1] - delta[1]),
                )


def verify_document_counts(db):
    """Independently compare counters with every document; legacy archives may lack them."""
    if not db.execute(
        "SELECT 1 FROM sqlite_master WHERE name='course_document_counts' AND type='table'"
    ).fetchone():
        return False
    saved = db.execute(
        "SELECT active,historical FROM course_document_counts WHERE id=1 AND initialized=1"
    ).fetchone()
    if saved is None:
        raise RuntimeError(
            "Snapshot document counters have not finished initialization"
        )
    actual = db.execute(
        "SELECT coalesce(sum(active IS 1),0),coalesce(sum(active IS 0),0) FROM documents"
    ).fetchone()
    if tuple(saved) != tuple(actual):
        raise RuntimeError(
            "Snapshot document counters differ from complete document rows"
        )
    return True
