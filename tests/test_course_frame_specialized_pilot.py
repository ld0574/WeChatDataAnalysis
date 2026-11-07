import base64
import io
import json
from types import SimpleNamespace

from PIL import Image
import pytest
from tokenizers import Tokenizer, models

from wechat_decrypt_tool import course_frame_specialized_pilot as m


def inputs(tmp_path, monkeypatch):
    root = tmp_path / "archive"
    video, frames = "a" * 24, [17, 18]
    store = m.knowledge.KnowledgeStore(root)
    receipts = []
    with store.write() as db:
        db.execute("INSERT INTO videos(id,path,title,status,error) VALUES(?,?,?,?,?)",
            (video, str(tmp_path / "synthetic.mp4"), "synthetic", "transcribed", "synthetic source failure"))
        for key in frames:
            path = root / "lessons" / video / "frames" / f"{key}.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (64, 64), (key, 20, 30)).save(path)
            db.execute("INSERT INTO frames(id,video,path) VALUES(?,?,?)", (key, video, str(path.relative_to(root))))
            receipts.append({"path": str(path), "sha256": m.whole.sha(path.read_bytes()), "width": 64, "height": 64})
    drafts = root / "wiki/drafts"
    drafts.mkdir(parents=True)
    tokenizer = root / "wiki/tokenizer.json"
    Tokenizer(models.WordLevel({"[UNK]": 0}, unk_token="[UNK]")).save(str(tokenizer))
    weights = {}
    for task in m.MODELS:
        path = drafts / (task + ".onnx")
        path.write_bytes(task.encode())
        weights[task] = m.whole.sha(path.read_bytes())
    monkeypatch.setattr(m, "MODELS", weights)
    m.knowledge.atomic_json(drafts / "started.json", {"policy": m.DRAFT_POLICY,
        "code_sha256": m.bindings()["course_specialized_ocr.py"], "network_connections_allowed": False,
        "inputs": receipts, "parameters": {task.capitalize() + ".model_path": str(drafts / (task + ".onnx")) for task in weights}})
    for index, image in enumerate(receipts):
        m.knowledge.atomic_json(drafts / f"{index:02d}.json", {"policy": m.DRAFT_POLICY, "image": image,
            "providers": {task: ["CPUExecutionProvider"] for task in weights}, "gpu_calls": 0,
            "semantic_acceptance": False, "reader_publication_allowed": False,
            "lines": [{"text": "synthetic = 1", "polygon": [[1, 2], [30, 2], [30, 20], [1, 20]], "confidence": .01}]})
    m.knowledge.atomic_json(drafts / "result.json", {"images": 2, "gpu_calls": 0})
    monkeypatch.setattr(m.shared, "checkpoint", lambda _: None)
    monkeypatch.setattr(m.shared, "local_model_digest", lambda _: "synthetic_local_vision")
    monkeypatch.setattr(m.shared, "live", lambda _: False)
    monkeypatch.setattr(m.pixel, "checked_sample", lambda _: reading())
    monkeypatch.setattr(m.time, "sleep", lambda _: None)
    return root, video, frames, drafts, tokenizer


def prepared(tmp_path, monkeypatch):
    args = inputs(tmp_path, monkeypatch)
    result = m.prepare(*args)
    return (*m.load(args[0], result["job"]), result["job"], args)


def reading(temperature=54):
    return {"temperature_c": temperature, "power_limit_w": 180, "power_w": 100}


def backend(monkeypatch, *, negative_correct=None, negative_review=None, fail=None):
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
                return SimpleNamespace(status_code=503, content=b"synthetic private service material",
                    raise_for_status=lambda: (_ for _ in ()).throw(RuntimeError("synthetic private service material")))
            if role == "correct":
                value = {"edits": [], "omissions": [], "diagram_relations": "", "uncertainties": [],
                    "complete_visible_text": frame != negative_correct, "complete_visible_diagrams": True}
            else:
                value = {"checks": {k: k != "no_unsupported_text" or frame != negative_review for k in m.pixel.CHECKS},
                    "reason": "synthetic separate pixel review"}
            response = {"done": True, "done_reason": "stop", "eval_count": 50, "prompt_eval_count": 200,
                "message": {"content": __import__("json").dumps(value)}}
            return SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: response)

        def get(self, url):
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"models": [{
                "digest": "synthetic_local_vision", "context_length": m.pixel.OPTIONS["num_ctx"], "size_vram": 12345}]})

    monkeypatch.setattr(m.httpx, "Client", Client)
    return calls


