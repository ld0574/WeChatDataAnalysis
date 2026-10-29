"""Merge immutable historical search results and the transactional live overlay."""
import json
import hashlib
import sqlite3
import time
from pathlib import Path

from .chat_helpers import _build_fts_query
from .live_archive import ArchiveStore,identity,origin,media_origin


def search(config,query,offset=0,limit=50):
    if not query.strip():
        return {'hits':[],'hasMore':False}
    match=_build_fts_query(query)
    if not match:
        return {'hits':[],'hasMore':False}
    store=ArchiveStore(Path(config['storage']))
    deadline=time.monotonic()+45
    wanted=offset+limit+1
    hits={}
    with store.connect() as c:
        for row in c.execute("SELECT m.* FROM archive_fts f JOIN messages m ON m.id=f.rowid WHERE archive_fts MATCH ? ORDER BY create_time DESC,CAST(json_extract(payload,'$.sortSeq') AS INTEGER) DESC,CAST(json_extract(payload,'$.localId') AS INTEGER) DESC LIMIT ?", (match,wanted)):
            hit=json.loads(row['payload'])
            hit['voiceTranscript']=row['transcript']
            hits[identity(hit)]=hit
        # Transcripts of baseline messages live in jobs; retain their source identity.
        # A separate FTS table is unnecessary: ingest a rendered overlay at transcription time.
        index=Path(config['baseline'])/'chat_search_index.db'
        with sqlite3.connect(index.resolve().as_uri()+'?mode=ro&immutable=1',uri=True) as old:
            old.row_factory=sqlite3.Row
            old.set_progress_handler(lambda:int(time.monotonic()>deadline),10000)
            skip=0
            baseline_kept=set()
            # Page past overridden rows rather than silently losing later matches.
            while True:
                rows=old.execute('SELECT payload_json FROM message_fts WHERE message_fts MATCH ? ORDER BY CAST(create_time AS INTEGER) DESC,CAST(sort_seq AS INTEGER) DESC,CAST(local_id AS INTEGER) DESC LIMIT ? OFFSET ?', (match,wanted,skip)).fetchall()
                if not rows:
                    break
                kept=0
                for row in rows:
                    hit=json.loads(row['payload_json'])
                    db=str(hit.get('db') or '')
                    if not db.endswith('.db'):
                        db+='.db'
                    key=origin(db,hit['table'],hit['localId'])
                    overridden=c.execute('SELECT 1 FROM origins WHERE origin=?',(key,)).fetchone()
                    if not overridden and str(hit.get('serverIdStr') or hit.get('serverId') or '0')!='0':
                        overridden=c.execute('SELECT 1 FROM messages WHERE username=? AND server_id=?',
                            (hit['username'],str(hit.get('serverIdStr') or hit.get('serverId')))).fetchone()
                    if overridden:
                        continue
                    hits.setdefault(identity(hit),hit)
                    baseline_kept.add(identity(hit))
                    kept+=1
                skip+=len(rows)
                # Once enough baseline candidates are collected, its unseen tail
                # cannot outrank these candidates in the global merge.
                if len(rows)<wanted or len(baseline_kept)>=wanted:
                    break
        ordered=sorted(hits.values(),key=lambda h:(int(h.get('createTime') or 0),int(h.get('sortSeq') or 0),int(h.get('localId') or 0)),reverse=True)
        page=ordered[offset:offset+limit]
        for hit in page:
            db=str(hit.get('db') or '')
            if not db.endswith('.db'):
                db+='.db'
            key=origin(db,hit['table'],hit['localId'])
            media_key=media_origin(key,hit)
            # Search-visible media should not wait behind the historical backlog.
            store.enqueue(c,key,hit)
            c.execute("UPDATE jobs SET priority=1,due=CASE WHEN priority=0 THEN MIN(due,0) ELSE due END WHERE origin=? AND status!='ready'",(media_key,))
            jobs=c.execute('SELECT kind,path,status,transcript,error FROM jobs WHERE origin=?',(media_key,)).fetchall()
            hit['archiveMedia']=[dict(j) for j in jobs]
            hit['archiveMediaKey']=media_key
            hit['archiveId']=hashlib.sha256(json.dumps(identity(hit)).encode()).hexdigest()
            hit.pop('archiveRawText',None)
    return {'hits':page,'hasMore':len(ordered)>offset+limit}
