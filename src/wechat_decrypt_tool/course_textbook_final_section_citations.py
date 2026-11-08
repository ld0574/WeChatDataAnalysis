"""Review the same final proposal after citation-only metadata recovery.

The failed writer stays spent. Only its original, unused independent reviewer
may run; no prose, point mapping, diagram or old failure is replaced.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys
import time

import jsonschema

from . import course_textbook_final_section_edit as edit
from . import course_textbook_reviewable_write as reviewable
from .course_knowledge import atomic_json, schema_lease

POLICY = "same-final-proposal-citation-metadata-original-review-budget-v1"
ERROR = "requires_private_composition_primary_original_support_in_body"


def require(value, code):
    if not value:
        raise ValueError("requires_final_section_citations_" + code)


def reconstruct(root, key):
    root, folder, recipe, data = edit.load(root, key)
    for name in ("started.json", "guard-started.json"):
        require(not edit.writing.identity_live(edit.whole.read(folder / name)), "original_processes_exited")
    status, failure = [edit.whole.read(folder / name) for name in ("progress.json", "failure.json")]
    require(status["state"] == "stopped" and status["stage"] == "retained_failure"
        and failure["reason_code"] == "wiki_stage_attempt_budget_exhausted_editorial-write", "original_failed_writer_retained")
    paths = sorted((folder / "attempts").glob("editorial-write-*.json"))
    require([p.name for p in paths] == ["editorial-write-01.json"]
        and not (folder / "editorial-write.json").exists(), "one_spent_unaccepted_writer")
    row = edit.whole.read(paths[0])
    job = edit.Job(root, folder, recipe, data, read_only=True)
    schema, binding = job.stage_input(edit.EDIT, data["schema"], data["context"])
    require(row["stage"] == "editorial-write" and row["attempt"] == 1
        and row["input_sha256"] == binding and row["model_digest"] == recipe["models"]["qwen3.5:9b"]
        and row["request_sha256"] == edit.digest(edit.messages(edit.EDIT, data["context"]))
        and row.get("error_type") == "ValueError" and row.get("validation_error") == ERROR
        and not row.get("accepted_structure"), "actual_original_request_and_only_known_gate")
    edit.whole.editor.e.verify_response(folder, "editorial-write", row)
    receipts = [p for p in folder.glob("response-*.json") if edit.whole.read(p).get("stage") == "editorial-write"]
    require(len(receipts) == 1, "one_actual_original_receipt")
    receipt = edit.whole.read(receipts[0])
    require(receipt["schema_sha256"] == edit.digest(schema)
        and receipt["request_sha256"] == row["request_sha256"]
        and receipt["metrics"]["model_digest"] == job.model_digest, "actual_decode_schema_and_model")
    original = json.loads(row["raw_response"])
    jsonschema.validate(original, data["schema"])
    proposed, changes = reviewable.propose_citations(original, data["ids"], data["ledger"], data["inventory"])
    require(bool(changes), "actual_citation_only_change")
    restored = copy.deepcopy(proposed)
    for old, new in zip(original["paragraphs"], restored["paragraphs"], strict=True):
        new["citations"] = old["citations"]
    require(restored == original, "all_prose_diagrams_questions_and_mappings_unchanged")
    jsonschema.validate(proposed, data["schema"])
    edit.writing.validate_section(proposed, data["ids"], data["ledger"], data["inventory"])
    sealed = [folder / n for n in ("recipe.json", "inputs.json", "progress.json", "failure.json",
        "started.json", "guard-started.json", "guard-failure.json", "guard-progress.json")]
    sealed.extend([*paths, *receipts])
    proof = {"policy": POLICY, "job": key, "original_files": {p.name if p.parent == folder else
        p.relative_to(folder).as_posix(): edit.whole.sha(p.read_bytes()) for p in sealed},
        "code_sha256": {Path(__file__).name: edit.whole.sha(Path(__file__).read_bytes()),
            Path(reviewable.__file__).name: edit.whole.sha(Path(reviewable.__file__).read_bytes()), **edit.bindings()},
        "original_candidate_sha256": edit.digest(original), "candidate": proposed, "citation_changes": changes,
        "body_changed": False, "point_mapping_changed": False, "factual_acceptance": False,
        "writer_budget_refreshed": False, "new_writer_calls": 0, "maximum_original_review_calls": 1,
        "automatic_semantic_repairs": 0, "reader_publication_allowed": False}
    return root, folder, recipe, data, proof


def prepare(root, key):
    edit.private_guard.checkpoint(Path(root).resolve())
    root, folder, _, data, proof = reconstruct(root, key)
    dest = folder / "citation-review"
    require(not dest.exists(), "same_original_review_once_no_new_recipe")
    require(not list((folder / "attempts").glob("editorial-review-*.json"))
        and not (folder / "editorial-review.json").exists(), "original_reviewer_unused")
    with schema_lease(folder / "citation-review-prepare.lock", timeout=0):
        require(not dest.exists(), "same_original_review_once_no_new_recipe")
        dest.mkdir()
        atomic_json(dest / "proposal.json", proof)
    return {"state": "same_proposal_citation_metadata_prepared", "job": key, "points": len(data["ids"]),
        "citation_paragraphs": len(proof["citation_changes"]), "new_model_calls": 0,
        "original_writer_remains_failed": True, "reader_publication_allowed": False}


def load(root, key):
    root, folder, recipe, data, proof = reconstruct(root, key)
    dest = folder / "citation-review"
    require(edit.whole.read(dest / "proposal.json") == proof, "exact_original_and_unchanged_metadata_proposal")
    return root, folder, dest, recipe, data, proof


class ReviewJob(edit.Job):
    def __init__(self, root, folder, dest, recipe, data, *, read_only=False):
        super().__init__(root, folder, recipe, data, read_only=read_only)
        self.dest = dest

    def progress(self, state, stage, **extra):
        self.current = stage
        if not self.read_only:
            atomic_json(self.dest / "progress.json", {**self.identity, "job": self.key, "state": state,
                "stage": stage, "updated": time.time(), "reader_publication_allowed": False, **extra})


def review(job, data, proof):
    candidate = proof["candidate"]
    context = {k: v for k, v in data["context"].items() if k not in {"candidate", "original_negative_review"}}
    context.update(candidate=candidate, citation_metadata_is_unproved=True,
        citation_metadata_proposals=proof["citation_changes"], original_writer_structure_failed=True)
    schema = edit.writing.review_schema(candidate, data["ids"])
    audit = job.stage("editorial-review", edit.REVIEW, schema, context,
        lambda value: edit.writing.author.validate_review(value, schema))
    negatives = sum(not v for v in edit.writing.author.verdicts(audit))
    return {"state": "same_final_proposal_review_passed" if not negatives else "same_final_proposal_review_unresolved",
        "job": job.key, "section_editorial_review_passed": not negatives, "negative_checks": negatives,
        "candidate_sha256": edit.digest(candidate), "audit_sha256": edit.digest(audit),
        "points": len(data["ids"]), "citation_paragraphs": len(proof["citation_changes"]),
        "original_parent_issues_retained": data["parent_issue_count"], "original_write_failure_retained": True,
        "new_writer_calls": 0, "automatic_semantic_repairs": 0,
        "reader_publication_allowed": False, "source_deletion_allowed": False}


def worker(root, key):
    root, folder, dest, recipe, data, proof = load(root, key)
    edit.private_guard.checkpoint(root)
    job = ReviewJob(root, folder, dest, recipe, data)
    with schema_lease(dest / "worker.lock", timeout=0):
        require(not (dest / "started.json").exists(), "worker_once_no_role_refresh")
        require(not list((folder / "attempts").glob("editorial-review-*.json")), "original_review_still_unused")
        atomic_json(dest / "started.json", job.identity)
        try:
            result = review(job, data, proof)
            atomic_json(dest / "result.json", result)
            job.progress(result["state"], "complete")
            return result
        except BaseException as exc:
            atomic_json(dest / "failure.json", edit.whole.failure_status(exc))
            job.progress("stopped", "retained_failure")
            raise


def verify(root, key):
    root, folder, dest, recipe, data, proof = load(root, key)
    status = edit.whole.read(dest / "progress.json")
    require(status["stage"] == "complete" and not edit.writing.identity_live(status), "completed_actual_review_worker_exited")
    result = review(ReviewJob(root, folder, dest, recipe, data, read_only=True), data, proof)
    require(result == edit.whole.read(dest / "result.json"), "actual_result_reconstructed_without_inference")
    return {**result, "new_model_calls": 0}


def guard(root, key):
    root, _, dest, _, _, _ = load(root, key)
    outcome = edit.private_guard.run(root, dest, [sys.executable, "-m", __spec__.name, "--root", str(root), "worker", "--job", key])
    if outcome["state"] != "private_worker_exited":
        return outcome
    result = verify(root, key)
    atomic_json(dest / "checked-result.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--job", required=True)
    parser.add_argument("command", choices=["prepare", "worker", "guard", "verify"])
    args = parser.parse_args()
    try:
        print(json.dumps(globals()[args.command](args.root, args.job)))
    except BaseException as exc:
        print(json.dumps(edit.whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