def test_real_polygons_and_all_low_confidence_lines_are_bound_without_fake_windows_receipts(tmp_path, monkeypatch):
    root, folder, recipe, contexts, key, _ = prepared(tmp_path, monkeypatch)
    row = contexts["17"]["primary_lines"]["L0000"]
    assert row["confidence"] == .01 and "polygon" in row and "words" not in row
    assert contexts["17"]["draft_engine"].startswith("PP-OCRv5")
    assert not recipe["database_write_allowed"] and not recipe["different_model_pixel_review"]
    assert not list(folder.glob("*-zh-Hans-CN.json"))
    calls = backend(monkeypatch)
    m.worker(root, key)
    result = m.verify(root, key)
    assert calls == [(17, "correct"), (17, "review"), (18, "correct"), (18, "review")]
    assert result["supported"] == 2 and result["database_writes"] == 0 and result["new_model_calls"] == 0
    assert m.shared.rows(root, recipe["video"], 17)[1]["status"] == "pending"


def test_negatives_are_not_retried_or_promoted_and_budget_cannot_be_refreshed(tmp_path, monkeypatch):
    root, folder, _, _, key, args = prepared(tmp_path, monkeypatch)
    calls = backend(monkeypatch, negative_correct=17, negative_review=18)
    m.worker(root, key)
    assert m.verify(root, key)["supported"] == 0
    original = (folder / "outcomes.json").read_bytes()
    with pytest.raises(ValueError, match="worker_once_without_retry"):
        m.worker(root, key)
    with pytest.raises(ValueError, match="new_frame_not_old_role_retry"):
        m.prepare(*args)
    assert len(calls) == 3 and (folder / "outcomes.json").read_bytes() == original


def test_http_failure_is_retained_and_stdout_stays_private(tmp_path, monkeypatch, capsys):
    root, folder, _, _, key, _ = prepared(tmp_path, monkeypatch)
    backend(monkeypatch, fail=17)
    m.worker(root, key)
    assert m.verify(root, key)["supported"] == 1
    failure = m.whole.read(folder / "17-failure.json")
    assert "synthetic private service material" not in json.dumps(failure)
    assert (folder / "17-correct-http-failure.json").is_file()
    assert not capsys.readouterr().out


@pytest.mark.parametrize("change", ["draft", "native_png", "original", "tokenizer", "model_receipt"])
def test_changed_evidence_is_refused(tmp_path, monkeypatch, change):
    root, folder, recipe, _, key, args = prepared(tmp_path, monkeypatch)
    backend(monkeypatch)
    m.worker(root, key)
    path = {"draft": args[3] / "00.json", "native_png": folder / "17.png",
        "original": root / recipe["targets"]["17"]["frame"]["path"], "tokenizer": args[4],
        "model_receipt": folder / "17-review-receipt.json"}[change]
    path.write_bytes(b"synthetic changed evidence")
    with pytest.raises(ValueError, match="requires_specialized_frame_"):
        m.verify(root, key)


def test_verify_is_read_only_and_uses_no_model_api(tmp_path, monkeypatch):
    root, folder, _, _, key, _ = prepared(tmp_path, monkeypatch)
    backend(monkeypatch)
    m.worker(root, key)
    before = {p: p.read_bytes() for p in folder.iterdir() if p.is_file()}
    monkeypatch.setattr(m.shared, "local_model_digest", lambda *_: pytest.fail("verification queried model"))
    monkeypatch.setattr(m.httpx, "Client", lambda **_: pytest.fail("verification called model"))
    assert m.verify(root, key)["supported"] == 2
    assert all(p.read_bytes() == raw for p, raw in before.items())


