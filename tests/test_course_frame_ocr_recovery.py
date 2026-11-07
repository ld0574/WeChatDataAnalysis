import base64
import copy
import io
import json
from types import SimpleNamespace

from PIL import Image
import pytest

from wechat_decrypt_tool import course_frame_ocr_recovery as module


def prepared(tmp_path, monkeypatch):
    root = tmp_path / "archive"
    store = module.knowledge.KnowledgeStore(root)
    video, frame = "a" * 24, 17
    image = root / "lessons" / video / "frames/0.png"
    image.parent.mkdir(parents=True)
    Image.new("RGB", (1290, 1080), "white").save(image)
    model, source_sha = "synthetic-local-vision", module.whole.sha(image.read_bytes())
    policy = "frame-vision-v2-overlap64-depth3/" + module.MODEL + "@" + model
    folder = root / "lessons" / video / "frame-ocr-cache" / module.whole.sha((source_sha + "\0" + policy).encode())
    folder.mkdir(parents=True)
    for name, extra in {"0-1080.json": {"split": True}, "0-572.json": {"text": "synthetic complete top"},
            "508-1080.json": {"split": True}, "508-826.json": {"text": "synthetic complete middle"},
            "762-1080.json": {"split": True}, "762-953.json": {"split": True}}.items():
        module.knowledge.atomic_json(folder / name, {"source_digest": source_sha, "policy": policy, **extra})
    module.knowledge.atomic_json(folder / "762-953.json.truncated.json", {
        "local_output": "synthetic discarded partial", "accepted_as_evidence": False})
    with store.write() as db:
        db.execute("INSERT INTO videos(id,path,title,status,error) VALUES(?,?,?,?,?)",
            (video, str(tmp_path / "video.mp4"), "synthetic", "transcribed", module.LEGACY_FAILURE))
        db.execute("INSERT INTO frames(id,video,path) VALUES(?,?,?)", (frame, video, str(image.relative_to(root))))
    module.knowledge.atomic_json(root / "gpu-thermal-guard.json", {
        "pid": 111, "created": 1, "state": "worker_exited", "returncode": 1})
    module.knowledge.atomic_json(root / "job-progress.json", {
        "pid": 112, "stage": "ocr_inference", "video": video, "frame": frame})
    monkeypatch.setattr(module, "local_model_digest", lambda _: model)
    monkeypatch.setattr(module, "checkpoint", lambda _: None)
    monkeypatch.setattr(module, "live", lambda _: False)
    monkeypatch.setattr(module.psutil, "pid_exists", lambda _: False)
    result = module.prepare(root, video, frame)
    return (*module.load(root, result["job"]), result["job"])


def receipt(body, sha, plan, **change):
    output = {"text": "synthetic complete tile", "visible_diagram_relations": "", "uncertainties": [],
        "complete_visible_region": True, "has_readable_content": True}
    response = {"done": True, "done_reason": "stop", "eval_count": 50, "prompt_eval_count": 700,
        "message": {"content": json.dumps(output)}}
    value = {"request_sha256": module.digest(body), "tile_sha256": sha, "response": response,
        "loaded_model": {"digest": plan["model_digest"], "context_length": module.OPTIONS["num_ctx"], "size_vram": 12345}}
    value.update(change)
    return value


def saved_result(root, folder, plan, key):
    receipts = {}
    for index in range(len(plan["tiles"])):
        body, sha = module.request(root, plan, index)
        path = folder / f"tile-{index:03d}-receipt.json"
        module.knowledge.atomic_json(path, receipt(body, sha, plan))
        receipts[path.name] = module.whole.sha(path.read_bytes())
    module.knowledge.atomic_json(folder / "progress.json", {"pid": 113, "created": 1,
        "stage": "all_tiles_saved", "completed_tiles": len(plan["tiles"]), "receipts_sha256": receipts})
    return module.verify(root, key)


def test_only_uncovered_pixels_get_new_requests_and_complete_originals_stay_whole(tmp_path, monkeypatch):
    root, folder, plan, key = prepared(tmp_path, monkeypatch)
    assert len(plan["complete_legacy_regions"]) == 2
    assert plan["tiles"] == [[0, 794, 640, 1080], [608, 794, 1248, 1080], [1216, 794, 1290, 1080]]
    originals = {name: (root / name).read_bytes() for name in plan["legacy_files"]}
    result = saved_result(root, folder, plan, key)
    assert result["model_calls"] == 0 and not result["semantic_acceptance"]
    assert "synthetic complete top" in result["description"] and "discarded partial" not in result["description"]
    assert all((root / name).read_bytes() == raw for name, raw in originals.items())
    for index, box in enumerate(plan["tiles"]):
        body, _ = module.request(root, plan, index)
        with Image.open(io.BytesIO(base64.b64decode(body["messages"][-1]["images"][0]))) as tile:
            assert tile.size == (box[2] - box[0], box[3] - box[1])


