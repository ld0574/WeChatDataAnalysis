"""Real note publications restored from a base plus immutable small deltas."""
from pathlib import Path
import shutil
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from test_course_library_queue import archive as source_archive
from test_course_library_queue import published as source_published
from test_course_library_queue import waiting_publication as source_waiting
from wechat_decrypt_tool import course_library_delta as delta
from wechat_decrypt_tool.course_knowledge import atomic_json
from wechat_decrypt_tool.course_library import Library, create_app
from wechat_decrypt_tool.course_library_queue import publish_once
from wechat_decrypt_tool.course_video_visual_audit import ReadOnlyArchive
from wechat_decrypt_tool.course_wiki_snapshot import PublishedStore

archive = source_archive
published = source_published
waiting_publication = source_waiting


@pytest.fixture
def changed(waiting_publication, tmp_path):
    store, old, new = waiting_publication
    base = tmp_path / 'base'
    shutil.copytree(store.root / 'library', base / 'library')
    shutil.copyfile(store.root / 'inventory-unique.json', base / 'inventory-unique.json')
    rows = [{'path': p.relative_to(base).as_posix(), 'bytes': p.stat().st_size,
             'sha256': delta.digest(p.read_bytes())} for p in base.rglob('*') if p.is_file()]
    atomic_json(base / 'wiki-snapshot.json', {
        'policy': 'published-course-wiki-snapshot-v1', 'id': 'base',
        'scope': 'published_wiki_and_source_notes_only', 'created': 1, 'files': rows})
    parent = tmp_path / 'deltas'
    parent.mkdir()
    with TestClient(create_app(ReadOnlyArchive(store.root))) as client:
        publish_once(ReadOnlyArchive(store.root), client)
    return store, old, new, base, parent


def save(case, **kwargs):
    store, _, _, base, parent = case
    return delta.save(store.root, parent, base / 'wiki-snapshot.json',
                      base / 'library/catalog.json', delta.digest((base / 'wiki-snapshot.json').read_bytes()),
                      reserve_gib=0, **kwargs)


def test_real_http_checks_and_restore_without_original_or_sqlite(changed, monkeypatch):
    store, old, new, base, parent = changed
    before = {p: p.read_bytes() for p in base.rglob('*') if p.is_file()}
    with TestClient(create_app(ReadOnlyArchive(store.root))) as client:
        result = save(changed, client=client)
    assert result['new_sources'] == result['changed_sources'] == [new]
    assert result['http_checks'][0]['sections_search_verified'] == 2
    assert result['model_calls'] == 0 and not result['independent_execution_verified']
    backup = Path(result['backup'])
    manifest = delta.load(backup / 'source-note-delta.json')
    old_artifact = next(row['artifact'] for row in Library(base).catalog()['lessons'] if row['id'] == old)
    assert 'library/objects/' + old_artifact + '.json' not in manifest['files']
    restored = parent.parent / 'restored'
    shutil.copytree(base, restored)
    shutil.copytree(backup / 'files', restored, dirs_exist_ok=True)
    shutil.copyfile(backup / 'combined-wiki-snapshot.json', restored / 'wiki-snapshot.json')
    actual_open = Path.open
    def guarded(path, *args, **kwargs):
        if path.resolve().is_relative_to(store.root.resolve()):
            raise AssertionError('restored reader accessed original archive')
        return actual_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', guarded)
    import sqlite3
    monkeypatch.setattr(sqlite3, 'connect', lambda *_a, **_k: pytest.fail('restored reader opened SQLite'))
    with TestClient(create_app(PublishedStore(restored))) as client:
        checks = delta.verify_notes(restored, client, Library(restored).catalog(), [old, new])
    assert len(checks) == 2 and all(x['sections_search_verified'] == 2 for x in checks)
    assert before == {p: p.read_bytes() for p in before}


def test_unchanged_notes_do_not_create_redundant_backup(changed):
    result = save(changed)
    backup = Path(result['backup'])
    before = set(changed[-1].iterdir())
    again = delta.save(changed[0].root, changed[-1], backup / 'combined-wiki-snapshot.json',
                       backup / 'files/library/catalog.json', result['combined_manifest_sha256'], reserve_gib=0)
    assert again['state'] == 'no_new_source_note_objects' and again['files_written'] == 0
    assert before == set(changed[-1].iterdir())


