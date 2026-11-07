"""Reconstruct a finished complete-record coverage audit without inference.

The execution's cached judgments, actual model requests and GPU receipts are
checked by its original verifier. Only a new hash-only proof may be saved.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

from tokenizers import Tokenizer

from . import course_textbook_whole_coverage as coverage
from .course_knowledge import atomic_json, schema_lease

POLICY = "read-only-reconstructed-complete-whole-coverage-proof-v1"


def require(ok, code):
    if not ok:
        raise ValueError("requires_coverage_snapshot_" + code)


class ReadOnlyCoverageJob(coverage.CoverageJob):
    def __init__(self, store, manuscript, plan, tokenizer, adapter, section, expected_root):
        self.expected_root = Path(expected_root).resolve()
        self.authenticated_stages = set()
        super().__init__(store, manuscript, plan, tokenizer, adapter, section)

    @property
    def root(self):
        return self._root

    @root.setter
    def root(self, value):
        value = Path(value).resolve()
        require(value == self.expected_root and (value / "manifest.json").is_file(),
            "existing_exact_execution_manifest_before_initialization")
        self._root = value

    def stage(self, name, *args, **kwargs):
        require((self.root / (name + ".json")).is_file(), "original_accepted_stage_cache_required")
        result = super().stage(name, *args, **kwargs)
        self.authenticated_stages.add(name)
        return result

    def progress(self, state, stage, **extra):
        self.current = stage

    def generate_response(self, *args, **kwargs):
        raise ValueError("requires_coverage_snapshot_inference_forbidden")

    def slot(self, *args, **kwargs):
        raise ValueError("requires_coverage_snapshot_gpu_acquisition_forbidden")


def reconstruct(root, parent, tokenizer, adapter, section="section-0007"):
    root, tokenizer = Path(root).resolve(), Path(tokenizer).resolve()
    require(re.fullmatch(r"[a-f0-9]{32}", parent) is not None, "parent_writer_id")
    require(adapter in {"original", "continuation"}, "known_parent_adapter")
    contract_path = root / "wiki/textbook-whole-authoring-jobs" / parent / coverage.CONTRACT
    contract = coverage.writing.whole.read(contract_path)
    key = contract["job"]
    require(re.fullmatch(r"[a-f0-9]{32}", key) is not None and contract["parent_writer"] == parent,
        "actual_bound_coverage_job")
    folder = root / "wiki/whole-manuscript-coverage-jobs" / key
    with schema_lease(root / "wiki" / (parent + "-whole-coverage-worker.lock"), timeout=0):
        status = coverage.writing.whole.read(folder / "progress.json")
        require(status["job"] == key and status["stage"] == "complete" and status["state"] in {
            "automatic_whole_source_coverage_complete", "whole_manuscript_requires_coverage_or_classification_repair"}
            and not coverage.writing.identity_live(status), "actually_finished_coverage_worker")
        manuscript = (coverage.continuation.reconstruct(root, parent, section, tokenizer, complete=True)
            if adapter == "continuation" else coverage.snapshot.reconstruct(root, parent, tokenizer, complete=True))
        require(manuscript["private"] is not None
            and manuscript["summary"]["verified_units"] == manuscript["summary"]["total_units"],
            "authenticated_entire_parent_manuscript")
        plan = coverage.make_plan(manuscript["inventory"], manuscript["private"], manuscript["working"],
            Tokenizer.from_file(str(tokenizer)))
        job = ReadOnlyCoverageJob(coverage.writing.TextbookStore(root), manuscript, plan, tokenizer, adapter, section, folder)
        binding = {"policy": coverage.POLICY, "job": job.key, "parent_writer": parent,
            "snapshot_sha256": coverage.digest(manuscript["proof"]), "plan_sha256": coverage.digest(plan),
            "module_sha256": coverage.writing.whole.sha(Path(coverage.__file__).read_bytes()),
            "helpers_sha256": coverage.bindings(), "semantic_repairs": 0, "old_budgets_reset": False,
            "reader_publication_allowed": False}
        require(contract == binding, "unchanged_original_execution_contract")
        require(coverage.writing.whole.read(folder / "review-plan.json") == plan
            and coverage.writing.whole.read(folder / "parent-snapshot.json") == manuscript["proof"],
            "original_complete_coverage_inputs")
        items = coverage.article_items(manuscript["private"], manuscript["inventory"])
        states, paths = {}, {contract_path, folder / "manifest.json", folder / "review-plan.json",
            folder / "parent-snapshot.json", folder / "coverage-result.json"}
        for number, batch in enumerate(plan["batches"]):
            rows = coverage.review_batch(job, manuscript["inventory"], plan, items, batch, number)
            path = folder / f"batch-{number:04d}-result.json"
            require(coverage.writing.whole.read(path) == rows, "actual_reconstructed_batch_result")
            paths.add(path)
            states.update(rows)
        result = coverage.compile_result(manuscript, plan, job, states)
        require(coverage.writing.whole.read(folder / "coverage-result.json") == result,
            "actual_reconstructed_complete_result")
        require(status["state"] == result["state"] and status["completed_batches"] == len(plan["batches"])
            and status["completed_clauses"] == plan["clauses"]
            and status["unresolved_clauses"] == result["unresolved_clauses"], "actual_terminal_counts")
        for stage in job.authenticated_stages:
            paths.add(folder / (stage + ".json"))
            paths.update((folder / "attempts").glob(stage + "-[0-9][0-9].json"))
        for path in folder.glob("response-*.json"):
            if coverage.writing.whole.read(path)["stage"] in job.authenticated_stages:
                paths.add(path)
        for relative, expected in manuscript["proof"]["files"].items():
            path = (root / relative).resolve()
            require(path.is_relative_to(root)
                and coverage.writing.whole.sha(path.read_bytes()) == expected, "unchanged_parent_evidence")
            paths.add(path)
        require(coverage.writing.whole.read(folder / "progress.json") == status, "stable_finished_execution")
        summary = {**coverage.public_result(result), "state": "complete_whole_coverage_reconstructed_without_inference",
            "execution_result_state": result["state"], "authenticated_model_stages": len(job.authenticated_stages),
            "inherited_manuscript_issues": len(manuscript["issues"]), "model_calls": 0,
            "original_budgets_reset": False}
        proof = {"policy": POLICY, **summary,
            "verifier_sha256": coverage.writing.whole.sha(Path(__file__).read_bytes()),
            "execution_contract_sha256": coverage.digest(contract),
            "files": {path.relative_to(root).as_posix(): coverage.writing.whole.sha(path.read_bytes()) for path in sorted(paths)},
            "semantic_acceptance": False, "reader_publication_allowed": False, "source_deletion_allowed": False}
        return {"summary": summary, "proof": proof}


def save(root, result):
    proof = result["proof"]
    path = Path(root).resolve() / "wiki/whole-coverage-snapshots" / (coverage.digest(proof) + ".json")
    require(not path.exists() or coverage.writing.whole.read(path) == proof, "immutable_coverage_proof")
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(path, proof)
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--parent", required=True)
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("--adapter", choices=["original", "continuation"], required=True)
    parser.add_argument("--section", default="section-0007")
    args = parser.parse_args()
    try:
        result = reconstruct(args.root, args.parent, args.tokenizer, args.adapter, args.section)
        path = save(args.root, result)
        print(json.dumps({**result["summary"], "snapshot": str(path)}))
    except Exception as exc:
        print(json.dumps(coverage.writing.whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
