"""Local-only, retryable media retention for live archives."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import time
from pathlib import Path
from datetime import datetime

from PIL import Image, ImageOps


def atomic_bytes(path: Path, data: bytes):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_bytes(data)
    os.replace(temp,path)


def compress_image(data: bytes) -> tuple[bytes,str]:
    with Image.open(io.BytesIO(data)) as image:
        if getattr(image,'is_animated',False):
            return data,{'GIF':'.gif','WEBP':'.webp','PNG':'.png'}.get(image.format,'.gif')
        image=ImageOps.exif_transpose(image)
        image.thumbnail((1920,1920),Image.Resampling.LANCZOS)
        image=image.convert('RGBA' if 'A' in image.getbands() else 'RGB')
        output=io.BytesIO()
        image.save(output,format='WEBP',quality=82,method=4)
        return output.getvalue(),'.webp'


def local_image_path(config,hit,md5):
    """Bounded direct probes; never walk the complete attachment tree."""
    from .media_helpers import _try_find_decrypted_resource
    from .app_paths import get_output_databases_dir
    for account in (Path(config['baseline']),get_output_databases_dir()/config['source_account']):
        cached=_try_find_decrypted_resource(account,md5)
        if cached:
            return cached
    chat=Path(config['source']).parent/'msg'/'attach'/hashlib.md5(hit['username'].encode()).hexdigest()
    month=datetime.fromtimestamp(int(hit['createTime'])).strftime('%Y-%m')
    directories=[chat/month/'Img',chat/'Img']
    if chat.is_dir():
        # Imported media may live in the newest month rather than message month.
        directories.extend(p/'Img' for p in sorted(chat.iterdir(),key=lambda p:p.name,reverse=True)[:3] if p.is_dir())
    for folder in dict.fromkeys(directories):
        for suffix in ('','_b','_h','_W','_b_W','_h_W','_t','_t_W','_c','_c_W'):
            for ext in ('.dat','.jpg','.png','.webp','.gif'):
                path=folder/(md5+suffix+ext)
                if path.is_file():
                    return path
    raise FileNotFoundError('本机未缓存该图片，保留任务并稍后重试')


def voice_bytes(config, server_id):
    from .live_archive import NativeArchiveReader
    from .voice_transcription import load_voice_data
    # The snapshot remains readable after WeChat exits.
    data=load_voice_data(Path(config['baseline']),server_id)
    if data:
        return data
    reader=NativeArchiveReader(config)
    try:
        errors=[]
        for path in sorted((reader.source/'message').glob('media_*.db'),key=lambda p:p.stat().st_mtime_ns,reverse=True):
            try:
                rows=reader.query(path.name,f'SELECT hex(voice_data) AS data FROM VoiceInfo WHERE svr_id={int(server_id)} LIMIT 1')
            except Exception as exc:
                errors.append(str(exc))
                continue
            if rows and rows[0].get('data'):
                return bytes.fromhex(rows[0]['data'])
        if errors:
            raise RuntimeError('部分语音分片暂时不可读，将重试：'+errors[0])
    finally:
        reader.close()
    raise RuntimeError('语音源文件尚未找到，稍后重试')


def image_source(config,store,job,hit):
    """Prefer the on-disk identifier in packed metadata over the XML content hash."""
    import sqlite3
    from .chat_helpers import _extract_md5_from_packed_info,_quote_ident
    db,table,lid=json.loads(job['origin'])[:3]
    packed=None
    sid=str(hit.get('serverIdStr') or hit.get('serverId') or '0')
    with store.connect() as c:
        # A zero server ID is not unique; use the source location in that case.
        if sid!='0':
            saved=c.execute('SELECT raw FROM messages WHERE username=? AND server_id=?',(hit['username'],sid)).fetchone()
        else:
            from .live_archive import origin
            saved=c.execute('SELECT m.raw FROM messages m JOIN origins o ON o.message_id=m.id WHERE o.origin=?',
                            (origin(db,table,lid),)).fetchone()
    if saved and saved['raw']!='baseline-transcript':
        packed=bytes.fromhex(json.loads(saved['raw']).get('packed_info_data') or '')
    else:
        path=Path(config['baseline'])/db
        if path.is_file():
            conn=sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True)
            try:
                row=conn.execute(f'SELECT packed_info_data FROM {_quote_ident(table)} WHERE local_id=?',(lid,)).fetchone()
                packed=row[0] if row else None
            finally:
                conn.close()
    candidates=[_extract_md5_from_packed_info(packed)]
    candidates.extend(re.findall(r'\bmd5=[\"\']([0-9a-fA-F]{32})',str(hit.get('archiveRawText') or '')))
    candidates=list(dict.fromkeys(x.lower() for x in candidates if x))
    if not candidates:
        raise FileNotFoundError('图片文件标识尚未解析，稍后重试')
    for md5 in candidates:
        try:
            return local_image_path(config,hit,md5)
        except FileNotFoundError:
            continue
    raise FileNotFoundError('本机未缓存该图片，保留任务并稍后重试')


def process_job(config,store,job,stop):
    hit=json.loads(job['payload'])
    key=hashlib.sha256(job['origin'].encode()).hexdigest()
    media=store.root/'media'
    if shutil.disk_usage(store.root).free<5*1024**3:
        raise RuntimeError('归档盘可用空间不足 5 GB，媒体任务已暂停')
    if job['kind']=='image':
        source=image_source(config,store,job,hit)
        from .media_helpers import _read_and_maybe_decrypt_media
        from .app_paths import get_output_databases_dir
        data,_mime=_read_and_maybe_decrypt_media(source,account_dir=get_output_databases_dir()/config['source_account'],
                                               weixin_root=Path(config['source']).parent)
        payload,ext=compress_image(data)
        path=media/(key+ext)
        atomic_bytes(path,payload)
        with store.connect() as c:
            note='原图未缓存，已保存缩略图' if source.stem.endswith(('_t','_t_W')) else ''
            c.execute("UPDATE jobs SET status='ready',path=?,error=? WHERE origin=? AND kind=?",(path.name,note,job['origin'],'image'))
    else:
        from .voice_transcription import get_voice_transcription_service,_convert_silk_to_browser_audio
        raw_path=media/(key+'.silk')
        data=raw_path.read_bytes() if raw_path.exists() else voice_bytes(config,int(hit.get('serverIdStr') or hit.get('serverId') or 0))
        if not raw_path.exists():
            atomic_bytes(raw_path,data)
        audio,ext,_mime=_convert_silk_to_browser_audio(data,preferred_format='mp3')
        path=media/(key+'.'+ext)
        if audio:
            atomic_bytes(path,audio)
        else:
            path=raw_path
        # Retain audio even if ASR is unavailable or fails.
        with store.connect() as c:
            c.execute("UPDATE jobs SET path=? WHERE origin=? AND kind='voice'",(path.name,job['origin']))
        transcript=str(hit.get('voiceTranscript') or '')
        if not transcript:
            service=get_voice_transcription_service()
            from .runtime_settings import read_effective_voice_transcription_model
            if service.config.model != read_effective_voice_transcription_model()[0]:
                from .voice_transcription import _reset_voice_transcription_service
                service=_reset_voice_transcription_service()
            if config.get('voice_model') and service.config.model != config['voice_model']:
                raise RuntimeError('GPU 转写模型正在准备；音频已保存，稍后自动重试')
            result=service.transcribe_voice(account_dir=store.root,server_id=int(hit.get('serverIdStr') or hit.get('serverId') or 0),voice_data=data,cancel_event=stop)
            transcript=str(result.get('text') or '')
        store.save_transcript(job['origin'],transcript)


def media_loop(config,store,stop,kind):
    while not stop.is_set():
        try:
            with store.connect() as c:
                job=c.execute("SELECT * FROM jobs WHERE kind=? AND status IN ('pending','retry') AND due<=? ORDER BY priority DESC, attempts, due LIMIT 1",(kind,time.time())).fetchone()
                job=dict(job) if job else None
            if not job:
                stop.wait(3)
                continue
            try:
                process_job(config,store,job,stop)
            except Exception as exc:
                attempts=job['attempts']+1
                with store.connect() as c:
                    c.execute("UPDATE jobs SET status='retry',attempts=?,due=?,error=? WHERE origin=? AND kind=?",
                        (attempts,time.time()+min(3600,30*2**min(attempts,7)),str(exc),job['origin'],job['kind']))
        except Exception as exc:
            store.state(mediaError=str(exc))
            stop.wait(10)


def seed_history_media(config,store,stop,kind):
    """Backfill known image/voice messages without scanning the attachment tree."""
    import sqlite3
    from .account_identity import resolve_account_self_rowid
    from .chat_helpers import _iter_message_db_paths,_quote_ident,_row_to_search_hit,_decode_message_content
    from .live_archive import origin
    base=Path(config['baseline'])
    try:
        with store.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS history_media_v2_cursor(db TEXT,tab TEXT,kind TEXT,after_id INTEGER,done INTEGER,PRIMARY KEY(db,tab,kind))')
        def order(path):
            suffix=path.stem.rsplit('_',1)[-1]
            return (path.name.startswith('biz_'),-int(suffix) if suffix.isdigit() else 0)
        for path in sorted(_iter_message_db_paths(base),key=order):
            store.state(**{kind+'HistoryCurrentDb':path.name})
            with sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True) as source:
                source.row_factory=sqlite3.Row
                names={hashlib.md5(str(r[0]).encode()).hexdigest():str(r[0]) for r in source.execute('SELECT user_name FROM Name2Id')}
                myid,selfname=resolve_account_self_rowid(source,base)
                tables=[r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'")]
                for table in tables:
                    if stop.is_set(): return
                    with store.connect() as c:
                        c.execute('INSERT OR IGNORE INTO history_media_v2_cursor VALUES(?,?,?,0,0)',(path.name,table,kind))
                        cur=c.execute('SELECT * FROM history_media_v2_cursor WHERE db=? AND tab=? AND kind=?',(path.name,table,kind)).fetchone()
                        after,done=cur['after_id'],cur['done']
                    if done: continue
                    while not stop.is_set():
                        with store.connect() as c:
                            queued=c.execute("SELECT COUNT(*) FROM jobs WHERE kind=? AND status!='ready'",(kind,)).fetchone()[0]
                        if queued>=5000:
                            stop.wait(10); continue
                        rows=source.execute(f'SELECT m.*,n.user_name AS sender_username FROM {_quote_ident(table)} m '
                            'LEFT JOIN Name2Id n ON n.rowid=m.real_sender_id WHERE m.local_id>? AND (m.local_type & 65535)=? '
                            'ORDER BY m.local_id ASC LIMIT 100',(after,34 if kind=='voice' else 3)).fetchall()
                        with store.connect() as c:
                            for row in rows:
                                user=names.get(table[4:].lower(),'unmapped:'+table[4:])
                                hit=_row_to_search_hit(row,db_path=path,table_name=table,username=user,account_dir=base,
                                    is_group=user.endswith('@chatroom'),my_rowid=myid,self_username=selfname)
                                hit['serverIdStr']=str(row['server_id'])
                                hit['archiveRawText']=_decode_message_content(row['compress_content'],row['message_content'])
                                store.enqueue(c,origin(path.name,table,row['local_id']),hit)
                            after=int(rows[-1]['local_id']) if rows else after
                            done=int(len(rows)<100)
                            c.execute('UPDATE history_media_v2_cursor SET after_id=?,done=? WHERE db=? AND tab=? AND kind=?',(after,done,path.name,table,kind))
                        if done: break
                        stop.wait(.5)
        store.state(**{kind+'HistorySeed':'ready'})
    except Exception as exc:
        store.state(**{kind+'HistorySeed':'error',kind+'HistoryError':str(exc)})
