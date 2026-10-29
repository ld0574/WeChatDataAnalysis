import json
import sqlite3
import threading
from pathlib import Path

import pytest
from PIL import Image
import io

from wechat_decrypt_tool.live_archive import ArchiveStore, origin, same_source_row,media_origin
from wechat_decrypt_tool.live_archive_media import compress_image
from wechat_decrypt_tool.live_archive_search import search
from wechat_decrypt_tool.chat_helpers import _to_char_token_text
from wechat_decrypt_tool.chat_search_index import _init_index_db


def hit(local=1,server=11,text='苹果归档',db='message_0'):
    return {'id':f'{db}:Msg_test:{local}','db':db,'table':'Msg_test','localId':local,
            'serverId':server,'serverIdStr':str(server),'username':'friend','renderType':'text',
            'content':text,'createTime':local,'sortSeq':local}


def put(store,h,raw='raw',same=False,high=None):
    key=origin(h['db']+'.db',h['table'],h['localId'])
    store.commit_page(h['db']+'.db',h['table'],[(key,h,raw,same)],high=high)
    return key


def test_replay_is_idempotent_and_server_ids_dedup_across_shards(tmp_path):
    s=ArchiveStore(tmp_path)
    s.cursor('message_0.db','Msg_test',0)
    put(s,hit(),high=1)
    put(s,hit(),high=1)
    put(s,hit(local=2,db='message_1'))
    with s.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM messages').fetchone()[0]==1
        assert c.execute('SELECT COUNT(*) FROM origins').fetchone()[0]==2
    assert s.cursor('message_0.db','Msg_test',0)['high']==1


def test_local_id_reuse_keeps_both_different_server_messages(tmp_path):
    s=ArchiveStore(tmp_path)
    put(s,hit(server=11),'old')
    put(s,hit(server=22,text='另一条消息'),'new')
    assert s.status()['overlayMessages']==2


def test_zero_server_id_gets_assigned_without_duplicate(tmp_path):
    s=ArchiveStore(tmp_path)
    put(s,hit(server=0),'pending')
    put(s,hit(server=11),'sent')
    assert s.status()['overlayMessages']==1


def test_cursor_and_fts_roll_back_with_failed_page(tmp_path):
    s=ArchiveStore(tmp_path)
    s.cursor('message_0.db','Msg_test',0)
    with pytest.raises(TypeError):
        s.commit_page('message_0.db','Msg_test',[
            (origin('message_0.db','Msg_test',1),hit(),'good',False),
            ('bad',{**hit(2), 'content':object()},'bad',False)],high=2)
    assert s.cursor('message_0.db','Msg_test',0)['high']==0
    assert s.status()['overlayMessages']==0
    with s.connect() as c:
        assert not c.execute('SELECT rowid FROM archive_fts WHERE archive_fts MATCH ?',('"苹 果"',)).fetchall()


def test_restart_retains_cursor_and_full_audit_does_not_skip_lower_ids(tmp_path):
    s=ArchiveStore(tmp_path)
    s.cursor('message_0.db','Msg_test',1000)
    put(s,hit(1200),high=1200)
    del s
    s=ArchiveStore(tmp_path)
    put(s,hit(50,server=55),high=50)
    assert s.cursor('message_0.db','Msg_test',0)['high']==1200
    assert s.status()['overlayMessages']==2


def test_baseline_text_and_blob_compare_without_creating_millions_of_copies():
    a={'message_content':'你好','compress_content':None,'packed_info_data':None,'local_type':1}
    b={'message_content':'你好'.encode(),'compress_content':b'','packed_info_data':b'','local_type':1}
    assert same_source_row(a,b)
    b['message_content']=b'changed'
    assert not same_source_row(a,b)


def baseline(tmp_path,items):
    b=tmp_path/'baseline'
    b.mkdir()
    with sqlite3.connect(b/'chat_search_index.db') as c:
        _init_index_db(c)
        for i,h in enumerate(items,1):
            c.execute('INSERT INTO message_fts(rowid,text,payload_json,create_time,sort_seq,local_id) VALUES(?,?,?,?,?,?)',
                (i,_to_char_token_text(h['content']),json.dumps(h,ensure_ascii=False),h['createTime'],h['sortSeq'],h['localId']))
    return b


