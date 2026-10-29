from types import SimpleNamespace
from unittest.mock import Mock
import threading
import hashlib

import numpy as np

from wechat_decrypt_tool import asr_backends as backends
from wechat_decrypt_tool.asr_worker import _batch_request
from wechat_decrypt_tool import voice_transcription as voice


def test_length_buckets_preserve_each_input_and_chunk_order():
    model=SimpleNamespace(chunk_seconds=1,precision='float16',
        transcribe_batch=lambda audios,language:[str(int(a[0])) for a in audios],
        transcribe=lambda audio,language:str(int(audio[0])))
    audios=[np.full(16000*2,3,dtype=np.float32),np.full(4000,1,dtype=np.float32),np.full(8000,2,dtype=np.float32)]
    result=backends.transcribe_audio_batch(model,audios,'en',2)
    assert [r['text'] for r in result]==['3 3 3','1','2']
    assert [r['duration'] for r in result]==[2,.25,.5]


def test_failed_batch_splits_on_same_model_and_only_bad_input_fails():
    def single(audio,language):
        if audio[0]==2:raise RuntimeError('bad input')
        return str(int(audio[0]))
    model=SimpleNamespace(chunk_seconds=25,precision='float16',transcribe=single,
        transcribe_batch=Mock(side_effect=RuntimeError('CUDA out of memory')))
    result=backends.transcribe_audio_batch(model,[np.full(4000,i,dtype=np.float32) for i in (1,2,3,4)],'zh',4)
    assert [r.get('text') for r in result]==['1',None,'3','4']
    assert result[1]['error']=='bad input'


def test_backend_without_batch_api_never_drops_other_inputs():
    model=SimpleNamespace(chunk_seconds=25,precision='int8',transcribe=lambda a,l:str(int(a[0])))
    results=backends.transcribe_audio_batch(model,[np.full(4000,i,dtype=np.float32) for i in (1,2,3)],'zh',2)
    assert [r['text'] for r in results]==['1','2','3']


def test_decode_prefetch_does_not_wait_for_later_files_before_gpu_inference(monkeypatch):
    inferred=threading.Event()
    def decode(path):
        if path in ('2','3'):
            assert inferred.wait(3),'later decoding blocked current inference'
        return np.ones(4000,dtype=np.float32)
    def transcribe(audio,language):
        inferred.set();return 'ok'
    model=SimpleNamespace(chunk_seconds=25,precision='float16',transcribe=transcribe)
    monkeypatch.setattr(backends,'read_audio',decode)
    result=_batch_request(model,['0','1','2','3'],'zh',1)
    assert [r['text'] for r in result]==['ok']*4


def test_service_batch_reuses_cache_and_caches_only_successes(tmp_path,monkeypatch):
    model=SimpleNamespace(precision='float16',transcribe_audio_batch=Mock(return_value=[
        dict(text='新的文字',language='zh',duration=2,precision='float16'),
        dict(error='unrecoverable clip',code='transcription_failed')]))
    service=voice.VoiceTranscriptionService(voice.VoiceTranscriptionConfig(model='qwen3-asr-06b-hf',device='cuda'),model_loader=lambda _:model)
    monkeypatch.setattr(voice,'_convert_silk_to_browser_audio',lambda data,**_:(b'RIFF'+data,'wav','audio/wav'))
    service._write_cache(tmp_path,1,hashlib.sha256(b'cached').hexdigest(),dict(text='已有文字',language='zh',duration=1))
    result=service.transcribe_voice_batch(account_dir=tmp_path,items=[dict(server_id=i,voice_data=data) for i,data in [(1,b'cached'),(2,b'new'),(3,b'bad')]],batch_size=2)
    assert [r.get('text') for r in result]==['已有文字','新的文字',None]
    assert result[0]['cached']
    assert len(model.transcribe_audio_batch.call_args.args[0])==2
    assert service._read_cache(tmp_path,2,hashlib.sha256(b'new').hexdigest())['text']=='新的文字'
    assert service._read_cache(tmp_path,3,hashlib.sha256(b'bad').hexdigest()) is None
    service.retire()


def test_full_precision_keeps_the_model_on_its_gpu(monkeypatch):
    import sys
    dtype=object();empty=Mock()
    monkeypatch.setitem(sys.modules,'torch',SimpleNamespace(float32=dtype,cuda=SimpleNamespace(empty_cache=empty)))
    backend=backends.QwenGpuBackend.__new__(backends.QwenGpuBackend)
    model=SimpleNamespace();model.to=Mock(return_value=model)
    backend.model=model;backend.precision='float16'
    backend.use_full_precision();backend.use_full_precision()
    model.to.assert_called_once_with(dtype=dtype)
    assert backend.precision=='float32'
