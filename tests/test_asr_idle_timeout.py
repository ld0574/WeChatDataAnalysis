import sys
import time
import types

import pytest

from wechat_decrypt_tool import asr_worker


def fake_worker_entry(connection, backend, folder, threads, idle_timeout):
    """A real spawned worker/pipe with synthetic output, no model or input audio."""
    from wechat_decrypt_tool import asr_worker as module

    fake = types.ModuleType("wechat_decrypt_tool.asr_backends")
    fake.load_backend = lambda *args: types.SimpleNamespace(precision="float32")
    fake.audio_chunks = fake.read_audio = fake.transcribe_chunk = lambda *args: None
    sys.modules["wechat_decrypt_tool.asr_backends"] = fake
    module._batch_request = lambda model, paths, language, batch_size: [
        {"text": "synthetic_fixture"} for _ in paths
    ]
    module._worker(connection, backend, folder, threads, idle_timeout)


def test_worker_survives_gap_within_configured_idle_time_and_exits_afterwards(
    monkeypatch,
):
    monkeypatch.setattr(asr_worker, "_worker", fake_worker_entry)
    backend = asr_worker.ProcessBackend(
        "fixture", "unused", "float32", idle_timeout=1.0
    )
    try:
        first = backend.transcribe_audio_batch(["not-read.wav"], "zh", batch_size=1)
        identity = backend.process.pid
        time.sleep(0.15)
        second = backend.transcribe_audio_batch(["not-read.wav"], "zh", batch_size=1)
        assert first == second == [{"text": "synthetic_fixture"}]
        assert backend.process.pid == identity
        backend.process.join(timeout=5)
        assert not backend.process.is_alive()
        assert backend.process.exitcode == 0
    finally:
        backend.close()


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), 3601])
def test_idle_timeout_rejects_invalid_or_unbounded_values(value):
    with pytest.raises(ValueError):
        asr_worker.ProcessBackend("fixture", "unused", "float32", idle_timeout=value)


def test_interactive_default_remains_two_minutes():
    assert asr_worker.ProcessBackend("fixture", "unused", "float32").idle_timeout == 120
