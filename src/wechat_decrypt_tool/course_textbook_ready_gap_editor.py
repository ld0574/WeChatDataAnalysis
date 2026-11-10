"""Use first editorial roles for a ready manuscript's actual coverage gaps.

Completed coverage and its independent result restore are required. Every real
gap is retained; accepted coverage is not asked again. Exact duplicate citation
IDs are canonicalized before the independent review without changing prose or
source sets. A complete invalid response parks that batch instead of retrying
it or discarding the remaining, independent first-use roles.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import copy
from pathlib import Path
import re
import sys
import time
import json

import jsonschema
from tokenizers import Tokenizer

from . import course_textbook_ready_first_coverage as ready
from . import course_textbook_corpus_gap_editor as original
from . import course_textbook_corpus_gap_remaining as failures
from .course_knowledge import schema_lease

whole, guard, writing, digest, retained = original.whole, original.guard, original.writing, original.digest, original.retained
POLICY = "ready-complete-coverage-first-editorial-gaps-retain-invalid-independent-batches-v1"
BASE_BINDINGS, BASE_SCHEMA, BASE_EDIT = original.bindings, original.wire_schema, original.EDIT
BASE_COMPILE = original.compile_candidate
NOTICE = """
本执行保留全部原负面意见。每个新讲解至少80个实际字符，但不要以重复、空话或无依据扩展凑长度。
每个N段都必须被你明确选入实际covered组；不能只生成段落却不关联任何真实目标。
引用只选实际支持讲解的原文。重复的完全相同引用编号仅作无损列表去重，引用集合和正文不变；
去重不是事实支持、充分覆盖、主题位置或独立审查通过的证明。不重写已接受的其他分句。
"""


def require(ok, code):
    if not ok:
        raise ValueError("requires_ready_gap_editor_" + code)


def bindings():
    return {"module_sha256": whole.sha(Path(__file__).read_bytes()), "original": BASE_BINDINGS(),
        "ready": ready.bindings(), "invalid_response_authenticator_sha256": whole.sha(Path(failures.__file__).read_bytes())}


def wire_schema(context, units):
    schema = BASE_SCHEMA(context, units)
    for branch in schema["oneOf"]:
        for paragraph in branch["properties"]["additions"]["properties"].values():
            paragraph["properties"]["citations"].pop("uniqueItems", None)
    return schema


def candidate(raw, context, units):
    jsonschema.validate(raw, wire_schema(context, units))
    canonical = copy.deepcopy(raw)
    changes = []
    for key, row in canonical["additions"].items():
        refs = row["citations"]
        unique = list(dict.fromkeys(refs))
        if refs != unique:
            changes.append({"addition": key, "original_count": len(refs), "unique_count": len(unique)})
            row["citations"] = unique
    # The frozen compiler still enforces every target, actual paragraph use,
    # reference bounds, teaching dispositions and complete source grouping.
    compiled = BASE_COMPILE(canonical, context, units)
    return compiled, {"raw_sha256": digest(raw), "candidate_sha256": digest(compiled),
        "exact_duplicate_reference_changes": changes, "prose_changed": False, "source_sets_changed": False,
        "semantic_acceptance": False}


@contextmanager
def installed():
    saved = original.bindings, original.wire_schema, original.compile_candidate, original.EDIT
    original.bindings, original.wire_schema = bindings, wire_schema
    original.compile_candidate = lambda *args: candidate(*args)[0]
    original.EDIT = BASE_EDIT + NOTICE
    try:
        yield
    finally:
        original.bindings, original.wire_schema, original.compile_candidate, original.EDIT = saved


def finished_inputs(bundle, tokenizer):
    root, folder = bundle.root, bundle.folder
    status, protected = [whole.read(path) for path in (bundle.job.root / "progress.json", folder / "guard-progress.json")]
    restored = whole.read(folder / "independent-result-restore.json")
    final = whole.read(folder / "cpu-finalization/result.json")
    final_status = whole.read(folder / "cpu-finalization/progress.json")
    require(not any(writing.identity_live(row) for row in (status, protected, final_status))
        and status["job"] == bundle.job.key and status["stage"] == "complete"
        and status["completed_batches"] == len(bundle.plan["batches"])
        and status["completed_clauses"] == bundle.plan["clauses"]
        and protected["state"] == "worker_exited" and protected["returncode"] == 0,
        "actual_complete_exited_coverage_and_finalizer")
    require(restored["state"] == "ready_first_full_coverage_results_independently_reconstructed"
        and restored["job"] == bundle.job.key and restored["parent_writer"] == bundle.contract["parent_writer"]
        and restored["actual_coverage_roles_replayed"] is True
        and restored["original_root_reads"] == restored["new_model_calls"] == 0
        and restored["plan_sha256"] == digest(bundle.plan)
        and restored["manuscript_sha256"] == digest(bundle.manuscript)
        and final["state"] == final_status["state"] == "actual_first_full_coverage_independently_restored_not_reader_accepted"
        and final["job"] == bundle.job.key and final["backup"] == restored
        and final["result"] == restored["result"] == whole.read(folder / "cpu-finalization/actual-result-proof.json"),
        "same_actual_independently_reconstructed_complete_result")
    manifest = Path(restored["backup"]) / "manifest.json"
    require(whole.sha(manifest.read_bytes()) == restored["manifest_sha256"], "independent_result_manifest")
    index = whole.read(manifest)
    require(index["mode"] == "results" and index["actual_result"] == restored["result"], "actual_result_backup_not_inputs_only")
    selected = [bundle.job.root / name for name in ("coverage-result.json", "review-plan.json", "progress.json", ready.first.CONTRACT)]
    selected += [ready.snapshot_path(root, bundle.contract["parent_writer"]), Path(tokenizer)]
    for path in selected:
        name = "output/course-knowledge/" + path.relative_to(root).as_posix()
        require(whole.sha(path.read_bytes()) == index["files"][name]["sha256"], "same_restored_complete_original_bytes")
    result = whole.read(bundle.job.root / "coverage-result.json")
    require(result["parent_writer"] == bundle.contract["parent_writer"] and result["job"] == bundle.job.key
        and result["unresolved_clauses"] == restored["result"]["unresolved_clauses"], "same_actual_coverage_gaps")
    return result, selected + [manifest, folder / "independent-result-restore.json", folder / "cpu-finalization/result.json"]


def editorial_inputs(manuscript, plan, result, coverage_folder, tokenizer):
    """Rebuild the exact private plan from complete originals and real negatives."""
    negative = {}
    for key, row in result["coverage"].items():
        if row["resolved"]:
            continue
        for assessment in row["assessments"]:
            name = assessment["verification_stage"] + ":" + assessment["group"]
            if name not in negative:
                path = Path(coverage_folder) / (assessment["verification_stage"] + ".json")
                checked = whole.read(path)["data"]["groups"][assessment["group"]]
                require(not checked["passed"], "actual_negative_independent_review")
                negative[name] = {**checked, "clauses": []}
            negative[name]["clauses"].append(key)
    with installed():
        contexts = original.make_batches(manuscript, plan, result, negative, Tokenizer.from_file(str(tokenizer)))
        ids = original.targets(result, plan)
    return {"parent_proof": manuscript["proof"], "contexts": contexts,
        "units": {key: plan["units"][key] for key in ids},
        "original_unresolved": [key for key, row in result["coverage"].items() if not row["resolved"]],
        "original_source_conflicts": result["classification_conflicts"], "inherited_parent_issues": manuscript["issues"]}


def prepare(root, parent, tokenizer):
    root, tokenizer = Path(root).resolve(), Path(tokenizer).resolve()
    guard.checkpoint(root)
    require(tokenizer.is_relative_to(root), "portable_complete_tokenizer")
    with schema_lease(root / "wiki/corpus-final-gap-prepare.lock", timeout=0):
        reserved = original.registration(root, parent)
        require(not reserved.exists(), "same_one_editorial_registration_per_parent")
        bundle = ready.load(root, parent, tokenizer)
        result, selected = finished_inputs(bundle, tokenizer)
        data = editorial_inputs(bundle.manuscript, bundle.plan, result, bundle.job.root, tokenizer)
        contexts, ids = data["contexts"], list(data["units"])
        with installed():
            # The external backup manifest is checked above; the portable plan
            # binds its hash through the already-restored local receipt.
            paths = {p for p in selected if p.is_relative_to(root)}
            paths.update(root / name for name in bundle.manuscript["proof"]["files"])
            paths.update(bundle.job.root.rglob("*.json"))
            paths.update(bundle.folder / name for name in ("guard-progress.json", "cpu-finalization/actual-result-proof.json", "cpu-finalization/progress.json"))
            recipe = {"policy": original.POLICY, "ready_gap_policy": POLICY, "parent": parent, "coverage_job": bundle.job.key,
                "code": bindings(), "inputs_sha256": digest(data), "tokenizer": tokenizer.relative_to(root).as_posix(),
                "models": bundle.job.models, "files": {p.relative_to(root).as_posix(): whole.sha(p.read_bytes()) for p in sorted(paths)},
                "batches": len(contexts), "target_clauses": len(ids), "original_unresolved_clauses": len(data["original_unresolved"]),
                "source_conflicts": len(data["original_source_conflicts"]), "attempts_per_role": 1, "semantic_repairs": 0,
                "original_budgets_reset": False, "original_coverage_files_changed": False,
                "reader_publication_allowed": False, "source_deletion_allowed": False}
            key = digest(recipe)[:32]
            folder = original.folder_for(root, key)
            require(not folder.exists(), "first_ready_gap_execution")
            retained(folder / "recipe.json", recipe)
            retained(folder / "inputs.json", data)
            (folder / "guard").mkdir()
            retained(reserved, {"parent": parent, "job": key, "recipe_sha256": digest(recipe)})
            return {"state": "first_ready_gap_inputs_prepared", "job": key, "batches": len(contexts),
                "target_clauses": len(ids), "source_conflicts": len(data["original_source_conflicts"]), "new_model_calls": 0}


def load(root, key):
    with installed():
        loaded = original.load(root, key)
    require(loaded[2].get("ready_gap_policy") == POLICY, "first_use_ready_gap_contract")
    return loaded


def stages(job, recipe, data):
    outcomes, parked = {}, {}
    for number, context in enumerate(data["contexts"]):
        prefix = f"batch-{number:04d}"
        failed_path = job.root / (prefix + "-unresolved.json")
        failed = whole.read(failed_path) if job.read_only and failed_path.exists() else None
        candidate_value = None
        for role in ("edit", "review"):
            if role == "review":
                context = {**context, "candidate": {"additions": candidate_value["additions"],
                    "groups": {f"G{i:04d}": row for i, row in enumerate(candidate_value["groups"])}}}
            schema = wire_schema(context, data["units"]) if role == "edit" else original.review_schema(candidate_value, context)
            prompt = original.EDIT if role == "edit" else original.REVIEW
            validate = (lambda v, c=context: candidate(v, c, data["units"])) if role == "edit" else (
                lambda v, c=context: original.assess(candidate_value, v, c))
            try:
                if failed and failed["actual_failure"]["stage"] == prefix + "-" + role:
                    failure = failures.rejected(job.root, recipe, data, number, role, context, schema)
                else:
                    value = job.stage(prefix + "-" + role, prompt, schema, context, validate)
                    if role == "edit":
                        candidate_value, derivation = candidate(value, context, data["units"])
                        retained(job.root / (prefix + "-reference-derivation.json"), derivation, read_only=job.read_only)
                    else:
                        outcome = original.assess(candidate_value, value, context)
                        outcome.update(candidate_sha256=digest(candidate_value), audit_sha256=digest(value))
                        retained(job.root / (prefix + "-result.json"), outcome, read_only=job.read_only)
                        outcomes[str(number)] = outcome
                    continue
            except ValueError as exc:
                require(not job.read_only and str(exc) == "wiki_stage_attempt_budget_exhausted_" + prefix + "-" + role,
                    "no_transport_pause_or_unbound_failure_continuation")
                failure = failures.rejected(job.root, recipe, data, number, role, context, schema)
            parked[str(number)] = {"batch": number, "actual_failure": failure,
                "all_target_clauses_unresolved": list(data["contexts"][number]["target_clauses"]),
                "new_attempts": 0, "semantic_acceptance": False}
            if role == "edit":
                require(not list((job.root / "attempts").glob(prefix + "-review-*.json"))
                    and not (job.root / (prefix + "-review.json")).exists(), "invalid_edit_review_stays_unused")
            retained(failed_path, parked[str(number)], read_only=job.read_only)
            break
        job.completed_batches = len(outcomes)
        job.progress("first_ready_gap_batches_accounted_with_failures_retained", prefix,
            accounted_batches=len(outcomes) + len(parked), retained_unresolved_batches=len(parked))
    resolved = {key for row in outcomes.values() for key in row["resolved_target_clauses"]}
    relevant = {ref: [n for n, context in enumerate(data["contexts"]) if ref in context["source_conflicts"]]
        for ref in data["original_source_conflicts"]}
    cleared = {ref for ref, numbers in relevant.items() if numbers and all(
        ref in outcomes.get(str(n), {}).get("cleared_source_conflicts", []) for n in numbers)}
    return {"state": "first_ready_gap_roles_finished_integration_and_all_parent_issues_required", "job": job.key,
        "reviewed_batches": len(outcomes), "retained_unresolved_batches": len(parked), "original_batches": len(data["contexts"]),
        "accounted_batches": len(outcomes) + len(parked), "remaining_original_unresolved": len(set(data["original_unresolved"]) - resolved),
        "remaining_source_conflicts": len(set(data["original_source_conflicts"]) - cleared),
        "new_explanations": sum(row["new_explanations"] for row in outcomes.values()),
        "negative_checks": sum(row["negative_checks"] for row in outcomes.values()),
        "original_parent_issues_retained": len(data["inherited_parent_issues"]), "new_attempts_for_parked_batches": 0,
        "original_budgets_reset": False, "semantic_acceptance": False, "reader_publication_allowed": False, "source_deletion_allowed": False}


def worker(root, key):
    root, folder, recipe, data = load(root, key)
    guard.checkpoint(root)
    with schema_lease(folder / "worker.lock", timeout=0):
        require(not (folder / "started.json").exists(), "worker_once_no_retry")
        restored = whole.read(folder / "independent-input-restore.json")
        require(restored["state"] == "ready_gap_inputs_independently_restored" and restored["job"] == key
            and restored["inputs_sha256"] == recipe["inputs_sha256"] and restored["original_root_reads"] == restored["new_model_calls"] == 0,
            "actual_independent_complete_input_restore")
        with installed():
            job = original.Job(root, folder, recipe, data)
            retained(folder / "started.json", job.identity)
            try:
                result = stages(job, recipe, data)
                retained(folder / "result.json", result)
                job.progress(result["state"], "complete")
                return result
            except BaseException as exc:
                failure = whole.failure_status(exc)
                retained(folder / ("failure-" + str(time.time_ns()) + ".json"), failure)
                job.progress(failure["state"], "retained_failure")
                raise


def verify(root, key):
    root, folder, recipe, data = load(root, key)
    status = whole.read(folder / "progress.json")
    require(status["stage"] == "complete" and not writing.identity_live(status), "actual_complete_exited_worker")
    with installed():
        result = stages(original.Job(root, folder, recipe, data, read_only=True), recipe, data)
    require(result == whole.read(folder / "result.json"), "same_actual_result_and_retained_failures")
    return {**result, "new_model_calls": 0}


def guarded(root, key):
    root, folder, _, _ = load(root, key)
    with schema_lease(folder / "dispatch.lock", timeout=0):
        require(not (folder / "dispatch-started.json").exists(), "first_dispatch_no_automatic_retry")
        owner = guard.psutil.Process()
        retained(folder / "dispatch-started.json", {"pid": owner.pid, "created": owner.create_time()})
        return guard.run(root, folder / "guard", [sys.executable, "-m", __spec__.name,
            "--root", str(root), "--job", key, "worker"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--parent")
    parser.add_argument("--job")
    parser.add_argument("--tokenizer", type=Path)
    parser.add_argument("command", choices=["prepare", "load", "worker", "guard", "verify"])
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = prepare(args.root, args.parent, args.tokenizer)
        elif args.command == "load":
            _, _, recipe, _ = load(args.root, args.job)
            result = {"state": "same_first_ready_gap_inputs_authenticated", "job": args.job,
                "batches": recipe["batches"], "target_clauses": recipe["target_clauses"], "new_model_calls": 0}
        else:
            result = {"worker": worker, "guard": guarded, "verify": verify}[args.command](args.root, args.job)
        print(json.dumps(result))
    except BaseException as exc:
        failure = whole.failure_status(exc)
        if args.command == "prepare" and isinstance(args.parent, str) and re.fullmatch(r"[a-f0-9]{32}", args.parent):
            retained(args.root.resolve() / "wiki/ready-gap-preparations" / args.parent
                / ("failure-" + str(time.time_ns()) + ".json"), failure)
        print(json.dumps(failure))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
