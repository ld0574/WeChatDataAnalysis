import hashlib
import json
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from wechat_decrypt_tool.live_archive import ArchiveStore, origin, media_origin
from wechat_decrypt_tool import live_archive_voice as pipeline


def enqueue(store, n=1):
    hit={'username':'friend','serverIdStr':str(100+n),'renderType':'voice','content':'[语音]',
         'db':'message_0','table':'Msg_test','localId':n,'createTime':n}
    key=origin('message_0.db','Msg_test',n)
    with store.connect() as c:
        store.enqueue(c,key,hit)
    return media_origin(key,hit)


def stage(store,key,name):
    with store.connect() as c:
        return dict(c.execute('SELECT j.payload,s.* FROM voice_stages s JOIN jobs j ON j.origin=s.origin '
                             "WHERE s.origin=? AND s.stage=? AND j.kind='voice'",(key,name)).fetchone())


def test_capture_keeps_saving_audio_while_transcription_is_unprocessed(tmp_path,monkeypatch):
    from wechat_decrypt_tool import live_archive_media as media
    store=ArchiveStore(tmp_path);keys=[enqueue(store,n) for n in (1,2)]
    monkeypatch.setattr(media,'voice_bytes',lambda *_:b'original-silk')
    for key in keys:
        pipeline.process_stage({},store,stage(store,key,'capture'),threading.Event())
    assert all(pipeline.raw_path(store,stage(store,key,'transcribe')).read_bytes()==b'original-silk' for key in keys)
    assert all(stage(store,key,'transcribe')['status']=='pending' for key in keys)
    assert all(stage(store,key,'playback')['status']=='pending' for key in keys)


def test_newly_collected_old_message_preempts_the_historical_backlog(tmp_path):
    store=ArchiveStore(tmp_path)
    enqueue(store,10000)
    hit={'username':'friend','serverIdStr':'101','renderType':'voice','content':'[语音]',
         'db':'message_0','table':'Msg_test','localId':1,'createTime':1}
    source=origin('message_0.db','Msg_test',1)
    store.commit_page('message_0.db','Msg_test',[(source,hit,'new source bytes',False)])
    assert pipeline.next_stage_jobs(store,'capture')[0]['origin']==media_origin(source,hit)


def test_restart_recovers_original_written_before_capture_commit(tmp_path,monkeypatch):
    from wechat_decrypt_tool import live_archive_media as media
    store=ArchiveStore(tmp_path);key=enqueue(store)
    saved=tmp_path/'media'/(hashlib.sha256(key.encode()).hexdigest()+'.silk')
    saved.parent.mkdir();saved.write_bytes(b'already durable')
    read=Mock(side_effect=AssertionError('must not fetch source again'))
    monkeypatch.setattr(media,'voice_bytes',read)
    pipeline.process_stage({},ArchiveStore(tmp_path),stage(store,key,'capture'),threading.Event())
    assert stage(store,key,'capture')['status']=='ready'
    assert saved.read_bytes()==b'already durable'


def test_migration_preserves_existing_audio_transcripts_and_retry_deadlines(tmp_path):
    store=ArchiveStore(tmp_path);key='["message_0.db","Msg_test",1,"101"]'
    raw=hashlib.sha256(key.encode()).hexdigest()+'.silk'
    (tmp_path/'media').mkdir();(tmp_path/'media'/raw).write_bytes(b'silk')
    (tmp_path/'media'/'old.mp3').write_bytes(b'mp3')
    with store.connect() as c:
        c.execute("INSERT INTO jobs(origin,kind,payload,status,attempts,due,error,path) VALUES(?,'voice','{}','retry',3,12345,'ASR failed','old.mp3')",(key,))
    pipeline.initialize_voice_stages(store)
    pipeline.initialize_voice_stages(store)
    with store.connect() as c:
        stages={r['stage']:dict(r) for r in c.execute('SELECT * FROM voice_stages')}
    assert stages['capture']['status']=='ready'
    assert stages['playback']['status']=='ready'
    assert stages['transcribe']['status']=='retry'
    assert stages['transcribe']['due']==12345
    assert stages['transcribe']['attempts']==3


