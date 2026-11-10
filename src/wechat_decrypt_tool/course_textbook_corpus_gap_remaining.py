"""Use untouched independent gap batches, retaining failed batches unresolved.

The original ten reviewed batches are replayed, never inferred again. The spent
short-paragraph edit and its unused review remain untouched. Each later role
keeps its original single attempt; a complete authenticated invalid response
parks that whole batch as unresolved rather than blocking unrelated batches.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

from . import course_textbook_corpus_gap_metadata as previous
from .course_knowledge import schema_lease

original, whole, guard, writing, digest, retained = (
    previous.original, previous.whole, previous.guard, previous.writing, previous.digest, previous.retained)
POLICY = "same-unused-independent-final-gap-batches-retain-invalid-batches-unresolved-v1"
DIRECTORY = "corpus-gap-remaining-batches"
PREFIX, FAILED, FIRST, TOTAL = 10, 10, 11, 24


def require(ok, code):
    if not ok:
        raise ValueError("requires_gap_remaining_" + code)


def folder_for(root, key):
    previous.folder_for(root, key)
    return Path(root).resolve() / "wiki" / DIRECTORY / key


def bindings():
    return {"module_sha256": whole.sha(Path(__file__).read_bytes()), "previous": previous.bindings()}


def rejected(old, recipe, data, number, role, context, schema):
    name = f"batch-{number:04d}-{role}"
    files = sorted((old / "attempts").glob(name + "-[0-9][0-9].json"))
    require([p.name for p in files] == [name + "-01.json"] and not (old / (name + ".json")).exists(),
        "actual_single_failed_role_without_accepted_cache")
    row = whole.read(files[0])
    model = "qwen3.5:9b" if role == "edit" else "course-defiant:q4"
    prompt = original.EDIT if role == "edit" else original.REVIEW
    require(row["stage"] == name and row["attempt"] == 1 and not row.get("accepted_structure")
        and row["error_type"] in {"ValidationError", "ValueError", "JSONDecodeError"}
        and row["model_digest"] == recipe["models"][model] and row["model_metrics"]["done_reason"] == "stop",
        "complete_actual_invalid_original_role_not_a_transport_or_thermal_failure")
    probe = object.__new__(original.Job)
    probe.packet = {"sources": {ref: source for c in data["contexts"]
        for ref, source in {**c["sources"], **c["context_sources"]}.items()}}
    probe.model_digest, probe.range_notice = row["model_digest"], ""
    actual, expected = probe.stage_input(prompt, schema, context)
    messages = [{"role": "system", "content": prompt}, {"role": "user", "content": json.dumps(
        {**context, "previous_validation_failure": ""}, ensure_ascii=False)}]
    require(row["input_sha256"] == expected and row["request_sha256"] == digest(messages), "same_actual_failed_request")
    whole.editor.e.verify_response(old, name, row)
    matches = [p for p in old.glob("response-*.json") if (lambda r: r["stage"] == name
        and r["request_sha256"] == row["request_sha256"] and r.get("schema_sha256") == digest(actual)
        and r["response"]["message"]["content"] == row["raw_response"] and r["metrics"] == row["model_metrics"])(whole.read(p))]
    require(len(matches) == 1, "exact_actual_response_schema_and_metrics")
    files += matches
    return {"stage": name, "actual_failed_response_sha256": whole.sha(row["raw_response"].encode()),
        "files": {p.relative_to(old).as_posix(): whole.sha(p.read_bytes()) for p in files},
        "attempt_budget": 1, "failed_role_accepted": False, "new_model_calls": 0}


def unused(old):
    expected = {f"batch-{n:04d}-{role}-01.json" for n in range(PREFIX) for role in ("edit", "review")}
    expected.add(f"batch-{FAILED:04d}-edit-01.json")
    require({p.name for p in (old / "attempts").glob("*.json")} == expected, "all_later_original_roles_and_failed_batch_review_unused")
    stages = [whole.read(p)["stage"] + "-01.json" for p in old.glob("response-*.json")]
    require(len(stages) == len(expected) and set(stages) == expected, "no_orphan_actual_later_role_responses")
    require(not (old / f"batch-{FAILED:04d}-review.json").exists()
        and all(not (old / f"batch-{n:04d}-{role}.json").exists() for n in range(FIRST, TOTAL) for role in ("edit", "review")),
        "unused_original_roles_have_no_cache")


def prefix(root, prior, old, recipe, data, candidate, proof):
    # Keep the full original packet for identical input bindings, while replaying
    # only the ten actually completed stages from the old metadata continuation.
    job = previous.Job(root, old, recipe, data, prior, read_only=True)
    result = previous.stages(job, {**data, "contexts": data["contexts"][:PREFIX]}, candidate, proof)
    require(result["completed_batches"] == PREFIX, "entire_actual_reviewed_prefix_without_inference")
    outcomes = {str(n): whole.read(prior / f"batch-{n:04d}-result.json") for n in range(PREFIX)}
    return result, outcomes


def inspect(root, key):
    root, prior, old, recipe, data, candidate, previous_proof = previous.load(root, key)
    state, protected = [whole.read(prior / name) for name in ("progress.json", "guard/guard-progress.json")]
    require(state["state"] == "stopped" and state["completed_batches"] == PREFIX
        and state["reason_code"] == "requires_gap_metadata_only_exact_duplicate_addition_citations"
        and protected["state"] == "worker_exited" and protected["returncode"] == 1
        and not writing.identity_live(state) and not writing.identity_live(protected)
        and len(data["contexts"]) == TOTAL, "exact_original_ten_batch_failed_terminal")
    unused(old)
    first_failure = rejected(old, recipe, data, FAILED, "edit", data["contexts"][FAILED],
        original.wire_schema(data["contexts"][FAILED], data["units"]))
    prefix_result, outcomes = prefix(root, prior, old, recipe, data, candidate, previous_proof)
    paths = [*prior.glob("*.json"), *prior.joinpath("guard").glob("*.json"),
        *old.glob("*.json"), *(old / "attempts").glob("*.json")]
    proof = {"policy": POLICY, "job": key, "code": bindings(), "previous_execution_sha256": digest(previous_proof),
        "original_recipe_sha256": digest(recipe), "prefix_summary": prefix_result, "prefix_outcomes": outcomes,
        "initial_failed_edit": first_failure, "retained_failed_batch": FAILED, "remaining_original_batches": list(range(FIRST, TOTAL)),
        "files": {p.relative_to(root).as_posix(): whole.sha(p.read_bytes()) for p in paths},
        "independent_invalid_batches_remain_unresolved": True, "original_budgets_reset": False,
        "automatic_retry_allowed": False, "semantic_acceptance": False,
        "reader_publication_allowed": False, "source_deletion_allowed": False}
    return root, prior, old, recipe, data, proof


def prepare(root, key):
    guard.checkpoint(Path(root).resolve())
    with schema_lease(Path(root) / "wiki/corpus-gap-remaining-prepare.lock", timeout=0):
        root, _, _, _, data, proof = inspect(root, key)
        folder = folder_for(root, key)
        require(not folder.exists(), "one_original_unused_batch_continuation")
        retained(folder / "execution.json", proof)
        (folder / "guard").mkdir()
        return {"state": "original_unused_gap_batches_prepared_with_retained_failed_batch", "job": key,
            "reviewed_prefix": PREFIX, "retained_failed_batch_target_clauses": len(data["contexts"][FAILED]["target_clauses"]),
            "remaining_original_batches": TOTAL - FIRST, "model_calls": 0}


def load(root, key):
    root, prior, old, recipe, data, _, prior_proof = previous.load(root, key)
    folder = folder_for(root, key)
    proof = whole.read(folder / "execution.json")
    require(proof["policy"] == POLICY and proof["job"] == key and proof["code"] == bindings()
        and proof["previous_execution_sha256"] == digest(prior_proof) and proof["original_recipe_sha256"] == digest(recipe),
        "same_frozen_remaining_roles_and_original_inputs")
    for name, checksum in proof["files"].items():
        path = (root / name).resolve()
        require(path.is_relative_to(root) and whole.sha(path.read_bytes()) == checksum, "original_prefix_failure_and_spent_roles_preserved")
    return root, folder, prior, old, recipe, data, proof


def park(job, number, context, failure):
    row = {"state": "retained_unresolved_original_batch", "batch": number,
        "all_target_clauses_unresolved": list(context["target_clauses"]),
        "all_source_conflicts_unresolved": list(context["source_conflicts"]), "actual_failure": failure,
        "new_edit_or_review_attempts": 0, "semantic_acceptance": False}
    retained(job.continuation / f"batch-{number:04d}-unresolved.json", row, read_only=job.read_only)
    return row


def stages(job, data, proof):
    outcomes = dict(proof["prefix_outcomes"])
    parked = {str(FAILED): park(job, FAILED, data["contexts"][FAILED], proof["initial_failed_edit"])}
    unmapped = proof["prefix_summary"]["remaining_unmapped_additions"]
    for number in proof["remaining_original_batches"]:
        context, conversion = data["contexts"][number], None
        prefix_name = f"batch-{number:04d}"
        edit_schema = original.wire_schema(context, data["units"])
        parked_path = job.continuation / (prefix_name + "-unresolved.json")
        retained_failure = whole.read(parked_path) if job.read_only and parked_path.exists() else None
        if retained_failure and retained_failure["actual_failure"]["stage"].endswith("-edit"):
            failed = rejected(job.root, job.original_recipe, data, number, "edit", context, edit_schema)
            require(not (job.root / "attempts" / (prefix_name + "-review-01.json")).exists()
                and not (job.root / (prefix_name + "-review.json")).exists(), "parked_invalid_edit_review_remains_unused")
            parked[str(number)] = park(job, number, context, failed)
            continue
        normalized_path = job.continuation / (prefix_name + "-rejected-edit-proof.json")
        try:
            if job.read_only and normalized_path.exists():
                candidate, failed, _ = previous.failed_candidate(job.root, job.original_recipe, data, number)
                require(whole.read(normalized_path) == failed, "same_actual_lossless_failed_edit_derivation")
                conversion = failed["conversion"]
            else:
                raw = job.stage(prefix_name + "-edit", original.EDIT, edit_schema, context,
                    lambda value: original.compile_candidate(value, context, data["units"]))
                candidate = original.compile_candidate(raw, context, data["units"])
        except ValueError as exc:
            require(not job.read_only and str(exc) == "wiki_stage_attempt_budget_exhausted_" + prefix_name + "-edit",
                "no_transport_control_or_unbound_failure_continuation")
            try:
                candidate, failed, _ = previous.failed_candidate(job.root, job.original_recipe, data, number)
            except ValueError:
                failed = rejected(job.root, job.original_recipe, data, number, "edit", context, edit_schema)
                parked[str(number)] = park(job, number, context, failed)
                job.progress("retained_invalid_batch_other_original_roles_continue", prefix_name,
                    accounted_original_batches=len(outcomes) + len(parked), retained_unresolved_batches=len(parked))
                continue
            retained(job.continuation / (prefix_name + "-rejected-edit-proof.json"), failed, read_only=job.read_only)
            conversion = failed["conversion"]
        review = {**context, "candidate": {"additions": candidate["additions"],
            "groups": {f"G{i:04d}": row for i, row in enumerate(candidate["groups"])}}}
        if conversion is not None:
            review["untrusted_metadata_normalization"] = conversion
            unmapped += len(conversion["unmapped_addition_ids"])
        schema = original.review_schema(candidate, context)
        if retained_failure:
            require(retained_failure["actual_failure"]["stage"] == prefix_name + "-review", "only_actual_parked_review_is_replayed")
            failed = rejected(job.root, job.original_recipe, data, number, "review", review, schema)
            parked[str(number)] = park(job, number, context, failed)
            continue
        try:
            audit = job.stage(prefix_name + "-review", original.REVIEW, schema, review,
                lambda value: original.assess(candidate, value, context))
        except ValueError as exc:
            require(not job.read_only and str(exc) == "wiki_stage_attempt_budget_exhausted_" + prefix_name + "-review",
                "only_actual_original_invalid_review_can_be_parked")
            failed = rejected(job.root, job.original_recipe, data, number, "review", review, schema)
            parked[str(number)] = park(job, number, context, failed)
            job.progress("retained_invalid_review_other_original_roles_continue", prefix_name,
                accounted_original_batches=len(outcomes) + len(parked), retained_unresolved_batches=len(parked))
            continue
        outcome = original.assess(candidate, audit, context)
        outcome.update(candidate_sha256=digest(candidate), audit_sha256=digest(audit))
        retained(job.continuation / (prefix_name + "-result.json"), outcome, read_only=job.read_only)
        outcomes[str(number)] = outcome
        job.completed_batches = len(outcomes)
        job.progress("other_original_independent_batches_reviewed", prefix_name,
            accounted_original_batches=len(outcomes) + len(parked), retained_unresolved_batches=len(parked))
    resolved = {cid for row in outcomes.values() for cid in row["resolved_target_clauses"]}
    relevant = {ref: [n for n, context in enumerate(data["contexts"]) if ref in context["source_conflicts"]]
        for ref in data["original_source_conflicts"]}
    cleared = {ref for ref, numbers in relevant.items() if numbers and all(
        ref in outcomes.get(str(n), {}).get("cleared_source_conflicts", []) for n in numbers)}
    return {"state": "original_gap_roles_processed_with_retained_unresolved_batches_integration_required", "job": job.key,
        "reviewed_batches": len(outcomes), "retained_unresolved_batches": len(parked),
        "accounted_original_batches": len(outcomes) + len(parked), "original_batches": len(data["contexts"]),
        "parked_target_clauses": sum(len(row["all_target_clauses_unresolved"]) for row in parked.values()),
        "remaining_original_unresolved": len(set(data["original_unresolved"]) - resolved),
        "remaining_source_conflicts": len(set(data["original_source_conflicts"]) - cleared),
        "remaining_unmapped_additions": unmapped, "original_parent_issues_retained": len(data["inherited_parent_issues"]),
        "negative_checks": sum(row["negative_checks"] for row in outcomes.values()),
        "new_explanations": sum(row["new_explanations"] for row in outcomes.values()),
        "original_budgets_reset": False, "semantic_acceptance": False,
        "reader_publication_allowed": False, "source_deletion_allowed": False}


def worker(root, key):
    root, folder, _, old, recipe, data, proof = load(root, key)
    guard.checkpoint(root)
    with schema_lease(folder / "worker.lock", timeout=0):
        require(not (folder / "started.json").exists(), "single_worker_no_automatic_retry")
        restored = whole.read(folder / "independent-input-restore.json")
        require(restored["state"] == "corpus_gap_remaining_inputs_independently_restored" and restored["job"] == key
            and restored["original_root_reads"] == restored["model_calls"] == 0, "actual_independent_prefix_failure_and_input_restore")
        unused(old)
        job = previous.Job(root, old, recipe, data, folder)
        job.completed_batches = PREFIX
        retained(folder / "started.json", job.identity)
        try:
            result = stages(job, data, proof)
            retained(folder / "result.json", result)
            job.progress(result["state"], "complete")
            return result
        except BaseException as exc:
            failure = whole.failure_status(exc)
            retained(folder / ("failure-" + str(time.time_ns()) + ".json"), failure)
            job.progress(failure["state"], "retained_failure", reason_code=failure["reason_code"])
            raise


def verify(root, key):
    root, folder, prior, old, recipe, data, proof = load(root, key)
    state = whole.read(folder / "progress.json")
    protected = whole.read(folder / "guard/guard-progress.json")
    require(state["stage"] == "complete" and protected["state"] == "worker_exited" and protected["returncode"] == 0
        and not writing.identity_live(state) and not writing.identity_live(protected), "actual_successful_exited_remaining_roles")
    _, _, _, _, _, candidate, previous_proof = previous.load(root, key)
    summary, outcomes = prefix(root, prior, old, recipe, data, candidate, previous_proof)
    require(summary == proof["prefix_summary"] and outcomes == proof["prefix_outcomes"], "same_full_actual_reviewed_prefix")
    result = stages(previous.Job(root, old, recipe, data, folder, read_only=True), data, proof)
    require(result == whole.read(folder / "result.json"), "same_all_reviewed_and_retained_unresolved_batches")
    return {**result, "new_model_calls": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("command", choices=["prepare", "load", "worker", "guard", "verify"])
    args = parser.parse_args()
    try:
        if args.command == "guard":
            root, folder, *_ = load(args.root, args.job)
            result = guard.run(root, folder / "guard", [sys.executable, "-m", __spec__.name,
                "--root", str(root), "--job", args.job, "worker"])
        elif args.command == "load":
            load(args.root, args.job)
            result = {"state": "same_original_remaining_gap_roles_inputs", "job": args.job, "model_calls": 0}
        else:
            result = {"prepare": prepare, "worker": worker, "verify": verify}[args.command](args.root, args.job)
        print(json.dumps(result))
    except BaseException as exc:
        print(json.dumps(whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
