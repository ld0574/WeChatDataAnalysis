import base64
import io
from types import SimpleNamespace

from PIL import Image
import pytest
from tokenizers import Tokenizer, models

from wechat_decrypt_tool import course_frame_assisted_pilot as module
from wechat_decrypt_tool.course_video_visual_audit import ReadOnlyArchive


def prepared(tmp_path, monkeypatch, *, empty_secondary=False, fail_native=False):
    root = tmp_path / "archive"
    store = module.knowledge.KnowledgeStore(root)
    video, frames = "a" * 24, [17, 18, 19]
    with store.write() as db:
        db.execute("INSERT INTO videos(id,path,title,status,error) VALUES(?,?,?,?,?)",
            (video, str(tmp_path / "synthetic.mp4"), "synthetic", "transcribed", "synthetic_original_source_failure"))
        for frame in frames:
            path = root / "lessons" / video / "frames" / f"{frame}.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (64, 64), (frame, 20, 30)).save(path)
            db.execute("INSERT INTO frames(id,video,path) VALUES(?,?,?)", (frame, video, str(path.relative_to(root))))
    tokenizer = root / "wiki/tokenizer.json"
    tokenizer.parent.mkdir(parents=True, exist_ok=True)
    Tokenizer(models.WordLevel({"[UNK]": 0}, unk_token="[UNK]")).save(str(tokenizer))
    monkeypatch.setattr(module.shared, "checkpoint", lambda _: None)
    monkeypatch.setattr(module.shared, "local_model_digest", lambda _: "synthetic_installed_vision")
    monkeypatch.setattr(module.shared, "live", lambda _: False)
    monkeypatch.setattr(module, "checked_sample", lambda _: reading())
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    cpu_calls = []

    def recognize(image, output, language):
        cpu_calls.append((image, language))
        module.require(not fail_native, "installed_windows_ocr_success")
        value = {"width": 64, "height": 64, "image_sha256": module.whole.sha(image.read_bytes()),
            "language": language, "gpu_calls": 0, "lines": [{"text": "x=1", "words": [{"text": "x=1", "box": [1, 1, 20, 10]}]},
                {"text": "print(x)", "words": [{"text": "print(x)", "box": [1, 20, 40, 10]}]}]}
        if empty_secondary and language == "en-US":
            value["lines"] = []
        module.knowledge.atomic_json(output, value)
        return .05

    result = module.prepare(root, video, frames, tokenizer, recognize=recognize)
    return (*module.load(root, result["job"]), result["job"], recognize, cpu_calls)


def reading(temperature=54):
    return {"temperature_c": temperature, "power_limit_w": 180, "power_w": 100, "free_gib": {"C": 40, "D": 40}}


def correction():
    return {"edits": [{"line": "L0000", "text": "x = 1"}], "omissions": [], "diagram_relations": "", "uncertainties": [],
        "complete_visible_text": True, "complete_visible_diagrams": True}


def backend(monkeypatch, *, negative_correction=None, negative_review=None, fail=None):
    calls = []

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, *, json):
            request = json
            with Image.open(io.BytesIO(base64.b64decode(request["messages"][-1]["images"][0]))) as image:
                frame = image.getpixel((0, 0))[0]
            role = "correct" if "edits" in request["format"]["properties"] else "review"
            calls.append((frame, role))
            if frame == fail:
                return SimpleNamespace(status_code=503, content=b"synthetic unavailable",
                    raise_for_status=lambda: (_ for _ in ()).throw(RuntimeError("synthetic_http_failure")))
            if role == "correct":
                value = correction()
                if frame == negative_correction:
                    value.update(complete_visible_text=False, uncertainties=["synthetic unreadable area"])
            else:
                value = {"checks": {key: True for key in module.CHECKS}, "reason": "synthetic separate pixel review"}
                if frame == negative_review:
                    value["checks"]["no_unsupported_text"] = False
            response = {"done": True, "done_reason": "stop", "eval_count": 50, "prompt_eval_count": 200,
                "total_duration": 2_000_000_000, "eval_duration": 1_000_000_000,
                "message": {"content": __import__("json").dumps(value)}}
            return SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: response)

        def get(self, url):
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"models": [{
                "digest": "synthetic_installed_vision", "context_length": module.OPTIONS["num_ctx"], "size_vram": 12345}]})

    monkeypatch.setattr(module.httpx, "Client", Client)
    return calls


