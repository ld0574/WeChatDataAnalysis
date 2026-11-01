import base64
import copy
import io
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from PIL import Image

from wechat_decrypt_tool import course_wiki_visual_review as visual
from wechat_decrypt_tool.course_knowledge import GpuBusyError, atomic_json
from wechat_decrypt_tool.course_lesson_exports import digest


def judgment(target="section-0"):
    return {
        "target": target,
        "relevance": "partial",
        "caption_support": "supported",
        "reason": "Synthetic figure supports only the depicted condition.",
        "observations": [
            {
                "description": "Synthetic visible box.",
                "visible_text": "",
                "box": [0, 0, 1000, 1000],
            }
        ],
        "missing_visual_information": ["Synthetic condition not depicted."],
        "limitations": ["This is an unverified local model observation."],
    }


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    published = {"article": {"sections": []}, "packet": {"sources": {}}}
    originals = {}
    folder = tmp_path / "library/media"
    folder.mkdir(parents=True)
    for i, color in enumerate(("red", "blue")):
        output = io.BytesIO()
        Image.new("RGB", (64, 32), color=color).save(output, format="JPEG")
        raw = output.getvalue()
        sha = digest(raw)
        (folder / (sha + ".jpg")).write_bytes(raw)
        source = f"frame-{i}"
        published["packet"]["sources"][source] = {
            "kind": "frame",
            "image_sha256": sha,
            "text": f"Synthetic OCR description {i}",
        }
        published["article"]["sections"].append(
            {
                "heading": f"Synthetic chapter {i}",
                "paragraphs": [
                    {
                        "text": f"Complete synthetic explanation {i}",
                        "citations": [source],
                    }
                ],
                "image": source,
                "image_caption": f"Synthetic caption {i}",
                "code": "",
            }
        )
        originals[f"section-{i}"] = raw
    monkeypatch.setattr(
        visual,
        "Wiki",
        lambda root: SimpleNamespace(article=lambda key: copy.deepcopy(published)),
    )
    monkeypatch.setattr(
        visual, "local_model_digest", lambda model: "fixed-local-weights"
    )

    @contextmanager
    def slot(*args):
        yield

    monkeypatch.setattr(visual, "slot", slot)
    calls = []

    def chat(model, messages, *, schema):
        context = json.loads(messages[1]["content"])
        target = context["target"]
        assert base64.b64decode(messages[1]["images"][0]) == originals[target]
        assert (
            context["paragraphs"]
            == published["article"]["sections"][int(target[-1])]["paragraphs"]
        )
        assert schema["properties"]["target"]["enum"] == [target]
        calls.append(target)
        return json.dumps(judgment(target)), {
            "prompt_eval_count": 1000,
            "eval_count": 100,
            "context_length": 16384,
            "size_vram": 8 * 1024**2,
        }

    monkeypatch.setattr(visual, "vision_chat", chat)
    return SimpleNamespace(
        store=SimpleNamespace(root=tmp_path, checkpoint=lambda: None),
        published=published,
        originals=originals,
        calls=calls,
    )


def test_native_images_and_complete_chapters_are_checked_without_quality_claims(
    pipeline,
):
    p = pipeline
    result = visual.review(p.store, "a" * 32)
    assert p.calls == ["section-0", "section-1"]
    assert result["summary"]["relevance"]["partial"] == 2
    assert result["original_native_images_sent_to_local_model"]
    assert not result["image_relevance_verified"]
    assert not result["semantic_accuracy_verified"]
    assert not result["independent_semantic_review"]
    assert not result["source_deletion_allowed"]
    assert visual.report(p.store.root, "a" * 32, job=result["job"]) == result
    assert visual.review(p.store, "a" * 32) == result
    assert len(p.calls) == 2


def test_gpu_deferral_retains_completed_image_without_spending_new_attempt(
    pipeline, monkeypatch
):
    p = pipeline
    original = visual.vision_chat
    busy = [True]

    def defer(model, messages, **kwargs):
        if json.loads(messages[1]["content"])["target"] == "section-1" and busy:
            busy.pop()
            raise GpuBusyError("Synthetic GPU handoff")
        return original(model, messages, **kwargs)

    monkeypatch.setattr(visual, "vision_chat", defer)
    with pytest.raises(GpuBusyError):
        visual.review(p.store, "a" * 32)
    assert len(list(p.store.root.glob("wiki/visual-review-jobs/*/section-0.json"))) == 1
    assert not list(
        p.store.root.glob("wiki/visual-review-jobs/*/section-1.attempt-*.json")
    )
    visual.review(p.store, "a" * 32)
    assert p.calls == ["section-0", "section-1"]


