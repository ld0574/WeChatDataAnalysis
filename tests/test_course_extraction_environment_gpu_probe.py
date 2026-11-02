import json
import hashlib
from types import SimpleNamespace

import pytest

from tools.course_extraction_environment_gpu_probe import (
    PHRASE,
    PORT,
    loopback_endpoint,
    restored_model_digests,
    await_model_tags,
    wait_for_gpu_permit,
    HANDOFF_POLICY,
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


def model_manifest_fixture(tmp_path):
    rows = []

    def put(relative, data):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        sha = hashlib.sha256(data).hexdigest()
        rows.append({"path": relative, "sha256": sha, "bytes": len(data)})
        return sha

    def blob(data):
        sha = hashlib.sha256(data).hexdigest()
        put("ollama-models/blobs/sha256-" + sha, data)
        return {"digest": "sha256:" + sha, "size": len(data)}

    config, weights = blob(b"synthetic-config"), blob(b"synthetic-weights")
    child = {"runner": "llamacpp", "format": "gguf", "config": config, "layers": [weights]}
    child_descriptor = blob(json.dumps(child).encode())
    child_descriptor.update(runner="llamacpp", format="gguf")
    index = {"mediaType": "application/vnd.ollama.manifest.list.v2+json", "manifests": [child_descriptor,
             {"runner": "mlx", "format": "safetensors", "digest": "sha256:" + "f" * 64}]}
    list_sha = put("ollama-models/manifests-v2/ollama.com/library/qwen3.5/9b", json.dumps(index).encode())
    return {"files": rows}, child_descriptor["digest"][7:], list_sha, weights


def test_v2_tag_uses_frozen_gguf_child_not_list_digest(tmp_path):
    manifest, child, listing, _ = model_manifest_fixture(tmp_path)
    expected = restored_model_digests(tmp_path, manifest)
    assert expected == {child}
    assert listing not in expected


def test_missing_frozen_weight_cannot_pass_on_child_name_alone(tmp_path):
    manifest, _, _, weights = model_manifest_fixture(tmp_path)
    manifest["files"] = [row for row in manifest["files"] if row["sha256"] != weights["digest"][7:]]
    with pytest.raises(ValueError, match="blob_not_frozen"):
        restored_model_digests(tmp_path, manifest)


def test_changed_child_and_unlisted_tag_cannot_be_accepted(tmp_path):
    manifest, child, _, _ = model_manifest_fixture(tmp_path)
    path = tmp_path / ("ollama-models/blobs/sha256-" + child)
    path.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="manifest_changed"):
        restored_model_digests(tmp_path, manifest)


def test_gpu_cannot_start_until_exact_live_owner_grants_fresh_permit(tmp_path):
    token = "a" * 64
    stale = tmp_path / ("gpu-permit-" + "b" * 64 + ".json")
    stale.write_text("{}")

    def grant(_):
        ready = json.loads((tmp_path / "gpu-ready.json").read_bytes())
        assert ready["cuda_initialized"] is False
        assert ready["policy"] == HANDOFF_POLICY
        (tmp_path / ("gpu-permit-" + token + ".json")).write_text(json.dumps(ready))

    assert wait_for_gpu_permit(tmp_path, token, (12, 34), "c" * 64,
                               cuda_initialized=False, alive=lambda: True, sleep=grant) == HANDOFF_POLICY


def test_dead_owner_and_changed_permit_fail_closed(tmp_path):
    with pytest.raises(ValueError, match="owner_exited"):
        wait_for_gpu_permit(tmp_path, "a" * 64, (12, 34), "c" * 64,
                            cuda_initialized=False, alive=lambda: False)
    (tmp_path / ("gpu-permit-" + "a" * 64 + ".json")).write_text("{}")
    with pytest.raises(ValueError, match="identity_changed"):
        wait_for_gpu_permit(tmp_path, "a" * 64, (12, 34), "c" * 64,
                            cuda_initialized=False, alive=lambda: True)


def test_permit_rejects_already_initialized_cuda(tmp_path):
    with pytest.raises(ValueError, match="preflight_invalid"):
        wait_for_gpu_permit(tmp_path, "a" * 64, (12, 34), "c" * 64,
                            cuda_initialized=True)
    assert not (tmp_path / "gpu-ready.json").exists()


def test_status_wait_allows_cold_read_and_records_transient_errors(tmp_path):
    import httpx

    clock = [0.0]
    calls = []
    responses = [httpx.ReadTimeout("synthetic"), httpx.Response(503),
                 httpx.Response(200, json={"models": [{"name": "synthetic"}]},
                                request=httpx.Request("GET", "http://127.0.0.1/api/tags"))]

    def get(path, timeout):
        calls.append(timeout)
        clock[0] += 1
        reply = responses.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    result = await_model_tags(SimpleNamespace(get=get), SimpleNamespace(poll=lambda: None),
                              tmp_path, monotonic=lambda: clock[0], sleep=lambda n: clock.__setitem__(0, clock[0] + n))
    assert result == [{"name": "synthetic"}]
    assert calls[0].read == 30 and calls[0].connect == 2
    receipt = json.loads((tmp_path / "startup-status.json").read_bytes())
    assert receipt["state"] == "ready" and len(receipt["attempts"]) == 3


def test_status_deadline_and_owned_server_exit_are_terminal(tmp_path):
    import httpx

    clock = [0.0]

    def get(path, timeout):
        assert timeout.read <= 3
        clock[0] += 3
        raise httpx.ReadTimeout("synthetic")

    with pytest.raises(ValueError, match="startup_timeout"):
        await_model_tags(SimpleNamespace(get=get), SimpleNamespace(poll=lambda: None), tmp_path,
                         timeout=3, monotonic=lambda: clock[0], sleep=lambda n: None)
    with pytest.raises(ValueError, match="server_exited"):
        await_model_tags(SimpleNamespace(get=get), SimpleNamespace(poll=lambda: 1), tmp_path)
