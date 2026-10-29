"""Durable live archive overlay. Historical snapshots are opened read-only.

Every source table has an ascending cursor and a separate reconciliation cursor.
A page, its FTS changes, media jobs, and cursor commit in one SQLite transaction.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .app_paths import get_output_dir
from .chat_helpers import _quote_ident, _to_char_token_text, _build_fts_query


def config_path() -> Path:
    return get_output_dir() / 'live-archive.json'


def load_config() -> dict:
    try:
        value = json.loads(config_path().read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def origin(db: str, table: str, local_id: int) -> str:
    return json.dumps([db, table, int(local_id)], separators=(',', ':'))


def media_origin(key: str, hit: dict) -> str:
    return json.dumps([*json.loads(key)[:3],str(hit.get('serverIdStr') or hit.get('serverId') or '0')],separators=(',', ':'))


def identity(hit: dict) -> tuple:
    sid = str(hit.get('serverIdStr') or hit.get('serverId') or '0')
    if sid not in ('0', ''):
        return ('server', str(hit.get('username') or ''), sid)
    return ('local', str(hit.get('db') or ''), str(hit.get('table') or ''), int(hit.get('localId') or 0))


def json_raw(row: dict) -> str:
    return json.dumps({k: (bytes(v).hex() if isinstance(v, (bytes, bytearray, memoryview)) else v)
                       for k, v in row.items()}, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def same_source_row(previous, row):
    for key in ('server_id','local_type','sort_seq','real_sender_id','create_time',
                'message_content','compress_content','packed_info_data'):
        if key not in previous.keys():
            continue
        a,b=previous[key],row[key]
        if key.endswith('content') or key=='packed_info_data':
            a=a.encode('utf-8') if isinstance(a,str) else bytes(a or b'')
            b=b.encode('utf-8') if isinstance(b,str) else bytes(b or b'')
        else:
            a,b=int(a or 0),int(b or 0)
        if a!=b:
            return False
    return True


class ArchiveStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'archive.db'
        with self.connect() as c:
            c.execute('PRAGMA journal_mode=WAL')
            c.executescript('''
                CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS cursors(db TEXT, tab TEXT, high INTEGER NOT NULL,
                    audit INTEGER NOT NULL DEFAULT 0, audited_at REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY(db,tab));
                CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, username TEXT NOT NULL,
                    server_id TEXT NOT NULL, payload TEXT NOT NULL, raw TEXT NOT NULL,
                    search_text TEXT NOT NULL, create_time INTEGER NOT NULL, transcript TEXT NOT NULL DEFAULT '');
                CREATE UNIQUE INDEX IF NOT EXISTS message_server ON messages(username,server_id)
                    WHERE server_id != '0' AND server_id != '';
                CREATE TABLE IF NOT EXISTS origins(origin TEXT PRIMARY KEY, message_id INTEGER NOT NULL
                    REFERENCES messages(id));
                CREATE TABLE IF NOT EXISTS revisions(id INTEGER PRIMARY KEY, message_id INTEGER,
                    saved_at REAL, payload TEXT, raw TEXT);
                CREATE VIRTUAL TABLE IF NOT EXISTS archive_fts USING fts5(search_text,
                    content='messages', content_rowid='id', tokenize='unicode61');
                CREATE TRIGGER IF NOT EXISTS archive_ai AFTER INSERT ON messages BEGIN
                    INSERT INTO archive_fts(rowid,search_text) VALUES(new.id,new.search_text); END;
                CREATE TRIGGER IF NOT EXISTS archive_ad AFTER DELETE ON messages BEGIN
                    INSERT INTO archive_fts(archive_fts,rowid,search_text) VALUES('delete',old.id,old.search_text); END;
                CREATE TRIGGER IF NOT EXISTS archive_au AFTER UPDATE ON messages BEGIN
                    INSERT INTO archive_fts(archive_fts,rowid,search_text) VALUES('delete',old.id,old.search_text);
                    INSERT INTO archive_fts(rowid,search_text) VALUES(new.id,new.search_text); END;
                CREATE TABLE IF NOT EXISTS jobs(origin TEXT, kind TEXT, payload TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                    due REAL NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '', path TEXT NOT NULL DEFAULT '',
                    transcript TEXT NOT NULL DEFAULT '', PRIMARY KEY(origin,kind));
                CREATE INDEX IF NOT EXISTS jobs_due ON jobs(status,due);
            ''')
            columns={r[1] for r in c.execute('PRAGMA table_info(cursors)')}
            for col in ('audit_stamp','audit_target'):
                if col not in columns:
                    c.execute(f"ALTER TABLE cursors ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
            job_columns={r[1] for r in c.execute('PRAGMA table_info(jobs)')}
            if 'priority' not in job_columns:
                try:
                    c.execute('ALTER TABLE jobs ADD COLUMN priority INTEGER NOT NULL DEFAULT 0')
                except sqlite3.OperationalError as exc:
                    if 'duplicate column' not in str(exc).lower():
                        raise
            c.execute('CREATE INDEX IF NOT EXISTS jobs_priority ON jobs(kind,status,priority DESC,attempts,due)')
            for old in c.execute('SELECT origin,payload FROM jobs WHERE json_array_length(origin)=3').fetchall():
                c.execute('UPDATE OR IGNORE jobs SET origin=? WHERE origin=?',(media_origin(old['origin'],json.loads(old['payload'])),old['origin']))

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.path, timeout=15)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        c.execute('PRAGMA synchronous=FULL')
        try:
            with c:
                yield c
        finally:
            c.close()

    def state(self, **values):
        with self.connect() as c:
            c.executemany('INSERT OR REPLACE INTO state VALUES(?,?)',
                          [(k, json.dumps(v, ensure_ascii=False)) for k, v in values.items()])

    def status(self):
        with self.connect() as c:
            result = {r['key']: json.loads(r['value']) for r in c.execute('SELECT * FROM state')}
            result['overlayMessages'] = c.execute('SELECT COUNT(*) FROM messages').fetchone()[0]
            result['tables'] = c.execute('SELECT COUNT(*) FROM cursors').fetchone()[0]
            result['media'] = [dict(r) for r in c.execute('SELECT kind,status,COUNT(*) AS count FROM jobs GROUP BY kind,status')]
            result['mediaErrors'] = [dict(r) for r in c.execute("SELECT kind,error,COUNT(*) AS count FROM jobs WHERE status='retry' GROUP BY kind,error LIMIT 5")]
        return result

    def cursor(self, db, table, initial, baseline_stamp=''):
        with self.connect() as c:
            c.execute('INSERT OR IGNORE INTO cursors(db,tab,high,audit_stamp) VALUES(?,?,?,?)', (db,table,initial,baseline_stamp))
            return dict(c.execute('SELECT * FROM cursors WHERE db=? AND tab=?', (db,table)).fetchone())

    @staticmethod
    def enqueue(c, key, hit):
        kind = hit.get('renderType')
        if kind in ('image', 'voice'):
            c.execute('INSERT OR IGNORE INTO jobs(origin,kind,payload,due) VALUES(?,?,?,?)',
                      (media_origin(key,hit),kind,json.dumps(hit,ensure_ascii=False),-int(hit.get('createTime') or 0)))

    def commit_page(self, db, table, rows, *, high=None, audit=None, audited_at=None,audit_stamp=None,audit_target=None,include_baseline_media=True):
        """rows: (origin, rendered hit, raw JSON, equals baseline). No cursor on failure."""
        with self.connect() as c:
            for key, hit, raw, same in rows:
                if include_baseline_media or not same:
                    self.enqueue(c,key,hit)
                existing = c.execute('SELECT m.* FROM messages m JOIN origins o ON m.id=o.message_id WHERE o.origin=?',(key,)).fetchone()
                sid = str(hit.get('serverIdStr') or hit.get('serverId') or '0')
                user = str(hit.get('username') or '')
                server = c.execute('SELECT * FROM messages WHERE username=? AND server_id=?',(user,sid)).fetchone() if sid != '0' else None
                if same and existing is None and server is None:
                    continue
                # local_id can be reused after rotation/reset: retain the old message.
                if existing and existing['server_id'] not in ('0','',sid) and sid!='0':
                    existing=None
                if server is not None and existing is not None and server['id'] != existing['id']:
                    c.execute('INSERT INTO revisions(message_id,saved_at,payload,raw) VALUES(?,?,?,?)',
                              (server['id'],time.time(),existing['payload'],existing['raw']))
                    c.execute('UPDATE origins SET message_id=? WHERE message_id=?',(server['id'],existing['id']))
                    c.execute('DELETE FROM messages WHERE id=?',(existing['id'],))
                current = server or existing
                transcript = current['transcript'] if current else str(hit.get('voiceTranscript') or '')
                payload = json.dumps(hit,ensure_ascii=False,separators=(',',':'))
                text = _to_char_token_text('\n'.join(str(hit.get(k) or '') for k in
                    ('content','title','url','quoteTitle','quoteContent','amount'))+'\n'+transcript)
                if current:
                    mid = current['id']
                    if current['raw'] != raw or current['payload'] != payload:
                        c.execute('INSERT INTO revisions(message_id,saved_at,payload,raw) VALUES(?,?,?,?)',
                                  (mid,time.time(),current['payload'],current['raw']))
                        c.execute('UPDATE messages SET username=?,server_id=?,payload=?,raw=?,search_text=?,create_time=? WHERE id=?',
                                  (user,sid,payload,raw,text,int(hit.get('createTime') or 0),mid))
                else:
                    mid = c.execute('INSERT INTO messages(username,server_id,payload,raw,search_text,create_time,transcript) VALUES(?,?,?,?,?,?,?)',
                                    (user,sid,payload,raw,text,int(hit.get('createTime') or 0),transcript)).lastrowid
                c.execute('INSERT OR REPLACE INTO origins VALUES(?,?)',(key,mid))
            if high is not None:
                c.execute('UPDATE cursors SET high=MAX(high,?) WHERE db=? AND tab=?',(high,db,table))
            if audit is not None:
                c.execute('UPDATE cursors SET audit=? WHERE db=? AND tab=?',(audit,db,table))
            if audited_at is not None:
                c.execute('UPDATE cursors SET audited_at=? WHERE db=? AND tab=?',(audited_at,db,table))
            if audit_stamp is not None:
                c.execute('UPDATE cursors SET audit_stamp=? WHERE db=? AND tab=?',(audit_stamp,db,table))
            if audit_target is not None:
                c.execute('UPDATE cursors SET audit_target=? WHERE db=? AND tab=?',(audit_target,db,table))

    def save_transcript(self, key: str, text: str):
        with self.connect() as c:
            job=c.execute("SELECT payload FROM jobs WHERE origin=? AND kind='voice'",(key,)).fetchone()
        if not job:
            raise ValueError('语音任务不存在')
        hit=json.loads(job['payload'])
        db,table,lid=json.loads(key)[:3]
        source_key=origin(db,table,lid)
        sid=str(hit.get('serverIdStr') or hit.get('serverId') or '0')
        with self.connect() as c:
            present=c.execute('SELECT 1 FROM messages WHERE username=? AND server_id=?',(hit['username'],sid)).fetchone() if sid!='0' else c.execute('SELECT 1 FROM origins WHERE origin=?',(source_key,)).fetchone()
        if not present:
            self.commit_page(db,table,[(source_key,hit,'baseline-transcript',False)])
        with self.connect() as c:
            r=c.execute('SELECT * FROM messages WHERE username=? AND server_id=?',(hit['username'],sid)).fetchone() if sid!='0' else c.execute('SELECT m.* FROM messages m JOIN origins o ON m.id=o.message_id WHERE o.origin=?',(source_key,)).fetchone()
            if r:
                p=json.loads(r['payload'])
                p['voiceTranscript']=text
                search=_to_char_token_text('\n'.join(str(p.get(k) or '') for k in ('content','title','url','quoteContent'))+'\n'+text)
                c.execute('UPDATE messages SET transcript=?,payload=?,search_text=? WHERE id=?',(text,json.dumps(p,ensure_ascii=False),search,r['id']))
            c.execute("UPDATE jobs SET transcript=?,status='ready',error='' WHERE origin=? AND kind='voice'",(text,key))


class NativeArchiveReader:
    def __init__(self, config):
        from .wcdb_realtime import open_account
        from .key_store import get_account_keys_from_store
        self.source = Path(config['source'])
        self._inventory_cache={}
        key = get_account_keys_from_store(config['source_account']).get('db_key')
        if not key:
            raise RuntimeError('归档账号缺少数据库密钥')
        self.handle = open_account(self.source/'session'/'session.db',key,
                                  account='live-archive-reader',db_storage_dir=self.source)

    def close(self):
        from .wcdb_realtime import close_account
        close_account(self.handle)

    def query(self, db, sql):
        from .wcdb_realtime import exec_query
        return exec_query(self.handle,kind='message',path=str(self.source/'message'/db),sql=sql)

    def inventory(self):
        for path in sorted((self.source/'message').glob('*.db'),key=lambda p:p.stat().st_mtime_ns,reverse=True):
            if not re.fullmatch(r'(?:biz_)?message(?:_\d+)?\.db',path.name,re.I):
                continue
            stamp=self.signature(path.name)
            cached=self._inventory_cache.get(path.name)
            if cached and cached[0]==stamp:
                yield from cached[1]
                continue
            names = self.query(path.name,'SELECT user_name FROM Name2Id')
            users={hashlib.md5(str(r['user_name']).encode()).hexdigest():str(r['user_name']) for r in names}
            tables=self.query(path.name,"SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'")
            result=[]
            for row in tables:
                table=str(row['name'])
                digest=table[4:].lower()
                # Unknown tables are an error, never silently claim full coverage.
                user=users.get(digest)
                if not user:
                    user='unmapped:'+digest
                result.append((path.name,table,user))
            self._inventory_cache[path.name]=(stamp,result)
            yield from result

    def signature(self,db):
        result=[]
        for suffix in ('','-wal'):
            try:
                stat=(self.source/'message'/(db+suffix)).stat()
                result.extend((stat.st_size,stat.st_mtime_ns))
            except FileNotFoundError:
                result.extend((0,0))
        return result

    def fetch(self, db, table, after, limit):
        sql=(f'SELECT m.local_id,m.server_id,m.local_type,m.sort_seq,m.real_sender_id,m.create_time,'
             f'hex(m.message_content) AS content_hex,hex(m.compress_content) AS compress_hex,'
             f'hex(m.packed_info_data) AS packed_hex,n.user_name AS sender_username '
             f'FROM {_quote_ident(table)} m LEFT JOIN Name2Id n ON n.rowid=m.real_sender_id '
             f'WHERE m.local_id>{int(after)} ORDER BY m.local_id ASC LIMIT {int(limit)}')
        rows=self.query(db,sql)
        for r in rows:
            for dest,src in (('message_content','content_hex'),('compress_content','compress_hex'),('packed_info_data','packed_hex')):
                r[dest]=bytes.fromhex(str(r.pop(src) or ''))
            for key in ('local_id','server_id','local_type','sort_seq','real_sender_id','create_time'):
                r[key]=int(r.get(key) or 0)
        return rows

    def max_ids(self,db,tables):
        result={}
        # A single read-only query can inspect hundreds of primary-key ends.
        # This avoids one broker round trip per inactive conversation.
        for offset in range(0,len(tables),250):
            sql=' UNION ALL '.join("SELECT '"+t.replace("'","''")+"' AS tab,COALESCE(MAX(local_id),0) AS high FROM "+_quote_ident(t)
                                   for t in tables[offset:offset+250])
            for row in self.query(db,sql):
                result[str(row['tab'])]=int(row['high'] or 0)
        return result


class LiveArchiveService:
    def __init__(self):
        self.stop_event=threading.Event()
        self.thread=None

    def start(self):
        config=load_config()
        if not config.get('enabled') or (self.thread and self.thread.is_alive()):
            return
        self.stop_event.clear()
        self.thread=threading.Thread(target=self.run,args=(config,),daemon=True,name='live-archive')
        self.thread.start()

    def stop(self):
        self.stop_event.set()

    def run(self, config):
        # One collector/media writer per archive, including across app processes.
        root=Path(config['storage'])
        root.mkdir(parents=True,exist_ok=True)
        lock=(root/'writer.lock').open('a+b')
        try:
            if os.name=='nt':
                import msvcrt
                lock.seek(0)
                if not lock.read(1):
                    lock.write(b'0'); lock.flush()
                lock.seek(0)
                msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            lock.close()
            return
        try:
            self._run_locked(config)
        finally:
            lock.close()

    def _run_locked(self, config):
        from .live_archive_media import media_loop
        store=ArchiveStore(Path(config['storage']))
        # The media worker is independent of source scanning and cannot hold its cursor.
        for kind in ('voice','image'):
            threading.Thread(target=media_loop,args=(config,store,self.stop_event,kind),daemon=True,name='archive-'+kind).start()
        from .live_archive_media import seed_history_media
        for kind in ('voice','image'):
            threading.Thread(target=seed_history_media,args=(config,store,self.stop_event,kind),daemon=True,name='archive-history-'+kind).start()
        while not self.stop_event.is_set():
            reader=None
            try:
                reader=NativeArchiveReader(config)
                self.collect(config,store,reader)
            except Exception as exc:
                store.state(status='error',error=str(exc),heartbeat=time.time())
            finally:
                if reader:
                    reader.close()
            self.stop_event.wait(10)

    def collect(self,config,store,reader):
        from .account_identity import resolve_account_self_rowid
        from .chat_helpers import _row_to_search_hit
        base=Path(config['baseline'])
        connections={}
        self_ids={}
        last_signatures=store.status().get('sourceSignatures',{})
        baseline_signatures={}
        # The immutable baseline is already a complete snapshot for unchanged shards.
        try:
            manifest=json.loads(Path(config['baseline_manifest']).read_text(encoding='utf-8'))
            for rel,s in manifest['source_signatures_after'].items():
                name=Path(rel).name
                w=s.get('siblings',{}).get('-wal',{})
                stamp=[s['size'],s['mtime_ns'],w.get('size',0),w.get('mtime_ns',0)]
                baseline_signatures[name]=stamp
                last_signatures.setdefault(name,stamp)
        except (OSError,KeyError,ValueError):
            pass
        try:
            while not self.stop_event.is_set():
                started=time.time()
                store.state(status='scanning',error='',heartbeat=started)
                inventory=list(reader.inventory())
                store.state(sourceTables=len(inventory),heartbeat=time.time())
                signatures={db:reader.signature(db) for db,_,_ in inventory}
                high_by_db={}
                for db in signatures:
                    if signatures[db]!=last_signatures.get(db):
                        high_by_db[db]=reader.max_ids(db,[t for d,t,_u in inventory if d==db])
                        store.state(heartbeat=time.time(),currentDb=db)
                pending_dbs=set()
                with store.connect() as c:
                    cursor_map={(r['db'],r['tab']):dict(r) for r in c.execute('SELECT * FROM cursors')}
                for db,table,user in inventory:
                    if self.stop_event.is_set():
                        return
                    conn=connections.get(db)
                    if db not in connections:
                        path=base/db
                        conn=sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True) if path.is_file() else None
                        if conn:
                            conn.row_factory=sqlite3.Row
                        connections[db]=conn
                    cursor=cursor_map.get((db,table))
                    if cursor is None:
                        try:
                            initial=int(conn.execute(f'SELECT COALESCE(MAX(local_id),0) FROM {_quote_ident(table)}').fetchone()[0]) if conn else 0
                        except sqlite3.OperationalError:
                            initial=0
                        cursor=store.cursor(db,table,initial,json.dumps(baseline_signatures.get(db,[])))
                        cursor_map[(db,table)]=cursor
                    if db not in self_ids:
                        self_ids[db]=resolve_account_self_rowid(conn,base) if conn else (None,'')
                    myid,selfname=self_ids[db]
                    if signatures[db]==last_signatures.get(db):
                        continue
                    if high_by_db[db].get(table,0)<=cursor['high']:
                        continue
                    # Ascending pages never jump over a backlog larger than one poll.
                    for mode,after in (('tail',cursor['high']),):
                        if mode=='audit' and cursor['audited_at'] and started-cursor['audited_at']<86400:
                            continue
                        batch=reader.fetch(db,table,after,200)
                        if len(batch)==200:
                            pending_dbs.add(db)
                        prepared=[]
                        for row in batch:
                            same=False
                            if conn:
                                try:
                                    previous=conn.execute(f'SELECT * FROM {_quote_ident(table)} WHERE local_id=?',(row['local_id'],)).fetchone()
                                    if previous:
                                        same=same_source_row(previous,row)
                                except sqlite3.OperationalError:
                                    pass
                            hit=_row_to_search_hit(row,db_path=base/db,table_name=table,username=user,
                                account_dir=base,is_group=user.endswith('@chatroom'),my_rowid=myid,self_username=selfname)
                            hit['serverIdStr']=str(row['server_id'])
                            from .chat_helpers import _decode_message_content
                            hit['archiveRawText']=_decode_message_content(row['compress_content'],row['message_content'])
                            prepared.append((origin(db,table,row['local_id']),hit,json_raw(row),same))
                        end=int(batch[-1]['local_id']) if batch else int(after)
                        if mode=='tail':
                            store.commit_page(db,table,prepared,high=end)
                        else:
                            finished=len(batch)<200
                            store.commit_page(db,table,prepared,audit=0 if finished else end,
                                              audited_at=started if finished else None)
                        store.state(heartbeat=time.time(),currentDb=db)
                # Reconcile below the tail watermark too: late imports and old edits
                # must not be skipped merely because their local_id is smaller.
                audit_deadline=time.monotonic()+10
                for db,table,user in inventory:
                    if self.stop_event.is_set() or time.monotonic()>audit_deadline:
                        break
                    cur=cursor_map[(db,table)]
                    stamp=json.dumps(signatures[db])
                    if cur['audit_stamp']==stamp:
                        continue
                    if not cur['audit_target']:
                        cur['audit_target']=stamp
                        store.commit_page(db,table,[],audit_target=stamp)
                    conn=connections.get(db)
                    myid,selfname=self_ids[db]
                    while not self.stop_event.is_set() and time.monotonic()<audit_deadline:
                        batch=reader.fetch(db,table,cur['audit'],500)
                        prepared=[]
                        for row in batch:
                            previous=None
                            if conn:
                                try: previous=conn.execute(f'SELECT * FROM {_quote_ident(table)} WHERE local_id=?',(row['local_id'],)).fetchone()
                                except sqlite3.OperationalError: pass
                            hit=_row_to_search_hit(row,db_path=base/db,table_name=table,username=user,account_dir=base,
                                is_group=user.endswith('@chatroom'),my_rowid=myid,self_username=selfname)
                            hit['serverIdStr']=str(row['server_id'])
                            from .chat_helpers import _decode_message_content
                            hit['archiveRawText']=_decode_message_content(row['compress_content'],row['message_content'])
                            prepared.append((origin(db,table,row['local_id']),hit,json_raw(row),bool(previous and same_source_row(previous,row))))
                        finished=len(batch)<500
                        cur['audit']=0 if finished else int(batch[-1]['local_id'])
                        store.commit_page(db,table,prepared,audit=cur['audit'],audited_at=started if finished else None,
                                          audit_stamp=cur['audit_target'] if finished else None,
                                          audit_target='' if finished else None,include_baseline_media=False)
                        store.state(heartbeat=time.time(),reconcilingDb=db)
                        if finished:
                            break
                with store.connect() as c:
                    reconciled=sum(r['audit_stamp']==json.dumps(signatures.get(r['db'],[])) for r in c.execute('SELECT db,audit_stamp FROM cursors'))
                for db,stamp in signatures.items():
                    if db not in pending_dbs and reader.signature(db)==stamp:
                        last_signatures[db]=stamp
                store.state(status='watching',lastPass=time.time(),sourceTables=len(inventory),
                            reconciledTables=reconciled,sourceSignatures=last_signatures,
                            pendingDatabases=len(pending_dbs),unmappedTables=sum(u.startswith('unmapped:') for _,_,u in inventory),heartbeat=time.time())
                self.stop_event.wait(max(1,15-(time.time()-started)))
        finally:
            for conn in connections.values():
                if conn:
                    conn.close()


LIVE_ARCHIVE=LiveArchiveService()