def test_three_invalid_regions_exhaust_budget_across_restart(pipeline, monkeypatch):
    p = pipeline
    calls = []

    def invalid(model, messages, **kwargs):
        calls.append(True)
        value = judgment()
        value["observations"][0]["box"] = [4, 4, 4, 4]
        return json.dumps(value), {"prompt_eval_count": 1000}

    monkeypatch.setattr(visual, "vision_chat", invalid)
    for _ in range(2):
        with pytest.raises(ValueError, match="attempt_budget_exhausted"):
            visual.review(p.store, "a" * 32)
    assert len(calls) == 3
    assert (
        len(
            list(
                p.store.root.glob("wiki/visual-review-jobs/*/section-0.attempt-*.json")
            )
        )
        == 3
    )
    assert not list(p.store.root.glob("wiki/visual-review-jobs/*/receipt.json"))


@pytest.mark.parametrize(
    "box",
    [
        [1, 2, 1, 3],
        [-1, 0, 10, 10],
        [0, 0, 1001, 1000],
        [False, 0, 10, 10],
        [0, 0, 1.5, 2],
    ],
)
def test_invalid_visible_region_cannot_be_saved_as_visual_evidence(box):
    value = judgment()
    value["observations"][0]["box"] = box
    with pytest.raises(ValueError, match="invalid_region"):
        visual.validate(value, "section-0")


def test_support_requires_actual_visible_observation():
    value = judgment()
    value["observations"] = []
    with pytest.raises(ValueError, match="without_visible_observation"):
        visual.validate(value, "section-0")


@pytest.mark.parametrize(
    "change", ["article", "image", "result", "acceptance_flag", "prompt_metrics"]
)
def test_report_rejects_changed_inputs_or_false_acceptance(pipeline, change):
    p = pipeline
    result = visual.review(p.store, "a" * 32)
    folder = p.store.root / "wiki/visual-review-jobs" / result["job"]
    if change == "article":
        p.published["article"]["sections"][0]["image_caption"] += " changed"
    elif change == "image":
        next((p.store.root / "library/media").glob("*.jpg")).write_bytes(b"corrupt")
    elif change in ("result", "prompt_metrics"):
        path = folder / "section-0.json"
        stage = json.loads(path.read_bytes())
        if change == "result":
            stage["data"]["reason"] += " changed"
        else:
            stage["model_metrics"]["prompt_eval_count"] = 16000
        atomic_json(path, stage)
    else:
        result["image_relevance_verified"] = True
        atomic_json(folder / "receipt.json", result)
    with pytest.raises((ValueError, OSError)):
        visual.report(p.store.root, "a" * 32, job=result["job"])


def test_no_images_never_loads_model(pipeline, monkeypatch):
    p = pipeline
    for section in p.published["article"]["sections"]:
        section["image"] = ""
    monkeypatch.setattr(
        visual,
        "local_model_digest",
        lambda name: pytest.fail("No image must mean no model call"),
    )
    assert visual.review(p.store, "a" * 32)["state"] == "no_body_images"
    assert not p.calls


def test_oversized_complete_context_is_rejected_instead_of_truncated(pipeline):
    p = pipeline
    p.published["article"]["sections"][0]["paragraphs"][0]["text"] = "A" * 7000
    with pytest.raises(ValueError, match="complete_context_exceeds_limit"):
        visual.review(p.store, "a" * 32)
    assert not p.calls


def test_transport_uses_loopback_without_proxies_and_installed_weights(monkeypatch):
    import httpx

    checked = []

    def installed(model):
        checked.append(model)
        return "fixed-local-weights"

    monkeypatch.setattr(visual, "local_model_digest", installed)

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, *, json):
            assert url == "http://127.0.0.1:11434/api/chat"
            assert checked == ["local-model"]
            assert json["options"]["num_gpu"] == 999
            assert json["options"]["num_ctx"] == 16384
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {
                    "done_reason": "stop",
                    "message": {"content": "synthetic"},
                    "prompt_eval_count": 1000,
                },
            )

        def get(self, url):
            assert url == "http://127.0.0.1:11434/api/ps"
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {
                    "models": [
                        {
                            "digest": "fixed-local-weights",
                            "size_vram": 8 * 1024**2,
                            "context_length": 16384,
                        }
                    ]
                },
            )

    monkeypatch.setattr(httpx, "Client", Client)
    assert visual.vision_chat("local-model", [], schema={})[0] == "synthetic"
