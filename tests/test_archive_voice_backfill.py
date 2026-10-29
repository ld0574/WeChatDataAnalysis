import hashlib
import json
import sqlite3

import pytest

from wechat_decrypt_tool.live_archive import ArchiveStore
from wechat_decrypt_tool.live_archive_media import seed_history_media


@pytest.mark.parametrize('backlog',['asr','missing'])
def test_asr_and_missing_source_backlogs_do_not_block_other_original_audio(tmp_path,backlog):
    base=tmp_path/'baseline';base.mkdir()
    table='Msg_'+hashlib.md5(b'friend').hexdigest()
    with sqlite3.connect(base/'message_0.db') as c:
        c.execute('CREATE TABLE Name2Id(user_name TEXT,is_session INTEGER)')
        c.execute("INSERT INTO Name2Id VALUES('friend',1)")
        c.execute(f'CREATE TABLE "{table}"(local_id INTEGER PRIMARY KEY,server_id INTEGER,local_type INTEGER,sort_seq INTEGER,real_sender_id INTEGER,create_time INTEGER,message_content BLOB,compress_content BLOB,packed_info_data BLOB)')
        c.execute(f'INSERT INTO "{table}" VALUES(1,123,34,1,1,1,?,?,?)',(b'[voice]',b'',b''))
    store=ArchiveStore(tmp_path/'store')
    with store.connect() as c:
        keys=[json.dumps(['old.db','Msg_old',i,str(i)]) for i in range(1,5001)]
        c.executemany("INSERT INTO jobs(origin,kind,payload,status) VALUES(?,'voice','{}','pending')",[(k,) for k in keys])
        c.executemany("INSERT INTO voice_stages(origin,stage,status) VALUES(?,'capture',?)",
                      [(k,'ready' if backlog=='asr' else 'retry') for k in keys])
    class StopOnWait:
        stopped=False
        def is_set(self):return self.stopped
        def wait(self,_seconds):self.stopped=True
    seed_history_media({'baseline':str(base)},store,StopOnWait(),'voice')
    with store.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==5001
        assert c.execute("SELECT COUNT(*) FROM voice_stages WHERE stage='capture' AND status='pending'").fetchone()[0]==1
    assert store.status()['voiceHistorySeed']=='ready'
