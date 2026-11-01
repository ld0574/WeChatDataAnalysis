import json

import pytest

from tools.course_extraction_environment_gpu_probe import (
    PHRASE,
    PORT,
    loopback_endpoint,
    validate_asr,
    validate_sentinel,
)


@pytest.mark.parametrize("address", [
    ("127.0.0.1.evil.example", PORT), ("example.com", PORT),
    ("127.0.0.1", 443), ("::1", PORT, 0, 0), "127.0.0.1", (),
])
def test_only_exact_owned_local_endpoint_is_allowed(address):
    assert not loopback_endpoint(address)
    assert loopback_endpoint(("127.0.0.1", PORT))


@pytest.mark.parametrize("cuda,fp32,text", [
    (False, True, PHRASE), (True, False, PHRASE),
    (True, True, PHRASE + " invented"),
])
def test_asr_cannot_pass_on_cpu_or_mismatched_speech(cuda, fp32, text):
    with pytest.raises(ValueError, match="environment_gpu_asr_"):
        validate_asr(text, all_cuda=cuda, all_fp32=fp32)
    validate_asr(PHRASE + ".", all_cuda=True, all_fp32=True)


@pytest.mark.parametrize("value,models", [
    (734, [{"digest": "anchored", "size_vram": 1}]),
    (733.0, [{"digest": "anchored", "size_vram": 1}]),
    (733, [{"digest": "anchored", "size_vram": 0}]),
    (733, [{"digest": "unrelated", "size_vram": 1}]),
])
def test_text_success_needs_the_anchored_model_on_gpu(value, models):
    with pytest.raises(ValueError, match="environment_gpu_"):
        validate_sentinel({"message": {"content": json.dumps({"value": value})}}, models, 733, "anchored")
