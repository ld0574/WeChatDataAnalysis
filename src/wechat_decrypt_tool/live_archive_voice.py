"""Independent durable stages: capture original audio, make playback, transcribe."""
from __future__ import annotations

import hashlib
import json
import shutil
import time


def initialize_voice_stages(store):
    """Resume earlier versions without deleting media or resetting backoff."""
    with store.connect() as c:
        rows=c.execute("SELECT * FROM jobs WHERE kind='voice' AND NOT EXISTS "
                       '(SELECT 1 FROM voice_stages s WHERE s.origin=jobs.origin)').fetchall()
    for job in rows:
        raw=hashlib.sha256(job['origin'].encode()).hexdigest()+'.silk'
        has_raw=(store.root/'media'/raw).is_file()
        has_playback=bool(job['path']) and not job['path'].endswith('.silk') and (store.root/'media'/job['path']).is_file()
        with store.connect() as c:
            for stage,ready,path in (('capture',has_raw,raw if has_raw else ''),
                                     ('playback',has_playback,job['path'] if has_playback else ''),
                                     ('transcribe',job['status']=='ready','')):
                status='ready' if ready else 'pending' if stage=='capture' or has_raw else 'blocked'
                if not ready and job['status']=='retry' and (stage=='capture' and not has_raw or stage=='transcribe' and has_raw):
                    status='retry'
                c.execute('INSERT OR IGNORE INTO voice_stages(origin,stage,status,attempts,due,error,path) VALUES(?,?,?,?,?,?,?)',
                    (job['origin'],stage,status,job['attempts'] if status=='retry' else 0,
                     job['due'],job['error'] if status=='retry' else '',path))
            store.sync_voice_status(c,job['origin'])


def next_stage_jobs(store,stage,*,retry_turn=False,limit=1):
    with store.connect() as c:
        return [dict(r) for r in c.execute(
            "SELECT j.payload,j.priority,s.* FROM voice_stages s JOIN jobs j ON j.origin=s.origin AND j.kind='voice' "
            "WHERE s.stage=? AND s.status IN ('pending','retry') AND s.due<=? "
            'ORDER BY CASE WHEN s.status=? THEN 0 ELSE 1 END,j.priority DESC,s.due,s.attempts LIMIT ?',
            (stage,time.time(),'retry' if retry_turn else 'pending',limit))]


def finish_stage(store,job,path=''):
    with store.connect() as c:
        c.execute("UPDATE voice_stages SET status='ready',path=?,error='' WHERE origin=? AND stage=?",
                  (path,job['origin'],job['stage']))
        if job['stage']=='capture':
            c.execute("UPDATE voice_stages SET status='pending' WHERE origin=? AND status='blocked'",(job['origin'],))
        if job['stage']=='playback':
            c.execute("UPDATE jobs SET path=? WHERE origin=? AND kind='voice'",(path,job['origin']))
        store.sync_voice_status(c,job['origin'])


def retry_stage(store,job,error):
    attempts=job['attempts']+1
    with store.connect() as c:
        c.execute("UPDATE voice_stages SET status='retry',attempts=?,due=?,error=? WHERE origin=? AND stage=?",
            (attempts,time.time()+min(3600,30*2**min(attempts,7)),str(error)[:500],job['origin'],job['stage']))
        store.sync_voice_status(c,job['origin'])


def raw_path(store,job):
    with store.connect() as c:
        row=c.execute("SELECT path FROM voice_stages WHERE origin=? AND stage='capture'",(job['origin'],)).fetchone()
    if not row or not row['path']:
        raise RuntimeError('原始音频尚未保存')
    return store.root/'media'/row['path']


def check_storage(store):
    free=shutil.disk_usage(store.root).free
    if free<5*1024**3:
        raise RuntimeError('归档盘可用空间不足 5 GB，媒体任务已暂停；消息采集继续')


def transcription_service(config):
    from .voice_transcription import get_voice_transcription_service,_reset_voice_transcription_service
    from .runtime_settings import read_effective_voice_transcription_model
    service=get_voice_transcription_service()
    if service.config.model!=read_effective_voice_transcription_model()[0]:
        service=_reset_voice_transcription_service()
    if config.get('voice_model') and service.config.model!=config['voice_model']:
        raise RuntimeError('GPU 转写模型正在准备；音频已保存，稍后自动重试')
    return service


