"""First complete source coverage for authenticated original corpus manuscripts.

Every original source and clause is retained. New parents have one immutable
coverage budget; existing coverage budgets are rejected before reconstruction.
Only first-use roles run, with complete boundary groups and independent checks.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import time
from types import SimpleNamespace

from tokenizers import Tokenizer

from . import course_textbook_corpus_index_review as reviewed
from . import course_textbook_corpus_unused_tail as second
from . import course_textbook_ordered_boundary_tail as boundaries
from .course_knowledge import atomic_json, schema_lease

coverage, whole, guard = boundaries.coverage, boundaries.whole, boundaries.ordered.private_guard
digest, retained = boundaries.digest, boundaries.assignments.retained
POLICY = "first-complete-corpus-manuscript-boundary-coverage-v1"
CONTRACT = "first-boundary-coverage-execution.json"
DERIVATION = "-first-boundary-derivation.json"


def require(ok, code):
    if not ok:
        raise ValueError("requires_first_boundary_coverage_" + code)


def bindings():
    return {Path(m.__file__).stem: whole.sha(Path(m.__file__).read_bytes())
        for m in (reviewed, second, boundaries, coverage, guard)} | {Path(__file__).stem: whole.sha(Path(__file__).read_bytes())}


def parent(root, key, kind, batch):
    require(kind in {"index-reviewed", "original-second-course"}, "known_complete_parent_provider")
    if kind == "index-reviewed":
        require(batch is None, "index_review_has_no_second_batch")
        result = reviewed.reconstruct(root, key)
    else:
        require(isinstance(batch, str) and re.fullmatch(r"[a-f0-9]{32}", batch), "original_second_batch_required")
        result = second.verify_writer(root, batch)
    require(result["summary"]["job"] == key and result["private"] is not None
        and result["summary"]["verified_units"] == result["summary"]["total_units"], "actual_complete_same_parent_manuscript")
    return result


def adapter(kind, batch):
    return "first-complete-boundary:" + kind + ":" + (batch or "none")


def folder_for(root, key):
    return root / "wiki/first-boundary-coverage-dispatch" / key


def predecessor(root, kind, key):
    require(kind in {"ordered-boundary-tail", "first-boundary"} and re.fullmatch(r"[a-f0-9]{32}", key), "known_predecessor")
    original = whole.read(root / "wiki/textbook-whole-authoring-jobs" / key / coverage.CONTRACT)
    job = original["job"]
    folder = boundaries.folder_for(root, job) if kind == "ordered-boundary-tail" else folder_for(root, job)
    contract = folder / (boundaries.CONTRACT if kind == "ordered-boundary-tail" else CONTRACT)
    execution = whole.read(contract)
    require(execution["policy"] == (boundaries.POLICY if kind == "ordered-boundary-tail" else POLICY)
        and execution["job"] == job and execution["parent_writer"] == key, "actual_predecessor_execution")
    worker = whole.read(root / "wiki/whole-manuscript-coverage-jobs" / job / "progress.json")
    protected = whole.read(folder / "guard-progress.json")
    require(coverage.writing.identity_live(worker) and coverage.writing.identity_live(protected)
        and protected["state"] == "running" and worker["state"] not in {"stopped", "paused"}, "exact_healthy_predecessor_worker_and_guard")
    return {"kind": kind, "parent_writer": key, "job": job,
        "worker": {k: worker[k] for k in ("pid", "created")}, "guard": {k: protected[k] for k in ("pid", "created")},
        "contract_path": contract.relative_to(root).as_posix(), "contract_sha256": whole.sha(contract.read_bytes()),
        "guard_directory": folder.relative_to(root).as_posix()}


class BoundaryJob(coverage.CoverageJob):
    read_only = False

    def __init__(self, *args):
        super().__init__(*args)
        self.authenticated_stages = set()

    def stage(self, name, prompt, schema, context, validate, **kwargs):
        require(re.fullmatch(r"batch-[0-9]{4}-body-[0-9]{4}-(review|groups-[0-9]{4}-verify)", name) is not None,
            "original_coverage_role_name")
        if self.read_only:
            require((self.root / (name + ".json")).is_file(), "existing_cache_without_inference")
        if name.endswith("-review"):
            require(prompt == coverage.REVIEW, "declared_proposal_role")
            ids, article_ids = list(context["clauses"]), list(context["article"])
            order = boundaries.assignments.by_source(ids, self.plan["units"])
            actual_context = {**context, "ordered_clause_ids": order, "boundaries_are_factual_evidence": False}
            actual_prompt = prompt + boundaries.NOTICE
            actual_schema = boundaries.wire_schema(ids, article_ids, self.plan["units"])
            with boundaries.assignments.prompt_alias("REVIEW", actual_prompt):
                raw = super().stage(name, actual_prompt, actual_schema, actual_context,
                    lambda row: boundaries.compile_wire(row, ids, article_ids, self.plan["units"]), **kwargs)
            value = boundaries.compile_wire(raw, ids, article_ids, self.plan["units"])
            artifact = {"policy": POLICY, "actual_payload_sha256": digest(raw), "compiled_candidate_sha256": digest(value),
                "ordered_clause_ids_sha256": digest(order), "actual_cache_sha256": whole.sha((self.root / (name + ".json")).read_bytes()),
                "every_clause_assigned_once": True, "program_unassessed_clauses": [], "semantic_acceptance": False}
            retained(self.root / (name + DERIVATION), artifact, read_only=self.read_only)
            validate(value)
        else:
            require(prompt == coverage.VERIFY, "declared_independent_role")
            proposal = name.split("-groups-")[0] + "-review"
            artifact = whole.read(self.root / (proposal + DERIVATION))
            actual_context = {**context, "candidate_derivation": {"artifact_sha256": digest(artifact),
                "program_unassessed_clauses": [], "is_factual_evidence": False}}
            actual_prompt = prompt + boundaries.VERIFY_NOTICE
            with boundaries.assignments.prompt_alias("VERIFY", actual_prompt):
                value = super().stage(name, actual_prompt, schema, actual_context, validate, **kwargs)
        self.authenticated_stages.add(name)
        return value

    def progress(self, state, stage, **extra):
        if self.read_only:
            self.current = stage
        else:
            super().progress(state, stage, **extra)

    def generate_response(self, *args, **kwargs):
        require(not self.read_only, "inference_forbidden_in_reconstruction")
        return super().generate_response(*args, **kwargs)

    def slot(self, *args, **kwargs):
        require(not self.read_only, "gpu_acquisition_forbidden_in_reconstruction")
        return super().slot(*args, **kwargs)


class ReadOnlyBoundaryJob(BoundaryJob):
    read_only = True

    def __init__(self, *args, expected_root):
        self.expected_root = Path(expected_root).resolve()
        super().__init__(*args)

    @property
    def root(self):
        return self._root

    @root.setter
    def root(self, value):
        value = Path(value).resolve()
        require(value == self.expected_root and (value / "manifest.json").is_file(), "existing_exact_original_manifest")
        self._root = value


def binding(job, manuscript, plan, tokenizer, kind, batch, wait_for):
    return {"policy": POLICY, "job": job.key, "parent_writer": manuscript["summary"]["job"], "provider": kind,
        "provider_batch": batch, "parent_snapshot_sha256": digest(manuscript["proof"]), "plan_sha256": digest(plan),
        "code": bindings(), "tokenizer_sha256": whole.sha(Path(tokenizer).read_bytes()), "models": job.models,
        "predecessor": wait_for, "original_stage_attempt_budget": 2, "semantic_repairs": 0,
        "original_budgets_reset": False, "reader_publication_allowed": False, "source_deletion_allowed": False}


def preflight(job, manuscript, plan):
    items = coverage.article_items(manuscript["private"], manuscript["inventory"])
    inputs = []
    for number, batch in enumerate(plan["batches"]):
        ids = batch["clauses"]
        for article_ids in batch["article_blocks"]:
            context = coverage.context_for(manuscript["inventory"], plan["units"], ids, items, article_ids)
            actual = {**context, "ordered_clause_ids": boundaries.assignments.by_source(ids, plan["units"]),
                "boundaries_are_factual_evidence": False, "previous_validation_failure": ""}
            messages = [{"role": "system", "content": coverage.REVIEW + boundaries.NOTICE},
                {"role": "user", "content": json.dumps(actual, ensure_ascii=False)}]
            count, output, context_size = job.budget(messages, f"batch-{number:04d}-body-0000-review")
            inputs.append((count, output, context_size))
    return {"complete_proposal_requests": len(inputs), "maximum_input_tokens": max(v[0] for v in inputs),
        "reserved_output": max(v[1] for v in inputs), "maximum_requested_context": max(v[2] for v in inputs),
        "full_originals_retained": True, "independent_requests_measured_at_execution": True, "model_calls": 0}


def prepare(root, key, tokenizer, *, kind="index-reviewed", batch=None, wait_kind=None, wait_parent=None):
    root = Path(root).resolve()
    require(re.fullmatch(r"[a-f0-9]{32}", key) is not None, "parent_writer_id")
    guard.checkpoint(root)
    contract_path = root / "wiki/textbook-whole-authoring-jobs" / key / coverage.CONTRACT
    require(not contract_path.exists(), "parent_has_no_previous_coverage_budget")
    with schema_lease(root / "wiki" / (key + "-whole-coverage-worker.lock"), timeout=0):
        manuscript = parent(root, key, kind, batch)
        plan = coverage.make_plan(manuscript["inventory"], manuscript["private"], manuscript["working"], Tokenizer.from_file(str(tokenizer)))
        job = BoundaryJob(coverage.writing.TextbookStore(root), manuscript, plan, tokenizer, adapter(kind, batch), "section-0000")
        require(not contract_path.exists() and not (job.root / "progress.json").exists()
            and not list((job.root / "attempts").glob("*.json")), "first_registration_before_all_original_roles")
        require((wait_kind is None) == (wait_parent is None), "complete_wait_descriptor")
        wait_for = None if wait_kind is None else predecessor(root, wait_kind, wait_parent)
        actual_preflight = preflight(job, manuscript, plan)
        contract = binding(job, manuscript, plan, tokenizer, kind, batch, wait_for)
        folder = folder_for(root, job.key)
        for path, value in [(contract_path, contract), (folder / CONTRACT, contract), (job.root / CONTRACT, contract),
                (job.root / "parent-snapshot.json", manuscript["proof"]), (job.root / "review-plan.json", plan),
                (folder / "complete-input-preflight.json", actual_preflight)]:
            retained(path, value)
        return {"state": "first_complete_corpus_boundary_coverage_prepared", "job": job.key, "parent_writer": key,
            "batches": len(plan["batches"]), "clauses": plan["clauses"], "sources": plan["sources"],
            "source_characters": plan["source_characters"], "inherited_manuscript_issues": len(manuscript["issues"]),
            "preflight": actual_preflight, "waits_for_exact_existing_job": wait_for is not None,
            "model_calls": 0, "reader_publication_allowed": False}


def load(root, key, tokenizer, *, read_only=True):
    root = Path(root).resolve()
    require(re.fullmatch(r"[a-f0-9]{32}", key) is not None, "parent_writer_id")
    contract = whole.read(root / "wiki/textbook-whole-authoring-jobs" / key / coverage.CONTRACT)
    require(contract["policy"] == POLICY and contract["code"] == bindings() and contract["parent_writer"] == key,
        "same_frozen_first_coverage_execution")
    kind, batch = contract["provider"], contract["provider_batch"]
    manuscript = parent(root, key, kind, batch)
    directory = root / "wiki/whole-manuscript-coverage-jobs" / contract["job"]
    plan = whole.read(directory / "review-plan.json")
    require(plan == coverage.make_plan(manuscript["inventory"], manuscript["private"], manuscript["working"], Tokenizer.from_file(str(tokenizer)))
        and whole.read(directory / "parent-snapshot.json") == manuscript["proof"], "actual_complete_original_plan")
    cls, options = (ReadOnlyBoundaryJob, {"expected_root": directory}) if read_only else (BoundaryJob, {})
    job = cls(coverage.writing.TextbookStore(root), manuscript, plan, tokenizer, adapter(kind, batch), "section-0000", **options)
    folder = folder_for(root, job.key)
    require(binding(job, manuscript, plan, tokenizer, kind, batch, contract["predecessor"]) == contract
        == whole.read(folder / CONTRACT) == whole.read(job.root / CONTRACT), "single_original_parent_budget")
    if contract["predecessor"] is not None:
        p = contract["predecessor"]
        require(whole.sha((root / p["contract_path"]).read_bytes()) == p["contract_sha256"], "unchanged_existing_predecessor_execution")
    return SimpleNamespace(root=root, manuscript=manuscript, plan=plan, job=job, folder=folder, contract=contract, tokenizer=tokenizer)


def unused(bundle):
    require(not (bundle.job.root / "progress.json").exists() and not list((bundle.job.root / "attempts").glob("*.json"))
        and not list(bundle.job.root.glob("batch-*.json")), "all_original_first_use_roles_unused")


def wait(bundle, *, live=coverage.writing.identity_live, sleep=time.sleep):
    p = bundle.contract["predecessor"]
    if p is None:
        return None
    while live(p["worker"]) or live(p["guard"]):
        guard.checkpoint(bundle.root)
        sleep(15)
    guard.checkpoint(bundle.root)
    status = whole.read(bundle.root / "wiki/whole-manuscript-coverage-jobs" / p["job"] / "progress.json")
    protected = whole.read(bundle.root / p["guard_directory"] / "guard-progress.json")
    require(all(status[k] == v for k, v in p["worker"].items())
        and all(protected[k] == v for k, v in p["guard"].items())
        and protected["state"] == "worker_exited" and protected.get("returncode") == 0
        and status["state"] in {"automatic_whole_source_coverage_complete", "whole_manuscript_requires_coverage_or_classification_repair"}
        and status["completed_batches"] == status["total_batches"], "actual_complete_exited_predecessor_not_failed_or_paused")
    require(whole.sha((bundle.root / p["contract_path"]).read_bytes()) == p["contract_sha256"], "same_predecessor_contract_after_wait")
    verifier = boundaries.verify if p["kind"] == "ordered-boundary-tail" else verify
    result = verifier(bundle.root, p["parent_writer"], bundle.tokenizer)
    require(result["job"] == p["job"] and result["model_calls"] == 0, "actual_readonly_complete_predecessor_verification")
    retained(bundle.folder / "predecessor-complete-proof.json", result)
    return result


def backup(bundle):
    paths = [p for p in bundle.job.root.rglob("*.json") if p.name != "progress.json"]
    paths.extend(p for p in bundle.folder.glob("*.json") if p.name not in {"guard-progress.json", "predecessor-wait-progress.json"})
    paths.extend([Path(__file__), Path(boundaries.__file__)])
    coverage.backup(bundle.job, paths, "first-boundary-immutable-evidence")


def run(root, key, tokenizer):
    root = Path(root).resolve()
    guard.checkpoint(root)
    with schema_lease(root / "wiki" / (key + "-whole-coverage-worker.lock"), timeout=0):
        bundle = load(root, key, tokenizer, read_only=False)
        unused(bundle)
        require(not (bundle.folder / "worker-started.json").exists(), "worker_once_without_automatic_retry")
        if bundle.contract["predecessor"] is not None:
            require((bundle.folder / "predecessor-complete-proof.json").is_file(), "predecessor_verified_before_worker")
        retained(bundle.folder / "worker-started.json", bundle.job.identity)
        job, states = bundle.job, {}
        items = coverage.article_items(bundle.manuscript["private"], bundle.manuscript["inventory"])
        try:
            backup(bundle)
            for number, batch in enumerate(bundle.plan["batches"]):
                rows = coverage.review_batch(job, bundle.manuscript["inventory"], bundle.plan, items, batch, number)
                retained(job.root / f"batch-{number:04d}-result.json", rows)
                states.update(rows)
                job.completed_batches += 1
                job.completed_clauses += len(rows)
                job.progress("reviewing_all_original_corpus_clauses", f"batch-{number:04d}")
                backup(bundle)
            result = coverage.compile_result(bundle.manuscript, bundle.plan, job, states)
            retained(job.root / "coverage-result.json", result)
            backup(bundle)
            job.progress(result["state"], "complete", unresolved_clauses=result["unresolved_clauses"])
            return coverage.public_result(result)
        except BaseException as exc:
            failure = whole.failure_status(exc)
            retained(job.root / "failures" / (str(time.time_ns()) + ".json"), failure)
            job.progress(failure["state"], job.current, **{k: v for k, v in failure.items() if k != "state"})
            raise


def verify(root, key, tokenizer):
    root = Path(root).resolve()
    with schema_lease(root / "wiki" / (key + "-whole-coverage-worker.lock"), timeout=0):
        bundle = load(root, key, tokenizer)
        status = whole.read(bundle.job.root / "progress.json")
        require(not coverage.writing.identity_live(status) and status["completed_batches"] == len(bundle.plan["batches"])
            and status["state"] in {"automatic_whole_source_coverage_complete", "whole_manuscript_requires_coverage_or_classification_repair"},
            "actual_complete_exited_first_coverage")
        states, job = {}, bundle.job
        items = coverage.article_items(bundle.manuscript["private"], bundle.manuscript["inventory"])
        for number, batch in enumerate(bundle.plan["batches"]):
            rows = coverage.review_batch(job, bundle.manuscript["inventory"], bundle.plan, items, batch, number)
            require(whole.read(job.root / f"batch-{number:04d}-result.json") == rows, "actual_reconstructed_batch_result")
            states.update(rows)
        result = coverage.compile_result(bundle.manuscript, bundle.plan, job, states)
        require(result == whole.read(job.root / "coverage-result.json") and status["state"] == result["state"]
            and status["completed_clauses"] == len(states) and status["unresolved_clauses"] == result["unresolved_clauses"]
            and whole.read(job.root / "progress.json") == status, "same_complete_result_and_stable_final_status")
        return {"state": "first_corpus_boundary_coverage_reconstructed_without_inference", "job": job.key,
            "verified_batches": len(bundle.plan["batches"]), "clauses": len(states),
            "unresolved_clauses": result["unresolved_clauses"], "inherited_manuscript_issues": len(bundle.manuscript["issues"]),
            "authenticated_model_stages": len(job.authenticated_stages), "model_calls": 0, "reader_publication_allowed": False}


def guarded(root, key, tokenizer):
    bundle = load(root, key, tokenizer)
    with schema_lease(bundle.folder / "dispatch.lock", timeout=0):
        require(not (bundle.folder / "dispatch-started.json").exists(), "dispatch_once_without_automatic_retry")
        retained(bundle.folder / "dispatch-started.json", bundle.job.identity)
        try:
            atomic_json(bundle.folder / "predecessor-wait-progress.json", {**bundle.job.identity,
                "state": "waiting_for_exact_predecessor_coverage", "job": bundle.job.key, "model_calls": 0})
            wait(bundle)
            return guard.run(bundle.root, bundle.folder, [sys.executable, "-m", __spec__.name,
                "--root", str(bundle.root), "--parent", key, "--tokenizer", str(Path(tokenizer).resolve()), "worker"])
        except BaseException as exc:
            failure = whole.failure_status(exc)
            retained(bundle.folder / ("dispatch-failure-" + str(time.time_ns()) + ".json"), failure)
            atomic_json(bundle.folder / "predecessor-wait-progress.json", {**bundle.job.identity, **failure, "job": bundle.job.key})
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--parent", required=True)
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("--provider", choices=["index-reviewed", "original-second-course"], default="index-reviewed")
    parser.add_argument("--batch")
    parser.add_argument("--wait-kind", choices=["ordered-boundary-tail", "first-boundary"])
    parser.add_argument("--wait-parent")
    parser.add_argument("command", choices=["prepare", "load", "worker", "guard", "verify"])
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = prepare(args.root, args.parent, args.tokenizer, kind=args.provider, batch=args.batch,
                wait_kind=args.wait_kind, wait_parent=args.wait_parent)
        elif args.command == "load":
            bundle = load(args.root, args.parent, args.tokenizer)
            result = {"state": "first_corpus_boundary_inputs_authenticated", "job": bundle.job.key,
                "batches": len(bundle.plan["batches"]), "clauses": bundle.plan["clauses"], "model_calls": 0}
        else:
            result = {"worker": run, "guard": guarded, "verify": verify}[args.command](args.root, args.parent, args.tokenizer)
        print(json.dumps(result))
    except BaseException as exc:
        print(json.dumps(whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
