import io
import sqlite3
import threading
import zipfile
from pathlib import Path
from types import SimpleNamespace

from wechat_decrypt_tool import voice_transcription as voice
from wechat_decrypt_tool import wcdb_realtime
from wechat_decrypt_tool import account_source_policy
from wechat_decrypt_tool.live_archive_media import voice_bytes


def seed(path,rows):
    with sqlite3.connect(path) as c:
        c.execute('CREATE TABLE VoiceInfo(svr_id INTEGER,create_time INTEGER,voice_data BLOB)')
        c.executemany('INSERT INTO VoiceInfo VALUES(?,?,?)',rows)


def test_business_voice_is_read_from_the_local_snapshot(tmp_path):
    seed(tmp_path/'media_0.db',[(1,10,b'ordinary')])
    seed(tmp_path/'biz_media_0.db',[(2,20,b'business')])
    assert voice.load_voice_data(tmp_path,2)==b'business'


def test_newest_audio_wins_across_both_shard_families(tmp_path):
    seed(tmp_path/'media_0.db',[(1,10,b'old')])
    seed(tmp_path/'biz_media_2.db',[(1,20,b'new')])
    assert voice.load_voice_data(tmp_path,1)==b'new'


def test_voice_enumeration_includes_business_and_deduplicates_ids(tmp_path,monkeypatch):
    seed(tmp_path/'media_1.db',[(1,1,b'one')])
    seed(tmp_path/'biz_media_0.db',[(1,1,b'one'),(2,2,b'two')])
    seed(tmp_path/'biz_media_backup.db',[(999,1,b'not a shard')])
    monkeypatch.setattr(account_source_policy,'account_prefers_decrypted_snapshot',lambda _:True)
    assert voice.list_voice_server_ids(tmp_path)==[1,2]


def test_live_voice_reading_checks_business_media(tmp_path,monkeypatch):
    media=tmp_path/'message';media.mkdir()
    (media/'biz_media_0.db').touch()
    realtime=SimpleNamespace(handle=1,lock=threading.Lock(),db_storage_dir=tmp_path)
    calls=[]
    monkeypatch.setattr(wcdb_realtime.WCDB_REALTIME,'ensure_connected',lambda _:realtime)
    def query(handle,*,kind,path,sql):
        calls.append(Path(path).name)
        return [{'voice_data':b'live-business'.hex()}]
    monkeypatch.setattr(wcdb_realtime,'exec_query',query)
    assert voice.load_voice_data(tmp_path/'account',2)==b'live-business'
    assert calls==['biz_media_0.db']


def test_archive_live_lookup_checks_business_media_and_closes_reader(tmp_path,monkeypatch):
    from wechat_decrypt_tool import live_archive
    media=tmp_path/'message';media.mkdir()
    (media/'biz_media_0.db').touch()
    monkeypatch.setattr(voice,'load_voice_data',lambda *_:b'')
    closed=[]
    class Reader:
        source=tmp_path
        def __init__(self,config):pass
        def query(self,db,sql):
            assert db=='biz_media_0.db'
            return [{'data':b'archived-business'.hex()}]
        def close(self):closed.append(True)
    monkeypatch.setattr(live_archive,'NativeArchiveReader',Reader)
    assert voice_bytes({'baseline':str(tmp_path/'base')},2)==b'archived-business'
    assert closed==[True]


def test_voice_export_uses_business_audio_without_requiring_live_wechat(tmp_path,monkeypatch):
    from wechat_decrypt_tool import chat_export_service as export
    seed(tmp_path/'biz_media_0.db',[(2,20,b'business')])
    monkeypatch.setattr(export,'_convert_silk_to_browser_audio',lambda data,**kw:(b'MP3:'+data,'mp3','audio/mpeg'))
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w') as zf:
        arc,created=export._materialize_voice(zf=zf,account_dir=tmp_path,media_db_path=tmp_path/'media_0.db',server_id=2,media_written={})
        assert created
        assert zf.read(arc)==b'MP3:business'
