import json
import sqlite3

from wechat_decrypt_tool.live_archive import ArchiveStore,LiveArchiveService
from wechat_decrypt_tool.voice_transcription import _publish_verified_voice_model


class StopsAfterPasses:
    def __init__(self,passes): self.left=passes
    def is_set(self): return self.left<=0
    def wait(self,_seconds): self.left-=1


class Reader:
    def __init__(self,rows): self.rows=rows; self.requested=[]
    def inventory(self): return [('message_0.db','Msg_test','friend')]
    def signature(self,db): return [10,20,30,40]
    def max_ids(self,db,tables): return {t:max(r['local_id'] for r in self.rows) for t in tables}
    def fetch(self,db,table,after,limit):
        self.requested.append(after)
        return [r for r in self.rows if r['local_id']>after][:limit]


def raw(n):
    return dict(local_id=n,server_id=100000+n,local_type=1,sort_seq=n,real_sender_id=2,
        create_time=n,message_content=f'message {n}'.encode(),compress_content=b'',packed_info_data=b'',sender_username='friend')


def seed(path):
    path.mkdir()
    with sqlite3.connect(path/'message_0.db') as c:
        c.execute('CREATE TABLE Name2Id(user_name TEXT,is_session INTEGER)')
        c.executemany('INSERT INTO Name2Id VALUES(?,1)',[('baseline',),('friend',)])
        c.execute('CREATE TABLE Msg_test(local_id INTEGER PRIMARY KEY,server_id INTEGER,local_type INTEGER,sort_seq INTEGER,real_sender_id INTEGER,create_time INTEGER,message_content TEXT,compress_content BLOB,packed_info_data BLOB)')
        r=raw(1)
        c.execute('INSERT INTO Msg_test VALUES(?,?,?,?,?,?,?,?,?)',tuple(r[k] for k in ('local_id','server_id','local_type','sort_seq','real_sender_id','create_time','message_content','compress_content','packed_info_data')))


def test_more_than_one_poll_page_does_not_lose_backlog(tmp_path):
    base=tmp_path/'baseline'; seed(base)
    store=ArchiveStore(tmp_path/'store')
    store.cursor('message_0.db','Msg_test',1)
    # Isolate tail paging from historical reconciliation.
    store.commit_page('message_0.db','Msg_test',[],audit_stamp=json.dumps([10,20,30,40]))
    reader=Reader([raw(n) for n in range(1,1502)])
    service=LiveArchiveService(); service.stop_event=StopsAfterPasses(9)
    service.collect({'baseline':str(base)},store,reader)
    assert store.cursor('message_0.db','Msg_test',0)['high']==1501
    assert store.status()['overlayMessages']==1500
    assert 201 in reader.requested and 1401 in reader.requested


def test_reconciliation_captures_old_rows_below_high_watermark(tmp_path):
    base=tmp_path/'baseline'; seed(base)
    store=ArchiveStore(tmp_path/'store')
    store.cursor('message_0.db','Msg_test',1000)
    reader=Reader([raw(1),raw(50),raw(1100)])
    service=LiveArchiveService(); service.stop_event=StopsAfterPasses(1)
    service.collect({'baseline':str(base)},store,reader)
    assert store.status()['overlayMessages']==2
    assert store.cursor('message_0.db','Msg_test',0)['audit_stamp']==json.dumps(reader.signature('message_0.db'))


def test_audit_round_robin_survives_restart_and_inventory_reordering(tmp_path,monkeypatch):
    import wechat_decrypt_tool.live_archive as module
    base=tmp_path/'baseline'; seed(base)
    store=ArchiveStore(tmp_path/'store')
    for tab in ('Msg_large','Msg_other'):
        store.cursor('message_0.db',tab,2000)
    clock=[0.0]
    monkeypatch.setattr(module.time,'monotonic',lambda:clock[0])

    class BusyReader(Reader):
        def __init__(self):super().__init__([raw(n) for n in range(1,1001)])
        def inventory(self):return [('message_0.db',t,'friend') for t in ('Msg_large','Msg_other')]
        def fetch(self,db,table,after,limit):
            self.requested.append((table,after))
            clock[0]+=11  # One slow page consumes this pass's audit budget.
            return [r for r in self.rows if r['local_id']>after][:limit]
    reader=BusyReader()
    for _ in range(3):
        service=LiveArchiveService();service.stop_event=StopsAfterPasses(1)
        service.collect({'baseline':str(base)},store,reader)
        store=ArchiveStore(store.root)  # Resume solely from persisted state.
        reader.inventory=lambda:[('message_0.db',t,'friend') for t in ('Msg_other','Msg_large')]
    assert reader.requested==[('Msg_large',0),('Msg_other',0),('Msg_large',500)]
    assert store.cursor('message_0.db','Msg_other',0)['audit_sequence']==2
    assert store.cursor('message_0.db','Msg_large',0)['audit']==1000
    assert store.status()['auditPendingTables']==2
    assert store.status()['auditServedTables']==2


def test_windows_model_publication_falls_back_to_verified_file_copies(tmp_path,monkeypatch):
    import wechat_decrypt_tool.voice_transcription as module
    stage=tmp_path/'stage'; stage.mkdir()
    (stage/'model.bin').write_bytes(b'model')
    dest=tmp_path/'model'
    original=module.os.replace
    def replace(a,b):
        if a==stage: raise PermissionError('directory held by scanner')
        return original(a,b)
    monkeypatch.setattr(module.os,'replace',replace)
    monkeypatch.setattr(module,'_model_directory_is_ready',lambda p,m:(p/'model.bin').read_bytes()==b'model')
    _publish_verified_voice_model(stage,dest,'test-model')
    assert (dest/'model.bin').read_bytes()==b'model'
