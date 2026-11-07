"""Build bounded choice exercises from an authenticated unpublished manuscript.

Reuse the existing choice job identity and roles. Preparing or reviewing a pack
does not publish an article, practice pointer, or editorial acceptance.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

from . import course_textbook_practice_build as build
from . import course_textbook_whole_continuation as continuation
from .course_knowledge import atomic_json, schema_lease
from .course_library import canonical

POLICY = "authenticated-private-manuscript-choice-practice-v1"
CONTRACT = "private-choice-practice-execution.json"
whole = build.whole


def require(ok, code):
    if not ok:
        raise ValueError("requires_private_practice_" + code)


def digest(value):
    return whole.sha(canonical(value))


def retained(path, value, *, read_only=False):
    if path.exists():
        require(whole.read(path) == value, "immutable_private_artifact")
    else:
        require(not read_only, "required_existing_private_artifact")
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(path, value)


def manuscript(root, parent, tokenizer, adapter, section):
    require(re.fullmatch(r"[a-f0-9]{32}", parent) is not None, "parent_writer_id")
    if adapter == "original":
        result = continuation.snapshot.reconstruct(root, parent, tokenizer, complete=True)
    elif adapter == "continuation":
        result = continuation.reconstruct(root, parent, section, tokenizer, complete=True)
    elif adapter == "disposition":
        from . import course_textbook_whole_disposition_mapping as mapping

        with mapping.hooks():
            result = continuation.reconstruct(root, parent, section, tokenizer, complete=True)
    else:
        raise ValueError("requires_private_practice_known_manuscript_adapter")
    require(result["book"] is not None and result["summary"]["job"] == parent
        and result["summary"]["verified_units"] == result["summary"]["total_units"]
        and result["proof"]["article_sha256"] == digest(result["book"]), "complete_actual_manuscript")
    return result


def binding(result, job, adapter, section):
    return {"policy": POLICY, "parent_writer": result["summary"]["job"], "job": job.key,
        "article_sha256": digest(result["book"]), "parent_snapshot_sha256": digest(result["proof"]),
        "adapter": adapter, "section": section,
        "module_sha256": whole.sha(Path(__file__).read_bytes()),
        "choice_manifest_sha256": whole.sha((job.root / "manifest.json").read_bytes()),
        "original_stage_attempt_budget": 2, "maximum_semantic_repairs": 1,
        "original_budgets_reset": False, "reader_publication_allowed": False,
        "source_deletion_allowed": False}


def prepare(root, parent, tokenizer, adapter, section="section-0007", *, read_only=False):
    root = Path(root).resolve()
    result = manuscript(root, parent, tokenizer, adapter, section)
    contract = root / "wiki/textbook-whole-authoring-jobs" / parent / CONTRACT
    if read_only:
        old = whole.read(contract)
        require(re.fullmatch(r"[a-f0-9]{32}", old["job"]) is not None, "retained_choice_job_id")
        job = ReadOnlyJob(build.TextbookStore(root), result["book"], digest(result["book"]), tokenizer,
            root / "wiki/textbook-choice-quiz-jobs" / old["job"])
    else:
        job = build.Job(build.TextbookStore(root), result["book"], digest(result["book"]), tokenizer)
    value = binding(result, job, adapter, section)
    retained(contract, value, read_only=read_only)
    retained(job.root / "private-parent-snapshot.json", result["proof"], read_only=read_only)
    return result, job, contract


def stages(job, book):
    context = {"sections": build.practice.source_sections(book), "existing_exercises": book["quiz"]}
    # The old verifier used prefix globs, which overlap repair and repair-review.
    # Opt into its existing exact-role adapter without changing frozen modules.
    with continuation.exact_role_cache():
        raw = job.stage("choices-write", build.WRITE, build.MODEL_SCHEMA, context,
            lambda value: build.compile_candidate(value, book))
        value = build.compile_candidate(raw, book)

        def review(value, name):
            schema = build.review_schema(value)
            return job.stage(name, build.REVIEW, schema,
                {"sections": context["sections"], "candidate": build.review_candidate(value)},
                lambda row: build.practice.jsonschema.validate(row, schema))

        audit = review(value, "choices-review")
        writer, reviewer = "choices-write", "choices-review"
        if not all(row["passed"] for row in audit.values()):
            raw = job.stage("choices-repair", build.WRITE, build.MODEL_SCHEMA,
                {**context, "candidate": build.review_candidate(value), "review": audit},
                lambda row: build.compile_candidate(row, book))
            value = build.compile_candidate(raw, book)
            audit = review(value, "choices-repair-review")
            writer, reviewer = "choices-repair", "choices-repair-review"
    require(all(row["passed"] for row in audit.values()), "independent_review_not_passed")
    return raw, value, writer, reviewer


class ReadOnlyJob(build.Job):
    def __init__(self, store, book, revision, tokenizer, expected_root):
        self.expected_root = Path(expected_root).resolve()
        self.authenticated_stages = set()
        super().__init__(store, book, revision, tokenizer)

    @property
    def root(self):
        return self._root

    @root.setter
    def root(self, value):
        value = Path(value).resolve()
        require(value == self.expected_root and (value / "manifest.json").is_file(), "existing_exact_choice_manifest")
        self._root = value

    def stage(self, name, prompt, schema, context, validate, **kwargs):
        require((self.root / (name + ".json")).is_file(), "existing_stage_no_inference")
        model = "course-defiant:q4" if prompt == build.REVIEW else "qwen3.5:9b"
        previous = self.model, self.model_digest
        self.model, self.model_digest = model, self.models[model]
        try:
            actual_schema, binding_sha = self.stage_input(prompt, schema, context, **kwargs)
            value = super().stage(name, prompt, schema, context, validate, **kwargs)
            paths = sorted((self.root / "attempts").glob(name + "-[0-9][0-9].json"))
            require(0 < len(paths) <= self.max_attempts, "original_choice_attempt_budget")
            previous_error = ""
            receipts = [whole.read(path) for path in self.root.glob("response-*.json")]
            for number, path in enumerate(paths, 1):
                row = whole.read(path)
                messages = [{"role": "system", "content": prompt + self.range_notice},
                    {"role": "user", "content": json.dumps({**context, "previous_validation_failure": previous_error},
                        ensure_ascii=False)}]
                require(path.name == f"{name}-{number:02d}.json" and row["stage"] == name and row["attempt"] == number
                    and row["input_sha256"] == binding_sha and row["model_digest"] == self.models[model]
                    and row["request_sha256"] == digest(messages), "actual_choice_request_chain")
                if row.get("raw_response"):
                    whole.editor.e.verify_response(self.root, name, row)
                    require(row["model_metrics"]["model_digest"] == self.models[model], "actual_choice_gpu_model")
                    require(any(receipt["stage"] == name and receipt["request_sha256"] == row["request_sha256"]
                        and receipt.get("schema_sha256") == digest(actual_schema)
                        and receipt["response"]["message"]["content"] == row["raw_response"]
                        and receipt["metrics"] == row["model_metrics"] for receipt in receipts), "actual_choice_decoding_schema")
                else:
                    require(not row.get("accepted_structure") and row.get("error_type"), "recorded_failed_choice_attempt")
                require(bool(row.get("accepted_structure")) == (number == len(paths)), "one_terminal_accepted_choice_attempt")
                previous_error = row.get("validation_error", "")
            self.authenticated_stages.add(name)
            return value
        finally:
            self.model, self.model_digest = previous

    def progress(self, *args, **kwargs):
        pass

    def generate_response(self, *args, **kwargs):
        raise ValueError("requires_private_practice_inference_forbidden")

    def slot(self, *args, **kwargs):
        raise ValueError("requires_private_practice_gpu_acquisition_forbidden")


def result_for(parent, job, value):
    return {"state": "private_choice_practice_reviewed_not_published", "parent_writer": parent, "job": job.key,
        "article_sha256": job.whole_key, "questions": len(value["questions"]),
        "single": sum(row["type"] == "single" for row in value["questions"]),
        "multiple": sum(row["type"] == "multiple" for row in value["questions"]),
        "reader_publication_allowed": False, "source_deletion_allowed": False}


def artifacts(parent, job, raw, value, writer, reviewer, *, read_only=False):
    proof = {"format": build.POLICY, "job": job.key, "article_sha256": job.whole_key,
        "questions_sha256": digest(value["questions"]), "models": job.models,
        "stages": {name: whole.sha((job.root / (name + ".json")).read_bytes()) for name in [writer, reviewer]},
        "independent_review_passed": True}
    retained(job.root / "program-bound-candidate.json", {"model_stage": writer, "candidate": value,
        "source_binding": "complete_actual_article_sections", "original_model_payload_sha256": digest(raw)}, read_only=read_only)
    retained(job.root / "accepted-practice-proof.json", proof, read_only=read_only)
    private = {"format": "course-choice-practice-v1", "article_sha256": job.whole_key, "questions": value["questions"],
        "proof": {**proof, "proof_sha256": digest(proof)}}
    retained(job.root / "private-practice.json", private, read_only=read_only)
    result = result_for(parent, job, value)
    retained(job.root / "private-result.json", result, read_only=read_only)
    return result


def run(root, parent, tokenizer, adapter, section="section-0007", *, read_only=False):
    root = Path(root).resolve()
    store = build.TextbookStore(root)
    if not read_only:
        store.checkpoint()
        require(not (root / "supervisor-stop.flag").exists(), "supervisor_stop_preserved")
    with schema_lease(root / "wiki/choice-practice-worker.lock", timeout=0):
        result, job, contract = prepare(root, parent, tokenizer, adapter, section, read_only=read_only)
        try:
            raw, value, writer, reviewer = stages(job, result["book"])
            status = artifacts(parent, job, raw, value, writer, reviewer, read_only=read_only)
            if read_only:
                return {**status, "state": "private_choice_practice_reconstructed_without_inference",
                    "model_calls": 0, "authenticated_stages": len(job.authenticated_stages)}
            dest = Path.home() / "Documents/CourseKnowledgeBackups/private-choice-practice" / job.key
            for source in [*job.root.rglob("*.json"), contract, Path(__file__)]:
                if source.name != "progress.json":
                    relative = source.relative_to(job.root) if source.is_relative_to(job.root) else Path("inputs") / source.name
                    whole.backup(source, dest / relative)
            job.progress(status["state"], "complete")
            return status
        except Exception as exc:
            if not read_only:
                status = whole.failure_status(exc)
                job.progress(status["state"], job.current, **{k: v for k, v in status.items() if k != "state"})
            raise


def guard(root, parent, tokenizer, adapter, section):
    from .course_archive_gpu import checked_sample
    from .course_gpu_guard import run as guarded

    root = Path(root).resolve()
    build.TextbookStore(root).checkpoint()
    require(not (root / "supervisor-stop.flag").exists(), "supervisor_stop_preserved")
    with schema_lease(root / "wiki/private-choice-practice-dispatch.lock", timeout=0):
        path = root / "review-experiment-thermal-guard.json"
        if path.exists():
            previous = whole.read(path)
            require(not continuation.writing.identity_live(previous), "existing_review_guard_not_live")
            whole.backup(path, root / "wiki/private-choice-guard-history" / (whole.sha(path.read_bytes()) + ".json"))
        prepare(root, parent, tokenizer, adapter, section)
        guarded(root, [sys.executable, "-m", __spec__.name, "--root", str(root), "--parent", parent,
            "--tokenizer", str(Path(tokenizer).resolve()), "--adapter", adapter, "--section", section, "worker"],
            maximum_temperature=70, maximum_power=200, guard_name="review-experiment-thermal-guard",
            sampler=lambda: checked_sample(root))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--parent", required=True)
    p.add_argument("--tokenizer", type=Path, required=True)
    p.add_argument("--adapter", choices=["original", "continuation", "disposition"], required=True)
    p.add_argument("--section", default="section-0007")
    p.add_argument("command", choices=["prepare", "worker", "verify", "guard"])
    a = p.parse_args()
    try:
        if a.command == "prepare":
            result, job, _ = prepare(a.root, a.parent, a.tokenizer, a.adapter, a.section)
            status = {"state": "private_choice_inputs_prepared", "parent_writer": a.parent, "job": job.key,
                "article_sha256": digest(result["book"]), "article_sections": len(build.practice.source_sections(result["book"])),
                "inherited_issues": len(result["issues"]), "model_calls": 0, "reader_publication_allowed": False}
        elif a.command == "guard":
            guard(a.root, a.parent, a.tokenizer, a.adapter, a.section)
            status = {"state": "private_choice_guard_returned"}
        else:
            status = run(a.root, a.parent, a.tokenizer, a.adapter, a.section, read_only=a.command == "verify")
        print(json.dumps(status))
    except Exception as exc:
        print(json.dumps(whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
