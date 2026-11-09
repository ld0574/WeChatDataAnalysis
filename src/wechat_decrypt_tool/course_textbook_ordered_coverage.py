"""First-use whole-source coverage with lossless ordered assignment arrays.

Only an authenticated complete bounded-paragraph manuscript is supported. The
parent's single coverage contract is registered before any model role. A finite
three-batch pilot retains the full plan and original two-attempt role budgets.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import re
import sys
import time

from tokenizers import Tokenizer

from . import course_textbook_bounded_paragraph_resume as parent
from . import course_textbook_whole_coverage_assignments as assignments
from . import course_wiki_private_guard as private_guard
from .course_knowledge import schema_lease

coverage, whole = assignments.coverage, assignments.whole
digest = coverage.digest
BASE = coverage.CoverageJob
POLICY = "first-whole-source-coverage-complete-ordered-assignment-arrays-v1"
CONTRACT = "ordered-assignment-execution.json"
PILOT_BATCHES = 3
ADAPTER = "complete-bounded-paragraph-ordered-assignment-arrays"
NOTICE = """
输出 source_groups 中每个来源的 groups 和 assignments 数组，不重复输出长分句编号。
ordered_clause_ids 明确给出每个来源的完整目标顺序；assignments[i] 只对应同来源 ordered_clause_ids[i]。
数组长度必须与该来源目标分句数完全相同，每个值是该来源 groups 的从零组号。
组号从0开始、连续且不递减，每组必须使用；不得跨来源、跨不连续成员或改动分句顺序。
每个分句都须判断，短句、代码、重复、非教学也不能省略。每组全部成员都必须满足同一判断。
groups 中仍写 kind、真实 article_items 和具体 reason；完整原文和正文都必须逐项核对。
"""
VERIFY_NOTICE = """
candidate_encoding 只绑定实际模型输出的顺序和程序无损转录，不是事实依据或语义证明。
程序没有判断任何分句的教学性质、覆盖或遗漏；仍逐组逐成员独立核对完整原文和实际正文。
"""


def require(ok, code):
    if not ok:
        raise ValueError("requires_ordered_coverage_" + code)


def wire_schema(ids, article_ids, units):
    result = assignments.wire_schema(ids, article_ids, units)
    for ref, keys in assignments.by_source(ids, units).items():
        props = result["properties"]["source_groups"]["properties"][ref]["properties"]
        props["assignments"] = {"type": "array", "minItems": len(keys), "maxItems": len(keys),
            "items": {"type": "integer", "minimum": 0, "maximum": len(keys) - 1}}
    return result


def compile_wire(raw, ids, article_ids, units):
    coverage.jsonschema.validate(raw, wire_schema(ids, article_ids, units))
    value = copy.deepcopy(raw)
    for ref, keys in assignments.by_source(ids, units).items():
        row = value["source_groups"][ref]
        row["assignments"] = dict(zip(keys, row["assignments"], strict=True))
    return assignments.compile_wire(value, ids, article_ids, units)


def bindings():
    modules = (sys.modules[__name__], parent, assignments, private_guard, coverage)
    return {m.__name__: whole.sha(Path(m.__file__).read_bytes()) for m in modules}


class OrderedJob(BASE):
    read_only = False

    def __init__(self, *args):
        super().__init__(*args)
        self.authenticated_stages = set()

    def stage(self, name, prompt, schema, context, validate, **kwargs):
        require(re.fullmatch(r"batch-[0-9]{4}-body-[0-9]{4}-(review|groups-[0-9]{4}-verify)", name) is not None,
            "original_role_name")
        if self.read_only:
            require((self.root / (name + ".json")).is_file(), "existing_cache_without_inference")
        if name.endswith("-review"):
            require(prompt == coverage.REVIEW, "original_proposal_role")
            ids, article_ids = list(context["clauses"]), list(context["article"])
            order = assignments.by_source(ids, self.plan["units"])
            actual_schema = wire_schema(ids, article_ids, self.plan["units"])
            actual_context = {**context, "ordered_clause_ids": order, "assignment_arrays_are_factual_evidence": False}
            actual_prompt = prompt + NOTICE
            with assignments.prompt_alias("REVIEW", actual_prompt):
                raw = super().stage(name, actual_prompt, actual_schema, actual_context,
                    lambda row: compile_wire(row, ids, article_ids, self.plan["units"]), **kwargs)
            value = compile_wire(raw, ids, article_ids, self.plan["units"])
            artifact = {"policy": POLICY, "stage": name, "ordered_clause_ids_sha256": digest(order),
                "actual_payload_sha256": digest(raw), "compiled_groups_sha256": digest(value),
                "actual_cache_sha256": whole.sha((self.root / (name + ".json")).read_bytes()),
                "every_clause_assigned_once": True, "semantic_acceptance": False}
            assignments.retained(self.root / (name + "-ordered-derivation.json"), artifact, read_only=self.read_only)
            validate(value)
        else:
            require(prompt == coverage.VERIFY, "original_independent_role")
            proposal = name.split("-groups-")[0] + "-review"
            artifact = whole.read(self.root / (proposal + "-ordered-derivation.json"))
            actual_context = {**context, "candidate_encoding": {"derivation_sha256": digest(artifact),
                "is_factual_evidence": False, "unassessed_clauses_created_by_program": 0}}
            actual_prompt = prompt + VERIFY_NOTICE
            with assignments.prompt_alias("VERIFY", actual_prompt):
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


class ReadOnlyJob(OrderedJob):
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
        require(value == self.expected_root and (value / "manifest.json").is_file(), "existing_exact_execution_manifest")
        self._root = value


def authenticated_parent(root, key, tokenizer):
    result = parent.reconstruct(root, key, tokenizer, complete=True)
    require(result["private"] is not None and result["summary"]["verified_units"] == result["summary"]["total_units"],
        "actual_complete_parent_not_prefix")
    return result


def binding(job, manuscript, plan, tokenizer):
    return {"policy": POLICY, "job": job.key, "parent_writer": manuscript["summary"]["job"],
        "parent_snapshot_sha256": digest(manuscript["proof"]), "plan_sha256": digest(plan), "code": bindings(),
        "tokenizer_sha256": whole.sha(Path(tokenizer).read_bytes()), "models": job.models,
        "prompts_sha256": [digest(NOTICE), digest(VERIFY_NOTICE)], "pilot_batches": PILOT_BATCHES,
        "complete_plan_batches": len(plan["batches"]), "original_stage_attempt_budget": 2,
        "semantic_repairs": 0, "original_budgets_reset": False,
        "reader_publication_allowed": False, "source_deletion_allowed": False}


def prepare(root, key, tokenizer):
    root = Path(root).resolve()
    require(re.fullmatch(r"[a-f0-9]{32}", key) is not None, "parent_writer_id")
    private_guard.checkpoint(root)
    contract = root / "wiki/textbook-whole-authoring-jobs" / key / coverage.CONTRACT
    require(not contract.exists(), "parent_has_no_previous_coverage_budget")
    with schema_lease(root / "wiki" / (key + "-whole-coverage-worker.lock"), timeout=0):
        manuscript = authenticated_parent(root, key, tokenizer)
        plan = coverage.make_plan(manuscript["inventory"], manuscript["private"], manuscript["working"], Tokenizer.from_file(str(tokenizer)))
        job = OrderedJob(coverage.writing.TextbookStore(root), manuscript, plan, tokenizer, ADAPTER, "section-0008")
        require(not contract.exists() and not (job.root / CONTRACT).exists()
            and not list((job.root / "attempts").glob("*.json")) and not (job.root / "progress.json").exists(),
            "first_registration_before_all_model_roles")
        value = binding(job, manuscript, plan, tokenizer)
        for path, data in [(contract, value), (job.root / CONTRACT, value),
            (job.root / "parent-snapshot.json", manuscript["proof"]), (job.root / "review-plan.json", plan)]:
            assignments.retained(path, data)
        return {"state": "first_complete_ordered_coverage_plan_prepared", "job": job.key, "parent_writer": key,
            "batches": len(plan["batches"]), "clauses": plan["clauses"], "sources": plan["sources"],
            "source_characters": plan["source_characters"], "pilot_batches": min(PILOT_BATCHES, len(plan["batches"])),
            "inherited_manuscript_issues": len(manuscript["issues"]), "model_calls": 0,
            "reader_publication_allowed": False}


def load(root, key, tokenizer, *, read_only=True):
    root = Path(root).resolve()
    require(re.fullmatch(r"[a-f0-9]{32}", key) is not None, "parent_writer_id")
    contract_path = root / "wiki/textbook-whole-authoring-jobs" / key / coverage.CONTRACT
    contract = whole.read(contract_path)
    require(contract["policy"] == POLICY and contract["parent_writer"] == key and contract["code"] == bindings(),
        "frozen_original_ordered_execution")
    manuscript = authenticated_parent(root, key, tokenizer)
    folder = root / "wiki/whole-manuscript-coverage-jobs" / contract["job"]
    plan = whole.read(folder / "review-plan.json")
    require(plan == coverage.make_plan(manuscript["inventory"], manuscript["private"], manuscript["working"], Tokenizer.from_file(str(tokenizer)))
        and whole.read(folder / "parent-snapshot.json") == manuscript["proof"], "actual_complete_original_plan")
    cls = ReadOnlyJob if read_only else OrderedJob
    options = {"expected_root": folder} if read_only else {}
    job = cls(coverage.writing.TextbookStore(root), manuscript, plan, tokenizer, ADAPTER, "section-0008", **options)
    require(binding(job, manuscript, plan, tokenizer) == contract == whole.read(folder / CONTRACT),
        "same_parent_and_unreset_role_budget")
    return root, manuscript, plan, job, contract


def reconstruct_batches(manuscript, plan, job, count):
    require(job.read_only and type(count) is int and 0 < count <= len(plan["batches"]), "finite_completed_batch_count")
    items = coverage.article_items(manuscript["private"], manuscript["inventory"])
    states = {}
    for number, batch in enumerate(plan["batches"][:count]):
        rows = coverage.review_batch(job, manuscript["inventory"], plan, items, batch, number)
        require(whole.read(job.root / f"batch-{number:04d}-result.json") == rows, "actual_reconstructed_batch")
        states.update(rows)
    return states


def pilot_result(plan, job, states, count):
    return {"state": "finite_ordered_coverage_pilot_finished", "job": job.key, "completed_batches": count,
        "total_batches": len(plan["batches"]), "completed_clauses": len(states),
        "covered_clauses": sum(s["resolved"] and s["kind"] == "covered" for s in states.values()),
        "unresolved_clauses": sum(not s["resolved"] for s in states.values()), "reader_publication_allowed": False}


def backup(job):
    paths = [p for p in job.root.rglob("*.json") if p.name != "progress.json"]
    coverage.backup(job, paths, "ordered-immutable-evidence")
    coverage.backup(job, [Path(__file__), Path(parent.__file__), Path(assignments.__file__)], "ordered-code")


def prior_pilot_guard(root, key):
    path = root / "wiki/ordered-coverage-dispatch" / key / "pilot/guard-progress.json"
    row = whole.read(path)
    require(row["state"] == "worker_exited" and row.get("returncode") == 0
        and not coverage.writing.identity_live(row), "actual_successful_exited_pilot_guard")


def run(root, key, tokenizer, *, tail=False):
    root = Path(root).resolve()
    with schema_lease(root / "wiki" / (key + "-whole-coverage-worker.lock"), timeout=0):
        root, manuscript, plan, job, contract = load(root, key, tokenizer, read_only=False)
        private_guard.checkpoint(root)
        phase = "tail" if tail else "pilot"
        folder = root / "wiki/ordered-coverage-dispatch" / job.key / phase
        require(not (folder / "worker-started.json").exists(), "finite_worker_once_no_automatic_retry")
        start, end = (min(PILOT_BATCHES, len(plan["batches"])), len(plan["batches"])) if tail else (0, min(PILOT_BATCHES, len(plan["batches"])))
        require(start < end, "nonempty_unused_phase")
        if tail:
            prior_pilot_guard(root, job.key)
            status = whole.read(job.root / "progress.json")
            require(status["state"] == "finite_ordered_coverage_pilot_finished" and status["completed_batches"] == start
                and not coverage.writing.identity_live(status), "actual_successful_exited_pilot")
            readonly = ReadOnlyJob(coverage.writing.TextbookStore(root), manuscript, plan, tokenizer, ADAPTER, "section-0008", expected_root=job.root)
            states = reconstruct_batches(manuscript, plan, readonly, start)
        else:
            require(not (job.root / "progress.json").exists() and not list((job.root / "attempts").glob("*.json")), "all_initial_roles_unused")
            states = {}
        require(all(not list((job.root / "attempts").glob(f"batch-{n:04d}-*.json"))
            and not list(job.root.glob(f"batch-{n:04d}-*.json")) for n in range(start, end)), "all_selected_roles_unused")
        assignments.retained(folder / "worker-started.json", {**job.identity, "phase": phase})
        items = coverage.article_items(manuscript["private"], manuscript["inventory"])
        job.completed_batches, job.completed_clauses = start, len(states)
        try:
            backup(job)
            for number in range(start, end):
                rows = coverage.review_batch(job, manuscript["inventory"], plan, items, plan["batches"][number], number)
                assignments.retained(job.root / f"batch-{number:04d}-result.json", rows)
                states.update(rows)
                job.completed_batches += 1
                job.completed_clauses += len(rows)
                job.progress("reviewing_all_original_clauses", f"batch-{number:04d}")
                backup(job)
            if end == len(plan["batches"]):
                result = coverage.compile_result(manuscript, plan, job, states)
                assignments.retained(job.root / "coverage-result.json", result)
                backup(job)
                job.progress(result["state"], "complete", unresolved_clauses=result["unresolved_clauses"])
                return coverage.public_result(result)
            result = pilot_result(plan, job, states, end)
            assignments.retained(job.root / "pilot-result.json", result)
            backup(job)
            job.progress(result["state"], "pilot_complete", unresolved_clauses=result["unresolved_clauses"])
            return result
        except BaseException as exc:
            failure = whole.failure_status(exc)
            assignments.retained(job.root / "failures" / (str(time.time_ns()) + ".json"), failure)
            job.progress(failure["state"], job.current, **{k: v for k, v in failure.items() if k != "state"})
            raise


def verify(root, key, tokenizer, *, complete=False):
    root = Path(root).resolve()
    with schema_lease(root / "wiki" / (key + "-whole-coverage-worker.lock"), timeout=0):
        root, manuscript, plan, job, contract = load(root, key, tokenizer)
        status = whole.read(job.root / "progress.json")
        require(not coverage.writing.identity_live(status) and status["state"] in {
            "finite_ordered_coverage_pilot_finished", "automatic_whole_source_coverage_complete",
            "whole_manuscript_requires_coverage_or_classification_repair"}, "actual_successful_exited_phase")
        count = status["completed_batches"]
        require(not complete or count == len(plan["batches"]), "entire_original_plan_finished")
        states = reconstruct_batches(manuscript, plan, job, count)
        if count == len(plan["batches"]):
            result = coverage.compile_result(manuscript, plan, job, states)
            require(whole.read(job.root / "coverage-result.json") == result and status["state"] == result["state"], "actual_complete_result")
        else:
            require(count == min(PILOT_BATCHES, len(plan["batches"]))
                and whole.read(job.root / "pilot-result.json") == pilot_result(plan, job, states, count),
                "original_finite_pilot_result")
        require(status["completed_clauses"] == len(states)
            and status["unresolved_clauses"] == sum(not s["resolved"] for s in states.values())
            and status["total_clauses"] == plan["clauses"] and status["total_batches"] == len(plan["batches"]),
            "actual_terminal_phase_counts")
        require(whole.read(job.root / "progress.json") == status, "stable_finished_phase")
        return {"state": "ordered_coverage_phase_reconstructed_without_inference", "job": job.key,
            "verified_batches": count, "total_batches": len(plan["batches"]), "clauses": len(states),
            "authenticated_model_stages": len(job.authenticated_stages),
            "inherited_manuscript_issues": len(manuscript["issues"]), "model_calls": 0,
            "reader_publication_allowed": False}


def guarded(root, key, tokenizer, *, tail=False):
    root, _, _, job, _ = load(root, key, tokenizer)
    folder = root / "wiki/ordered-coverage-dispatch" / job.key / ("tail" if tail else "pilot")
    folder.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-m", __spec__.name, "--root", str(root), "--parent", key,
        "--tokenizer", str(Path(tokenizer).resolve()), "tail-worker" if tail else "pilot-worker"]
    return private_guard.run(root, folder, command)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--parent", required=True)
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("command", choices=["prepare", "load", "pilot-worker", "tail-worker", "pilot-guard", "tail-guard", "verify", "verify-complete"])
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = prepare(args.root, args.parent, args.tokenizer)
        elif args.command == "load":
            _, _, plan, job, _ = load(args.root, args.parent, args.tokenizer)
            result = {"state": "original_ordered_coverage_inputs_authenticated", "job": job.key,
                "batches": len(plan["batches"]), "clauses": plan["clauses"], "model_calls": 0}
        elif args.command.startswith("verify"):
            result = verify(args.root, args.parent, args.tokenizer, complete=args.command == "verify-complete")
        elif args.command.endswith("guard"):
            result = guarded(args.root, args.parent, args.tokenizer, tail=args.command.startswith("tail"))
        else:
            result = run(args.root, args.parent, args.tokenizer, tail=args.command.startswith("tail"))
        print(json.dumps(result))
    except BaseException as exc:
        print(json.dumps(whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