@pytest.mark.parametrize("change", ["original_image", "successful_cache", "failed_cache", "new_cache_file"])
def test_reconstruction_refuses_changed_success_failure_or_original_pixels(tmp_path, monkeypatch, change):
    root, folder, plan, key = prepared(tmp_path, monkeypatch)
    saved_result(root, folder, plan, key)
    if change == "original_image":
        (root / plan["frame_path"]).write_bytes(b"changed image")
    elif change == "new_cache_file":
        (root / plan["legacy_folder"] / "new.json").write_text("{}")
    else:
        name = "0-572.json" if change == "successful_cache" else "762-953.json.truncated.json"
        (root / plan["legacy_folder"] / name).write_text("{}")
    with pytest.raises(ValueError, match="requires_frame_recovery_"):
        module.verify(root, key)


@pytest.mark.parametrize("bad", ["length", "cpu", "context", "missing_owner", "changed_wire", "unresolved", "blank_positive"])
def test_incomplete_gpu_unresolved_and_changed_requests_never_become_evidence(tmp_path, monkeypatch, bad):
    root, _, plan, _ = prepared(tmp_path, monkeypatch)
    body, sha = module.request(root, plan, 0)
    value = receipt(body, sha, plan)
    if bad == "length":
        value["response"]["done_reason"] = "length"
    elif bad == "cpu":
        value["loaded_model"]["size_vram"] = 0
    elif bad == "context":
        value["loaded_model"]["context_length"] = 8192
    elif bad == "missing_owner":
        value["loaded_model"] = {}
    elif bad == "changed_wire":
        body["messages"][-1]["images"] = ["different"]
    else:
        output = json.loads(value["response"]["message"]["content"])
        output["complete_visible_region"] = bad != "unresolved"
        if bad == "blank_positive":
            output["text"] = " "
        value["response"]["message"]["content"] = json.dumps(output)
    with pytest.raises(ValueError, match="requires_frame_recovery_"):
        module.checked_receipt(value, body, sha, plan)


def test_receipt_changed_after_completion_is_refused_without_any_model_call(tmp_path, monkeypatch):
    root, folder, plan, key = prepared(tmp_path, monkeypatch)
    saved_result(root, folder, plan, key)
    path = folder / "tile-000-receipt.json"
    value = module.whole.read(path)
    value["response"]["eval_count"] += 1
    module.knowledge.atomic_json(path, value)
    monkeypatch.setattr(module, "local_model_digest", lambda _: pytest.fail("read-only reconstruction contacted model"))
    monkeypatch.setattr(module.httpx, "Client", lambda **_: pytest.fail("reconstruction contacted service"))
    with pytest.raises(ValueError, match="original_complete_server_receipt"):
        module.verify(root, key)


def test_checked_application_preserves_original_source_failure_and_all_caches(tmp_path, monkeypatch):
    root, folder, plan, key = prepared(tmp_path, monkeypatch)
    saved_result(root, folder, plan, key)
    original_source = copy.deepcopy(plan["original_source"])
    result = module.apply(root, key)
    source, frame = module.rows(root, plan["video"], plan["frame"])
    assert source == original_source and source["error"] == module.LEGACY_FAILURE
    assert frame["status"] == "ready" and frame["model"] == module.MODEL and result["source_failure_unchanged"]
    with pytest.raises(ValueError, match="one_checked_frame_application"):
        module.apply(root, key)


def test_worker_stops_after_one_http_failure_and_never_retries(tmp_path, monkeypatch):
    root, folder, plan, key = prepared(tmp_path, monkeypatch)
    calls = []

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, **kwargs):
            calls.append(url)
            return SimpleNamespace(status_code=503, content=b"synthetic failure",
                raise_for_status=lambda: (_ for _ in ()).throw(RuntimeError("synthetic_http_failed")))

    monkeypatch.setattr(module.httpx, "Client", Client)
    with pytest.raises(RuntimeError, match="synthetic_http_failed"):
        module.worker(root, key)
    assert len(calls) == 1 and module.whole.read(folder / "progress.json")["stage"] == "stopped"
    with pytest.raises(ValueError, match="one_attempt_without_retry"):
        module.worker(root, key)
    assert len(calls) == 1 and module.rows(root, plan["video"], plan["frame"])[1]["status"] == "pending"


def test_tiles_cover_every_missing_pixel_with_bounded_size():
    complete = [{"box": [0, 0, 1441, 100]}, {"box": [0, 200, 1441, 430]}]
    tiles = module.missing_tiles(complete, 1441, 1080)
    assert all(right - left <= 640 and bottom - top <= 384 for left, top, right, bottom in tiles)
    for y in range(1080):
        intervals = sorted((left, right) for left, top, right, bottom in
            [*[row["box"] for row in complete], *tiles] if top <= y < bottom)
        end = 0
        for left, right in intervals:
            assert left <= end
            end = max(end, right)
        assert end == 1441


def test_changed_failed_guard_cannot_refresh_the_same_frame_budget(tmp_path, monkeypatch):
    root, _, plan, _ = prepared(tmp_path, monkeypatch)
    guard = module.whole.read(root / "gpu-thermal-guard.json")
    guard["pid"] = 200
    module.knowledge.atomic_json(root / "gpu-thermal-guard.json", guard)
    with pytest.raises(ValueError, match="explicit_recovery_already_registered"):
        module.prepare(root, plan["video"], plan["frame"])
