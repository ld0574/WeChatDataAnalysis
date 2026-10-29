import threading
import time

from wechat_decrypt_tool.live_archive import ArchiveStore
from wechat_decrypt_tool import live_archive_media as media


def enqueue(store, key, status='pending', due=0, attempts=0, priority=0):
    with store.connect() as c:
        c.execute('INSERT INTO jobs(origin,kind,payload,status,due,attempts,priority) VALUES(?,?,?,?,?,?,?)',
                  (key, 'voice', '{}', status, due, attempts, priority))


def test_retry_and_fresh_backlogs_both_make_progress(tmp_path, monkeypatch):
    store = ArchiveStore(tmp_path)
    for i in range(10):
        enqueue(store, f'pending-{i}', due=-i)
    for i in range(10):
        enqueue(store, f'retry-{i}', 'retry', due=time.time() - 60, attempts=4)
    stop = threading.Event()
    processed = []

    def process(config, store, job, stop):
        processed.append(job['status'])
        with store.connect() as c:
            c.execute("UPDATE jobs SET status='ready' WHERE origin=?", (job['origin'],))
        if len(processed) == 6:
            stop.set()

    monkeypatch.setattr(media, 'process_job', process)
    media.media_loop({}, store, stop, 'voice')
    assert processed == ['retry', 'pending', 'pending', 'pending', 'pending', 'retry']


def test_retry_backoff_and_visible_media_priority_are_respected(tmp_path):
    store = ArchiveStore(tmp_path)
    enqueue(store, 'future', 'retry', due=time.time() + 3600, priority=2)
    enqueue(store, 'history', due=-1000)
    enqueue(store, 'visible', priority=1)
    assert media._next_media_job(store, 'voice', retry_turn=True)['origin'] == 'visible'
    assert media._next_media_job(store, 'voice')['origin'] == 'visible'
    with store.connect() as c:
        c.execute("UPDATE jobs SET status='ready' WHERE origin IN ('history','visible')")
    assert media._next_media_job(store, 'voice', retry_turn=True) is None


def test_due_retries_run_when_no_fresh_work_exists(tmp_path):
    store = ArchiveStore(tmp_path)
    enqueue(store, 'retry', 'retry', due=time.time() - 60, attempts=10)
    assert media._next_media_job(store, 'voice')['origin'] == 'retry'