def test_full_ocr_and_source_pixels_are_preserved_and_secondary_can_be_empty(tmp_path, monkeypatch):
    root, folder, recipe, contexts, key, _, cpu_calls = prepared(tmp_path, monkeypatch, empty_secondary=True)
    assert len(cpu_calls) == 6 and len(contexts) == 3
    assert all(not c["secondary_lines"] and len(c["primary_lines"]) == 2 for c in contexts.values())
    request = module.body(folder, "17", module.CORRECT, contexts["17"], module.schema_for(contexts["17"]))
    assert len(request["messages"][-1]["images"]) == 1
    assert base64.b64decode(request["messages"][-1]["images"][0]) == (folder / "17.png").read_bytes()
    assert recipe["targets"]["17"]["frame"]["status"] == "pending"
    module.load(root, key)


def test_changed_lines_are_unique_and_unmodified_text_indentation_are_not_lost():
    context = {"primary_lines": {"L0000": {"text": "  original", "box": [0, 0, 100, 100]},
        "L0001": {"text": "keep me", "box": [0, 100, 100, 200]}}}
    value = correction()
    value["edits"] = [{"line": "L0000", "text": "    corrected"}]
    result = module.reconstruction(context, value)
    assert result["primary_lines"][0]["text"] == "    corrected" and result["primary_lines"][1]["text"] == "keep me"
    value["edits"].append(value["edits"][0])
    with pytest.raises(ValueError, match="unique_changed_line"):
        module.reconstruction(context, value)


def test_successful_checks_apply_only_frames_and_keep_source_failure(tmp_path, monkeypatch):
    root, folder, recipe, _, key, _, _ = prepared(tmp_path, monkeypatch)
    calls = backend(monkeypatch)
    module.worker(root, key)
    checked = module.verify(root, key)
    assert len(calls) == 6 and checked["summary"]["supported"] == 3 and checked["summary"]["model_calls"] == 0
    result = module.apply(root, key)
    assert result["applied_frames"] == 3 and result["source_failure_unchanged"]
    with ReadOnlyArchive(root).connect() as db:
        assert db.execute("SELECT error FROM videos WHERE id=?", (recipe["video"],)).fetchone()[0] == "synthetic_original_source_failure"
        assert db.execute("SELECT COUNT(*) FROM frames WHERE video=? AND status='ready'", (recipe["video"],)).fetchone()[0] == 3
        assert db.execute("SELECT COUNT(*) FROM events WHERE kind='frame_assisted_pixel_ocr_applied'").fetchone()[0] == 3
    with pytest.raises(ValueError, match="apply_checked_frames_once"):
        module.apply(root, key)


def test_negative_and_failed_frames_remain_pending_without_refreshing_roles(tmp_path, monkeypatch):
    root, folder, recipe, _, key, _, _ = prepared(tmp_path, monkeypatch)
    calls = backend(monkeypatch, negative_correction=17, negative_review=18, fail=19)
    module.worker(root, key)
    result = module.verify(root, key)
    assert result["summary"]["supported"] == 0 and result["summary"]["unresolved"] == 3
    assert calls == [(17, "correct"), (18, "correct"), (18, "review"), (19, "correct")]
    assert (folder / "19-correct-http-failure.json").is_file() and (folder / "19-failure.json").is_file()
    module.apply(root, key)
    with ReadOnlyArchive(root).connect() as db:
        assert db.execute("SELECT COUNT(*) FROM frames WHERE video=? AND status='pending'", (recipe["video"],)).fetchone()[0] == 3
    with pytest.raises(ValueError, match="worker_once_without_retry"):
        module.worker(root, key)
    assert len(calls) == 4


