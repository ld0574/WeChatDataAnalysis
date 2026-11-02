"""Versioned local text inference with measured GPU context and output headroom."""

from __future__ import annotations

from .course_knowledge import ModelOutputTruncatedError
from .course_pdf_ocr import local_model_digest

POLICY = "local-text-gpu-context16384-output4000-v1"
OPTIONS = {
    "temperature": 0,
    "presence_penalty": 0,
    "repeat_penalty": 1.05,
    "repeat_last_n": 256,
    "num_ctx": 16384,
    "num_predict": 4000,
    "num_gpu": 999,
}


def profile():
    return {"policy": POLICY, "options": dict(OPTIONS)}


def validate_metrics(metrics, model_digest):
    if (
        metrics.get("model_digest") != model_digest
        or metrics.get("done_reason") != "stop"
        or type(metrics.get("prompt_eval_count")) is not int
        or not 0
        < metrics["prompt_eval_count"]
        < OPTIONS["num_ctx"] - OPTIONS["num_predict"]
        or type(metrics.get("eval_count")) is not int
        or not 0 < metrics["eval_count"] <= OPTIONS["num_predict"]
        or metrics.get("context_length") != OPTIONS["num_ctx"]
        or type(metrics.get("size_vram")) is not int
        or metrics["size_vram"] <= 0
    ):
        raise ValueError("omissions_text_inference_budget_or_gpu_unproven")


def chat(model, messages, *, schema):
    import httpx

    weights = local_model_digest(model)
    with httpx.Client(trust_env=False, timeout=600) as client:
        response = client.post(
            "http://127.0.0.1:11434/api/chat",
            json={
                "model": model,
                "messages": messages,
                "stream": False,
                "think": False,
                "keep_alive": "5m",
                "format": schema,
                "options": dict(OPTIONS),
            },
        )
        response.raise_for_status()
        value = response.json()
        loaded = client.get("http://127.0.0.1:11434/api/ps")
        loaded.raise_for_status()
        owners = [
            row
            for row in loaded.json().get("models", [])
            if row.get("digest") == weights
        ]
    if local_model_digest(model) != weights:
        raise ValueError("local_model_revision_changed")
    if value.get("done_reason") != "stop":
        raise ModelOutputTruncatedError(value)
    if len(owners) != 1:
        raise ValueError("omissions_text_inference_gpu_owner_unproven")
    metrics = {
        key: value.get(key)
        for key in ("done_reason", "prompt_eval_count", "eval_count", "eval_duration")
    }
    metrics.update(
        model_digest=weights,
        context_length=owners[0].get("context_length"),
        size_vram=owners[0].get("size_vram"),
    )
    validate_metrics(metrics, weights)
    return value["message"]["content"], metrics
