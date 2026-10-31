import sqlite3

import pytest

from wechat_decrypt_tool.course_archive_integrity import audit_document_locations


def fixture_db():
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE documents(path TEXT,source_key TEXT,locator TEXT,active INTEGER)')
    db.execute('CREATE INDEX document_source ON documents(path,source_key,active)')
    db.executemany('INSERT INTO documents VALUES(?,?,?,?)', [
        ('a', 'x', 'p1', 1), ('a', 'x', 'p1', 1), ('a', 'x', 'p1', 1),
        ('a', 'y', 'p1', 1), ('b', 'x', 'p1', 1), ('a', 'x', 'p2', 1),
        ('a', 'x', 'p2', 0), ('a', 'x', 'p2', 0),
        (None, 'x', None, 1), (None, 'x', None, 1),
        ('', 'x', '', 1), ('a', 'x', 'bad', None), ('a', 'x', 'bad', 2),
        ('a', 'x', 'bad', 'invalid'), ('a', 'x', 'bad', 0.5),
        ('a', 'x', 'P1', 1),
    ])
    return db


@pytest.mark.parametrize('batch_size', [1, 3, 4096])
def test_complete_scan_matches_sqlite_grouping_and_flags(batch_size):
    db = fixture_db()
    events = []
    trace = []
    db.set_trace_callback(trace.append)
    before = db.total_changes
    result = audit_document_locations(db, batch_size=batch_size, progress=lambda **x: events.append(x))
    assert result == dict(documents=16, active_documents=10,
                          duplicate_active_document_locations=2,
                          invalid_document_active_flags=4)
    assert result['duplicate_active_document_locations'] == db.execute(
        'SELECT count(*) FROM (SELECT 1 FROM documents WHERE active=1 '
        'GROUP BY path,source_key,locator HAVING count(*)>1)').fetchone()[0]
    assert db.total_changes == before
    assert 'NOT INDEXED' in trace[0]
    assert events[-1]['documents'] == result['documents']
    assert all(isinstance(value, int) for item in events for value in item.values())
    db.close()


def test_memory_budget_failure_and_cancellation_never_return_success():
    db = fixture_db()
    with pytest.raises(MemoryError):
        audit_document_locations(db, max_memory_bytes=1)

    def cancel():
        raise InterruptedError('cancelled')

    with pytest.raises(InterruptedError):
        audit_document_locations(db, checkpoint=cancel)
    db.close()


def test_empty_archive():
    db = fixture_db()
    db.execute('DELETE FROM documents')
    assert not any(audit_document_locations(db).values())
    db.close()


def test_readonly_wal_snapshot_stays_consistent_while_writer_appends(tmp_path):
    path = tmp_path / 'archive.sqlite3'
    writer = sqlite3.connect(path)
    writer.execute('PRAGMA journal_mode=WAL')
    writer.execute('CREATE TABLE documents(path TEXT,source_key TEXT,locator TEXT,active INTEGER)')
    writer.executemany('INSERT INTO documents VALUES(?,?,?,?)', [
        ('a', 'x', 'p1', 1), ('a', 'x', 'p2', 1),
    ])
    writer.commit()
    reader = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    reader.execute('BEGIN')
    assert reader.execute('SELECT count(*) FROM documents').fetchone()[0] == 2

    def append(**counts):
        if counts['documents'] == 1:
            writer.execute("INSERT INTO documents VALUES('a','x','p1',1)")
            writer.commit()

    result = audit_document_locations(reader, batch_size=1, progress=append)
    assert result['documents'] == 2
    assert result['duplicate_active_document_locations'] == 0
    reader.rollback()
    assert audit_document_locations(reader)['duplicate_active_document_locations'] == 1
    reader.close()
    writer.close()