def test_read_only_reconstruction_never_calls_model_or_writes_receipts(tmp_path, monkeypatch):
    root, folder, _, _, key, _, _ = prepared(tmp_path, monkeypatch)
    backend(monkeypatch)
    module.worker(root, key)
    files = {p: p.read_bytes() for p in folder.iterdir() if p.is_file()}
    monkeypatch.setattr(module.shared, "local_model_digest", lambda _: pytest.fail("read-only model lookup"))
    monkeypatch.setattr(module.httpx, "Client", lambda **_: pytest.fail("read-only inference"))
    checked = module.verify(root, key)
    assert checked["summary"]["supported"] == 3 and all(p.read_bytes() == raw for p, raw in files.items())


@pytest.mark.parametrize("change", ["native_image", "source_image", "ocr_receipt", "model_receipt"])
def test_changed_actual_inputs_and_receipts_refuse_verification(tmp_path, monkeypatch, change):
    root, folder, recipe, _, key, _, _ = prepared(tmp_path, monkeypatch)
    backend(monkeypatch)
    module.worker(root, key)
    if change == "native_image":
        path = folder / "17.png"
    elif change == "source_image":
        path = root / recipe["targets"]["17"]["frame"]["path"]
    else:
        path = folder / ("17-zh-Hans-CN.json" if change == "ocr_receipt" else "17-review-receipt.json")
    path.write_bytes(b"changed synthetic input")
    with pytest.raises(ValueError, match="requires_assisted_frame_pilot_"):
        module.verify(root, key)


@pytest.mark.parametrize("bad", ["cpu", "context", "truncated", "wire", "input_tokens"])
def test_incomplete_context_cpu_and_modified_requests_cannot_be_accepted(tmp_path, monkeypatch, bad):
    root, folder, recipe, _, key, _, _ = prepared(tmp_path, monkeypatch)
    backend(monkeypatch)
    module.worker(root, key)
    value = module.whole.read(folder / "17-correct-receipt.json")
    _, _, _, contexts = module.load(root, key)
    request = module.body(folder, "17", module.CORRECT, contexts["17"], module.schema_for(contexts["17"]))
    if bad == "cpu":
        value["loaded_model"]["size_vram"] = 0
    elif bad == "context":
        value["loaded_model"]["context_length"] = 8192
    elif bad == "truncated":
        value["response"]["done_reason"] = "length"
    elif bad == "wire":
        request["messages"][-1]["images"] = ["changed"]
    else:
        value["text_tokens"] += 1
    tokenizer = Tokenizer.from_file(str(root / recipe["tokenizer"]))
    with pytest.raises(ValueError, match="requires_assisted_frame_pilot_"):
        module.checked_response(value, request, recipe, tokenizer)


def test_previously_registered_frame_cannot_get_a_new_budget(tmp_path, monkeypatch):
    root, _, recipe, _, _, recognize, cpu_calls = prepared(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="registered_once_no_budget_refresh"):
        module.prepare(root, recipe["video"], [17, 18, 19], root / recipe["tokenizer"], recognize=recognize)
    assert len(cpu_calls) == 6


def test_failed_native_prepare_keeps_registration_and_sanitized_failure(tmp_path, monkeypatch):
    original_lines = module.lines
    monkeypatch.setattr(module, "lines", lambda *_args, **_kw: (_ for _ in ()).throw(ValueError("synthetic private OCR material")))
    with pytest.raises(ValueError):
        prepared(tmp_path, monkeypatch)
    root = tmp_path / "archive"
    jobs = list((root / "wiki/frame-assisted-pilots").iterdir())
    assert len(jobs) == 1
    folder = jobs[0]
    failure = module.whole.read(folder / "prepare-failure.json")
    assert "synthetic private OCR material" not in str(failure)
    assert len(list((root / "wiki/frame-assisted-registrations").glob("*.json"))) == 3
    assert not (folder / "contract.json").exists()
    monkeypatch.setattr(module, "lines", original_lines)
    with pytest.raises(ValueError, match="registered_once_no_budget_refresh"):
        module.prepare(root, "a" * 24, [17, 18, 19], root / "wiki/tokenizer.json",
            recognize=lambda *_: pytest.fail("failed preparation received new OCR budget"))


