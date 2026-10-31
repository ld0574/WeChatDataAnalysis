"""可取消、空闲自动退出的本地语音工作进程；模型和语音不会联网。"""
from __future__ import annotations

import multiprocessing
import math
import os
import threading
import time
import sys
from concurrent.futures import ThreadPoolExecutor


class AsrCancelled(RuntimeError):
    pass


class AsrError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _batch_request(model,paths,language,batch_size):
    from .asr_backends import read_audio,transcribe_audio_batch
    def decode(path):
        try:
            return read_audio(path)
        except Exception as exc:
            return exc
    # Two CPU decoders run ahead while the GPU consumes the current window.
    # Requests are capped at 16 files by the parent, bounding prefetched memory.
    with ThreadPoolExecutor(max_workers=2,thread_name_prefix='asr-decode') as pool:
        pending=[pool.submit(decode,path) for path in paths]
        results=[]
        window=max(1,batch_size)*2
        for offset in range(0,len(pending),window):
            audios=[future.result() for future in pending[offset:offset+window]]
            results.extend(transcribe_audio_batch(model,audios,language,batch_size))
        return results


def _worker(connection, backend: str, folder: str, threads: int, idle_timeout: float = 120):
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1",
                      HF_HUB_DISABLE_PROGRESS_BARS="1", OMP_NUM_THREADS=str(threads),
                      MKL_NUM_THREADS=str(threads), OPENBLAS_NUM_THREADS=str(threads))
    model = None
    try:
        if backend == "qwen-hf":
            from .qwen_gpu_runtime import activate_for_worker
            activate_for_worker()
        from .asr_backends import audio_chunks, load_backend, read_audio, transcribe_chunk
        while connection.poll(idle_timeout):
            request = connection.recv()
            try:
                if model is None:
                    model = load_backend(backend, folder, threads)
                if 'paths' in request:
                    if backend=='qwen-hf' and request.get('gpu_precision')=='float32':
                        model.use_full_precision()
                    torch=sys.modules.get('torch') if backend=='qwen-hf' else None
                    if torch is not None:
                        torch.cuda.reset_peak_memory_stats()
                    started=time.perf_counter()
                    results=_batch_request(model,request['paths'],request['language'],request['batch_size'])
                    metrics=dict(files=len(results),batchSize=request['batch_size'],elapsedSeconds=time.perf_counter()-started)
                    if torch is not None:
                        metrics['peakGpuBytes']=torch.cuda.max_memory_allocated()
                    connection.send(dict(results=results,precision=model.precision,metrics=metrics))
                    if results and all('error' in result for result in results):
                        break
                    continue
                audio = read_audio(request["path"])
                texts = []
                for chunk in audio_chunks(audio, model.chunk_seconds):
                    texts.extend(transcribe_chunk(model, chunk, request["language"]))
                separator = " " if request["language"] not in ("zh", "yue", "ja") else ""
                connection.send(dict(text=separator.join(texts), duration=len(audio) / 16000,
                                     language=request["language"], precision=model.precision))
            except Exception as exc:
                code = "dependency_missing" if isinstance(exc, ImportError) else "transcription_failed"
                connection.send(dict(error=str(exc)[:500], code=code))
                # 错误后退出，释放可能处于半初始化状态的模型和 CUDA 显存。
                break
    except (EOFError, BrokenPipeError, OSError):
        pass
    finally:
        connection.close()


class ProcessBackend:
    def __init__(self, backend: str, folder: str, precision: str, threads: int = 4, *, idle_timeout: float = 120):
        idle_timeout = float(idle_timeout)
        if not math.isfinite(idle_timeout) or not 0 < idle_timeout <= 3600:
            raise ValueError("ASR idle timeout must be positive and at most one hour")
        self.backend, self.folder, self.precision = backend, folder, precision
        self.idle_timeout = idle_timeout
        self.threads = max(1, min(threads, os.cpu_count() or 1))
        self.process = None
        self.connection = None

    @property
    def is_loaded(self):
        return self.process is not None and self.process.is_alive()

    def _start(self):
        if self.process is not None and self.process.is_alive():
            return
        self.close()
        context = multiprocessing.get_context("spawn")
        self.connection, child = context.Pipe()
        self.process = context.Process(target=_worker, args=(child, self.backend, self.folder, self.threads, self.idle_timeout), daemon=True)
        try:
            self.process.start()
        finally:
            child.close()

    def transcribe_audio(self, path: str, language: str, cancel_event=None):
        return self._request(dict(path=path,language=language),cancel_event)

    def transcribe_audio_batch(self,paths,language,batch_size=2,cancel_event=None,*,gpu_precision='auto'):
        if not 1<=len(paths)<=16 or batch_size not in (1,2,4):
            raise ValueError('语音批量范围无效')
        if gpu_precision not in ('auto','float32'):
            raise ValueError('GPU 计算精度无效')
        result=self._request(dict(paths=list(paths),language=language,batch_size=batch_size,gpu_precision=gpu_precision),cancel_event)
        self.last_batch_metrics=result.get('metrics',{})
        return result['results']

    def _request(self,request,cancel_event):
        if cancel_event is not None and cancel_event.is_set():
            raise AsrCancelled()
        self._start()
        deadline = time.monotonic() + 900
        try:
            self.connection.send(request)
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise AsrCancelled()
                if time.monotonic() >= deadline:
                    raise AsrError("transcription_timeout", "识别等待超时，请改用 Zipformer CTC 或较小模型。")
                if self.connection.poll(0.05):
                    result = self.connection.recv()
                    if "error" in result:
                        raise AsrError(result["code"], result["error"])
                    self.precision = result["precision"]
                    return result
                if not self.process.is_alive():
                    raise AsrError("worker_exited", "语音工作进程已退出，可能内存不足；请重试或选择较小模型。")
        except (EOFError, BrokenPipeError, OSError) as exc:
            self.close()
            raise AsrError("worker_exited", "语音工作进程中断，请重试或选择较小模型。") from exc
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        if self.process is not None:
            if self.process.pid is not None:
                if self.process.is_alive():
                    self.process.terminate()
                self.process.join(timeout=5)
                if self.process.is_alive():
                    self.process.kill()
                    self.process.join(timeout=2)
            self.process.close()
            self.process = None


