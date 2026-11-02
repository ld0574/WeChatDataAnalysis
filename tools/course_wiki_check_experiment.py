"""One retained local diagnostic call; never publish or reset production jobs."""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path

import psutil
from jsonschema import Draft202012Validator

from wechat_decrypt_tool import course_wiki_check_constraints as constraints
from wechat_decrypt_tool import course_wiki_completeness as omissions
from wechat_decrypt_tool import course_wiki_review as review
from wechat_decrypt_tool.course_knowledge import (
    GpuBusyError,
    atomic_json,
    local_chat,
    schema_lease,
)
from wechat_decrypt_tool.course_library import canonical
from wechat_decrypt_tool.course_lesson_exports import digest
from wechat_decrypt_tool.course_pdf_ocr import local_model_digest
from wechat_decrypt_tool.course_video_visual_audit import ReadOnlyArchive
from wechat_decrypt_tool.course_wiki import Wiki


def run(
    root,
    kind,
    article_id,
    stage,
    model,
    wait_seconds=3600,
    baseline_job=None,
    schema_variant="evidence",
):
    if kind not in {"forward", "reverse"}:
        raise ValueError("experiment_unknown_kind")
    if type(wait_seconds) is not int or not 0 <= wait_seconds <= 3600:
        raise ValueError("experiment_invalid_wait")
    if schema_variant not in {"evidence", "bounded-reason-v2"}:
        raise ValueError("experiment_unknown_schema_variant")
    if schema_variant == "bounded-reason-v2" and kind != "forward":
        raise ValueError("experiment_bounded_reason_requires_forward")
    store = ReadOnlyArchive(root)
    article = Wiki(root).article(article_id)
    baseline_folder = frozen = manifest_raw = None
    if baseline_job is not None:
        if not re.fullmatch(r"[a-f0-9]{32}", baseline_job):
            raise ValueError("experiment_invalid_baseline_job")
        baseline_folder = (
            store.root
            / "wiki"
            / ("review-jobs" if kind == "forward" else "completeness-jobs")
            / baseline_job
        )
        manifest_raw = (baseline_folder / "manifest.json").read_bytes()
        manifest = json.loads(manifest_raw)
        frozen = manifest if kind == "forward" else manifest["recipe"]
    if kind == "forward":
        targets = dict(review.review_batches(article["article"], frozen or {}))[stage]
        sources = {
            ref: article["packet"]["sources"][ref]
            for target in targets
            for ref in target["citations"]
        }
        context = review.review_request(targets, sources)
        system, schema = review.SYSTEM, constraints.forward(context)
    else:
        context = dict(omissions.stages(article, per_source=True))[stage]
        system, schema = omissions.SYSTEM, constraints.reverse(context)
        from wechat_decrypt_tool.course_wiki_evidence import RANGE_NOTICE

        if article["packet"].get("evidence_partitions"):
            system += RANGE_NOTICE
    if schema_variant == "bounded-reason-v2":
        for branch in schema["properties"]["reviews"]["items"]["oneOf"]:
            branch["properties"]["reason"]["maxLength"] = 700
    Draft202012Validator.check_schema(schema)
    if len(canonical(context).decode()) > (13500 if kind == "forward" else 16000):
        raise ValueError("experiment_context_exceeds_existing_bound")
    weights = local_model_digest(model)
    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": canonical({**context, "previous_error": ""}).decode()
            if kind == "forward"
            else json.dumps(
                {**context, "previous_validation_failure": ""}, ensure_ascii=False
            ),
        },
    ]
    baseline = None
    if baseline_job is not None:
        first_attempt = baseline_folder / (
            stage + "-attempt-1.json"
            if kind == "forward"
            else "attempts/" + stage + "-01.json"
        )
        first_raw = first_attempt.read_bytes()
        first = json.loads(first_raw)
        frozen_article = (
            frozen["article_sha256"]
            if kind == "forward"
            else frozen["extra"]["article_sha256"]
        )
        if (
            frozen_article != digest(canonical(article))
            or frozen["model_digest"] != weights
            or first["request_sha256"] != digest(canonical(messages))
        ):
            raise ValueError("experiment_baseline_request_or_identity_changed")
        baseline = {
            "job": baseline_job,
            "manifest_sha256": digest(manifest_raw),
            "first_attempt_sha256": digest(first_raw),
            "request_sha256": first["request_sha256"],
        }
    recipe = {
        "policy": constraints.POLICY,
        "kind": kind,
        "article": article_id,
        "article_sha256": digest(canonical(article)),
        "stage": stage,
        "model": model,
        "model_digest": weights,
        "system": system,
        "schema": schema,
        "context": context,
        "messages": messages,
        "baseline": baseline,
        "logical_call_budget": 1,
        "transport_attempts_per_call_max": 3,
        **({"schema_variant": schema_variant} if schema_variant != "evidence" else {}),
    }
    key = digest(canonical(recipe))[:32]
    folder = store.root / "wiki" / "check-experiments" / key
    identity = {"pid": os.getpid(), "created": psutil.Process().create_time()}

    def progress(state, **extra):
        atomic_json(
            folder / "progress.json",
            {"state": state, "updated": time.time(), **identity, **extra},
        )

    with schema_lease(store.root / "wiki-check-experiment.lock", timeout=0):
        folder.mkdir(parents=True, exist_ok=True)
        if (folder / "attempt.json").exists():
            raise ValueError("experiment_existing_attempt_preserved_no_retry")
        if (folder / "recipe.json").exists() and json.loads(
            (folder / "recipe.json").read_bytes()
        ) != recipe:
            raise ValueError("experiment_recipe_changed")
        atomic_json(folder / "recipe.json", recipe)
        raw = ""
        started = False
        progress("waiting_gpu")
        try:
            with review.slot(store, time.monotonic() + wait_seconds, progress):
                if local_model_digest(model) != weights:
                    raise ValueError("experiment_model_changed")
                atomic_json(
                    folder / "attempt.json",
                    {**identity, "state": "started", "updated": time.time()},
                )
                started = True
                raw = local_chat(model, messages, schema=schema)
                if local_model_digest(model) != weights:
                    raise ValueError("experiment_model_changed")
            data = json.loads(raw)
            Draft202012Validator(schema).validate(data)
            if kind == "forward":
                resolved = review.resolve_witnesses(data, context["witnesses"])
                review.validate(resolved, targets, sources)
            else:
                resolved = data
                omissions.validate(resolved, context)
            if (
                digest(canonical(Wiki(root).article(article_id)))
                != recipe["article_sha256"]
            ):
                raise ValueError("experiment_article_changed")
            atomic_json(
                folder / "attempt.json",
                {
                    "state": "accepted_by_existing_validators",
                    "raw": raw,
                    "data": resolved,
                    "updated": time.time(),
                },
            )
            verdict_key = "verdict" if kind == "forward" else "coverage"
            counts = {}
            for row in data["reviews"]:
                verdict = row[verdict_key]
                counts[verdict] = counts.get(verdict, 0) + 1
            receipt = {
                "state": "bounded_grammar_experiment_passed_structure_only",
                "job": key,
                "kind": kind,
                "article": article_id,
                "stage": stage,
                "summary": counts,
                "recipe_sha256": digest(canonical(recipe)),
                "raw_sha256": digest(raw.encode()),
                "semantic_accuracy_verified": False,
                "source_deletion_allowed": False,
                "updated": time.time(),
            }
            atomic_json(folder / "validation.json", receipt)
            progress("complete")
            return receipt
        except Exception as exc:
            if not started and isinstance(exc, (GpuBusyError, InterruptedError)):
                progress(
                    "deferred_without_model_attempt", error_type=type(exc).__name__
                )
                return {"state": "deferred", "job": key, "kind": kind}
            retained = getattr(exc, "local_output", raw)
            atomic_json(
                folder / "attempt.json",
                {
                    "state": "failed_no_automatic_retry",
                    "raw": retained,
                    "error_type": type(exc).__name__,
                    "error_sha256": digest(str(exc).encode()),
                    "updated": time.time(),
                },
            )
            progress("failed", error_type=type(exc).__name__)
            return {
                "state": "failed",
                "job": key,
                "kind": kind,
                "error_type": type(exc).__name__,
            }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--kind", choices=["forward", "reverse"], required=True)
    parser.add_argument("--article", required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--model", default="qwen3.5:9b")
    parser.add_argument("--baseline-job")
    parser.add_argument(
        "--schema-variant",
        choices=["evidence", "bounded-reason-v2"],
        default="evidence",
    )
    args = parser.parse_args()
    result = run(
        args.root,
        args.kind,
        args.article,
        args.stage,
        args.model,
        baseline_job=args.baseline_job,
        schema_variant=args.schema_variant,
    )
    print(json.dumps(result))
    raise SystemExit(
        0
        if result["state"] == "bounded_grammar_experiment_passed_structure_only"
        else 2
    )


if __name__ == "__main__":
    main()