@pytest.mark.parametrize("reason", ["temperature", "disk", "interruption"])
def test_guard_preflight_failure_is_terminal_and_does_not_auto_resume(tmp_path, monkeypatch, reason):
    root, folder, _, _, key, _, _ = prepared(tmp_path, monkeypatch)

    def sample():
        if reason == "disk":
            raise RuntimeError("course_archive_32gib_reserve_reached")
        return reading(70)

    if reason == "interruption":
        monkeypatch.setattr(module.shared, "checkpoint", lambda _: (_ for _ in ()).throw(InterruptedError("synthetic private stop")))
    def launch(*_args, **_kwargs):
        pytest.fail("paused preflight launched worker")
    if reason == "temperature":
        result = module.guard(root, key, sample=sample, launch=launch)
        assert result["state"] == "assisted_pilot_paused_without_automatic_resume"
    else:
        with pytest.raises(InterruptedError if reason == "interruption" else RuntimeError):
            module.guard(root, key, sample=sample, launch=launch)
        assert (folder / "guard-preflight-failure.json").is_file()
    state = module.whole.read(folder / "guard-progress.json")
    assert state["state"] == "paused" and state["worker_launched"] is False
    with pytest.raises(ValueError, match="guard_once_without_retry"):
        module.guard(root, key, sample=lambda: reading(40), launch=launch)


@pytest.mark.parametrize("reason", ["temperature", "disk", "existing_pause"])
def test_guard_stops_only_owned_process_without_overwriting_existing_guard_state(tmp_path, monkeypatch, reason):
    root, folder, _, _, key, _, _ = prepared(tmp_path, monkeypatch)
    existing_guard = root / "extraction-thermal-guard.json"
    existing_guard.write_bytes(b"synthetic existing healthy guard")
    readings, stopped = [], []

    def sample():
        readings.append(True)
        if len(readings) > 1 and reason == "disk":
            raise RuntimeError("course_archive_32gib_reserve_reached")
        return reading(70 if len(readings) > 1 and reason == "temperature" else 54)

    def launch(*args, **kwargs):
        if reason == "existing_pause":
            (root / "pause.flag").write_bytes(b"synthetic original pause")
        return SimpleNamespace(pid=123, poll=lambda: None, returncode=None)

    def stop(worker, task_root):
        stopped.append(worker.pid)
        (task_root / "pause.flag").write_bytes(b"synthetic stopper pause")

    monkeypatch.setattr(module, "verify", lambda *_: pytest.fail("paused pilot was verified"))
    if reason == "disk":
        with pytest.raises(RuntimeError, match="32gib"):
            module.guard(root, key, sample=sample, launch=launch, stopper=stop, sleep=lambda _: None)
    else:
        result = module.guard(root, key, sample=sample, launch=launch, stopper=stop, sleep=lambda _: None)
        assert result["state"] == "assisted_pilot_paused_without_automatic_resume"
    assert stopped == [123] and existing_guard.read_bytes() == b"synthetic existing healthy guard"
    if reason == "existing_pause":
        assert (root / "pause.flag").read_bytes() == b"synthetic original pause"
    assert module.whole.read(folder / "guard-progress.json")["state"] == "paused"


def incomplete_native_job(tmp_path, monkeypatch):
    from wechat_decrypt_tool import course_frame_assisted_native_repair as repair
    with pytest.raises(ValueError, match="installed_windows_ocr_success"):
        prepared(tmp_path, monkeypatch, fail_native=True)
    root = tmp_path / "archive"
    folder = next((root / "wiki/frame-assisted-pilots").iterdir())
    return repair, root, folder


