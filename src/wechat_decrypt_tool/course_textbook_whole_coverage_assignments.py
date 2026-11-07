"""Resume original coverage roles with complete per-source clause assignments.

The exhausted first proposal is never regenerated or promoted to a model cache.
Its earliest complete partial response is split without changing judgments;
omitted clauses receive explicit, conservative unassessed proposals. Original
unused independent checks decide the proposals. Later unused proposal roles
require every clause assignment in their decoding schema.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import copy
import json
from pathlib import Path
import re
import sys

from . import course_textbook_whole_coverage as coverage
from . import course_textbook_whole_coverage_snapshot as checked
from . import course_textbook_whole_disposition_mapping as mapping
from .course_knowledge import atomic_json, schema_lease

POLICY = "original-coverage-roles-complete-source-assignments-v1"
CONTRACT = "complete-assignment-execution.json"
FIRST = "batch-0000-body-0000-review"
SUFFIX = "-assignment-derivation.json"
BASE = coverage.CoverageJob
whole = coverage.writing.whole
digest = coverage.digest
WRITE_NOTICE = """
本次语义标准不变，输出改用 source_groups：必须为每个目标 reference 给出 groups 和 assignments。
assignments 的每个目标分句键都必须出现一次，值是该来源 groups 数组从零起的组编号。
同一来源按目标分句顺序分组，组编号从0开始、连续且不递减；不能交替回到先前组。
groups 每组仍含 kind、真实 article_items 和 reason；不要在组内重抄分句编号。
不得漏掉短句、重复、代码或非教学分句；如判断未覆盖或非教学，也要分配到对应的组。
每组所有成员都须满足该组判断，混合教学与非教学或覆盖状态不同时必须拆组。仅输出 schema 规定 JSON。
"""
VERIFY_NOTICE = """
candidate_derivation 仅记录程序如何转录候选，不是事实依据或通过证明。
其中 program_unassessed_clauses 在原模型完整响应中没有判断；程序保守提出 missing，绝不据此排除教学。
仅当完整原文确有教学且当前正文确未讲清，才可通过该 missing 提议；否则 passed=false，保留未决。
其他组是原真实候选的成员分组，程序没有改变其教学判断、依据或理由；仍需逐成员独立核对。
"""


def require(ok, code):
    if not ok:
        raise ValueError("requires_coverage_assignments_" + code)


def retained(path, value, *, read_only=False):
    if path.exists():
        require(whole.read(path) == value, "immutable_derivation")
    else:
        require(not read_only, "required_existing_derivation")
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(path, value)


def by_source(ids, units):
    result = {}
    for cid in ids:
        result.setdefault(units[cid]["reference"], []).append(cid)
    return result


def wire_schema(ids, article_ids, units):
    group_schema = coverage.schema_for(ids, article_ids)["properties"]["groups"]["items"]
    for branch in group_schema["oneOf"]:
        branch["properties"].pop("clauses")
        branch["required"].remove("clauses")
    return coverage.object_schema({"source_groups": coverage.object_schema({ref: coverage.object_schema({
        "assignments": coverage.object_schema({cid: {"type": "integer", "minimum": 0, "maximum": len(keys) - 1}
            for cid in keys}), "groups": {"type": "array", "minItems": 1, "maxItems": len(keys), "items": group_schema}})
        for ref, keys in by_source(ids, units).items()})})


def compile_wire(value, ids, article_ids, units):
    coverage.jsonschema.validate(value, wire_schema(ids, article_ids, units))
    result = []
    for ref, keys in by_source(ids, units).items():
        row = value["source_groups"][ref]
        assignments, groups = row["assignments"], row["groups"]
        sequence = [assignments[cid] for cid in keys]
        require(sequence == sorted(sequence) and set(sequence) == set(range(len(groups))), "contiguous_used_source_groups")
        result.extend({**group, "clauses": [cid for cid in keys if assignments[cid] == number]}
            for number, group in enumerate(groups))
    result = {"groups": result}
    coverage.validate_groups(result, ids, article_ids, units)
    return result


def complete_partial(candidate, ids, article_ids, units):
    """Preserve each real judgment; never turn an omitted clause into coverage."""
    coverage.jsonschema.validate(candidate, coverage.schema_for(ids, article_ids))
    members = [cid for group in candidate["groups"] for cid in group["clauses"]]
    require(len(members) == len(set(members)) and set(members) < set(ids), "partial_unique_actual_candidate")
    groups, splits = [], []
    positions = {cid: number for number, cid in enumerate(ids)}
    for original, group in enumerate(candidate["groups"]):
        ordered = sorted(group["clauses"], key=positions.__getitem__)
        runs = []
        for cid in ordered:
            if (not runs or units[cid]["reference"] != units[runs[-1][-1]]["reference"]
                    or units[cid]["ordinal"] != units[runs[-1][-1]]["ordinal"] + 1):
                runs.append([])
            runs[-1].append(cid)
        for run in runs:
            splits.append({"original_group": original, "derived_group": len(groups), "clauses": run})
            groups.append({**copy.deepcopy(group), "clauses": run})
    omitted = [cid for cid in ids if cid not in set(members)]
    for keys in by_source(omitted, units).values():
        runs = []
        for cid in keys:
            if not runs or units[cid]["ordinal"] != units[runs[-1][-1]]["ordinal"] + 1:
                runs.append([])
            runs[-1].append(cid)
        groups.extend({"clauses": run, "kind": "missing", "article_items": [],
            "reason": "Program proposal only: original response omitted these clauses; no model judgment or positive coverage is inferred."}
            for run in runs)
    result = {"groups": groups}
    coverage.validate_groups(result, ids, article_ids, units)
    return result, {"original_members": len(members), "original_groups": len(candidate["groups"]),
        "preserved_member_splits": splits, "program_unassessed_clauses": omitted,
        "omissions_count_as_covered": False, "semantic_acceptance": False}


def first_context(manuscript, plan):
    batch = plan["batches"][0]
    items = coverage.article_items(manuscript["private"], manuscript["inventory"])
    context = coverage.context_for(manuscript["inventory"], plan["units"], batch["clauses"], items, batch["article_blocks"][0])
    refs = set(context["sources"]) | set(context["context_sources"])
    packet = {"sources": {ref: manuscript["inventory"]["sources"][ref] for ref in sorted(refs)}}
    return context, packet


def first_artifact(job, context, packet):
    ids, article_ids = list(context["clauses"]), list(context["article"])
    schema = coverage.schema_for(ids, article_ids)
    actual_schema, binding = job.stage_input(coverage.REVIEW, schema, context, evidence_packet=packet)
    paths = sorted((job.root / "attempts").glob(FIRST + "-[0-9][0-9].json"))
    require([p.name for p in paths] == [FIRST + "-01.json", FIRST + "-02.json"], "exhausted_original_two_attempts")
    receipts = {p: whole.read(p) for p in job.root.glob("response-*.json")}
    previous, original, files = "", None, {}
    for number, path in enumerate(paths, 1):
        row = whole.read(path)
        messages = [{"role": "system", "content": coverage.REVIEW}, {"role": "user", "content": json.dumps(
            {**context, "previous_validation_failure": previous}, ensure_ascii=False)}]
        require(row["stage"] == FIRST and row["attempt"] == number and row["input_sha256"] == binding
            and row["model_digest"] == job.models["course-defiant:q4"] and row["request_sha256"] == digest(messages)
            and not row.get("accepted_structure"), "unchanged_failed_original_requests")
        matching = [(p, receipt) for p, receipt in receipts.items() if receipt["stage"] == FIRST
            and receipt["request_sha256"] == row["request_sha256"]]
        require(len(matching) == 1, "actual_failed_model_receipt")
        receipt_path, receipt = matching[0]
        metrics = receipt["metrics"]
        require(receipt["schema_sha256"] == digest(actual_schema) and metrics["model_digest"] == job.models["course-defiant:q4"]
            and metrics["size_vram"] > 0 and metrics["requested_context"] == metrics["measured_context"]
            and metrics["expected_content_tokens"] - 4 <= metrics["prompt_eval_count"] <= metrics["expected_content_tokens"] + 256
            and metrics["prompt_eval_count"] + metrics["reserved_output"] < metrics["measured_context"], "actual_complete_gpu_inputs")
        if number == 1:
            require(row["validation_error"] == "requires_whole_coverage_every_target_clause_exactly_once"
                and metrics["done_reason"] == "stop" and receipt["response"]["message"]["content"] == row["raw_response"],
                "earliest_complete_partial_response")
            whole.editor.e.verify_response(job.root, FIRST, row)
            original = json.loads(row["raw_response"])
        else:
            require(row["validation_error"] == "requires_gap_plan_complete_model_output" and metrics["done_reason"] != "stop",
                "second_incomplete_response_preserved_not_used")
        files.update({p.name: whole.sha(p.read_bytes()) for p in (path, receipt_path)})
        previous = row["validation_error"]
    require(not (job.root / (FIRST + ".json")).exists(), "no_fabricated_first_model_cache")
    value, derivation = complete_partial(original, ids, article_ids, job.plan["units"])
    return {"policy": POLICY, "data": value, "receipt": {**derivation, "original_files": files,
        "original_candidate_sha256": digest(original), "derived_candidate_sha256": digest(value),
        "original_input_sha256": binding, "original_attempt_budget_reset": False, "new_proposal_model_calls": 0}}


def execution(job, parent, artifact):
    original = job.store.root / "wiki/textbook-whole-authoring-jobs" / parent / coverage.CONTRACT
    return {"policy": POLICY, "job": job.key, "parent_writer": parent,
        "original_execution_sha256": whole.sha(original.read_bytes()), "plan_sha256": digest(job.plan),
        "first_derivation_sha256": digest(artifact), "module_sha256": whole.sha(Path(__file__).read_bytes()),
        "write_notice_sha256": digest(WRITE_NOTICE), "verify_notice_sha256": digest(VERIFY_NOTICE),
        "original_first_role_exhausted": True, "future_roles_unused_at_preparation": True,
        "original_stage_attempt_budget": 2, "semantic_repairs": 0, "original_budgets_reset": False,
        "reader_publication_allowed": False, "source_deletion_allowed": False}


@contextmanager
def prompt_alias(kind, prompt):
    previous = getattr(coverage, kind)
    setattr(coverage, kind, prompt)
    try:
        yield
    finally:
        setattr(coverage, kind, previous)


class AssignmentJob(BASE):
    read_only = False

    def __init__(self, store, manuscript, plan, tokenizer, adapter, section):
        super().__init__(store, manuscript, plan, tokenizer, adapter, section)
        context, packet = first_context(manuscript, plan)
        artifact = first_artifact(self, context, packet)
        require(whole.read(self.root / CONTRACT) == execution(self, manuscript["summary"]["job"], artifact),
            "same_explicit_assignment_execution")
        retained(self.root / (FIRST + SUFFIX), artifact, read_only=True)
        self.first = artifact

    def stage(self, name, prompt, schema, context, validate, **kwargs):
        require(re.fullmatch(r"batch-[0-9]{4}-body-[0-9]{4}-(review|groups-[0-9]{4}-verify)", name), "original_role_name")
        if name == FIRST:
            require(prompt == coverage.REVIEW and self.first == first_artifact(self, context, kwargs["evidence_packet"]),
                "same_failed_first_proposal_context")
            value = copy.deepcopy(self.first["data"])
            validate(value)
            return value
        if name.endswith("-review"):
            require(prompt == coverage.REVIEW, "original_proposal_role")
            ids, article_ids = list(context["clauses"]), list(context["article"])
            actual_schema = wire_schema(ids, article_ids, self.plan["units"])
            actual_prompt = prompt + WRITE_NOTICE
            with prompt_alias("REVIEW", actual_prompt):
                raw = super().stage(name, actual_prompt, actual_schema, context,
                    lambda row: compile_wire(row, ids, article_ids, self.plan["units"]), **kwargs)
            value = compile_wire(raw, ids, article_ids, self.plan["units"])
            artifact = {"policy": POLICY, "data": value, "receipt": {"model_stage": name,
                "actual_model_payload_sha256": digest(raw), "model_cache_sha256": whole.sha((self.root / (name + ".json")).read_bytes()),
                "required_all_target_assignments": True, "program_unassessed_clauses": [], "semantic_acceptance": False}}
            retained(self.root / (name + SUFFIX), artifact, read_only=self.read_only)
            validate(value)
            return value
        require(prompt == coverage.VERIFY, "original_independent_role")
        stem = name.split("-groups-")[0] + "-review"
        artifact = self.first if stem == FIRST else whole.read(self.root / (stem + SUFFIX))
        selected = {cid for group in context["groups"].values() for cid in group["clauses"]}
        actual_context = {**context, "candidate_derivation": {"artifact_sha256": digest(artifact),
            "program_unassessed_clauses": [cid for cid in artifact["receipt"]["program_unassessed_clauses"] if cid in selected],
            "is_factual_evidence": False}}
        actual_prompt = prompt + VERIFY_NOTICE
        with prompt_alias("VERIFY", actual_prompt):
            return super().stage(name, actual_prompt, schema, actual_context, validate, **kwargs)


class ReadOnlyAssignmentJob(AssignmentJob):
    read_only = True

    def __init__(self, store, manuscript, plan, tokenizer, adapter, section, expected_root):
        self.expected_root, self.authenticated_stages = Path(expected_root).resolve(), set()
        super().__init__(store, manuscript, plan, tokenizer, adapter, section)

    @property
    def root(self):
        return self._root

    @root.setter
    def root(self, value):
        value = Path(value).resolve()
        require(value == self.expected_root and (value / "manifest.json").is_file(), "existing_exact_original_manifest")
        self._root = value

    def stage(self, name, *args, **kwargs):
        require(name == FIRST or (self.root / (name + ".json")).is_file(), "existing_cache_without_inference")
        value = super().stage(name, *args, **kwargs)
        if name != FIRST:
            self.authenticated_stages.add(name)
        return value

    def progress(self, state, stage, **extra):
        self.current = stage

    def generate_response(self, *args, **kwargs):
        raise ValueError("requires_coverage_assignments_inference_forbidden")

    def slot(self, *args, **kwargs):
        raise ValueError("requires_coverage_assignments_gpu_acquisition_forbidden")


@contextmanager
def hooks():
    previous = coverage.CoverageJob, checked.ReadOnlyCoverageJob
    coverage.CoverageJob, checked.ReadOnlyCoverageJob = AssignmentJob, ReadOnlyAssignmentJob
    try:
        with mapping.hooks():
            yield
    finally:
        coverage.CoverageJob, checked.ReadOnlyCoverageJob = previous


def prepare(root, parent, tokenizer, section):
    root = Path(root).resolve()
    with mapping.hooks():
        manuscript, plan, job, _, _ = coverage.prepared(root, parent, tokenizer, "continuation", section)
    with schema_lease(root / "wiki" / (parent + "-whole-coverage-worker.lock"), timeout=0):
        state = whole.read(job.root / "progress.json")
        require(state["state"] == "stopped" and state["stage"] == FIRST and not coverage.writing.identity_live(state),
            "actually_stopped_first_proposal")
        context, packet = first_context(manuscript, plan)
        artifact = first_artifact(job, context, packet)
        contract = execution(job, parent, artifact)
        if not (job.root / CONTRACT).exists():
            require(all(p.name in {FIRST + "-01.json", FIRST + "-02.json"} for p in (job.root / "attempts").glob("*.json")),
                "all_remaining_original_roles_unused")
        history = job.root / "assignment-recovery-history" / (whole.sha((job.root / "progress.json").read_bytes()) + ".json")
        whole.backup(job.root / "progress.json", history)
        retained(job.root / (FIRST + SUFFIX), artifact)
        retained(job.root / CONTRACT, contract)
        return {"state": "original_unused_coverage_roles_prepared_with_complete_assignments", "job": job.key,
            "parent_writer": parent, "batches": len(plan["batches"]), "clauses": plan["clauses"],
            "retained_proposed_members": artifact["receipt"]["original_members"],
            "program_unassessed_clauses": len(artifact["receipt"]["program_unassessed_clauses"]),
            "derived_groups": len(artifact["data"]["groups"]), "model_calls": 0,
            "original_budgets_reset": False, "reader_publication_allowed": False}


def guard(root, parent, tokenizer, section):
    from .course_archive_gpu import checked_sample
    from .course_gpu_guard import run as guarded

    root = Path(root).resolve()
    coverage.checkpoint(coverage.writing.TextbookStore(root))
    with schema_lease(root / "wiki/whole-source-authoring-dispatch.lock", timeout=0):
        path = root / "wiki-revision-thermal-guard.json"
        old = whole.read(path)
        require(not coverage.writing.identity_live(old) and old["state"] == "worker_exited" and old.get("returncode") == 1,
            "original_failed_guard_actually_exited")
        whole.backup(path, root / "wiki/assignment-coverage-guard-history" / (whole.sha(path.read_bytes()) + ".json"))
        guarded(root, [sys.executable, "-m", __spec__.name, "--root", str(root), "--parent", parent,
            "--tokenizer", str(Path(tokenizer).resolve()), "--section", section, "worker"], maximum_temperature=70,
            maximum_power=200, guard_name="wiki-revision-thermal-guard", sampler=lambda: checked_sample(root))


def reconstruct(root, parent, tokenizer, section):
    root = Path(root).resolve()
    with hooks():
        result = checked.reconstruct(root, parent, tokenizer, "continuation", section)
    contract = whole.read(root / "wiki/textbook-whole-authoring-jobs" / parent / coverage.CONTRACT)
    folder = root / "wiki/whole-manuscript-coverage-jobs" / contract["job"]
    artifact = whole.read(folder / (FIRST + SUFFIX))
    paths = {folder / CONTRACT, *folder.glob("*" + SUFFIX)}
    for name in artifact["receipt"]["original_files"]:
        path = folder / ("attempts" if name.startswith(FIRST + "-") else "") / name
        require(whole.sha(path.read_bytes()) == artifact["receipt"]["original_files"][name], "unchanged_failed_model_evidence")
        paths.add(path)
    result["proof"]["files"].update({path.relative_to(root).as_posix(): whole.sha(path.read_bytes()) for path in sorted(paths)})
    result["proof"]["assignment_adapter_sha256"] = whole.sha(Path(__file__).read_bytes())
    result["proof"]["original_failed_first_candidate_not_promoted_to_cache"] = True
    result["proof"]["program_unassessed_first_clauses"] = len(artifact["receipt"]["program_unassessed_clauses"])
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--parent", required=True)
    p.add_argument("--tokenizer", type=Path, required=True)
    p.add_argument("--section", default="section-0007")
    p.add_argument("command", choices=["prepare", "worker", "guard", "verify"])
    a = p.parse_args()
    try:
        if a.command == "prepare":
            result = prepare(a.root, a.parent, a.tokenizer, a.section)
        elif a.command == "guard":
            guard(a.root, a.parent, a.tokenizer, a.section)
            result = {"state": "assignment_coverage_guard_returned"}
        elif a.command == "verify":
            value = reconstruct(a.root, a.parent, a.tokenizer, a.section)
            result = {**value["summary"], "snapshot": str(checked.save(a.root, value))}
        else:
            with hooks():
                result = coverage.run(a.root, a.parent, a.tokenizer, "continuation", a.section)
        print(json.dumps(result))
    except Exception as exc:
        print(json.dumps(whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
