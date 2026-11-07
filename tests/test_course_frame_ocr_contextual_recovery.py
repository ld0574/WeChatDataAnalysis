import base64
import io
import json
from types import SimpleNamespace

from PIL import Image
import pytest

from test_course_frame_ocr_recovery import prepared as base_prepared, receipt
from wechat_decrypt_tool import course_frame_ocr_contextual_recovery as module


def prepared(tmp_path, monkeypatch):
    root, old_folder, old_plan, parent = base_prepared(tmp_path, monkeypatch)
    monkeypatch.setattr(module, "live", lambda _: False)
    monkeypatch.setattr(module, "checkpoint", lambda _: None)
    for index in range(len(old_plan["tiles"])):
        body, sha = module.base.request(root, old_plan, index)
        value = receipt(body, sha, old_plan)
        if index == len(old_plan["tiles"]) - 1:
            output = json.loads(value["response"]["message"]["content"])
            output.update(text="", complete_visible_region=False, uncertainties=["synthetic unreadable region"])
            value["response"]["message"]["content"] = json.dumps(output)
        module.knowledge.atomic_json(old_folder / f"tile-{index:03d}-receipt.json", value)
    module.knowledge.atomic_json(old_folder / "failure.json", {
        "reason_code": "requires_frame_recovery_unresolved_visible_region_preserved"})
    module.knowledge.atomic_json(old_folder / "progress.json", {"pid": 113, "created": 1, "stage": "stopped"})
    module.knowledge.atomic_json(root / "gpu-thermal-guard.json", {
        "pid": 114, "created": 2, "state": "worker_exited", "returncode": 1})
    module.knowledge.atomic_json(old_folder / "guard-started.json", {"pid": 114, "created": 2})
    result = module.prepare(root, parent)
    return (*module.load(root, result["job"]), result["job"])


def saved(root, folder, plan, key):
    receipts = {}
    for index in range(len(plan["negative_tiles"])):
        body, sha, _ = module.request(root, plan, index)
        path = folder / f"context-{index:03d}-receipt.json"
        module.knowledge.atomic_json(path, receipt(body, sha, plan))
        receipts[path.name] = module.whole.sha(path.read_bytes())
    module.knowledge.atomic_json(folder / "progress.json", {"pid": 115, "created": 1,
        "stage": "all_tiles_saved", "completed_tiles": len(plan["negative_tiles"]), "receipts_sha256": receipts})
    return module.verify(root, key)


def test_native_whole_frame_and_wider_context_are_both_in_actual_request(tmp_path, monkeypatch):
    root, folder, plan, key = prepared(tmp_path, monkeypatch)
    body, _, box = module.request(root, plan, 0)
    images = []
    for raw in body["messages"][-1]["images"]:
        with Image.open(io.BytesIO(base64.b64decode(raw))) as image:
            images.append(image.size)
    assert images == [(1290, 1080), (640, 384)] and box == [650, 696, 1290, 1080]
    assert len(plan["positive_tiles"]) == 2 and plan["negative_tiles"] == [2]
    result = saved(root, folder, plan, key)
    assert result["old_negative_retained"] and result["model_calls"] == 0 and not result["semantic_acceptance"]
    assert "synthetic complete top" in result["description"] and "discarded partial" not in result["description"]


@pytest.mark.parametrize("which", ["success", "negative", "own_response"])
def test_changes_to_success_negative_or_followup_response_refuse_reconstruction(tmp_path, monkeypatch, which):
    root, folder, plan, key = prepared(tmp_path, monkeypatch)
    saved(root, folder, plan, key)
    if which == "own_response":
        path = folder / "context-000-receipt.json"
    else:
        index = plan["positive_tiles"][0] if which == "success" else plan["negative_tiles"][0]
        path = root / "wiki/frame-ocr-recoveries" / plan["parent"] / f"tile-{index:03d}-receipt.json"
    value = module.whole.read(path)
    value["response"]["eval_count"] += 1
    module.knowledge.atomic_json(path, value)
    monkeypatch.setattr(module.httpx, "Client", lambda **_: pytest.fail("read-only reconstruction contacted model"))
    with pytest.raises(ValueError, match="requires_"):
        module.verify(root, key)


def test_application_keeps_source_failure_and_original_negative_unchanged(tmp_path, monkeypatch):
    root, folder, plan, key = prepared(tmp_path, monkeypatch)
    saved(root, folder, plan, key)
    path = root / "wiki/frame-ocr-recoveries" / plan["parent"] / "tile-002-receipt.json"
    original = path.read_bytes()
    result = module.apply(root, key)
    source, frame = module.base.rows(root, plan["video"], plan["frame"])
    assert source["error"] == module.LEGACY_FAILURE and frame["status"] == "ready"
    assert result["source_failure_unchanged"] and path.read_bytes() == original
    with pytest.raises(ValueError, match="apply_checked_frame_once"):
        module.apply(root, key)


def test_original_negative_cannot_gain_a_new_automatic_budget(tmp_path, monkeypatch):
    root, _, plan, _ = prepared(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="one_followup_no_budget_refresh"):
        module.prepare(root, plan["parent"])


def test_another_negative_stops_and_is_retained_without_retry(tmp_path, monkeypatch):
    root, folder, plan, key = prepared(tmp_path, monkeypatch)
    calls = []

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, *, json):
            calls.append(url)
            body, sha, _ = module.request(root, plan, 0)
            value = receipt(body, sha, plan)["response"]
            output = __import__("json").loads(value["message"]["content"])
            output.update(complete_visible_region=False, uncertainties=["synthetic still unreadable"])
            value["message"]["content"] = __import__("json").dumps(output)
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: value)

        def get(self, url):
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"models": [{
                "digest": plan["model_digest"], "context_length": module.base.OPTIONS["num_ctx"], "size_vram": 12345}]})

    monkeypatch.setattr(module.httpx, "Client", Client)
    with pytest.raises(ValueError, match="unresolved_visible_region"):
        module.worker(root, key)
    assert len(calls) == 1 and module.whole.read(folder / "progress.json")["stage"] == "stopped"
    assert (folder / "context-000-receipt.json").is_file()
    with pytest.raises(ValueError, match="once_without_retry"):
        module.worker(root, key)
    assert len(calls) == 1