def test_search_combines_baseline_delta_and_no_duplicate(tmp_path):
    b=baseline(tmp_path,[hit(1),hit(2,server=22)])
    s=ArchiveStore(tmp_path/'store')
    put(s,hit(1,text='苹果归档已更新'))
    put(s,hit(3,server=33))
    result=search({'storage':str(s.root),'baseline':str(b)},'苹果')
    assert len(result['hits'])==3
    assert {h['serverId'] for h in result['hits']}=={11,22,33}
    assert result['hits'][-1]['content']=='苹果归档已更新'


def test_old_index_match_disappears_when_overlay_changed_text(tmp_path):
    b=baseline(tmp_path,[hit(1)])
    s=ArchiveStore(tmp_path/'store')
    put(s,hit(1,text='香蕉已更正'))
    assert not search({'storage':str(s.root),'baseline':str(b)},'苹果')['hits']
    assert len(search({'storage':str(s.root),'baseline':str(b)},'香蕉')['hits'])==1


def test_baseline_voice_transcript_search_and_media_queue_idempotence(tmp_path):
    h={**hit(),'renderType':'voice','content':'[语音]'}
    b=baseline(tmp_path,[h])
    s=ArchiveStore(tmp_path/'store')
    key=put(s,h,same=True)
    put(s,h,same=True)
    with s.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==1
    assert s.status()['overlayMessages']==0
    s.save_transcript(media_origin(key,h),'会议时间改到周五')
    assert len(search({'storage':str(s.root),'baseline':str(b)},'周五')['hits'])==1


def test_image_compression_preserves_orientation_dimensions_and_alpha():
    data=io.BytesIO()
    Image.new('RGBA',(3000,1200),(255,0,0,100)).save(data,'PNG')
    compressed,ext=compress_image(data.getvalue())
    assert ext=='.webp'
    with Image.open(io.BytesIO(compressed)) as image:
        assert max(image.size)==1920
        assert 'A' in image.getbands()


def test_same_baseline_rows_do_not_duplicate_archive(tmp_path):
    s=ArchiveStore(tmp_path)
    put(s,hit(),same=True)
    assert s.status()['overlayMessages']==0


def test_current_wechat_windows_thumbnail_suffix_is_found(tmp_path,monkeypatch):
    import hashlib
    from datetime import datetime
    from wechat_decrypt_tool.live_archive_media import local_image_path
    import wechat_decrypt_tool.media_helpers as media
    monkeypatch.setattr(media,'_try_find_decrypted_resource',lambda *_:None)
    h=hit()
    wx=tmp_path/'wx'; source=wx/'db_storage'
    folder=wx/'msg'/'attach'/hashlib.md5(h['username'].encode()).hexdigest()/datetime.fromtimestamp(h['createTime']).strftime('%Y-%m')/'Img'
    folder.mkdir(parents=True)
    md5='a'*32
    image=folder/(md5+'_t_W.dat'); image.write_bytes(b'fixture')
    config={'baseline':str(tmp_path/'base'),'source_account':'test','source':str(source)}
    assert local_image_path(config,h,md5)==image


def test_history_audit_leaves_baseline_media_to_bounded_backfill_queue(tmp_path):
    store=ArchiveStore(tmp_path)
    h={**hit(),'renderType':'image'}
    store.cursor('message_0.db','Msg_test',1)
    key=origin('message_0.db','Msg_test',1)
    store.commit_page('message_0.db','Msg_test',[(key,h,'raw',True)],audit=1,include_baseline_media=False)
    with store.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==0
    assert store.cursor('message_0.db','Msg_test',0)['audit']==1


