"""Exercise real publication, interruption recovery and bounded worker watching."""
import copy
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
import httpx
from PIL import Image
import pytest

from test_course_wiki import archive as source_archive
from test_course_wiki import published as source_published
from wechat_decrypt_tool import course_library as library
from wechat_decrypt_tool import course_library_queue as queue
from wechat_decrypt_tool.course_knowledge import atomic_json, schema_lease
from wechat_decrypt_tool.course_video_visual_audit import ReadOnlyArchive

archive = source_archive
published = source_published


@pytest.fixture
def waiting_publication(published, tmp_path):
    store, old = published
    inventory = json.loads((store.root / 'inventory-unique.json').read_bytes())
    path = tmp_path / 'second.mp4'
    path.write_bytes(b'Synthetic second source; never decoded.')
    item = {**inventory['videos'][0], 'path': str(path), 'title': 'Synthetic second',
        'relative_path': 'Collection/second.mp4', 'bytes': path.stat().st_size,
        'mtime_ns': path.stat().st_mtime_ns}
    key = store.register(item)
    with store.connect() as db:
        segments = [dict(r) for r in db.execute('SELECT * FROM segments WHERE video=? ORDER BY n', (old,))]
        frames = [dict(r) for r in db.execute('SELECT * FROM frames WHERE video=? ORDER BY id', (old,))]
        notes = [dict(r) for r in db.execute('SELECT * FROM notes WHERE video=? ORDER BY window', (old,))]
        model = db.execute('SELECT model FROM videos WHERE id=?', (old,)).fetchone()[0]
    for row in segments:
        store.save_segment(key, row['n'], row['start'], row['end'], row['text'])
    with store.write() as db:
        mapping = {old_row['id']: new_row['id'] for old_row, new_row in zip(segments,
            db.execute('SELECT id FROM segments WHERE video=? ORDER BY n', (key,)), strict=True)}
        for frame in frames:
            relative = f'lessons/{key}/frames/{frame["time"]}.jpg'
            image = store.root / relative
            image.parent.mkdir(parents=True, exist_ok=True)
            Image.new('RGB', (16, 16), 'white').save(image, 'JPEG')
            cursor = db.execute('INSERT INTO frames(video,time,path,description,status,model) VALUES(?,?,?,?,?,?)',
                (key, frame['time'], relative, frame['description'], 'ready', frame['model']))
            mapping[-frame['id']] = -cursor.lastrowid
        for row in notes:
            data = copy.deepcopy(json.loads(row['data']))
            for claim in data['concepts'] + data['relations']:
                claim['evidence'] = [mapping[n] for n in claim['evidence']]
            db.execute('INSERT INTO notes VALUES(?,?,?,?,?)',
                (key, row['window'], json.dumps(data), row['model'], 'pending'))
        db.execute("UPDATE videos SET status='transcribed',model=? WHERE id=?", (model, key))
    atomic_json(store.root / 'lessons' / key / 'frames-completed.json', {
        'step': 30, 'complete_visual_coverage': False})
    store.export(key)
    inventory['videos'].append(item)
    inventory['unique_readable_videos'] = 2
    atomic_json(store.root / 'inventory-unique.json', inventory)
    assert library.publish(ReadOnlyArchive(store.root))['published'] == 1
    with store.write() as db:
        db.execute("UPDATE notes SET status='ready' WHERE video=?", (key,))
    store.export(key)
    return store, old, key


def client_for(store):
    return TestClient(library.create_app(ReadOnlyArchive(store.root)))


def test_ready_lesson_publishes_once_and_checks_http_search_and_all_new_images(waiting_publication, monkeypatch):
    store, old, key = waiting_publication
    actual, calls = queue.publish, []
    monkeypatch.setattr(queue, 'publish', lambda s: (calls.append(True), actual(s))[1])
    with client_for(store) as client:
        result = queue.publish_once(ReadOnlyArchive(store.root), client)
        assert result['published'] == result['requested'] == 2
        assert result['added_sources'] == [{'video': key, 'sections': 2, 'evidence': 9,
            'keyword_target_sections_verified': 2, 'image_routes_verified': 1, 'actual_http_equal': True}]
        assert queue.available(library.Library(store.root).catalog()) == {old, key}
        assert (store.root / result['receipt']).is_file()
        assert queue.publish_once(ReadOnlyArchive(store.root), client)['state'] == 'waiting_for_complete_source_exports'
    assert len(calls) == 1
    assert not (store.root / 'wiki/library-publication-queue/pending.json').exists()