def test_transcription_can_succeed_while_playback_conversion_retries(tmp_path,monkeypatch):
    from wechat_decrypt_tool import live_archive_media as media,voice_transcription as voice,runtime_settings
    store=ArchiveStore(tmp_path);key=enqueue(store);stop=threading.Event()
    monkeypatch.setattr(media,'voice_bytes',lambda *_:b'silk')
    pipeline.process_stage({},store,stage(store,key,'capture'),stop)
    monkeypatch.setattr(voice,'_convert_silk_to_browser_audio',lambda *_a,**_kw:(b'','silk','audio/silk'))
    playback=stage(store,key,'playback')
    with pytest.raises(RuntimeError) as exc:
        pipeline.process_stage({},store,playback,stop)
    pipeline.retry_stage(store,playback,exc.value)
    service=SimpleNamespace(config=SimpleNamespace(model='gpu'),transcribe_voice=Mock(return_value={'text':'独立的转写结果'}))
    monkeypatch.setattr(voice,'get_voice_transcription_service',lambda:service)
    monkeypatch.setattr(runtime_settings,'read_effective_voice_transcription_model',lambda:('gpu','test'))
    pipeline.process_stage({'voice_model':'gpu'},store,stage(store,key,'transcribe'),stop)
    assert stage(store,key,'capture')['status']=='ready'
    assert stage(store,key,'playback')['status']=='retry'
    assert stage(store,key,'transcribe')['status']=='ready'
    with store.connect() as c:
        job=c.execute('SELECT status,transcript FROM jobs WHERE origin=?',(key,)).fetchone()
        assert job['status']=='retry'
        assert job['transcript']=='独立的转写结果'


def test_batch_commits_success_without_losing_another_tasks_saved_audio(tmp_path,monkeypatch):
    from wechat_decrypt_tool import live_archive_media as media
    store=ArchiveStore(tmp_path);keys=[enqueue(store,n) for n in (1,2)]
    monkeypatch.setattr(media,'voice_bytes',lambda *_:b'silk')
    for key in keys:
        pipeline.process_stage({},store,stage(store,key,'capture'),threading.Event())
        pipeline.finish_stage(store,stage(store,key,'playback'),'saved.mp3')
    service=SimpleNamespace(transcribe_voice_batch=Mock(return_value=[
        dict(text='成功文字',device='cuda',computeType='float32',cached=False),
        dict(error='one bad clip',code='transcription_failed')]))
    monkeypatch.setattr(pipeline,'transcription_service',lambda _:service)
    pipeline.process_transcription_jobs({},store,[stage(store,key,'transcribe') for key in keys],threading.Event(),2)
    assert stage(store,keys[0],'transcribe')['status']=='ready'
    assert stage(store,keys[1],'transcribe')['status']=='retry'
    assert stage(store,keys[1],'capture')['status']=='ready'
    assert pipeline.raw_path(store,stage(store,keys[1],'transcribe')).read_bytes()==b'silk'
    assert store.status()['voiceBatchDevice']=='cuda'
    assert store.status()['voiceBatchPrecision']=='float32'


def test_disk_reserve_stops_media_writes_without_discarding_message_jobs(tmp_path,monkeypatch):
    from wechat_decrypt_tool import live_archive_media as media
    store=ArchiveStore(tmp_path);key=enqueue(store)
    monkeypatch.setattr(pipeline.shutil,'disk_usage',lambda _:SimpleNamespace(free=1024))
    source=Mock();monkeypatch.setattr(media,'voice_bytes',source)
    job=stage(store,key,'capture')
    with pytest.raises(RuntimeError,match='空间不足') as exc:
        pipeline.process_stage({},store,job,threading.Event())
    pipeline.retry_stage(store,job,exc.value)
    source.assert_not_called()
    assert stage(store,key,'capture')['status']=='retry'
    assert stage(store,key,'transcribe')['status']=='blocked'