def process_transcription_jobs(config,store,jobs,stop,batch_size):
    check_storage(store)
    service=transcription_service(config)
    pending=[];items=[]
    for job in jobs:
        hit=json.loads(job['payload'])
        if hit.get('voiceTranscript'):
            store.save_transcript(job['origin'],str(hit['voiceTranscript']))
            continue
        try:
            items.append(dict(server_id=int(hit.get('serverIdStr') or hit.get('serverId') or 0),
                              voice_data=raw_path(store,job).read_bytes()))
            pending.append(job)
        except Exception as exc:
            retry_stage(store,job,exc)
    if not items:
        return
    results=service.transcribe_voice_batch(account_dir=store.root,items=items,batch_size=batch_size,
        gpu_precision=config.get('voice_gpu_precision','auto'),cancel_event=stop)
    if len(results)!=len(pending):
        raise RuntimeError('语音批量返回数量不匹配')
    fresh=[r for r in results if 'error' not in r and not r.get('cached')]
    if fresh:
        store.state(voiceBatchDevice=fresh[0].get('device',''),voiceBatchPrecision=fresh[0].get('computeType',''),
                    voiceBatchFiles=len(results),voiceBatchCompletedAt=time.time())
    for job,result in zip(pending,results):
        if 'error' in result:
            retry_stage(store,job,result['error'])
        else:
            store.save_transcript(job['origin'],str(result.get('text') or ''))


def process_stage(config,store,job,stop):
    from .live_archive_media import atomic_bytes,voice_bytes
    from .voice_transcription import _convert_silk_to_browser_audio
    check_storage(store)
    hit=json.loads(job['payload'])
    sid=int(hit.get('serverIdStr') or hit.get('serverId') or 0)
    if job['stage']=='capture':
        name=hashlib.sha256(job['origin'].encode()).hexdigest()+'.silk'
        path=store.root/'media'/name
        if not path.is_file():
            atomic_bytes(path,voice_bytes(config,sid))
        finish_stage(store,job,name)
    elif job['stage']=='playback':
        path=raw_path(store,job)
        audio,ext,_mime=_convert_silk_to_browser_audio(path.read_bytes(),preferred_format='mp3')
        if not audio or ext=='silk':
            raise RuntimeError('原始音频已保存，可播放副本解码失败，稍后重试')
        dest=path.with_suffix('.'+ext)
        atomic_bytes(dest,audio)
        finish_stage(store,job,dest.name)
    else:
        # ASR failure never changes capture/playback stage or removes audio.
        transcript=str(hit.get('voiceTranscript') or '')
        if not transcript:
            service=transcription_service(config)
            result=service.transcribe_voice(account_dir=store.root,server_id=sid,
                voice_data=raw_path(store,job).read_bytes(),cancel_event=stop)
            transcript=str(result.get('text') or '')
        store.save_transcript(job['origin'],transcript)


def voice_stage_loop(config,store,stop,stage):
    turns=0
    batch_size=config.get('voice_batch_size',1)
    batch_size=batch_size if batch_size in (1,2,4) else 1
    batching=stage=='transcribe' and config.get('voice_model')=='qwen3-asr-06b-hf'
    while not stop.is_set():
        if stage=='transcribe' and config.get('voice_asr_paused'):
            stop.wait(2)
            continue
        try:
            jobs=next_stage_jobs(store,stage,retry_turn=turns%5==0,limit=batch_size*4 if batching else 1)
            if not jobs:
                stop.wait(2)
                continue
            job=jobs[0];turns+=1
            try:
                if batching:
                    process_transcription_jobs(config,store,jobs,stop,batch_size)
                else:
                    process_stage(config,store,job,stop)
            except Exception as exc:
                if not stop.is_set():
                    for failed_job in jobs:
                        # Earlier successful items in a mixed batch stay ready.
                        with store.connect() as c:
                            saved=c.execute('SELECT status FROM voice_stages WHERE origin=? AND stage=?',
                                            (failed_job['origin'],stage)).fetchone()
                        if saved and saved['status']!='ready':
                            retry_stage(store,failed_job,exc)
        except Exception as exc:
            store.state(**{stage+'VoiceError':str(exc)[:500]})
            stop.wait(10)