def test_incomplete_source_does_not_replace_catalog_or_create_intent(waiting_publication):
    store, _, key = waiting_publication
    before = (store.root / 'library/catalog.json').read_bytes()
    with store.write() as db:
        db.execute("UPDATE notes SET status='pending' WHERE video=?", (key,))
    with client_for(store) as client:
        result = queue.publish_once(ReadOnlyArchive(store.root), client)
    assert result['state'] == 'waiting_for_complete_source_exports'
    assert result['pending_sources'][key] == 'notes_incomplete'
    assert (store.root / 'library/catalog.json').read_bytes() == before
    assert not (store.root / 'wiki/library-publication-queue/pending.json').exists()


def test_an_existing_publisher_is_not_duplicated(waiting_publication):
    store, _, _ = waiting_publication
    (store.root / 'wiki').mkdir(exist_ok=True)
    before = (store.root / 'library/catalog.json').read_bytes()
    with schema_lease(store.root / 'wiki/next-source-publication.lock', timeout=0):
        with client_for(store) as client:
            assert queue.publish_once(ReadOnlyArchive(store.root), client) == {
                'state': 'waiting_for_existing_publisher'}
    assert (store.root / 'library/catalog.json').read_bytes() == before


def test_http_failure_keeps_intent_and_recovers_without_second_publish(waiting_publication, monkeypatch):
    store, _, key = waiting_publication
    actual, calls = queue.publish, []
    monkeypatch.setattr(queue, 'publish', lambda s: (calls.append(True), actual(s))[1])
    class Disconnected:
        def get(self, *args, **kwargs):
            raise httpx.ConnectError('synthetic connection failure')
    with pytest.raises(httpx.ConnectError):
        queue.publish_once(ReadOnlyArchive(store.root), Disconnected())
    assert (store.root / 'wiki/library-publication-queue/pending.json').is_file()
    with client_for(store) as client:
        result = queue.publish_once(ReadOnlyArchive(store.root), client)
    assert result['verification_recovered_without_republishing']
    assert result['added_sources'][0]['video'] == key
    assert len(calls) == 1


def test_crash_before_saving_catalog_recovers_published_pointer_without_republishing(waiting_publication, monkeypatch):
    store, _, _ = waiting_publication
    actual_json, actual_publish, calls = queue.atomic_json, queue.publish, []
    monkeypatch.setattr(queue, 'publish', lambda s: (calls.append(True), actual_publish(s))[1])
    def crash(path, value):
        if path.name == 'pending.json' and 'catalog' in value:
            raise RuntimeError('synthetic crash after publication')
        actual_json(path, value)
    monkeypatch.setattr(queue, 'atomic_json', crash)
    with client_for(store) as client:
        with pytest.raises(RuntimeError, match='synthetic crash'):
            queue.publish_once(ReadOnlyArchive(store.root), client)
        monkeypatch.setattr(queue, 'atomic_json', actual_json)
        result = queue.publish_once(ReadOnlyArchive(store.root), client)
    assert result['verification_recovered_without_republishing']
    assert result['published'] == 2 and len(calls) == 1


def test_failure_stops_watch_and_preserves_pending_verification(waiting_publication):
    store, _, _ = waiting_publication
    class Disconnected:
        def get(self, *args, **kwargs):
            raise httpx.ConnectError('synthetic connection failure')
    assert queue.watch(ReadOnlyArchive(store.root), Disconnected(), 42, 1,
        alive=lambda: True, sleep=lambda _: pytest.fail('failure must stop polling')) == 1
    progress = json.loads((store.root / 'wiki/library-publication-queue-progress.json').read_bytes())
    assert progress['state'] == 'failed' and progress['error_type'] == 'ConnectError'
    assert (store.root / progress['failure_receipt']).is_file()
    assert (store.root / 'wiki/library-publication-queue/pending.json').is_file()


def test_pause_during_verification_recovers_without_republishing(waiting_publication, monkeypatch):
    store, _, _ = waiting_publication
    actual, calls = queue.publish, []
    monkeypatch.setattr(queue, 'publish', lambda s: (calls.append(True), actual(s))[1])
    with client_for(store) as client:
        class PauseAfterCatalog:
            def get(self, url, **kwargs):
                result = client.get(url, **kwargs)
                if url == '/library/api/catalog':
                    (store.root / 'pause.flag').write_text('user stop', encoding='utf-8')
                return result
        with pytest.raises(InterruptedError):
            queue.publish_once(ReadOnlyArchive(store.root), PauseAfterCatalog())
        (store.root / 'pause.flag').unlink()
        result = queue.publish_once(ReadOnlyArchive(store.root), client)
    assert result['verification_recovered_without_republishing'] and len(calls) == 1


