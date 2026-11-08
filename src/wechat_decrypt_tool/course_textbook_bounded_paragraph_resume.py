"""Use unused original review/repair roles after an unmapped complete write.

Failed prose and all omitted mappings stay untrusted. Future unused writes use
an exact paragraph array length and bounded coverage indices, making structural
coverage enforceable by the decoding schema without claiming semantic coverage.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import math
from pathlib import Path
import re
import sys
import time
from types import SimpleNamespace

import jsonschema

from . import course_textbook_checked_backup_resume as previous
from .course_knowledge import atomic_json, schema_lease

reviewed = previous.reviewed
mapped, fresh, writing, whole = reviewed.mapped, reviewed.fresh, reviewed.writing, reviewed.whole
digest = reviewed.digest
DIRECTORY = "bounded-paragraph-continuation"
POLICY = "unused-original-repair-with-exact-paragraph-count-and-bounded-indices-v1"
NOTICE = """
本次以解码 schema 为准，使用 coverage.paragraphs 从零索引，不输出段内 point_ids。
paragraphs 的 minItems 与 maxItems 相同，必须输出该数量的实际完整正文段落。
coverage 每个任务的索引必须指向实际段落；每个有效教学任务至少有一段充分解释。
这个数量只约束内部格式，不是老师的时间分段。按知识逻辑重组、归并相关解释；
不得填空段、仅列术语、机械拆句或声称填齐编号就等于完整覆盖。
"""


def require(ok, code):
    if not ok:
        raise ValueError("requires_bounded_paragraph_" + code)


def paragraph_count(points):
    require(type(points) is int and points > 0, "nonempty_assigned_points")
    return max(2, min(12, math.ceil(points / 4)))


def bounded_schema(schema, packet, pending):
    result = mapped.ORIGINAL_SCHEMA(schema, packet, pending)

    def visit(node):
        props = node.get("properties", {})
        if props.get("paragraphs", {}).get("type") == "array":
            count = paragraph_count(len(props["coverage"]["properties"]))
            props["paragraphs"].update(minItems=count, maxItems=count)

            def bound(row):
                array = row.get("properties", {}).get("paragraphs")
                if array is not None:
                    array["items"]["maximum"] = count - 1
                for branch in row.get("oneOf", []):
                    bound(branch)

            for row in props["coverage"]["properties"].values():
                bound(row)
        for branch in node.get("oneOf", []):
            visit(branch)

    visit(result)
    return result


@contextmanager
def bounded_format():
    old_schema, old_candidate = fresh.section_schema, fresh.citation_candidate
    prompts = {role: writing.composition.PROMPTS[role]
        for role in (writing.author.WRITE_PROMPT, writing.author.REPAIR_PROMPT)}
    fresh.section_schema, fresh.citation_candidate = bounded_schema, mapped.ORIGINAL_CANDIDATE
    try:
        for role, text in prompts.items():
            writing.composition.PROMPTS[role] = text + NOTICE
        yield
    finally:
        fresh.section_schema, fresh.citation_candidate = old_schema, old_candidate
        writing.composition.PROMPTS.update(prompts)


def failed_writes(bundle, job):
    ids = list(bundle.context["assigned_points"])
    schema = writing.section_schema(ids, bundle.working)
    actual = mapped.point_schema(schema, bundle.scope["expanded"], bundle.working["pending_source_cards"])
    with mapped.mapped_format():
        request = fresh.continuation.draft.capture_request(job, bundle.prefix + "-write",
            writing.author.WRITE_PROMPT, actual, bundle.context, bundle.scope["expanded"])
    prospective = bundle.job.root / (bundle.prefix + "-write-prospective-request.json")
    require(whole.read(prospective) == {"policy": fresh.POLICY, "execution_sha256": digest(bundle.prior.original),
        "stage": bundle.prefix + "-write", "original_schema_sha256": digest(schema),
        "actual_schema_sha256": digest(actual), "original_context_sha256": digest(bundle.context),
        "actual_context_sha256": digest(bundle.context), "original_packet_sha256": digest(bundle.scope["original"]),
        "actual_packet_sha256": digest(bundle.scope["expanded"]), "original_budgets_reset": False,
        "reader_publication_allowed": False}, "actual_failed_prospective_request")
    paths = sorted((bundle.job.root / "attempts").glob(bundle.prefix + "-write-[0-9][0-9].json"))
    require([p.name for p in paths] == [bundle.prefix + "-write-01.json", bundle.prefix + "-write-02.json"],
        "exact_two_original_write_attempts")
    require(not (bundle.job.root / (bundle.prefix + "-write.json")).exists(), "no_fabricated_failed_write_cache")
    receipts = [(p, whole.read(p)) for p in bundle.job.root.glob("response-*.json")]
    prior_error, choices = "", []
    bundle.paths.add(prospective)
    for number, path in enumerate(paths, 1):
        row = whole.read(path)
        require(row["stage"] == bundle.prefix + "-write" and row["attempt"] == number
            and row["input_sha256"] == request["binding"] and row["model_digest"] == job.models["qwen3.5:9b"]
            and row.get("error_type") == "ValueError" and not row.get("accepted_structure")
            and row["validation_error"].startswith("requires_point_mapped_missing_teaching_ids_"),
            "actual_unmapped_failed_write")
        messages = [{"role": "system", "content": request["prompt"]}, {"role": "user", "content": json.dumps(
            {**bundle.context, "previous_validation_failure": prior_error}, ensure_ascii=False)}]
        require(row["request_sha256"] == digest(messages), "actual_complete_failed_request")
        whole.editor.e.verify_response(bundle.job.root, bundle.prefix + "-write", row)
        matches = [p for p, r in receipts if r["stage"] == row["stage"]
            and r["request_sha256"] == row["request_sha256"] and r.get("schema_sha256") == digest(request["schema"])
            and r["response"]["message"]["content"] == row["raw_response"] and r["metrics"] == row["model_metrics"]]
        require(len(matches) == 1 and row["model_metrics"]["done_reason"] == "stop", "actual_complete_gpu_response")
        raw = json.loads(row["raw_response"])
        jsonschema.validate(raw, actual)
        candidate, metadata = reviewed.proposal(raw, bundle)
        choices.append((raw, candidate, metadata, path.name))
        bundle.paths.update([path, *matches])
        prior_error = row["validation_error"]
    # Select the last real complete response, without combining the two bodies.
    bundle.raw, bundle.candidate, bundle.metadata, bundle.selected = choices[-1]
    bundle.history = [{"attempt": row[3], "raw_sha256": digest(row[0]),
        "omitted_points": len(row[2]["omitted_point_ids"])} for row in choices]


def inputs(root, key, tokenizer, count):
    prior = previous.load(root, key, tokenizer)
    job = reviewed.resumed_type(prior, prior.checked_result, read_only=True)(
        writing.TextbookStore(prior.root), prior.inventory, prior.working, tokenizer)
    plan = writing.organize(job, prior.working)
    units = writing.writing_units(plan, prior.working, prior.inventory, job.tokenizer)
    require(job.reader.saved("private-working-inputs") == prior.working
        and job.reader.saved("concept-plan") == plan and job.reader.saved("writing-allocation") == units,
        "same_original_plan_and_inputs")
    require(type(count) is int and prior.count < count < len(units), "original_completed_prefix")
    sections, _, issues = mapped.snapshot.replay_units(job.reader, units, plan, count)
    unit = units[count]
    context = mapped.visible.refs.unit_context(unit, count, units, plan, sections, prior.working, prior.inventory)
    packet = mapped.visible.ORIGINAL_PACKET(unit["point_ids"], prior.working, prior.inventory)
    scope = fresh.continuation.scope_for(unit["point_ids"], prior.working, prior.inventory, context, packet)
    bundle = SimpleNamespace(prior=prior, root=prior.root, job=prior.job, inventory=prior.inventory,
        working=prior.working, units=units, count=count, prefix=unit["id"], context=context, scope=scope,
        issues=issues, paths=set(prior.paths) | job.reader.paths,
        folder=prior.folder / DIRECTORY)
    bundle.paths.update(prior.root / p for p in prior.checked_result["files"])
    bundle.paths.add(prior.folder / previous.DIRECTORY / "execution.json")
    failed_writes(bundle, job)
    return bundle


def bindings():
    return {Path(m.__file__).stem: whole.sha(Path(m.__file__).read_bytes())
        for m in (sys.modules[__name__], previous, reviewed, mapped)}


def execution(bundle):
    return {"policy": POLICY, "job": bundle.job.key, "inventory": digest(bundle.inventory),
        "ledger_job": bundle.working["job"], "prefix_units": bundle.count, "total_units": len(bundle.units),
        "prefix_issues": len(bundle.issues), "section": bundle.prefix,
        "selected_failed_write": bundle.selected, "history": bundle.history,
        "candidate_sha256": digest(bundle.candidate), "metadata": bundle.metadata,
        "original_checked_result_sha256": digest(bundle.prior.checked_result), "code": bindings(),
        "files": {p.relative_to(bundle.root).as_posix(): whole.sha(p.read_bytes()) for p in sorted(bundle.paths)},
        "future_units": [u["id"] for u in bundle.units[bundle.count + 1:]],
        "original_stage_attempt_budget": 2, "semantic_repairs_per_unit": 1,
        "mandatory_original_repair": True, "new_model_roles": 0, "original_budgets_reset": False,
        "reader_publication_allowed": False, "source_deletion_allowed": False}


def require_unused(bundle):
    names = [bundle.prefix + suffix for suffix in ("-review", "-repair", "-repair-review")]
    names += [u["id"] + "-" + role for u in bundle.units[bundle.count + 1:] for role in fresh.continuation.ROLES]
    require(all(not list((bundle.job.root / "attempts").glob(n + "-[0-9][0-9].json"))
        and not list(bundle.job.root.glob(n + "*.json")) for n in names)
        and not list(bundle.job.root.glob("reading-*.json")), "all_remaining_original_roles_unused")


def prepare(root, key, tokenizer):
    root = Path(root).resolve()
    status = whole.read(root / "wiki/textbook-whole-authoring-jobs" / key / "progress.json")
    require(status["state"] == "stopped" and not writing.identity_live(status), "original_worker_exited")
    bundle = inputs(root, key, tokenizer, status["completed_sections"])
    with schema_lease(root / "wiki/textbook-whole-record-evidence" / digest(bundle.inventory) / "authoring-worker.lock", timeout=0):
        require(whole.read(bundle.job.root / "progress.json") == status
            and status["stage"] == bundle.prefix + "-write"
            and status["reason_code"] == "wiki_stage_attempt_budget_exhausted_" + status["stage"], "exact_exhausted_stage")
        old = bundle.prior.folder / previous.DIRECTORY
        guard = whole.read(old / "guard-progress.json")
        require(not writing.identity_live(guard) and guard["state"] == "worker_exited"
            and guard.get("returncode") == 1, "previous_guard_really_exited")
        require_unused(bundle)
        require(not (bundle.folder / "execution.json").exists(), "prepare_once_no_budget_refresh")
        bundle.paths.update([old / "guard-progress.json", *old.glob("failure-*.json")])
        fresh.retain(bundle.folder / "execution.json", execution(bundle))
        fresh.retain(bundle.folder / "proposal.json", {"candidate": bundle.candidate, "metadata": bundle.metadata})
        fresh.retain(bundle.folder / "previous-status.json", status)
    return {"state": "unused_original_roles_prepared_for_bounded_paragraph_repair", "job": key,
        "prefix_units": bundle.count, "total_units": len(bundle.units), "omitted_points": len(bundle.metadata["omitted_point_ids"]),
        "repair_paragraphs": paragraph_count(len(bundle.context["assigned_points"])), "model_calls": 0}


def load(root, key, tokenizer):
    require(re.fullmatch(r"[a-f0-9]{32}", key) is not None, "job_id")
    root = Path(root).resolve()
    folder = root / "wiki/textbook-whole-authoring-jobs" / key / mapped.DIRECTORY / reviewed.DIRECTORY / DIRECTORY
    contract = whole.read(folder / "execution.json")
    require(contract["policy"] == POLICY and contract["code"] == bindings(), "frozen_adapter_code")
    require(all(whole.sha((root / name).read_bytes()) == checksum for name, checksum in contract["files"].items()),
        "unchanged_original_evidence")
    bundle = inputs(root, key, tokenizer, contract["prefix_units"])
    bundle.paths = {root / p for p in contract["files"]}
    require(execution(bundle) == contract and whole.read(folder / "proposal.json") == {
        "candidate": bundle.candidate, "metadata": bundle.metadata}, "same_authenticated_proposal_and_plan")
    bundle.contract = contract
    return bundle


def original_roles(bundle, tokenizer, *, read_only):
    job = mapped.ORIGINAL_FACTORY(bundle.prior.original, read_only=read_only)(
        writing.TextbookStore(bundle.root), bundle.inventory, bundle.working, tokenizer)
    job.finished_sections, job.section_count = bundle.count, len(bundle.units)
    ids = list(bundle.context["assigned_points"])
    common = {**bundle.context, "failed_write_is_untrusted": True, "original_repair_is_mandatory": True,
        "unverified_omitted_point_search_scope": bundle.metadata, "full_body_indices_are_search_scope_not_proof": True}

    def audit(candidate, suffix):
        schema = writing.review_schema(candidate, ids)
        return job.stage(bundle.prefix + suffix, writing.author.REVIEW_PROMPT, schema,
            {**common, "candidate": candidate}, lambda row: writing.author.validate_review(row, schema),
            evidence_packet=bundle.scope["original"])

    initial = audit(bundle.candidate, "-review")
    with bounded_format():
        repaired = job.stage(bundle.prefix + "-repair", writing.author.REPAIR_PROMPT,
            writing.section_schema(ids, bundle.working), {**common, "candidate": bundle.candidate, "review": initial},
            lambda row: writing.validate_section(row, ids, bundle.working, bundle.inventory),
            evidence_packet=bundle.scope["original"])
    final = audit(repaired, "-repair-review")
    paths = set(job.reader.paths) | bundle.paths | {bundle.folder / "execution.json", bundle.folder / "proposal.json"}
    paths.update(p for p in job.root.glob("response-*.json") if whole.read(p)["stage"].startswith(bundle.prefix + "-"))
    result = {"policy": POLICY, "candidate": repaired, "initial_review": initial, "final_review": final,
        "final_checks_passed": all(writing.author.verdicts(final)), "original_budgets_reset": False,
        "files": {p.relative_to(bundle.root).as_posix(): whole.sha(p.read_bytes()) for p in sorted(paths)},
        "reader_publication_allowed": False}
    fresh.retain(bundle.folder / "original-role-result.json", result, read_only=read_only)
    return result


def resumed_type(bundle, result, *, read_only):
    base = reviewed.resumed_type(bundle.prior, bundle.prior.checked_result, read_only=read_only)
    original_base = next(cls for cls in base.__mro__ if cls.__name__ == "FreshJob")

    class ResumedJob(base):
        def __init__(self, *args):
            super().__init__(*args)
            self.reader.paths.update(bundle.root / p for p in result["files"])
            self.reader.paths.update([bundle.folder / "execution.json", bundle.folder / "original-role-result.json"])

        def stage(self, name, prompt, schema, context, validate, **kwargs):
            match = re.fullmatch(fresh.PATTERN, name)
            if match and match[1] == bundle.prefix:
                role, candidate, audit = match[2], result["candidate"], result["final_review"]
                expected = dict(bundle.context)
                if role != "write":
                    expected["candidate"] = candidate
                if role == "repair":
                    expected["review"] = audit
                expected_prompt = writing.author.WRITE_PROMPT if role == "write" else (
                    writing.author.REPAIR_PROMPT if role == "repair" else writing.author.REVIEW_PROMPT)
                ids = list(bundle.context["assigned_points"])
                expected_schema = writing.section_schema(ids, bundle.working) if role in {"write", "repair"} else writing.review_schema(candidate, ids)
                require(context == expected and prompt == expected_prompt and schema == expected_schema
                    and kwargs == {"evidence_packet": bundle.scope["original"]}, "exact_original_orchestration_request")
                value = candidate if role in {"write", "repair"} else audit
                with mapped.visible.packet_scope(bundle.scope):
                    validate(value)
                path = bundle.folder / "derived" / (name + ".json")
                fresh.retain(path, {"policy": POLICY, "stage": name, "actual_model_stage": bundle.prefix + (
                    "-repair" if role in {"write", "repair"} else "-repair-review"), "actual_result_sha256": digest(result),
                    "failed_write_cache_created": False, "new_model_calls": 0, "negative_reviews_preserved": True}, read_only=read_only)
                self.reader.paths.add(path)
                return value
            if match and match[1] in bundle.contract["future_units"] and match[2] in {"write", "repair"}:
                with bounded_format():
                    return original_base.stage(self, name, prompt, schema, context, validate, **kwargs)
            return super().stage(name, prompt, schema, context, validate, **kwargs)

        def progress(self, state, stage, **extra):
            count, total = self.finished_sections, self.section_count
            self.finished_sections, self.section_count = max(count, bundle.count), max(total, len(bundle.units))
            try:
                super().progress(state, stage, **extra)
            finally:
                self.finished_sections, self.section_count = count, total

    return ResumedJob


@contextmanager
def runtime_policies(bundle):
    original_backup, original_issues = writing.backup_job, writing.final_issues

    def backup(job):
        previous.backup_job(job)
        path = Path(__file__)
        destination = Path.home() / "Documents/CourseKnowledgeBackups/whole-source-articles" / job.key
        whole.backup(path, destination / "adapter-code" / (whole.sha(path.read_bytes()) + "-" + path.name))

    def issues(inventory, private, existing):
        require(digest(inventory) == digest(bundle.inventory), "same_whole_original_inventory")
        return [*original_issues(inventory, private, existing), {
            "kind": "untrusted_failed_write_mapping_requires_whole_original_coverage", "section": bundle.prefix,
            "point_ids": bundle.metadata["omitted_point_ids"], "failed_candidate_sha256": digest(bundle.candidate)}]

    writing.backup_job, writing.final_issues = backup, issues
    try:
        with reviewed.retained_obligation(bundle.prior):
            yield
    finally:
        writing.backup_job, writing.final_issues = original_backup, original_issues


def worker(root, key, tokenizer):
    bundle = load(root, key, tokenizer)
    reviewed.guarder.checkpoint(bundle.root)
    require(not (bundle.folder / "started.json").exists(), "worker_once_no_automatic_retry")
    fresh.retain(bundle.folder / "started.json", bundle.job.identity)
    try:
        with schema_lease(bundle.root / "wiki/textbook-whole-record-evidence" / digest(bundle.inventory) / "authoring-worker.lock", timeout=0):
            result = original_roles(bundle, tokenizer, read_only=False)
        with runtime_policies(bundle), fresh.installed(resumed_type(bundle, result, read_only=False)):
            output = writing.run(bundle.root, digest(bundle.inventory), bundle.working["job"], tokenizer)
        fresh.retain(bundle.folder / "result.json", output)
        return output
    except BaseException as exc:
        atomic_json(bundle.folder / ("failure-" + str(time.time_ns()) + ".json"), whole.failure_status(exc))
        raise


def reconstruct(root, key, tokenizer, *, complete=True):
    bundle = load(root, key, tokenizer)
    result = original_roles(bundle, tokenizer, read_only=True)
    old = fresh.job_type
    fresh.job_type = lambda contract, *, read_only: resumed_type(bundle, result, read_only=read_only)
    try:
        with runtime_policies(bundle):
            rebuilt = fresh.reconstruct(root, digest(bundle.inventory), bundle.working["job"], tokenizer, complete=complete)
    finally:
        fresh.job_type = old
    rebuilt["proof"].update(policy=POLICY, bounded_paragraph_execution_sha256=digest(bundle.contract), adapter_code=bindings())
    if complete:
        require(whole.read(bundle.folder / "result.json") == whole.read(bundle.job.root / "result.json"), "actual_finished_continuation")
    return rebuilt


def guard(root, key, tokenizer):
    bundle = load(root, key, tokenizer)
    result = reviewed.guarder.run(bundle.root, bundle.folder, [sys.executable, "-m", __spec__.name,
        "--root", str(bundle.root), "--job", key, "--tokenizer", str(Path(tokenizer).resolve()), "worker"])
    if result["state"] == "private_worker_exited":
        rebuilt = reconstruct(root, key, tokenizer)
        return {**rebuilt["summary"], "snapshot": str(mapped.snapshot.save(root, rebuilt))}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("command", choices=["prepare", "load", "worker", "guard", "prefix", "verify"])
    args = parser.parse_args()
    try:
        if args.command == "load":
            bundle = load(args.root, args.job, args.tokenizer)
            output = {"state": "bounded_paragraph_inputs_authenticated", "job": args.job,
                "prefix_units": bundle.count, "omitted_points": len(bundle.metadata["omitted_point_ids"]), "model_calls": 0}
        elif args.command in {"prefix", "verify"}:
            rebuilt = reconstruct(args.root, args.job, args.tokenizer, complete=args.command == "verify")
            output = {**rebuilt["summary"], "snapshot": str(mapped.snapshot.save(args.root, rebuilt))}
        else:
            output = globals()[args.command](args.root, args.job, args.tokenizer)
        print(json.dumps(output))
    except BaseException as exc:
        print(json.dumps(whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