@pytest.mark.parametrize('mutation', ['base', 'catalog', 'chain', 'lost'])
def test_invalid_predecessor_or_lost_source_never_creates_delta(changed, mutation):
    store, old, _, base, parent = changed
    kwargs = {}
    if mutation == 'base':
        expected = delta.digest((base / 'wiki-snapshot.json').read_bytes())
        (base / 'wiki-snapshot.json').write_text('{}')
        with pytest.raises(ValueError, match='base_manifest_changed'):
            delta.save(store.root, parent, base / 'wiki-snapshot.json', base / 'library/catalog.json', expected, reserve_gib=0)
        return
    if mutation == 'catalog':
        (base / 'library/catalog.json').write_text('{}')
    elif mutation == 'chain':
        kwargs['required_manifests'] = [{'path': str(base / 'wiki-snapshot.json'), 'sha256': '0' * 64}]
    else:
        catalog = Library(store.root).catalog()
        catalog['lessons'] = [row for row in catalog['lessons'] if row['id'] != old]
        atomic_json(store.root / 'library/catalog.json', catalog)
    with pytest.raises(ValueError):
        save(changed, **kwargs)
    assert not list(parent.iterdir())


@pytest.mark.parametrize('name', ['../outside', '/absolute', 'C:/outside', r'..\outside'])
def test_manifest_paths_are_portable_and_cannot_escape(changed, name):
    base = changed[-2]
    value = delta.load(base / 'wiki-snapshot.json')
    value['files'].append({'path': name, 'sha256': 'f' * 64, 'bytes': 1})
    atomic_json(base / 'wiki-snapshot.json', value)
    with pytest.raises(ValueError, match='invalid_base_path'):
        save(changed)
    assert not list(changed[-1].iterdir())


def test_mutated_dependency_retains_partial_copy_without_publishing_manifest(changed, monkeypatch):
    store, _, new, _, parent = changed
    artifact = next(row['artifact'] for row in Library(store.root).catalog()['lessons'] if row['id'] == new)
    target = store.root / 'library/objects' / (artifact + '.json')
    original = delta.checkpoint
    calls = []
    def checkpoint(*args):
        calls.append(True)
        if len(calls) == 2:
            target.write_text('{}')
        original(*args)
    monkeypatch.setattr(delta, 'checkpoint', checkpoint)
    with pytest.raises(ValueError, match='changed_during_copy'):
        save(changed)
    [partial] = list(parent.iterdir())
    assert not (partial / 'source-note-delta.json').exists()
    assert not (partial / 'combined-wiki-snapshot.json').exists()


def test_pause_and_disk_reserve_stop_before_writing(changed, monkeypatch):
    store = changed[0]
    flag = store.root / 'pause.flag'
    flag.write_text('user pause')
    with pytest.raises(InterruptedError, match='pause_requested'):
        save(changed)
    flag.unlink()
    monkeypatch.setattr(delta.shutil, 'disk_usage', lambda _: SimpleNamespace(free=-1))
    with pytest.raises(InterruptedError, match='disk_reserve_reached'):
        save(changed)
    assert not list(changed[-1].iterdir())


def test_revised_existing_source_preserves_old_object_and_predecessor_chain(changed):
    store, old, _, base, parent = changed
    first = save(changed)
    previous = Path(first['backup'])
    catalog = Library(store.root).catalog()
    row = next(x for x in catalog['lessons'] if x['id'] == old)
    prior_artifact = row['artifact']
    note = Library(store.root).object(prior_artifact)
    note['revision_probe'] = True
    raw = __import__('json').dumps(note).encode()
    row['artifact'] = delta.digest(raw)
    (store.root / 'library/objects' / (row['artifact'] + '.json')).write_bytes(raw)
    atomic_json(store.root / 'library/catalog.json', catalog)
    second = delta.save(store.root, parent, previous / 'combined-wiki-snapshot.json',
                        previous / 'files/library/catalog.json', first['combined_manifest_sha256'], reserve_gib=0)
    assert second['new_sources'] == [] and second['changed_sources'] == [old]
    manifest = delta.load(Path(second['backup']) / 'combined-wiki-snapshot.json')
    assert any(x['path'] == 'library/objects/' + prior_artifact + '.json' for x in manifest['files'])
    assert {x['path'] for x in manifest['required_manifests']} >= {
        str((base / 'wiki-snapshot.json').resolve()), str((previous / 'source-note-delta.json').resolve())}