def test_native_recovery_keeps_original_failure_registrations_and_gpu_budget(tmp_path, monkeypatch):
    repair, root, folder = incomplete_native_job(tmp_path, monkeypatch)
    failure = (folder / "prepare-failure.json").read_bytes()
    registrations = {p: p.read_bytes() for p in (root / "wiki/frame-assisted-registrations").glob("*.json")}
    monkeypatch.setattr(module.shared, "local_model_digest", lambda _: pytest.fail("recovery called model API"))
    cpu = []

    def recognize(image, output, language):
        cpu.append(language)
        module.knowledge.atomic_json(output, {"width": 64, "height": 64, "image_sha256": module.whole.sha(image.read_bytes()),
            "language": language, "gpu_calls": 0, "lines": [{"text": "synthetic", "words": [{"box": [1, 1, 20, 10]}]}]})
        return .02

    result = repair.repair(root, folder.name, recognize=recognize)
    assert result["original_failure_preserved"] and not result["gpu_role_budget_refreshed"]
    assert len(cpu) == 6 and result["model_calls"] == 0
    assert (folder / "prepare-failure.json").read_bytes() == failure
    assert all(p.read_bytes() == raw for p, raw in registrations.items())
    assert not (folder / "started.json").exists()
    module.load(root, folder.name)
    with pytest.raises(ValueError, match="native_recovery_once"):
        repair.repair(root, folder.name, recognize=recognize)
    assert len(cpu) == 6


@pytest.mark.parametrize("change", ["source_row", "source_pixels", "existing_receipt", "recovery_failure"])
def test_native_recovery_refuses_changed_inputs_existing_results_and_second_failure(tmp_path, monkeypatch, change):
    repair, root, folder = incomplete_native_job(tmp_path, monkeypatch)
    if change == "source_row":
        with module.knowledge.KnowledgeStore(root).write() as db:
            db.execute("UPDATE videos SET error='changed synthetic original' WHERE id=?", ("a" * 24,))
    elif change == "source_pixels":
        (root / "lessons" / ("a" * 24) / "frames/17.png").write_bytes(b"changed synthetic pixel file")
    elif change == "existing_receipt":
        (folder / "17-en-US.json").write_bytes(b"synthetic existing receipt")
    else:
        def fail(*_args):
            raise RuntimeError("synthetic private launcher failure")
        with pytest.raises(RuntimeError):
            repair.repair(root, folder.name, recognize=fail)
        assert (folder / "native-prepare-recovery-failure.json").is_file()
    with pytest.raises(ValueError, match="requires_assisted_frame_pilot_"):
        repair.repair(root, folder.name, recognize=lambda *_: pytest.fail("invalid recovery called OCR"))


def test_native_child_uses_only_windows_modules_and_keeps_parent_environment(tmp_path, monkeypatch):
    from wechat_decrypt_tool import course_frame_assisted_native_repair as repair
    monkeypatch.setenv("PSModulePath", "synthetic PowerShell7 module directories")
    output = tmp_path / "ocr.json"

    def run(args, **kwargs):
        assert kwargs["env"]["PSModulePath"] == str(__import__("pathlib").Path(args[0]).parent / "Modules")
        assert "PowerShell7" not in kwargs["env"]["PSModulePath"]
        assert "-ExecutionPolicy" not in args and args[1] == "-NoProfile"
        output.write_bytes(b"synthetic receipt")
        return SimpleNamespace(returncode=0, stdout=b"synthetic summary", stderr=b"")

    assert repair.windows_ocr(tmp_path / "image.png", output, "en-US", run=run) >= 0
    assert __import__("os").environ["PSModulePath"] == "synthetic PowerShell7 module directories"
    assert (tmp_path / "ocr-launcher.stdout.bin").read_bytes() == b"synthetic summary"