def _torch_probe(connection):
    try:
        from .qwen_gpu_runtime import activate_for_worker
        activate_for_worker()
        import torch
        count = torch.cuda.device_count() if torch.cuda.is_available() else 0
        connection.send(dict(available=count > 0, deviceCount=count,
            devices=[dict(name=torch.cuda.get_device_name(i)) for i in range(count)],
            reason="" if count else "PyTorch CUDA 不可用，请安装 GPU 运行组件和显卡驱动，或选择 CPU 模型。"))
    except Exception:
        connection.send(dict(available=False, deviceCount=0, devices=[], reason="Qwen GPU 运行组件无法加载，请更新 GPU 组件或选择 CPU 模型。"))
    finally:
        connection.close()


_PROBE_LOCK = threading.Lock()
_PROBE_CACHE = None


def invalidate_qwen_probe():
    global _PROBE_CACHE
    with _PROBE_LOCK:
        _PROBE_CACHE = None


def _runtime_probe(connection, root, folder):
    try:
        from .qwen_gpu_runtime import activate_for_worker
        activate_for_worker(root)
        import torch
        # Import both public interfaces to validate this runtime installation.
        from transformers import AutoProcessor, AutoModelForMultimodalLM  # noqa: F401
        if not torch.cuda.is_available():
            raise ValueError("GPU 组件已安装，但 NVIDIA 显卡不可用，请检查显卡驱动。")
        # 实际执行 CUDA 运算，发现驱动或架构不兼容，而非只检查设备列表。
        value = (torch.ones(4, device="cuda") * 2).sum().item()
        if value != 8:
            raise ValueError("GPU 运算检查未通过，请检查显卡驱动。")
        if folder:
            os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
            from .asr_backends import QwenGpuBackend
            model = QwenGpuBackend(__import__("pathlib").Path(folder), 2)
            del model
        connection.send(dict(available=True, reason="", device=torch.cuda.get_device_name(0),
                             torchVersion=str(torch.__version__), torchModule=str(torch.__file__), modelLoaded=bool(folder)))
    except Exception as exc:
        connection.send(dict(available=False, reason=f"GPU 组件检查失败：{str(exc)[:300]}。请重试或选择 CPU 模型。"))
    finally:
        connection.close()


def check_qwen_runtime(root=None, folder=None, cancel_event=None):
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_runtime_probe, args=(child, root, folder), daemon=True)
    result = dict(available=False, reason="GPU 组件检查超时或进程退出，请重试或选择 CPU 模型。")
    try:
        process.start()
        child.close()
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                from .qwen_gpu_runtime import Paused
                raise Paused()
            if parent.poll(.1):
                result = parent.recv()
                break
            if not process.is_alive():
                break
    except (EOFError, OSError, RuntimeError):
        pass
    finally:
        parent.close()
        child.close()
        if process.pid is not None:
            process.join(timeout=1)
            if process.is_alive():
                process.terminate()
                process.join(timeout=2)
            process.close()
    return result


def probe_qwen_cuda():
    global _PROBE_CACHE
    with _PROBE_LOCK:
        if _PROBE_CACHE and time.monotonic() < _PROBE_CACHE[0]:
            return dict(_PROBE_CACHE[1])
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe(duplex=False)
        process = context.Process(target=_torch_probe, args=(child,), daemon=True)
        result = dict(available=False, deviceCount=0, devices=[], reason="Qwen GPU 检测未完成，请检查运行组件或改用 CPU 模型。")
        try:
            process.start()
            child.close()
            if parent.poll(30):
                result = parent.recv()
        except (EOFError, OSError, RuntimeError):
            pass
        finally:
            parent.close()
            child.close()
            if process.pid is not None:
                process.join(timeout=1)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=2)
                process.close()
        _PROBE_CACHE = (time.monotonic() + 60, result)
        return dict(result)