def test_pointer_can_advance_without_changing_captured_backup(changed, monkeypatch):
    store = changed[0]
    frozen = (store.root / 'library/catalog.json').read_bytes()
    def advance(*args):
        catalog = Library(store.root).catalog()
        catalog['concurrent_publication_probe'] = True
        atomic_json(store.root / 'library/catalog.json', catalog)
        return []
    monkeypatch.setattr(delta, 'verify_notes', advance)
    result = save(changed, client=object())
    assert (Path(result['backup']) / 'files/library/catalog.json').read_bytes() == frozen
    assert (store.root / 'library/catalog.json').read_bytes() != frozen


def concept_free_note(case, *, empty=False):
    store, _, key, _, _ = case
    catalog = Library(store.root).catalog()
    row = next(x for x in catalog['lessons'] if x['id'] == key)
    note = Library(store.root).object(row['artifact'])
    note['sections'][0]['concepts'] = []
    note['sections'][0]['summary'] = ''
    if empty:
        note['sections'][0]['context'] = []
        for source in note['evidence'].values():
            if source.get('kind') == 'frame':
                source['text'] = ''
    raw = __import__('json').dumps(note).encode()
    row['artifact'] = delta.digest(raw)
    (store.root / 'library/objects' / (row['artifact'] + '.json')).write_bytes(raw)
    atomic_json(store.root / 'library/catalog.json', catalog)
    return note


def test_concept_free_section_is_actually_searchable_after_delta_restore(changed):
    store, _, key, base, parent = changed
    note = concept_free_note(changed)
    query = delta.section_search_query(note, note['sections'][0])
    assert any(query in note['evidence'][str(ref)]['text'] for ref in note['sections'][0]['context'])
    with TestClient(create_app(ReadOnlyArchive(store.root))) as client:
        result = save(changed, client=client)
    assert result['http_checks'][0]['concept_free_sections_search_verified'] == 1
    assert result['http_checks'][0]['sections_search_verified'] == len(note['sections'])
    backup = Path(result['backup'])
    restored = parent.parent / 'concept-free-restored'
    shutil.copytree(base, restored)
    shutil.copytree(backup / 'files', restored, dirs_exist_ok=True)
    shutil.copyfile(backup / 'combined-wiki-snapshot.json', restored / 'wiki-snapshot.json')
    with TestClient(create_app(PublishedStore(restored))) as client:
        assert client.get('/library/api/lesson/' + key).json() == note
        page = client.get('/library/api/search', params={'q': query, 'video': key}).json()
        assert any(row['section'] == note['sections'][0]['id'] for row in page['items'])
        checked = delta.verify_notes(restored, client, Library(restored).catalog(), [key])
    assert checked[0]['concept_free_sections_search_verified'] == 1


def test_concept_free_section_does_not_bypass_actual_http_search(changed):
    import httpx

    concept_free_note(changed)
    class MissingSearch:
        def __init__(self, actual):
            self.actual = actual
        def get(self, route, **kwargs):
            if route == '/library/api/search':
                return httpx.Response(200, json={'items': [], 'next_offset': None},
                                      request=httpx.Request('GET', 'http://testserver' + route))
            return self.actual.get(route, **kwargs)
    with TestClient(create_app(ReadOnlyArchive(changed[0].root))) as client:
        with pytest.raises(ValueError, match='section_not_searchable'):
            save(changed, client=MissingSearch(client))
    assert not list(changed[-1].iterdir())


def test_empty_section_is_rejected_without_inventing_a_search_concept(changed):
    concept_free_note(changed, empty=True)
    with TestClient(create_app(ReadOnlyArchive(changed[0].root))) as client:
        with pytest.raises(ValueError, match='section_without_searchable_text'):
            save(changed, client=client)
    assert not list(changed[-1].iterdir())
