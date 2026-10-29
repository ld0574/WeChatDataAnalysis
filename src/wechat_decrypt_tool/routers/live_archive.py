from pathlib import Path
import sqlite3

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from starlette.responses import FileResponse

from ..live_archive import ArchiveStore, load_config
from ..live_archive_search import search

router=APIRouter()


class MediaStatusRequest(BaseModel):
    keys: list[str]


@router.post('/api/archive/media/status')
def media_status(req: MediaStatusRequest):
    if len(req.keys)>100 or any(len(key)>512 for key in req.keys):
        raise HTTPException(400,'媒体状态查询超出范围')
    _config,store=configured()
    items={key:[] for key in req.keys}
    if req.keys:
        with store.connect() as c:
            placeholders=','.join('?' for _ in req.keys)
            for row in c.execute(f'SELECT origin,kind,path,status,transcript,error FROM jobs WHERE origin IN ({placeholders})',req.keys):
                value=dict(row)
                items[value.pop('origin')].append(value)
    return {'items':items}


def configured():
    config=load_config()
    if not config.get('storage') or not config.get('baseline'):
        raise HTTPException(409,'持续归档尚未配置')
    return config,ArchiveStore(Path(config['storage']))


@router.get('/api/archive/status')
def status():
    config,store=configured()
    result=store.status()
    result.pop('sourceSignatures',None)
    return {'enabled':config.get('enabled',False),'baseline':Path(config['baseline']).name,
            'voiceModel':config.get('voice_model',''),**result}


@router.get('/api/archive/search')
def archive_search(q: str, offset: int=0, limit: int=50):
    if len(q)>200 or not 0<=offset<=5000 or not 1<=limit<=100:
        raise HTTPException(400,'搜索参数超出范围')
    config,_store=configured()
    try:
        return search(config,q,offset,limit)
    except sqlite3.OperationalError as exc:
        if 'interrupted' in str(exc).lower():
            raise HTTPException(504,'本次搜索超时，请使用更具体的关键词') from None
        raise


@router.get('/api/archive/media/{name}')
def media(name: str):
    _config,store=configured()
    path=store.root/'media'/name
    if Path(name).name!=name or not path.resolve().is_relative_to((store.root/'media').resolve()) or not path.is_file():
        raise HTTPException(404,'归档媒体不存在')
    media_types={'.webp':'image/webp','.jpg':'image/jpeg','.jpeg':'image/jpeg','.png':'image/png',
                 '.gif':'image/gif','.mp3':'audio/mpeg','.wav':'audio/wav','.silk':'audio/silk',
                 '.ogg':'audio/ogg','.opus':'audio/ogg'}
    return FileResponse(path,media_type=media_types.get(path.suffix.lower(),'application/octet-stream'),
                        headers={'X-Content-Type-Options':'nosniff'})