@pytest.mark.parametrize("registry", m.REGISTRIES[:-1])
def test_previous_gpu_frame_role_never_receives_new_budget(tmp_path, monkeypatch, registry):
    args = inputs(tmp_path, monkeypatch)
    root, video = args[:2]
    target = root / "wiki" / registry
    target.mkdir()
    record = target / f"{video}-17.json"
    record.write_bytes(b'{"job":"synthetic_old_failure"}')
    with pytest.raises(ValueError, match="new_frame_not_old_role_retry"):
        m.prepare(*args)
    assert record.read_bytes() == b'{"job":"synthetic_old_failure"}'
    assert not (root / "wiki/frame-specialized-pilots").exists()


def test_identical_pixel_alias_of_failed_frame_is_also_excluded(tmp_path, monkeypatch):
    args = inputs(tmp_path, monkeypatch)
    root, video = args[:2]
    _, frame = m.shared.rows(root, video, 17)
    alias = root / "lessons" / video / "frames/16.png"
    alias.write_bytes((root / frame["path"]).read_bytes())
    with m.knowledge.KnowledgeStore(root).write() as db:
        db.execute("INSERT INTO frames(id,video,path) VALUES(?,?,?)", (16, video, str(alias.relative_to(root))))
    registry = root / "wiki/frame-assisted-registrations"
    registry.mkdir()
    (registry / f"{video}-16.json").write_bytes(b'{}')
    with pytest.raises(ValueError, match="no_cache_or_attempted_pixel_alias"):
        m.prepare(*args)


@pytest.mark.parametrize("reason", ["temperature", "disk", "stop"])
def test_preflight_pause_is_terminal_and_never_launches_or_hot_resumes(tmp_path, monkeypatch, reason):
    root, folder, _, _, key, _ = prepared(tmp_path, monkeypatch)

    def sample():
        if reason == "disk":
            raise RuntimeError("course_archive_32gib_reserve_reached")
        return reading(70)

    if reason == "stop":
        monkeypatch.setattr(m.shared, "checkpoint", lambda _: (_ for _ in ()).throw(InterruptedError("synthetic stop")))
    kwargs = {"sample": sample, "launch": lambda *_a, **_k: pytest.fail("paused pilot launched worker")}
    if reason == "temperature":
        assert m.guard(root, key, **kwargs)["state"] == "paused_without_automatic_resume"
    else:
        with pytest.raises(InterruptedError if reason == "stop" else RuntimeError):
            m.guard(root, key, **kwargs)
    assert m.whole.read(folder / "guard-progress.json")["state"] == "paused"
    with pytest.raises(ValueError, match="guard_once_without_retry"):
        m.guard(root, key, sample=lambda: reading(40))


def test_live_control_markers_and_other_guards_are_preserved(tmp_path, monkeypatch):
    root, folder, _, _, key, _ = prepared(tmp_path, monkeypatch)
    other = root / "extraction-thermal-guard.json"
    other.write_bytes(b"synthetic other guard")
    stopped = []

    def launch(*_a, **_k):
        for name in ("pause.flag", "supervisor-stop.flag"):
            (root / name).write_bytes(b"synthetic original marker")
        return SimpleNamespace(pid=123, poll=lambda: None)

    def stop(worker, task_root):
        stopped.append(worker.pid)
        for name in ("pause.flag", "supervisor-stop.flag"):
            (task_root / name).write_bytes(b"synthetic replacement")

    result = m.guard(root, key, sample=reading, launch=launch, stopper=stop, sleep=lambda _: None)
    assert result["state"] == "paused_without_automatic_resume" and stopped == [123]
    assert other.read_bytes() == b"synthetic other guard"
    assert all((root / name).read_bytes() == b"synthetic original marker" for name in ("pause.flag", "supervisor-stop.flag"))
    assert len(list((folder / "retained-control-markers").iterdir())) == 2