def test_receipt_rename_interruption_retains_prior_receipt_and_can_recover(waiting_publication, monkeypatch):
    store, _, _ = waiting_publication
    pending = store.root / 'wiki/library-publication-queue/pending.json'
    actual_replace, actual_publish, calls = queue.Path.replace, queue.publish, []
    monkeypatch.setattr(queue, 'publish', lambda s: (calls.append(True), actual_publish(s))[1])
    def interrupted(path, target):
        if path == pending:
            raise OSError('synthetic rename interruption')
        return actual_replace(path, target)
    with client_for(store) as client:
        monkeypatch.setattr(queue.Path, 'replace', interrupted)
        with pytest.raises(OSError, match='synthetic rename interruption'):
            queue.publish_once(ReadOnlyArchive(store.root), client)
        original = next((store.root / 'wiki/library-publication-queue/receipts').glob('*/validation.json'))
        before = original.read_bytes()
        monkeypatch.setattr(queue.Path, 'replace', actual_replace)
        result = queue.publish_once(ReadOnlyArchive(store.root), client)
    assert result['verification_recovered_without_republishing'] and len(calls) == 1
    assert original.read_bytes() == before
    assert (store.root / result['receipt']).name.startswith('verification-recovered-')


def test_reused_pid_is_not_the_same_worker(monkeypatch):
    monkeypatch.setattr(queue.psutil, 'Process', lambda pid: SimpleNamespace(create_time=lambda: 2))
    assert not queue.identity_alive(17, 1)
    assert queue.identity_alive(17, 2)


@pytest.mark.parametrize('reason', ['pause', 'disk'])
def test_watch_respects_pause_and_reserve_without_clearing_supervisor_history(waiting_publication, monkeypatch, reason):
    store, _, _ = waiting_publication
    before = (store.root / 'library/catalog.json').read_bytes()
    stop = store.root / 'supervisor-stop.flag'
    stop.write_text('retained history', encoding='utf-8')
    if reason == 'pause':
        (store.root / 'pause.flag').write_text('user stop', encoding='utf-8')
    else:
        monkeypatch.setattr(queue.shutil, 'disk_usage', lambda _: SimpleNamespace(free=31 * 1024**3))
    assert queue.watch(ReadOnlyArchive(store.root), None, 42, 1, alive=lambda: True) == 0
    assert json.loads((store.root / 'wiki/library-publication-queue-progress.json').read_bytes())['state'] == 'paused'
    assert (store.root / 'library/catalog.json').read_bytes() == before
    assert stop.read_text(encoding='utf-8') == 'retained history'


def test_expired_worker_identity_cannot_publish(waiting_publication):
    store, _, _ = waiting_publication
    before = (store.root / 'library/catalog.json').read_bytes()
    assert queue.watch(ReadOnlyArchive(store.root), None, 42, 1, alive=lambda: False) == 1
    assert (store.root / 'library/catalog.json').read_bytes() == before


def test_worker_exit_finishes_one_publication_without_launching_a_worker(waiting_publication):
    store, _, _ = waiting_publication
    states = iter([True, False])
    with client_for(store) as client:
        assert queue.watch(ReadOnlyArchive(store.root), client, 42, 1,
            alive=lambda: next(states), sleep=lambda _: pytest.fail('unexpected poll')) == 0
    progress = json.loads((store.root / 'wiki/library-publication-queue-progress.json').read_bytes())
    assert progress['state'] == 'worker_exited' and progress['final_result']['published'] == 2


def test_second_queue_does_not_overwrite_live_progress(waiting_publication):
    store, _, _ = waiting_publication
    (store.root / 'wiki').mkdir(exist_ok=True)
    path = store.root / 'wiki/library-publication-queue-progress.json'
    atomic_json(path, {'state': 'existing owner'})
    before = path.read_bytes()
    with schema_lease(store.root / 'wiki/library-publication-queue.lock', timeout=0):
        with pytest.raises(RuntimeError, match='still holds library-publication-queue.lock'):
            queue.watch(ReadOnlyArchive(store.root), None, 42, 1, alive=lambda: True)
    assert path.read_bytes() == before


def test_inventory_change_is_not_silently_adopted(waiting_publication):
    store, _, _ = waiting_publication
    path = store.root / 'inventory-unique.json'
    path.write_bytes(path.read_bytes() + b'\n')
    with pytest.raises(ValueError, match='library_queue_inventory_changed'):
        queue.publish_once(ReadOnlyArchive(store.root), None)


@pytest.mark.parametrize('url', ['https://127.0.0.1:3003', 'http://example.com:3003',
    'http://127.0.0.1:3003/path', 'http://user:password@127.0.0.1:3003',
    'http://127.0.0.1:3003?query=x', 'http://localhost:3003'])
def test_reader_is_pinned_to_loopback_without_credentials_or_query(url):
    with pytest.raises(ValueError, match='library_queue_requires_loopback_reader'):
        queue.reader_url(url)
