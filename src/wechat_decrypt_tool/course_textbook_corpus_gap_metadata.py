"""Use the original unused review after an exact duplicate citation failure.

Only identical citation list entries can be removed. Unmapped new paragraphs
stay explicit unresolved defects, never receive guessed links. No prose,
source selection, coverage judgment or boundary changes. Failed edits are
never made accepted model caches; the original unused reviews audit them.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys
import time

import jsonschema

from . import course_textbook_corpus_gap_editor as original
from .course_knowledge import atomic_json, schema_lease

whole, guard, writing, digest, retained = original.whole, original.guard, original.writing, original.digest, original.retained
POLICY = "original-unused-final-gap-review-of-rejected-metadata-candidates-v1"
DIRECTORY = "corpus-gap-metadata-continuations"


def require(ok, code):
    if not ok:
        raise ValueError("requires_gap_metadata_" + code)


def folder_for(root, key):
    original.folder_for(root, key)
    return Path(root).resolve() / "wiki" / DIRECTORY / key


def bindings():
    return {"module_sha256": whole.sha(Path(__file__).read_bytes()), "original": original.bindings()}


def normalize(raw, context, units):
    count = raw.get("addition_count")
    require(type(count) is int and 0 <= count <= original.ADDITIONS, "actual_declared_addition_count")
    schema = original.wire_schema(context, units)
    errors = list(jsonschema.Draft202012Validator(schema["oneOf"][count]).iter_errors(raw))
    require(all(error.validator == "uniqueItems" and len(error.path) == 3
        and error.path[0] == "additions" and error.path[1] in raw["additions"] and error.path[2] == "citations"
        for error in errors), "only_exact_duplicate_addition_citations")
    normalized = copy.deepcopy(raw)
    changes = []
    for key, paragraph in normalized["additions"].items():
        refs = paragraph["citations"]
        unique = list(dict.fromkeys(refs))
        if unique != refs:
            require(set(unique) == set(refs) and all(ref in context["sources"] for ref in refs), "same_actual_known_source_set")
            changes.append({"addition": key, "original_count": len(refs), "unique_count": len(unique),
                "original_list_sha256": digest(refs), "normalized_list_sha256": digest(unique)})
            paragraph["citations"] = unique
    require(len(changes) == len(errors), "one_lossless_change_per_observed_error")
    jsonschema.validate(normalized, schema)
    try:
        candidate = original.compile_candidate(normalized, context, units)
        unmapped = []
    except ValueError as exc:
        require(str(exc) == "requires_corpus_gap_editor_every_new_explanation_explicitly_used",
            "no_other_candidate_error_can_be_audited_as_metadata")
        ids = list(context["target_clauses"])
        mapped, _ = original.runs(ids, units)
        grouped = original.boundaries.compile_wire({"source_groups": normalized["source_groups"]},
            ids, [*context["article"], *normalized["additions"]], mapped)
        original.coverage.validate_groups(grouped, ids, [*context["article"], *normalized["additions"]], units)
        used = {key for row in grouped["groups"] for key in row["article_items"]}
        unmapped = sorted(normalized["additions"].keys() - used)
        require(bool(unmapped), "actual_unmapped_explanations_retained_without_inventing_links")
        candidate = {"additions": copy.deepcopy(normalized["additions"]), "groups": grouped["groups"]}
    require(bool(errors) or bool(unmapped), "actual_rejected_metadata_or_unmapped_explanations")
    return candidate, {"raw_sha256": digest(raw), "normalized_wire_sha256": digest(normalized),
        "candidate_sha256": digest(candidate), "changes": changes, "prose_and_source_sets_changed": False,
        "coverage_judgments_and_members_changed": False, "unmapped_addition_ids": unmapped,
        "candidate_original_structural_validation_passed": not unmapped, "semantic_acceptance": False}


def failed_candidate(old, recipe, data, number):
    name = f"batch-{number:04d}-edit"
    attempts = sorted((old / "attempts").glob(name + "-[0-9][0-9].json"))
    require([p.name for p in attempts] == [name + "-01.json"] and not (old / (name + ".json")).exists(),
        "one_spent_edit_without_accepted_model_cache")
    row = whole.read(attempts[0])
    require(row["stage"] == name and row["attempt"] == 1 and not row.get("accepted_structure")
        and row["error_type"] in {"ValidationError", "ValueError"} and row["model_metrics"]["done_reason"] == "stop"
        and row["model_digest"] == recipe["models"]["qwen3.5:9b"], "complete_original_failed_wire_response")
    context = data["contexts"][number]
    probe = object.__new__(original.Job)
    probe.packet = {"sources": {ref: source for c in data["contexts"]
        for ref, source in {**c["sources"], **c["context_sources"]}.items()}}
    probe.model_digest, probe.range_notice = row["model_digest"], ""
    actual, expected = probe.stage_input(original.EDIT, original.wire_schema(context, data["units"]), context)
    messages = [{"role": "system", "content": original.EDIT}, {"role": "user", "content": json.dumps(
        {**context, "previous_validation_failure": ""}, ensure_ascii=False)}]
    require(row["input_sha256"] == expected and row["request_sha256"] == digest(messages), "same_original_actual_request")
    whole.editor.e.verify_response(old, name, row)
    receipts = [(p, whole.read(p)) for p in old.glob("response-*.json")]
    matches = [p for p, receipt in receipts if receipt["stage"] == name
        and receipt["request_sha256"] == row["request_sha256"] and receipt.get("schema_sha256") == digest(actual)
        and receipt["response"]["message"]["content"] == row["raw_response"] and receipt["metrics"] == row["model_metrics"]]
    require(len(matches) == 1, "exact_original_response_and_decoding_schema")
    candidate, conversion = normalize(json.loads(row["raw_response"]), context, data["units"])
    paths = [*attempts, *matches]
    proof = {"stage": name, "failed_response_sha256": whole.sha(row["raw_response"].encode()),
        "conversion": conversion, "files": {p.name: whole.sha(p.read_bytes()) for p in paths},
        "original_attempt_budget": 1, "new_model_calls": 0, "accepted_edit_cache_created": False}
    return candidate, proof, paths


def inspect(root, key):
    root, old, recipe, data = original.load(root, key)
    progress = whole.read(old / "progress.json")
    protected = whole.read(old / "guard/guard-progress.json")
    require(progress["state"] == "stopped" and progress["completed_batches"] == 0
        and progress["reason_code"] == "wiki_stage_attempt_budget_exhausted_batch-0000-edit"
        and protected["state"] == "worker_exited" and protected["returncode"] == 1
        and not writing.identity_live(progress) and not writing.identity_live(protected), "actual_original_failed_exited_edit_and_guard")
    attempts = sorted((old / "attempts").glob("*.json"))
    require([p.name for p in attempts] == ["batch-0000-edit-01.json"],
        "one_spent_edit_without_accepted_cache_and_all_other_roles_unused")
    candidate, failure, observed = failed_candidate(old, recipe, data, 0)
    paths = [old / "recipe.json", old / "inputs.json", old / "progress.json", old / "started.json",
        old / "guard/guard-progress.json", old / "guard/guard-started.json", old / "independent-input-restore.json",
        *old.glob("failure-*.json"), *observed]
    paths += list((old / "guard").glob("*failure*.json"))
    proof = {"policy": POLICY, "job": key, "original_recipe_sha256": digest(recipe), "code": bindings(),
        "original_guard": {k: protected[k] for k in ["pid", "created"]},
        "original_worker": {k: progress[k] for k in ["pid", "created"]},
        "failed_response_sha256": failure["failed_response_sha256"], "conversion": failure["conversion"],
        "files": {p.relative_to(root).as_posix(): whole.sha(p.read_bytes()) for p in paths},
        "original_edit_attempts": 1, "original_unused_review": True, "later_unused_batches": len(data["contexts"]) - 1,
        "future_failed_edit_audit_allowed_only_for_same_metadata_or_unmapped_defects": True,
        "new_model_calls": 0, "accepted_edit_cache_created": False, "original_budgets_reset": False,
        "reader_publication_allowed": False, "source_deletion_allowed": False}
    return root, old, recipe, data, candidate, proof


def prepare(root, key):
    guard.checkpoint(root)
    with schema_lease(Path(root) / "wiki/corpus-gap-metadata-prepare.lock", timeout=0):
        root, _, _, _, _, proof = inspect(root, key)
        folder = folder_for(root, key)
        require(not folder.exists(), "one_explicit_original_unused_role_continuation")
        retained(folder / "execution.json", proof)
        (folder / "guard").mkdir()
        return {"state": "original_unused_gap_roles_after_metadata_failure_prepared", "job": key,
            "duplicate_citation_lists": len(proof["conversion"]["changes"]), "later_unused_batches": proof["later_unused_batches"],
            "model_calls": 0}


def load(root, key):
    root = Path(root).resolve()
    folder = folder_for(root, key)
    proof = whole.read(folder / "execution.json")
    require(proof["policy"] == POLICY and proof["job"] == key and proof["code"] == bindings(), "same_frozen_continuation")
    for name, checksum in proof["files"].items():
        require(whole.sha((root / name).read_bytes()) == checksum, "original_failure_and_single_spent_edit_preserved")
    root, old, recipe, data = original.load(root, key)
    row = whole.read(old / "attempts/batch-0000-edit-01.json")
    require(not (old / "batch-0000-edit.json").exists(), "failed_edit_is_never_promoted_to_model_cache")
    candidate, conversion = normalize(json.loads(row["raw_response"]), data["contexts"][0], data["units"])
    require(conversion == proof["conversion"] and digest(recipe) == proof["original_recipe_sha256"], "same_exact_lossless_conversion")
    return root, folder, old, recipe, data, candidate, proof


class Job(original.Job):
    def __init__(self, root, folder, recipe, data, continuation, *, read_only=False):
        super().__init__(root, folder, recipe, data, read_only=read_only)
        self.continuation, self.original_recipe = continuation, recipe

    def progress(self, state, stage, **extra):
        self.current = stage
        if not self.read_only:
            atomic_json(self.continuation / "progress.json", {**self.identity, "job": self.key, "state": state,
                "stage": stage, "completed_batches": self.completed_batches, "updated": time.time(),
                "original_failed_progress_preserved": True, "reader_publication_allowed": False, **extra})


def stages(job, data, candidate, proof):
    resolved, conflicts, outcomes, unmapped_total = set(), set(), [], 0
    for number, context in enumerate(data["contexts"]):
        prefix = f"batch-{number:04d}"
        conversion = proof["conversion"] if number == 0 else None
        if number:
            failed_proof = job.continuation / (prefix + "-rejected-edit-proof.json")
            if failed_proof.exists():
                require(job.read_only, "failed_metadata_derivation_only_replayed_not_live_retried")
                candidate, failed, _ = failed_candidate(job.root, job.original_recipe, data, number)
                require(whole.read(failed_proof) == failed, "same_actual_rejected_later_edit")
                conversion = failed["conversion"]
            else:
                try:
                    raw = job.stage(prefix + "-edit", original.EDIT, original.wire_schema(context, data["units"]), context,
                        lambda value: original.compile_candidate(value, context, data["units"]))
                    candidate = original.compile_candidate(raw, context, data["units"])
                except ValueError as exc:
                    require(not job.read_only and str(exc) == "wiki_stage_attempt_budget_exhausted_" + prefix + "-edit",
                        "only_actual_original_spent_edit_can_use_its_unused_review")
                    candidate, failed, _ = failed_candidate(job.root, job.original_recipe, data, number)
                    retained(failed_proof, failed)
                    conversion = failed["conversion"]
        review = {**context, "candidate": {"additions": candidate["additions"],
            "groups": {f"G{i:04d}": row for i, row in enumerate(candidate["groups"])}}}
        if conversion is not None:
            review["untrusted_metadata_normalization"] = conversion
            unmapped_total += len(conversion.get("unmapped_addition_ids", []))
        audit = job.stage(prefix + "-review", original.REVIEW, original.review_schema(candidate, context), review,
            lambda value: original.assess(candidate, value, context))
        outcome = original.assess(candidate, audit, context)
        outcome.update(candidate_sha256=digest(candidate), audit_sha256=digest(audit))
        retained(job.continuation / (prefix + "-result.json"), outcome, read_only=job.read_only)
        outcomes.append(outcome)
        resolved.update(outcome["resolved_target_clauses"])
        conflicts.update(outcome["cleared_source_conflicts"])
        job.completed_batches += 1
        job.progress("original_final_gap_roles_checked_after_lossless_metadata", prefix)
    return {"state": "private_gap_editorial_checked_after_metadata_integration_still_required", "job": job.key,
        "completed_batches": len(outcomes), "target_clauses": len(data["units"]),
        "remaining_original_unresolved": len(set(data["original_unresolved"]) - resolved),
        "remaining_source_conflicts": len(set(data["original_source_conflicts"]) - conflicts),
        "new_explanations": sum(row["new_explanations"] for row in outcomes),
        "negative_checks": sum(row["negative_checks"] for row in outcomes),
        "original_parent_issues_retained": len(data["inherited_parent_issues"]),
        "remaining_unmapped_additions": unmapped_total,
        "accepted_failed_edit_cache_created": False, "original_budgets_reset": False,
        "semantic_acceptance": False, "reader_publication_allowed": False, "source_deletion_allowed": False}


def worker(root, key):
    root, folder, old, recipe, data, candidate, proof = load(root, key)
    guard.checkpoint(root)
    with schema_lease(folder / "worker.lock", timeout=0):
        require(not (folder / "started.json").exists(), "single_worker_no_automatic_retry")
        restored = whole.read(folder / "independent-input-restore.json")
        require(restored["state"] == "gap_metadata_continuation_inputs_independently_restored" and restored["job"] == key
            and restored["original_root_reads"] == restored["model_calls"] == 0, "actual_independent_input_restore")
        require(sorted(p.name for p in (old / "attempts").glob("*.json")) == ["batch-0000-edit-01.json"],
            "all_original_remaining_roles_unused")
        job = Job(root, old, recipe, data, folder)
        retained(folder / "started.json", job.identity)
        try:
            result = stages(job, data, candidate, proof)
            retained(folder / "result.json", result)
            job.progress(result["state"], "complete")
            return result
        except BaseException as exc:
            failure = whole.failure_status(exc)
            retained(folder / ("failure-" + str(time.time_ns()) + ".json"), failure)
            job.progress(failure["state"], "retained_failure", reason_code=failure["reason_code"])
            raise


def verify(root, key):
    root, folder, old, recipe, data, candidate, proof = load(root, key)
    state = whole.read(folder / "progress.json")
    require(state["stage"] == "complete" and not writing.identity_live(state), "actual_complete_exited_original_roles")
    result = stages(Job(root, old, recipe, data, folder, read_only=True), data, candidate, proof)
    require(result == whole.read(folder / "result.json"), "same_actual_reconstructed_results")
    return {**result, "new_model_calls": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("command", choices=["prepare", "load", "worker", "guard", "verify"])
    args = parser.parse_args()
    try:
        if args.command == "guard":
            root, folder, _, _, _, _, _ = load(args.root, args.job)
            result = guard.run(root, folder / "guard", [sys.executable, "-m", __spec__.name,
                "--root", str(root), "--job", args.job, "worker"])
        elif args.command == "load":
            _, _, _, _, _, _, proof = load(args.root, args.job)
            result = {"state": "same_original_failed_gap_edit_and_unused_role_inputs", "job": args.job,
                "duplicate_citation_lists": len(proof["conversion"]["changes"]), "model_calls": 0}
        else:
            result = {"prepare": prepare, "worker": worker, "verify": verify}[args.command](args.root, args.job)
        print(json.dumps(result))
    except BaseException as exc:
        print(json.dumps(whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
