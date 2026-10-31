import sqlite3

import pytest

from wechat_decrypt_tool.course_archive_integrity import (
    FTS_BINDINGS, audit_document_locations, audit_fts_indexes,
)


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


def fts_fixture():
    db = sqlite3.connect(':memory:')
    for index, source in FTS_BINDINGS:
        db.execute(f'CREATE TABLE {source}(id INTEGER PRIMARY KEY,search_text TEXT)')
        db.execute(f'CREATE VIRTUAL TABLE {index} USING fts5(search_text,content={source},content_rowid=id)')
        db.execute(f'INSERT INTO {source} VALUES(1,?)', ('alpha beta 中文',))
        db.execute(f'INSERT INTO {index}(rowid,search_text) VALUES(1,?)', ('alpha beta 中文',))
    db.commit()
    return db


def test_native_fts_check_covers_all_indexes_without_changing_snapshot():
    db = fts_fixture()
    db.execute('BEGIN')
    db.execute("INSERT INTO segments VALUES(2,'retained pending row')")
    db.execute("INSERT INTO segment_fts(rowid,search_text) VALUES(2,'retained pending row')")
    db.commit()
    before = db.serialize()
    events = []
    assert audit_fts_indexes(db, progress=lambda **x: events.append(x)) == {
        'segment_fts': 2, 'visual_fts': 1, 'document_fts': 1,
    }
    assert not db.in_transaction
    assert db.serialize() == before
    assert len(events) == 3
    db.close()


def test_native_fts_check_refuses_to_flush_a_callers_pending_transaction():
    db = fts_fixture()
    db.execute("INSERT INTO segments VALUES(2,'pending row')")
    with pytest.raises(ValueError, match='committed snapshot'):
        audit_fts_indexes(db)
    assert db.in_transaction
    db.rollback()
    assert db.execute('SELECT count(*) FROM segments').fetchone()[0] == 1
    db.close()


@pytest.mark.parametrize('index,source', FTS_BINDINGS)
def test_native_fts_check_rejects_wrong_non_probe_postings(index, source):
    db = fts_fixture()
    # The old alpha-only probe succeeds, yet beta's missing posting must fail.
    db.execute(f"INSERT INTO {index}({index},rowid,search_text) VALUES('delete',1,'alpha beta 中文')")
    db.execute(f"INSERT INTO {index}(rowid,search_text) VALUES(1,'alpha wrong 中文')")
    db.commit()
    assert db.execute(f'SELECT rowid FROM {index} WHERE {index} MATCH ?', ('alpha',)).fetchone()[0] == 1
    before = db.serialize()
    with pytest.raises(sqlite3.DatabaseError):
        audit_fts_indexes(db)
    assert db.serialize() == before
    assert not db.in_transaction
    db.close()


@pytest.mark.parametrize('kind', ['missing_row', 'extra_row'])
def test_native_fts_check_rejects_missing_and_extra_index_rows(kind):
    db = fts_fixture()
    if kind == 'missing_row':
        db.execute("INSERT INTO documents VALUES(2,'unindexed source')")
    else:
        db.execute("INSERT INTO document_fts(rowid,search_text) VALUES(2,'orphan index')")
    db.commit()
    with pytest.raises(sqlite3.DatabaseError):
        audit_fts_indexes(db)
    db.close()


def test_native_fts_check_honors_cancellation_and_releases_savepoint():
    db = fts_fixture()

    def cancel():
        raise InterruptedError('cancelled')

    with pytest.raises(InterruptedError):
        audit_fts_indexes(db, checkpoint=cancel)
    assert not db.in_transaction
    db.close()


@pytest.mark.parametrize('journal_mode', ['DELETE', 'WAL'])
def test_native_fts_audit_leaves_file_database_bytes_unchanged(tmp_path, journal_mode):
    path = tmp_path / 'isolated.sqlite3'
    memory = fts_fixture()
    disk = sqlite3.connect(path)
    memory.backup(disk)
    disk.execute('PRAGMA journal_mode=' + journal_mode)
    disk.close()
    memory.close()
    before = path.read_bytes()
    disk = sqlite3.connect(path)
    try:
        assert len(audit_fts_indexes(disk)) == 3
    finally:
        disk.close()
    assert path.read_bytes() == before