def test_media_response_has_explicit_image_type_even_if_windows_registry_is_wrong(tmp_path,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from wechat_decrypt_tool.routers import live_archive as router
    import mimetypes
    store=ArchiveStore(tmp_path)
    (tmp_path/'media').mkdir()
    (tmp_path/'media'/'sample.webp').write_bytes(b'RIFF-test-image')
    monkeypatch.setattr(router,'configured',lambda:({},store))
    monkeypatch.setattr(mimetypes,'guess_type',lambda *_a,**_k:('text/plain',None))
    app=FastAPI(); app.include_router(router.router)
    response=TestClient(app).get('/api/archive/media/sample.webp')
    assert response.status_code==200
    assert response.headers['content-type']=='image/webp'
    assert response.headers['x-content-type-options']=='nosniff'


def test_image_content_hash_does_not_override_the_packed_file_identifier(tmp_path,monkeypatch):
    from wechat_decrypt_tool.live_archive_media import image_source
    import wechat_decrypt_tool.live_archive_media as module
    store=ArchiveStore(tmp_path/'store')
    content_hash='a'*32
    file_hash='b'*32
    h={**hit(),'renderType':'image','archiveRawText':f'<img md5="{content_hash}" />'}
    put(store,h,raw=json.dumps({'packed_info_data':f'{file_hash}_t_W.dat'.encode().hex()}))
    requested=[]
    def locate(config,hit,digest):
        requested.append(digest)
        return tmp_path/(digest+'.dat')
    monkeypatch.setattr(module,'local_image_path',locate)
    key=media_origin(origin('message_0.db','Msg_test',1),h)
    result=image_source({'baseline':str(tmp_path/'baseline')},store,{'origin':key},h)
    assert result.name==file_hash+'.dat'
    assert requested==[file_hash]


def test_image_xml_identifier_remains_a_fallback_if_packed_file_is_missing(tmp_path,monkeypatch):
    from wechat_decrypt_tool.live_archive_media import image_source
    import wechat_decrypt_tool.live_archive_media as module
    store=ArchiveStore(tmp_path/'store')
    h={**hit(),'renderType':'image','archiveRawText':'<img md5="'+'a'*32+'" />'}
    put(store,h,raw=json.dumps({'packed_info_data':('b'*32+'_t_W.dat').encode().hex()}))
    def locate(config,hit,digest):
        if digest=='b'*32:raise FileNotFoundError()
        return tmp_path/(digest+'.dat')
    monkeypatch.setattr(module,'local_image_path',locate)
    result=image_source({'baseline':str(tmp_path/'base')},store,
        {'origin':media_origin(origin('message_0.db','Msg_test',1),h)},h)
    assert result.name=='a'*32+'.dat'


@pytest.mark.parametrize('suffix',['_t_W','_h_W','_W','_t','.h',''])
def test_packed_image_filename_wins_over_repeated_content_hashes(suffix):
    from wechat_decrypt_tool.chat_helpers import _extract_md5_from_packed_info
    blob=('a'*32+' ')*4+'b'*32+suffix+'.dat'
    assert _extract_md5_from_packed_info(blob.encode())=='b'*32


def test_search_promotes_visible_media_without_resetting_retry_backoff(tmp_path):
    import time
    h={**hit(),'renderType':'image','content':'[图片]'}
    b=baseline(tmp_path,[h]);store=ArchiveStore(tmp_path/'store')
    config={'baseline':str(b),'storage':str(store.root)}
    page=search(config,'图片')
    key=page['hits'][0]['archiveMediaKey']
    assert page['hits'][0]['archiveMedia'][0]['status']=='pending'
    with store.connect() as c:
        row=c.execute('SELECT priority FROM jobs WHERE origin=?',(key,)).fetchone()
        assert row['priority']==1
        retry_at=time.time()+3600
        c.execute("UPDATE jobs SET status='retry',due=? WHERE origin=?",(retry_at,key))
    search(config,'图片')
    with store.connect() as c:
        assert c.execute('SELECT due FROM jobs WHERE origin=?',(key,)).fetchone()[0]==retry_at


def test_visible_media_status_reports_newly_completed_files(tmp_path,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from wechat_decrypt_tool.routers import live_archive as router
    store=ArchiveStore(tmp_path)
    h={**hit(),'renderType':'image'}
    key=media_origin(put(store,h),h)
    monkeypatch.setattr(router,'configured',lambda:({},store))
    app=FastAPI();app.include_router(router.router)
    client=TestClient(app)
    assert client.post('/api/archive/media/status',json={'keys':[key]}).json()['items'][key][0]['status']=='pending'
    with store.connect() as c:
        c.execute("UPDATE jobs SET status='ready',path='saved.webp' WHERE origin=?",(key,))
    item=client.post('/api/archive/media/status',json={'keys':[key]}).json()['items'][key][0]
    assert item['status']=='ready' and item['path']=='saved.webp'
    assert client.post('/api/archive/media/status',json={'keys':['x']*101}).status_code==400
