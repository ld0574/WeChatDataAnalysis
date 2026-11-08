"""Resume an exhausted fresh draft through its unused original unit roles.

Failed prose remains an untrusted audit input. The original repair is mandatory;
future writers put point IDs inside paragraphs, so indices are derived from the
actual array. No failed write cache, semantic approval or new role is invented.
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

import jsonschema

from . import course_textbook_fresh_authoring as fresh
from . import course_textbook_structural_repair as structural
from . import course_wiki_private_guard as private_guard
from .course_knowledge import atomic_json, schema_lease

writing, visible, snapshot = fresh.writing, fresh.visible, fresh.snapshot
digest = fresh.digest
POLICY = "fresh-whole-unused-roles-paragraph-point-id-resumption-v1"
DIRECTORY = "point-mapped-resume"
ORIGINAL_FACTORY = fresh.job_type
ORIGINAL_SCHEMA = fresh.section_schema
ORIGINAL_CANDIDATE = fresh.citation_candidate
FORMAT_NOTICE = """
本角色使用新的段内覆盖格式，以本次解码 schema 为准：不要输出 coverage.paragraphs 数字索引。
在每个实际正文段落中输出 point_ids，逐一列出该段充分解释的 assigned_points ID。
ID 必须来自当前任务；一个任务可由多段共同解释，一段也可解释多个任务。
coverage 仍须逐项给出原要求的 reason 及适用的 teaching 判断；有效教学不能遗漏。
程序只按真实数组位置生成内部段落索引，不证明语义充分；独立复核仍逐项检查全部原文。
"""


def require(ok, code):
    if not ok:
        raise ValueError("requires_point_mapped_resume_" + code)


def point_schema(schema, packet, pending):
    result = ORIGINAL_SCHEMA(schema, packet, pending)

    def strip_indices(node):
        if "properties" in node and "paragraphs" in node["properties"]:
            del node["properties"]["paragraphs"]
            node["required"].remove("paragraphs")
        for branch in node.get("oneOf", []):
            strip_indices(branch)

    def visit(node):
        properties = node.get("properties", {})
        paragraphs = properties.get("paragraphs", {})
        if paragraphs.get("type") == "array":
            ids = list(properties["coverage"]["properties"])
            item = paragraphs["items"]
            item["properties"]["point_ids"] = {
                "type": "array", "uniqueItems": True,
                "items": {"type": "string", "enum": ids}}
            item["required"].append("point_ids")
            for row in properties["coverage"]["properties"].values():
                strip_indices(row)
        for branch in node.get("oneOf", []):
            visit(branch)

    visit(result)
    return result


def point_candidate(raw, ids, working, inventory, scope):
    schema = point_schema(writing.section_schema(ids, working), scope["expanded"], working["pending_source_cards"])
    jsonschema.validate(raw, schema)
    if raw.get("kind") == "non_teaching":
        return ORIGINAL_CANDIDATE(raw, ids, working, inventory, scope)
    candidate = copy.deepcopy(raw)
    mapping = {key: [] for key in ids}
    for number, paragraph in enumerate(candidate["paragraphs"]):
        for key in paragraph.pop("point_ids"):
            mapping[key].append(number)
    for key, row in candidate["coverage"].items():
        row["paragraphs"] = mapping[key]
    candidate, citations = ORIGINAL_CANDIDATE(candidate, ids, working, inventory, scope)
    return candidate, {"paragraph_indices_derived_from_actual_point_ids": mapping,
        "citation_proposals": citations, "semantic_acceptance": False}


@contextmanager
def mapped_format():
    old_schema, old_candidate = fresh.section_schema, fresh.citation_candidate
    prompts = {role: writing.composition.PROMPTS[role]
        for role in (writing.author.WRITE_PROMPT, writing.author.REPAIR_PROMPT)}
    fresh.section_schema, fresh.citation_candidate = point_schema, point_candidate
    try:
        for role, text in prompts.items():
            writing.composition.PROMPTS[role] = text + FORMAT_NOTICE
        yield
    finally:
        fresh.section_schema, fresh.citation_candidate = old_schema, old_candidate
        writing.composition.PROMPTS.update(prompts)


def prefix_inputs(root, inventory_key, ledger_job, tokenizer, count):
    root, inventory, working, base, original = fresh.bound(root, inventory_key, ledger_job, tokenizer)
    job = ORIGINAL_FACTORY(original, read_only=True)(writing.TextbookStore(root), inventory, working, tokenizer)
    plan = writing.organize(job, working)
    units = writing.writing_units(plan, working, inventory, job.tokenizer)
    require(job.reader.saved("private-working-inputs") == working
        and job.reader.saved("concept-plan") == plan
        and job.reader.saved("writing-allocation") == units, "original_plan_and_inputs")
    require(type(count) is int and 0 < count < len(units), "completed_prefix_before_failed_unit")
    sections, _, issues = snapshot.replay_units(job.reader, units, plan, count)
    unit = units[count]
    context = visible.refs.unit_context(unit, count, units, plan, sections, working, inventory)
    packet = visible.ORIGINAL_PACKET(unit["point_ids"], working, inventory)
    scope = fresh.continuation.scope_for(unit["point_ids"], working, inventory, context, packet)
    original_path = root / "wiki/textbook-keyed-jobs" / ledger_job / "whole-source-authoring-execution.json"
    require(writing.whole.read(original_path) == {"inventory": inventory_key,
        "ledger_sha256": digest(working), "job": base.key}, "original_writer_execution")
    job.reader.paths.update([original_path, base.root / "manifest.json", base.root / fresh.CONTRACT])
    for path in base.root.glob("response-*.json"):
        if writing.whole.read(path)["stage"] in job.reader.stages:
            job.reader.paths.add(path)
    return SimpleNamespace(root=root, inventory=inventory, working=working, job=base, original=original,
        prefix=unit["id"], count=count, units=units, context=context, scope=scope,
        issues=issues, paths=job.reader.paths)


def failed_candidate(bundle):
    job, context, scope = bundle.job, bundle.context, bundle.scope
    name = bundle.prefix + "-write"
    schema = writing.section_schema(list(context["assigned_points"]), bundle.working)
    actual_schema = ORIGINAL_SCHEMA(schema, scope["expanded"], bundle.working["pending_source_cards"])
    expected = {"policy": fresh.POLICY, "execution_sha256": digest(bundle.original), "stage": name,
        "original_schema_sha256": digest(schema), "actual_schema_sha256": digest(actual_schema),
        "original_context_sha256": digest(context), "actual_context_sha256": digest(context),
        "original_packet_sha256": digest(scope["original"]), "actual_packet_sha256": digest(scope["expanded"]),
        "original_budgets_reset": False, "reader_publication_allowed": False}
    request_path = job.root / (name + "-prospective-request.json")
    require(writing.whole.read(request_path) == expected, "actual_failed_prospective_request")
    candidate, history = fresh.continuation.repair.original_candidate(job, bundle.prefix,
        actual_schema, context, scope["expanded"], bundle.inventory)
    request = fresh.continuation.draft.capture_request(job, name, writing.author.WRITE_PROMPT,
        actual_schema, context, scope["expanded"])
    receipts = [(path, writing.whole.read(path)) for path in job.root.glob("response-*.json")]
    bundle.paths.add(request_path)
    for filename in history["original_write_attempts"]:
        path = job.root / "attempts" / filename
        row = writing.whole.read(path)
        matching = [p for p, receipt in receipts if receipt["stage"] == name
            and receipt["request_sha256"] == row["request_sha256"]
            and receipt.get("schema_sha256") == digest(request["schema"])
            and receipt["response"]["message"]["content"] == row["raw_response"]
            and receipt["metrics"] == row["model_metrics"]]
        require(len(matching) == 1, "actual_failed_decoding_schema_and_gpu_receipt")
        bundle.paths.update([path, *matching])
    require(not (job.root / (name + ".json")).exists(), "failed_write_cache_not_fabricated")
    defects = structural.defects(candidate, scope["expanded"])
    require(any(row["kind"] == "paragraph_mapping_out_of_bounds" for row in defects), "observed_index_defect")
    return candidate, history, defects


def bindings():
    return {module.__name__: writing.whole.sha(Path(module.__file__).read_bytes())
        for module in (fresh, structural, private_guard, fresh.continuation, visible, snapshot,
            fresh.grammar, fresh.continuation.draft, fresh.continuation.repair)}


def execution(bundle, candidate, history, defects):
    return {"policy": POLICY, "job": bundle.job.key, "inventory": digest(bundle.inventory),
        "ledger_job": bundle.working["job"], "working_sha256": digest(bundle.working),
        "prefix_units": bundle.count, "prefix_unresolved_issues": len(bundle.issues),
        "section": bundle.prefix, "total_units": len(bundle.units),
        "future_units": [unit["id"] for unit in bundle.units[bundle.count + 1:]],
        "original_execution_sha256": digest(bundle.original), "original_candidate_sha256": digest(candidate),
        "original_write_history": history, "structural_defects": defects,
        "actual_context_sha256": digest(bundle.context), "actual_packet_sha256": digest(bundle.scope["expanded"]),
        "files": {str(path.relative_to(bundle.root)): writing.whole.sha(path.read_bytes()) for path in sorted(bundle.paths)},
        "module_sha256": writing.whole.sha(Path(__file__).read_bytes()), "helpers_sha256": bindings(),
        "original_stage_attempt_budget": 2, "semantic_repairs_per_unit": 1, "additional_model_roles": 0,
        "mandatory_original_repair": True, "original_budgets_reset": False,
        "failed_candidate_is_untrusted": True, "future_paragraph_format": "point_ids_inside_actual_paragraphs",
        "reader_publication_allowed": False, "source_deletion_allowed": False}


def prepare(root, inventory_key, ledger_job, tokenizer):
    root, _, _, base, _ = fresh.bound(root, inventory_key, ledger_job, tokenizer)
    with schema_lease(root / "wiki/textbook-whole-record-evidence" / inventory_key / "authoring-worker.lock", timeout=0):
        status = writing.whole.read(base.root / "progress.json")
        require(status["state"] == "stopped" and not writing.identity_live(status), "failed_writer_exited")
        bundle = prefix_inputs(root, inventory_key, ledger_job, tokenizer, status["completed_sections"])
        require(status["stage"] == bundle.prefix + "-write"
            and status.get("reason_code") == "wiki_stage_attempt_budget_exhausted_" + status["stage"], "exact_exhausted_write")
        candidate, history, defects = failed_candidate(bundle)
        folder = base.root / DIRECTORY
        target = folder / "execution.json"
        require(not target.exists(), "prepare_once_before_remaining_roles")
        remaining = [bundle.prefix + suffix for suffix in ("-review", "-repair", "-repair-review")]
        remaining += [unit["id"] + "-" + role for unit in bundle.units[bundle.count + 1:]
            for role in fresh.continuation.ROLES]
        require(all(not any((base.root / "attempts").glob(name + "-[0-9][0-9].json"))
            and not any(base.root.glob(name + "*.json")) for name in remaining)
            and not any(base.root.glob("reading-*.json")), "all_remaining_original_roles_unused")
        bundle.paths.update(base.root.glob("failures/*.json"))
        old_guard = root / "review-experiment-thermal-guard.json"
        if old_guard.exists():
            guard = writing.whole.read(old_guard)
            require(not writing.identity_live(guard) and guard["state"] == "worker_exited"
                and guard.get("returncode") == 1, "original_failed_guard_exited")
            bundle.paths.add(old_guard)
        contract = execution(bundle, candidate, history, defects)
        fresh.retain(target, contract)
        fresh.retain(folder / "original-status.json", status)
        return {"state": "failed_fresh_unit_prepared_with_unused_original_roles", "job": base.key,
            "verified_prefix_units": bundle.count, "total_units": len(bundle.units),
            "structural_defects": len(defects), "future_units": len(contract["future_units"]),
            "model_calls": 0, "original_budgets_reset": False}


def load(root, key, tokenizer):
    require(re.fullmatch(r"[a-f0-9]{32}", key) is not None, "job_id")
    root = Path(root).resolve()
    folder = root / "wiki/textbook-whole-authoring-jobs" / key / DIRECTORY
    contract = writing.whole.read(folder / "execution.json")
    require(contract["policy"] == POLICY and contract["job"] == key
        and contract["module_sha256"] == writing.whole.sha(Path(__file__).read_bytes())
        and contract["helpers_sha256"] == bindings(), "frozen_recovery_code")
    require(all(writing.whole.sha((root / name).read_bytes()) == checksum
        for name, checksum in contract["files"].items()), "unchanged_original_files")
    bundle = prefix_inputs(root, contract["inventory"], contract["ledger_job"], tokenizer, contract["prefix_units"])
    candidate, history, defects = failed_candidate(bundle)
    bundle.paths = {root / name for name in contract["files"]}
    require(execution(bundle, candidate, history, defects) == contract, "original_bound_inputs_and_failures")
    bundle.folder, bundle.contract = folder, contract
    bundle.original_candidate, bundle.defects = candidate, defects
    return bundle


def repair_target(bundle, tokenizer, *, read_only):
    job = ORIGINAL_FACTORY(bundle.original, read_only=read_only)(writing.TextbookStore(bundle.root),
        bundle.inventory, bundle.working, tokenizer)
    job.finished_sections, job.section_count = bundle.count, len(bundle.units)
    ids, prefix = list(bundle.context["assigned_points"]), bundle.prefix
    common = {**bundle.context, "structural_defects": bundle.defects,
        "candidate_requires_original_role_repair": True, "candidate_is_untrusted": True}

    def audit(candidate, suffix):
        schema = writing.review_schema(candidate, ids)
        return job.stage(prefix + suffix, writing.author.REVIEW_PROMPT, schema,
            {**common, "candidate": candidate}, lambda row: writing.author.validate_review(row, schema),
            evidence_packet=bundle.scope["original"])

    initial = audit(bundle.original_candidate, "-review")
    with mapped_format():
        repaired = job.stage(prefix + "-repair", writing.author.REPAIR_PROMPT,
            writing.section_schema(ids, bundle.working),
            {**common, "candidate": bundle.original_candidate, "review": initial},
            lambda row: writing.validate_section(row, ids, bundle.working, bundle.inventory),
            evidence_packet=bundle.scope["original"])
    final = audit(repaired, "-repair-review")
    for path in job.root.glob("response-*.json"):
        if writing.whole.read(path)["stage"].startswith(prefix + "-"):
            job.reader.paths.add(path)
    result = {"policy": POLICY, "execution_sha256": digest(bundle.contract),
        "candidate": repaired, "initial_untrusted_candidate_review": initial, "final_review": final,
        "final_checks_passed": all(writing.author.verdicts(final)),
        "files": {str(path.relative_to(bundle.root)): writing.whole.sha(path.read_bytes())
            for path in sorted(job.reader.paths)}, "reader_publication_allowed": False}
    fresh.retain(bundle.folder / "original-role-result.json", result, read_only=read_only)
    return result


def derived_stage(bundle, result, name, prompt, schema, context, validate, kwargs, *, read_only):
    role = name.removeprefix(bundle.prefix + "-")
    require(role in fresh.continuation.ROLES, "derived_original_role")
    candidate, audit = result["candidate"], result["final_review"]
    expected = dict(bundle.context)
    if role != "write":
        expected["candidate"] = candidate
    if role == "repair":
        expected["review"] = audit
    expected_prompt = writing.author.WRITE_PROMPT if role == "write" else (
        writing.author.REPAIR_PROMPT if role == "repair" else writing.author.REVIEW_PROMPT)
    ids = list(bundle.context["assigned_points"])
    expected_schema = writing.section_schema(ids, bundle.working) if role in {"write", "repair"} else writing.review_schema(candidate, ids)
    require(prompt == expected_prompt and context == expected and schema == expected_schema
        and kwargs == {"evidence_packet": bundle.scope["original"]}, "exact_virtual_orchestration_request")
    value = candidate if role in {"write", "repair"} else audit
    with visible.packet_scope(bundle.scope):
        validate(value)
    actual_stage = bundle.prefix + ("-repair" if role in {"write", "repair"} else "-repair-review")
    receipt = {"policy": POLICY, "execution_sha256": digest(bundle.contract), "orchestration_stage": name,
        "actual_model_stage": actual_stage, "actual_finite_result_sha256": digest(result),
        "derived_value_sha256": digest(value), "failed_write_cache_created": False, "new_model_calls": 0,
        "negative_reviews_preserved": True, "reader_publication_allowed": False}
    path = bundle.folder / "derived" / (name + ".json")
    fresh.retain(path, receipt, read_only=read_only)
    return value, path


def job_type(bundle, result, *, read_only):
    base = ORIGINAL_FACTORY(bundle.original, read_only=read_only)

    class ResumedJob(base):
        def __init__(self, *args):
            super().__init__(*args)
            require(self.key == bundle.job.key, "unchanged_writer_identity")
            self.reader.paths.update(bundle.root / name for name in result["files"])
            self.reader.paths.update(bundle.paths | {bundle.folder / "execution.json",
                bundle.folder / "original-status.json", bundle.folder / "original-role-result.json"})

        def stage(self, name, prompt, schema, context, validate, **kwargs):
            match = re.fullmatch(fresh.PATTERN, name)
            if match and match[1] == bundle.prefix:
                value, path = derived_stage(bundle, result, name, prompt, schema, context, validate, kwargs, read_only=read_only)
                self.reader.paths.add(path)
                return value
            if match and match[1] in bundle.contract["future_units"] and match[2] in {"write", "repair"}:
                with mapped_format():
                    return super().stage(name, prompt, schema, context, validate, **kwargs)
            return super().stage(name, prompt, schema, context, validate, **kwargs)

        def progress(self, state, stage, **extra):
            old_count, old_total = self.finished_sections, self.section_count
            self.finished_sections = max(old_count, bundle.count)
            self.section_count = max(old_total, len(bundle.units))
            try:
                super().progress(state, stage, **extra)
            finally:
                self.finished_sections, self.section_count = old_count, old_total

    return ResumedJob


def worker(root, key, tokenizer):
    bundle = load(root, key, tokenizer)
    private_guard.checkpoint(bundle.root)
    with schema_lease(bundle.folder / "worker.lock", timeout=0):
        require(not (bundle.folder / "started.json").exists(), "worker_once_no_retry")
        atomic_json(bundle.folder / "started.json", bundle.job.identity)
        try:
            with schema_lease(bundle.root / "wiki/textbook-whole-record-evidence" / bundle.contract["inventory"] / "authoring-worker.lock", timeout=0):
                result = repair_target(bundle, tokenizer, read_only=False)
            with fresh.installed(job_type(bundle, result, read_only=False)):
                output = writing.run(bundle.root, bundle.contract["inventory"], bundle.contract["ledger_job"], tokenizer)
            fresh.retain(bundle.folder / "result.json", output)
            return output
        except BaseException as exc:
            atomic_json(bundle.folder / ("failure-" + str(time.time_ns()) + ".json"), writing.whole.failure_status(exc))
            raise


def reconstruct(root, key, tokenizer, *, complete=True):
    bundle = load(root, key, tokenizer)
    result = repair_target(bundle, tokenizer, read_only=True)
    original_factory = fresh.job_type
    fresh.job_type = lambda contract, *, read_only: job_type(bundle, result, read_only=read_only)
    try:
        rebuilt = fresh.reconstruct(bundle.root, bundle.contract["inventory"], bundle.contract["ledger_job"], tokenizer, complete=complete)
    finally:
        fresh.job_type = original_factory
    rebuilt["proof"].update(policy=POLICY, resumption_execution_sha256=digest(bundle.contract),
        resumption_module_sha256=writing.whole.sha(Path(__file__).read_bytes()))
    rebuilt["summary"]["state"] = "point_mapped_manuscript_reconstructed" if complete else "point_mapped_prefix_reconstructed"
    rebuilt["proof"]["state"] = rebuilt["summary"]["state"]
    if complete:
        require(writing.whole.read(bundle.folder / "result.json") == writing.whole.read(bundle.job.root / "result.json"), "actual_resumed_result")
    return rebuilt


def guard(root, key, tokenizer):
    bundle = load(root, key, tokenizer)
    result = private_guard.run(bundle.root, bundle.folder, [sys.executable, "-m", __spec__.name,
        "--root", str(bundle.root), "--job", key, "--tokenizer", str(Path(tokenizer).resolve()), "worker"])
    if result["state"] == "private_worker_exited":
        rebuilt = reconstruct(root, key, tokenizer)
        path = snapshot.save(root, rebuilt)
        return {**rebuilt["summary"], "snapshot": str(path)}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--job")
    parser.add_argument("--inventory")
    parser.add_argument("--ledger-job")
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("command", choices=["prepare", "load", "worker", "guard", "prefix", "verify"])
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            output = prepare(args.root, args.inventory, args.ledger_job, args.tokenizer)
        elif args.command == "load":
            bundle = load(args.root, args.job, args.tokenizer)
            output = {"state": "point_mapped_inputs_authenticated", "job": bundle.job.key,
                "prefix_units": bundle.count, "model_calls": 0}
        elif args.command in {"prefix", "verify"}:
            rebuilt = reconstruct(args.root, args.job, args.tokenizer, complete=args.command == "verify")
            output = {**rebuilt["summary"], "snapshot": str(snapshot.save(args.root, rebuilt))}
        else:
            output = globals()[args.command](args.root, args.job, args.tokenizer)
        print(json.dumps(output))
    except BaseException as exc:
        print(json.dumps(writing.whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
