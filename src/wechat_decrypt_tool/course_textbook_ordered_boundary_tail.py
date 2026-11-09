"""Continue unused original coverage roles after an exhausted ordered proposal.

No failed proposal is promoted to an accepted cache. Valid complete source
mappings keep their judgments; defective sources become explicit unassessed
missing proposals for the original unused independent reviewer. Later unused
proposals describe consecutive end boundaries with a mandatory final group.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import copy
import json
from pathlib import Path
import re
import sys
import time
from types import SimpleNamespace

from . import course_textbook_ordered_coverage as ordered
from .course_knowledge import schema_lease

coverage, whole, assignments = ordered.coverage, ordered.whole, ordered.assignments
digest = ordered.digest
PREFIX = 5
FAILED = "batch-0005-body-0000-review"
POLICY = "original-unused-ordered-coverage-review-and-boundary-tail-v1"
CONTRACT = "execution.json"
DERIVATION = "-boundary-derivation.json"
NOTICE = """
输出 source_groups 中每个来源的 prefix_groups 和 last_group，不输出逐句组号数组。
ordered_clause_ids 给出该来源本次目标分句的完整顺序，从第1句到最后一句都必须判断。
prefix_groups 可为空；每组 through_position 是该组最后一个成员在此顺序中的位置（从1开始）。
前组从第1句开始，后组从上一组结束的下一句开始；through_position 必须严格递增，且小于分句总数。
last_group 是必填的最后一组，从最后一个前组的下一句一直判断到该来源最后一句。
如全部判断相同，prefix_groups=[]，last_group 判断全部；如不同必须分组，不能丢掉任何成员。
每组仍含 kind、真实 article_items 和具体 reason，全部成员均须满足同一判断。
边界仅无损说明成员，不是事实依据；完整原文、相邻上下文和真实文章都必须逐项核对。
"""
VERIFY_NOTICE = """
candidate_derivation 仅记录真实候选的转录范围，不是事实依据或通过证明。
program_unassessed_clauses 的原分组索引无效，程序没有推断其覆盖或教学性质，只保守提出 missing。
这些 missing 仅在完整原文确有教学且本次真实正文确未讲清时才可 passed=true，否则保留未决。
其他组仍须逐成员独立检查。不得把边界、失败记录、候选理由或程序分组当作语义证据。
"""


def require(ok, code):
    if not ok:
        raise ValueError("requires_ordered_boundary_tail_" + code)


@contextmanager
def original_cli_identity():
    """Keep all frozen hashes, translating only the original CLI module key."""
    original = ordered.bindings

    def bindings():
        value = original()
        require(ordered.__name__ in value and "__main__" not in value, "known_original_cli_identity")
        value["__main__"] = value.pop(ordered.__name__)
        return value

    ordered.bindings = bindings
    try:
        yield
    finally:
        ordered.bindings = original


def wire_schema(ids, article_ids, units):
    group = copy.deepcopy(coverage.schema_for(ids, article_ids)["properties"]["groups"]["items"])
    for branch in group["oneOf"]:
        branch["properties"].pop("clauses")
        branch["required"].remove("clauses")
    sources = {}
    for ref, keys in assignments.by_source(ids, units).items():
        prefix = copy.deepcopy(group)
        for branch in prefix["oneOf"]:
            branch["properties"]["through_position"] = {"type": "integer", "minimum": 1, "maximum": max(1, len(keys) - 1)}
            branch["required"].append("through_position")
        sources[ref] = coverage.object_schema({"prefix_groups": {"type": "array", "minItems": 0,
            "maxItems": len(keys) - 1, "items": prefix}, "last_group": copy.deepcopy(group)})
    return coverage.object_schema({"source_groups": coverage.object_schema(sources)})


def compile_wire(raw, ids, article_ids, units):
    coverage.jsonschema.validate(raw, wire_schema(ids, article_ids, units))
    groups = []
    for ref, keys in assignments.by_source(ids, units).items():
        row, start = raw["source_groups"][ref], 0
        for group in row["prefix_groups"]:
            end = group["through_position"]
            require(end > start, "strictly_increasing_nonempty_boundaries")
            judgment = {k: copy.deepcopy(v) for k, v in group.items() if k != "through_position"}
            groups.append({**judgment, "clauses": keys[start:end]})
            start = end
        groups.append({**copy.deepcopy(row["last_group"]), "clauses": keys[start:]})
    result = {"groups": groups}
    coverage.validate_groups(result, ids, article_ids, units)
    return result


def unassessed_candidate(raw, ids, article_ids, units):
    """Retain whole valid source mappings; never guess invalid group members."""
    coverage.jsonschema.validate(raw, ordered.wire_schema(ids, article_ids, units))
    groups, unassessed, defective = [], [], []
    for ref, keys in assignments.by_source(ids, units).items():
        try:
            valid = ordered.compile_wire({"source_groups": {ref: copy.deepcopy(raw["source_groups"][ref])}},
                keys, article_ids, units)
        except ValueError as exc:
            require(str(exc) == "requires_coverage_assignments_contiguous_used_source_groups", "known_mapping_failure_only")
            defective.append(ref)
            unassessed.extend(keys)
            runs = []
            for cid in keys:
                if not runs or units[cid]["ordinal"] != units[runs[-1][-1]]["ordinal"] + 1:
                    runs.append([])
                runs[-1].append(cid)
            groups.extend({"clauses": run, "kind": "missing", "article_items": [],
                "reason": "Program proposal only: invalid original group mapping; no coverage or non-teaching judgment is inferred."}
                for run in runs)
        else:
            groups.extend(valid["groups"])
    require(bool(unassessed), "actual_defective_source_mappings")
    result = {"groups": groups}
    coverage.validate_groups(result, ids, article_ids, units)
    return result, {"program_unassessed_clauses": unassessed, "defective_sources": defective,
        "preserved_valid_members": len(ids) - len(unassessed), "semantic_acceptance": False,
        "invalid_assignments_relabelled": False, "original_failure_obligations_retained": True}


def failed_artifact(job, manuscript, plan):
    batch = plan["batches"][PREFIX]
    items = coverage.article_items(manuscript["private"], manuscript["inventory"])
    ids, article_ids = batch["clauses"], batch["article_blocks"][0]
    context = coverage.context_for(manuscript["inventory"], plan["units"], ids, items, article_ids)
    packet = {"sources": {ref: manuscript["inventory"]["sources"][ref]
        for ref in sorted(set(context["sources"]) | set(context["context_sources"]))}}
    actual_context = {**context, "ordered_clause_ids": assignments.by_source(ids, plan["units"]),
        "assignment_arrays_are_factual_evidence": False}
    prompt = coverage.REVIEW + ordered.NOTICE
    job.model, job.model_digest = "course-defiant:q4", job.models["course-defiant:q4"]
    schema, binding = job.stage_input(prompt, ordered.wire_schema(ids, article_ids, plan["units"]),
        actual_context, evidence_packet=packet)
    paths = sorted((job.root / "attempts").glob(FAILED + "-[0-9][0-9].json"))
    require([p.name for p in paths] == [FAILED + "-01.json", FAILED + "-02.json"]
        and not (job.root / (FAILED + ".json")).exists(), "original_two_exhausted_proposals_without_accepted_cache")
    receipts = [(p, whole.read(p)) for p in job.root.glob("response-*.json")]
    previous, files, original = "", {}, None
    for number, path in enumerate(paths, 1):
        row = whole.read(path)
        messages = [{"role": "system", "content": prompt}, {"role": "user", "content": json.dumps(
            {**actual_context, "previous_validation_failure": previous}, ensure_ascii=False)}]
        require(row["stage"] == FAILED and row["attempt"] == number and not row.get("accepted_structure")
            and row["input_sha256"] == binding and row["request_sha256"] == digest(messages)
            and row["model_digest"] == job.model_digest
            and row["validation_error"] == "requires_coverage_assignments_contiguous_used_source_groups",
            "real_original_failed_request_chain")
        whole.editor.e.verify_response(job.root, FAILED, row)
        matches = [p for p, receipt in receipts if receipt["stage"] == FAILED
            and receipt["request_sha256"] == row["request_sha256"] and receipt["schema_sha256"] == digest(schema)
            and receipt["response"]["message"]["content"] == row["raw_response"] and receipt["metrics"] == row["model_metrics"]]
        require(len(matches) == 1 and row["model_metrics"]["done_reason"] == "stop", "complete_real_failed_wire_and_schema")
        raw = json.loads(row["raw_response"])
        coverage.jsonschema.validate(raw, schema)
        failure = None
        try:
            ordered.compile_wire(raw, ids, article_ids, plan["units"])
        except ValueError as exc:
            failure = str(exc)
        require(failure == row["validation_error"], "reproduced_original_mapping_failure")
        if original is None:
            original = raw
        files.update({p.name: whole.sha(p.read_bytes()) for p in (path, matches[0])})
        previous = row["validation_error"]
    value, metadata = unassessed_candidate(original, ids, article_ids, plan["units"])
    return {"policy": POLICY, "data": value, "receipt": {**metadata, "original_files": files,
        "selected_original_attempt": paths[0].name, "original_candidate_sha256": digest(original),
        "original_context_sha256": digest(context), "original_packet_sha256": digest(packet),
        "derived_candidate_sha256": digest(value), "new_proposal_model_calls": 0, "original_budget_reset": False}}


def folder_for(root, key):
    return root / "wiki/ordered-coverage-dispatch" / key / "unused-independent-boundary-tail"


def prior_stopped(status, guard):
    require(status["state"] == "stopped" and status["completed_batches"] == PREFIX
        and status["reason_code"] == "wiki_stage_attempt_budget_exhausted_" + FAILED
        and guard["state"] == "worker_exited" and guard.get("returncode") == 1
        and not coverage.writing.identity_live(status) and not coverage.writing.identity_live(guard),
        "actual_stopped_exited_original_sixth_batch")


def unused(job, plan):
    require(not list((job.root / "attempts").glob("batch-0005-body-0000-groups-*.json"))
        and not list(job.root.glob("batch-0005-body-0000-groups-*.json")), "original_failed_batch_independent_roles_unused")
    require(all(not list((job.root / "attempts").glob(f"batch-{number:04d}-*.json"))
        and not list(job.root.glob(f"batch-{number:04d}-*.json")) for number in range(PREFIX + 1, len(plan["batches"]))),
        "all_later_original_roles_unused")
    require(len(plan["batches"][PREFIX]["article_blocks"]) == 1, "original_failed_batch_single_body_block")


def prefix_proof(manuscript, plan, job):
    states = ordered.reconstruct_batches(manuscript, plan, job, PREFIX)
    return states, {"batches": PREFIX, "clauses": len(states), "unresolved": sum(not s["resolved"] for s in states.values()),
        "states_sha256": digest(states), "authenticated_model_stages": len(job.authenticated_stages), "model_calls": 0}


def execution(job, original, artifact, status, guard, proof):
    return {"policy": POLICY, "job": job.key, "parent_writer": original["parent_writer"],
        "original_execution_sha256": digest(original), "plan_sha256": digest(job.plan),
        "derivation_sha256": digest(artifact), "prior_progress_sha256": digest(status),
        "prior_guard_sha256": digest(guard), "verified_prefix_sha256": digest(proof),
        "module_sha256": whole.sha(Path(__file__).read_bytes()),
        "notice_sha256": digest(NOTICE), "verify_notice_sha256": digest(VERIFY_NOTICE),
        "original_stage_attempt_budget": 2, "semantic_repairs": 0, "original_budgets_reset": False,
        "unused_roles_only": True, "reader_publication_allowed": False, "source_deletion_allowed": False}


def original_inputs(root, key, tokenizer):
    with original_cli_identity():
        return ordered.load(root, key, tokenizer)


def prepare(root, key, tokenizer):
    root = Path(root).resolve()
    ordered.private_guard.checkpoint(root)
    with schema_lease(root / "wiki" / (key + "-whole-coverage-worker.lock"), timeout=0):
        root, manuscript, plan, job, original = original_inputs(root, key, tokenizer)
        folder = folder_for(root, job.key)
        require(not (folder / CONTRACT).exists(), "prepare_once_without_new_budget")
        status = whole.read(job.root / "progress.json")
        guard = whole.read(root / "wiki/ordered-coverage-dispatch" / job.key / "tail/guard-progress.json")
        prior_stopped(status, guard)
        unused(job, plan)
        _, proof = prefix_proof(manuscript, plan, job)
        artifact = failed_artifact(job, manuscript, plan)
        contract = execution(job, original, artifact, status, guard, proof)
        for name, value in [(CONTRACT, contract), ("failed-proposal-derivation.json", artifact),
                ("prior-progress.json", status), ("prior-guard-progress.json", guard), ("verified-prefix.json", proof)]:
            assignments.retained(folder / name, value)
        return {"state": "original_unused_independent_and_boundary_tail_prepared", "job": job.key,
            "verified_prefix": proof, "unassessed_failed_batch_clauses": len(artifact["receipt"]["program_unassessed_clauses"]),
            "preserved_valid_members": artifact["receipt"]["preserved_valid_members"],
            "remaining_original_batches": len(plan["batches"]) - PREFIX, "model_calls": 0, "reader_publication_allowed": False}


def load(root, key, tokenizer):
    root, manuscript, plan, job, original = original_inputs(root, key, tokenizer)
    folder = folder_for(root, job.key)
    status, guard = whole.read(folder / "prior-progress.json"), whole.read(folder / "prior-guard-progress.json")
    prior_stopped(status, guard)
    require(whole.read(root / "wiki/ordered-coverage-dispatch" / job.key / "tail/guard-progress.json") == guard,
        "unchanged_original_failed_guard")
    proof = whole.read(folder / "verified-prefix.json")
    artifact = failed_artifact(job, manuscript, plan)
    assignments.retained(folder / "failed-proposal-derivation.json", artifact, read_only=True)
    contract = whole.read(folder / CONTRACT)
    require(contract == execution(job, original, artifact, status, guard, proof), "frozen_original_unreset_tail_execution")
    return SimpleNamespace(root=root, manuscript=manuscript, plan=plan, original_job=job, original=original,
        folder=folder, artifact=artifact, contract=contract, proof=proof, prior_status=status, tokenizer=tokenizer)


class BoundaryTailJob(ordered.OrderedJob):
    def stage(self, name, prompt, schema, context, validate, **kwargs):
        match = re.fullmatch(r"batch-([0-9]{4})-body-([0-9]{4})-(review|groups-[0-9]{4}-verify)", name)
        require(match is not None, "original_role_name")
        if int(match[1]) < PREFIX:
            return super().stage(name, prompt, schema, context, validate, **kwargs)
        if name == FAILED:
            require(prompt == coverage.REVIEW and digest(context) == self.failed["receipt"]["original_context_sha256"]
                and digest(kwargs["evidence_packet"]) == self.failed["receipt"]["original_packet_sha256"],
                "exact_original_failed_target_context")
            require(not (self.root / (FAILED + ".json")).exists(), "no_accepted_failed_proposal_cache")
            value = copy.deepcopy(self.failed["data"])
            validate(value)
            return value
        if self.read_only:
            require((self.root / (name + ".json")).is_file(), "existing_original_role_without_inference")
        if name.endswith("-review"):
            require(int(match[1]) > PREFIX and prompt == coverage.REVIEW, "original_later_proposal_only")
            ids, article_ids = list(context["clauses"]), list(context["article"])
            order = assignments.by_source(ids, self.plan["units"])
            actual_context = {**context, "ordered_clause_ids": order, "boundaries_are_factual_evidence": False}
            actual_prompt = prompt + NOTICE
            actual_schema = wire_schema(ids, article_ids, self.plan["units"])
            with assignments.prompt_alias("REVIEW", actual_prompt):
                raw = coverage.CoverageJob.stage(self, name, actual_prompt, actual_schema, actual_context,
                    lambda row: compile_wire(row, ids, article_ids, self.plan["units"]), **kwargs)
            value = compile_wire(raw, ids, article_ids, self.plan["units"])
            artifact = {"policy": POLICY, "actual_payload_sha256": digest(raw), "compiled_candidate_sha256": digest(value),
                "ordered_clause_ids_sha256": digest(order), "actual_cache_sha256": whole.sha((self.root / (name + ".json")).read_bytes()),
                "program_unassessed_clauses": [], "every_clause_assigned_once": True, "semantic_acceptance": False}
            assignments.retained(self.root / (name + DERIVATION), artifact, read_only=self.read_only)
            validate(value)
        else:
            require(prompt == coverage.VERIFY, "original_independent_role")
            proposal = name.split("-groups-")[0] + "-review"
            artifact = self.failed if proposal == FAILED else whole.read(self.root / (proposal + DERIVATION))
            unassessed = artifact["receipt"]["program_unassessed_clauses"] if proposal == FAILED else []
            selected = {cid for group in context["groups"].values() for cid in group["clauses"]}
            actual_context = {**context, "candidate_derivation": {"artifact_sha256": digest(artifact),
                "program_unassessed_clauses": [cid for cid in unassessed if cid in selected], "is_factual_evidence": False}}
            actual_prompt = prompt + VERIFY_NOTICE
            with assignments.prompt_alias("VERIFY", actual_prompt):
                value = coverage.CoverageJob.stage(self, name, actual_prompt, schema, actual_context, validate, **kwargs)
        self.authenticated_stages.add(name)
        return value


class ReadOnlyBoundaryTailJob(BoundaryTailJob):
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
        require(value == self.expected_root and (value / "manifest.json").is_file(), "existing_exact_original_job")
        self._root = value


def tail_job(bundle, *, read_only):
    cls = ReadOnlyBoundaryTailJob if read_only else BoundaryTailJob
    options = {"expected_root": bundle.original_job.root} if read_only else {}
    job = cls(coverage.writing.TextbookStore(bundle.root), bundle.manuscript, bundle.plan,
        bundle.tokenizer, ordered.ADAPTER, "section-0008", **options)
    require(job.key == bundle.original_job.key and job.max_attempts == 2, "same_original_job_and_role_budget")
    job.failed = bundle.artifact
    return job


def backup(bundle, job):
    paths = [p for p in job.root.rglob("*.json") if p.name != "progress.json"]
    paths.extend(p for p in bundle.folder.glob("*.json") if p.name not in {"guard-progress.json", "progress.json"})
    paths.append(Path(__file__))
    coverage.backup(job, paths, "boundary-tail-immutable-evidence")


def result_for(bundle, job, states):
    result = coverage.compile_result(bundle.manuscript, bundle.plan, job, states)
    unresolved = bundle.artifact["receipt"]["program_unassessed_clauses"]
    require(all(not states[cid]["resolved"] for cid in unresolved), "unassessed_failed_members_still_unresolved")
    result.update(policy=POLICY, continuation_execution_sha256=digest(bundle.contract),
        original_failed_proposal_obligations={"stage": FAILED, "clauses": unresolved,
            "derivation_sha256": digest(bundle.artifact), "independent_review_does_not_imply_proposal_acceptance": True},
        original_failed_proposal_unassessed_clauses=len(unresolved))
    return result


def public_result(result):
    return {k: v for k, v in coverage.public_result(result).items() if k != "original_failed_proposal_obligations"}


def run(root, key, tokenizer):
    root = Path(root).resolve()
    ordered.private_guard.checkpoint(root)
    with schema_lease(root / "wiki" / (key + "-whole-coverage-worker.lock"), timeout=0):
        bundle = load(root, key, tokenizer)
        job = tail_job(bundle, read_only=False)
        require(not (bundle.folder / "worker-started.json").exists(), "worker_once_without_automatic_retry")
        require(whole.read(job.root / "progress.json") == bundle.prior_status, "original_stopped_status_before_continuation")
        unused(job, bundle.plan)
        states, proof = prefix_proof(bundle.manuscript, bundle.plan, bundle.original_job)
        require(proof == bundle.proof, "actual_same_verified_prefix")
        assignments.retained(bundle.folder / "worker-started.json", job.identity)
        job.completed_batches, job.completed_clauses = PREFIX, len(states)
        items = coverage.article_items(bundle.manuscript["private"], bundle.manuscript["inventory"])
        try:
            backup(bundle, job)
            for number in range(PREFIX, len(bundle.plan["batches"])):
                rows = coverage.review_batch(job, bundle.manuscript["inventory"], bundle.plan, items,
                    bundle.plan["batches"][number], number)
                assignments.retained(job.root / f"batch-{number:04d}-result.json", rows)
                states.update(rows)
                job.completed_batches += 1
                job.completed_clauses += len(rows)
                job.progress("reviewing_original_unused_boundary_tail", f"batch-{number:04d}")
                backup(bundle, job)
            result = result_for(bundle, job, states)
            assignments.retained(job.root / "coverage-result.json", result)
            backup(bundle, job)
            job.progress(result["state"], "complete", unresolved_clauses=result["unresolved_clauses"])
            return public_result(result)
        except BaseException as exc:
            failure = whole.failure_status(exc)
            assignments.retained(job.root / "failures" / (str(time.time_ns()) + ".json"), failure)
            job.progress(failure["state"], job.current, **{k: v for k, v in failure.items() if k != "state"})
            raise


def verify(root, key, tokenizer):
    root = Path(root).resolve()
    with schema_lease(root / "wiki" / (key + "-whole-coverage-worker.lock"), timeout=0):
        bundle = load(root, key, tokenizer)
        status = whole.read(bundle.original_job.root / "progress.json")
        require(not coverage.writing.identity_live(status) and status["completed_batches"] == len(bundle.plan["batches"])
            and status["state"] == "whole_manuscript_requires_coverage_or_classification_repair", "actual_complete_exited_tail")
        states, proof = prefix_proof(bundle.manuscript, bundle.plan, bundle.original_job)
        require(proof == bundle.proof, "same_completed_original_prefix")
        job = tail_job(bundle, read_only=True)
        items = coverage.article_items(bundle.manuscript["private"], bundle.manuscript["inventory"])
        for number in range(PREFIX, len(bundle.plan["batches"])):
            rows = coverage.review_batch(job, bundle.manuscript["inventory"], bundle.plan, items,
                bundle.plan["batches"][number], number)
            require(whole.read(job.root / f"batch-{number:04d}-result.json") == rows, "actual_tail_batch_result")
            states.update(rows)
        result = result_for(bundle, job, states)
        require(result == whole.read(job.root / "coverage-result.json") and status["completed_clauses"] == len(states)
            and status["unresolved_clauses"] == result["unresolved_clauses"]
            and whole.read(job.root / "progress.json") == status, "same_complete_actual_result_and_stable_status")
        return {"state": "original_boundary_tail_reconstructed_without_inference", "job": job.key,
            "verified_batches": len(bundle.plan["batches"]), "clauses": len(states),
            "unresolved_clauses": result["unresolved_clauses"],
            "original_failed_proposal_unassessed_clauses": result["original_failed_proposal_unassessed_clauses"],
            "authenticated_model_stages": len(job.authenticated_stages) + proof["authenticated_model_stages"],
            "model_calls": 0, "reader_publication_allowed": False}


def guarded(root, key, tokenizer):
    bundle = load(root, key, tokenizer)
    return ordered.private_guard.run(bundle.root, bundle.folder, [sys.executable, "-m", __spec__.name,
        "--root", str(bundle.root), "--parent", key, "--tokenizer", str(Path(tokenizer).resolve()), "worker"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--parent", required=True)
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("command", choices=["prepare", "load", "worker", "guard", "verify"])
    args = parser.parse_args()
    try:
        if args.command == "load":
            bundle = load(args.root, args.parent, args.tokenizer)
            result = {"state": "original_unused_boundary_tail_inputs_authenticated", "job": bundle.original_job.key,
                "model_calls": 0, "reader_publication_allowed": False}
        else:
            result = {"prepare": prepare, "worker": run, "guard": guarded, "verify": verify}[args.command](
                args.root, args.parent, args.tokenizer)
        print(json.dumps(result))
    except BaseException as exc:
        print(json.dumps(whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
