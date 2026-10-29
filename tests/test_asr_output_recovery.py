from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock
import sys

import numpy as np
import pytest

from wechat_decrypt_tool.asr_backends import (
    AsrOutputLimitError, QwenGpuBackend, transcribe_chunk,
)


def test_capped_generation_retries_smaller_chunks_without_losing_samples():
    audio = np.arange(16000 * 7, dtype=np.float32) + 1
    accepted = []

    def transcribe(chunk, language):
        if len(chunk) > 16000 * 2:
            raise AsrOutputLimitError('capped')
        accepted.append(chunk.copy())
        return str(len(accepted))

    texts = transcribe_chunk(SimpleNamespace(transcribe=transcribe), audio, 'zh')
    assert len(texts) > 1
    np.testing.assert_array_equal(np.concatenate(accepted), audio)
    assert texts == [str(i + 1) for i in range(len(accepted))]


def test_unrecoverable_short_audio_keeps_the_error_instead_of_partial_text():
    model = SimpleNamespace(transcribe=Mock(side_effect=AsrOutputLimitError('capped')))
    with pytest.raises(AsrOutputLimitError):
        transcribe_chunk(model, np.ones(16000, dtype=np.float32), 'zh')
    assert model.transcribe.call_count == 1


def test_other_inference_errors_are_not_hidden_by_chunk_retries():
    model = SimpleNamespace(transcribe=Mock(side_effect=RuntimeError('CUDA OOM')))
    with pytest.raises(RuntimeError, match='CUDA OOM'):
        transcribe_chunk(model, np.ones(16000 * 10, dtype=np.float32), 'zh')
    assert model.transcribe.call_count == 1


def test_silent_subchunks_do_not_generate_hallucinated_text():
    model = SimpleNamespace(transcribe=Mock())
    assert transcribe_chunk(model, np.zeros(16000, dtype=np.float32), 'zh') == []
    model.transcribe.assert_not_called()


@pytest.mark.parametrize('eos', [151645, [151643, 151645]])
@pytest.mark.parametrize('ended', [False, True])
def test_gpu_token_cap_requires_eos_and_accepts_eos_at_the_exact_limit(monkeypatch, eos, ended):
    monkeypatch.setitem(sys.modules, 'torch', SimpleNamespace(inference_mode=nullcontext))

    class Inputs(dict):
        def to(self, *_):
            return self

    generated = np.ones((1, 514), dtype=np.int64)
    generated[0, -1] = 151645 if ended else 42
    backend = QwenGpuBackend.__new__(QwenGpuBackend)
    backend.model = SimpleNamespace(device='cuda', dtype='float16',
        generation_config=SimpleNamespace(eos_token_id=eos), generate=Mock(return_value=generated))
    backend.processor = SimpleNamespace(
        apply_transcription_request=Mock(return_value=Inputs(input_ids=np.ones((1, 2)))),
        decode=Mock(return_value=['complete']))
    if ended:
        assert backend.transcribe(np.ones(16000), 'zh') == 'complete'
    else:
        with pytest.raises(AsrOutputLimitError):
            backend.transcribe(np.ones(16000), 'zh')
        backend.processor.decode.assert_not_called()
