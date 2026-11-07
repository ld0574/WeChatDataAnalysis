import json
import socket
from types import SimpleNamespace

from PIL import Image
import pytest

from tools import course_specialized_ocr as module


def inputs(tmp_path, monkeypatch):
    models = tmp_path / "models"
    models.mkdir()
    weights = {}
    for task in ("det", "rec"):
        name, raw = task + ".onnx", (task + " synthetic weights").encode()
        (models / name).write_bytes(raw)
        weights[task] = (name, module.sha(raw))
    monkeypatch.setattr(module, "MODELS", weights)
    monkeypatch.setattr(module.importlib.metadata, "version", lambda n: {"rapidocr": "3.10.0", "onnxruntime": "1.28.0"}[n])
    pictures = []
    for index in range(2):
        picture = tmp_path / f"{index}.png"
        Image.new("RGB", (64, 64), (100, 50, index)).save(picture)
        pictures.append(picture)
    return models, pictures


def factory(*, params, providers=None):
    assert not params["EngineConfig.onnxruntime.use_cuda"]
    assert params["EngineConfig.onnxruntime.intra_op_num_threads"] == 2
    assert params["Global.text_score"] == 0
    session = SimpleNamespace(session=SimpleNamespace(get_providers=lambda: providers or ["CPUExecutionProvider"]))

    class Engine:
        text_det = text_rec = SimpleNamespace(session=session)

        def __call__(self, _picture):
            return SimpleNamespace(boxes=[[[1, 1], [20, 1], [20, 15], [1, 15]]], txts=["synthetic private OCR"], scores=[.01])

    return Engine()


def test_network_denied_restores_both_socket_methods_on_success_and_failure():
    connect, connect_ex = socket.socket.connect, socket.socket.connect_ex
    with module.network_denied(), socket.socket() as stream:
        with pytest.raises(RuntimeError, match="network_forbidden"):
            stream.connect(("127.0.0.1", 1))
        with pytest.raises(RuntimeError, match="network_forbidden"):
            stream.connect_ex(("127.0.0.1", 1))
    assert socket.socket.connect is connect and socket.socket.connect_ex is connect_ex


def test_all_returned_text_boxes_and_low_scores_are_private_without_publication(tmp_path, monkeypatch):
    models, pictures = inputs(tmp_path, monkeypatch)
    output = tmp_path / "drafts"
    result = module.recognize(models, output, pictures, factory=factory)
    assert result["images"] == 2 and result["lines"] == [1, 1] and result["gpu_calls"] == 0
    assert "synthetic private OCR" not in json.dumps(result)
    assert not result["semantic_acceptance"] and not result["reader_publication_allowed"]
    value = json.loads((output / "00.json").read_bytes())
    assert value["lines"][0]["text"] == "synthetic private OCR" and value["lines"][0]["confidence"] == .01
    assert value["image"]["sha256"] == module.sha(pictures[0].read_bytes())
    with pytest.raises(ValueError, match="finite_once_only"):
        module.recognize(models, output, pictures, factory=lambda **_: pytest.fail("draft was rerun"))


@pytest.mark.parametrize("change", ["bad_weights", "duplicate_image", "network_attempt", "wrong_provider", "input_changed_during_inference"])
def test_preflight_and_runtime_failures_are_not_accepted_or_retried(tmp_path, monkeypatch, change):
    models, pictures = inputs(tmp_path, monkeypatch)
    output = tmp_path / "drafts"
    selected = factory
    if change == "bad_weights":
        (models / "det.onnx").write_bytes(b"changed weights")
    elif change == "duplicate_image":
        pictures[1].write_bytes(pictures[0].read_bytes())
    elif change == "network_attempt":
        def selected(**_kwargs):
            with socket.socket() as stream:
                stream.connect(("127.0.0.1", 1))
    elif change == "wrong_provider":
        def selected(**kwargs):
            return factory(**kwargs, providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
    else:
        def selected(**kwargs):
            engine = factory(**kwargs)

            class MutatingEngine:
                def __call__(self, picture):
                    result = engine(picture)
                    __import__("pathlib").Path(picture).write_bytes(b"changed synthetic image")
                    return result

            return MutatingEngine()
    with pytest.raises((ValueError, RuntimeError)):
        module.recognize(models, output, pictures, factory=selected)
    assert not (output / "result.json").exists()
    if output.exists():
        assert (output / "failure.json").is_file()
        with pytest.raises(ValueError, match="finite_once_only"):
            module.recognize(models, output, pictures, factory=factory)


def test_out_of_bounds_and_partial_line_sets_refuse_receipts():
    value = SimpleNamespace(boxes=[[[1, 1], [65, 1], [65, 15], [1, 15]]], txts=["synthetic"], scores=[.99])
    with pytest.raises(ValueError, match="bounded_original"):
        module.rows(value, 64, 64)
    value.boxes = []
    with pytest.raises(ValueError, match="all_returned_lines"):
        module.rows(value, 64, 64)
    value.boxes = value.txts = value.scores = None
    assert module.rows(value, 64, 64) == []


def test_failure_metadata_never_exposes_private_exception_text():
    result = module.failure_status(ValueError("synthetic private OCR contents"))
    assert result["reason_code"] is None and "synthetic private OCR contents" not in json.dumps(result)
    assert result["error_sha256"] == module.sha(b"synthetic private OCR contents")
