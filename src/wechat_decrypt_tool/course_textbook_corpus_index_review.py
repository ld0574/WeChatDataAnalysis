"""Review the last exhausted repair as an explicit, untrusted index proposal.

Only the original unused final review and later original roles may run. Future
writes select a paragraph count in the decoding grammar, which then bounds all
coverage indices to that same actual length. Original failures remain intact.
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

import jsonschema

from . import course_textbook_corpus_structural_resume as prior
from .course_knowledge import atomic_json, schema_lease

fresh, writing, whole = prior.fresh, prior.writing, prior.whole
digest, guard = prior.digest, prior.guard
POLICY = "original-unused-corpus-final-review-untrusted-index-search-scope-v1"
DIRECTORY = "index-scope-review"
NOTICE = """
先输出 paragraph_count，选择实际需要的 2–12 段完整正文；后续解码分支锁定同一段数。
paragraphs 必须正好该数量，coverage.paragraphs 的从零索引最大只能为 paragraph_count - 1。
段数只约束内部表示，不证明教学覆盖；按知识逻辑充分讲解全部有效教学。
不得用空段、重复段、机械拆句凑数，也不得删掉任务或改成非教学来满足格式。
"""


def require(ok, code):
    if not ok:
        raise ValueError("requires_corpus_index_review_" + code)


def tagged_schema(schema, packet, pending):
    result = prior.section_schema(schema, packet, pending)

    def bound(node, count):
        array = node.get("properties", {}).get("paragraphs")
        if array is not None:
            array["items"].update(minimum=0, maximum=count - 1)
        for branch in node.get("oneOf", []):
            bound(branch, count)

    def visit(node):
        props = node.get("properties", {})
        if props.get("paragraphs", {}).get("type") == "array" and "coverage" in props:
            branches = []
            for count in range(2, 13):
                branch = copy.deepcopy(node)
                fields = branch["properties"]
                branch["properties"] = {"paragraph_count": {"type": "integer", "const": count}, **fields}
                branch["required"] = ["paragraph_count", *branch["required"]]
                fields["paragraphs"].update(minItems=count, maxItems=count)
                for row in fields["coverage"]["properties"].values():
                    bound(row, count)
                branches.append(branch)
            return {"oneOf": branches}
        result = copy.deepcopy(node)
        if "oneOf" in result:
            result["oneOf"] = [visit(branch) for branch in result["oneOf"]]
        return result

    return visit(result)


def tagged_candidate(raw, ids, working, inventory, scope):
    value = copy.deepcopy(raw)
    if value.get("kind") != "non_teaching":
        count = value.pop("paragraph_count", None)
        require(type(count) is int and 2 <= count <= 12 and count == len(value["paragraphs"]),
            "actual_tagged_paragraph_count")
    candidate, changes = prior.citation_candidate(value, ids, working, inventory, scope)
    return candidate, {"citation_proposals": changes, "paragraph_count_tag_removed_only": True,
        "prose_and_teaching_unchanged": True}


@contextmanager
def future_format():
    previous = fresh.section_schema, fresh.citation_candidate
    prompts = {role: writing.composition.PROMPTS[role]
        for role in (writing.author.WRITE_PROMPT, writing.author.REPAIR_PROMPT)}
    fresh.section_schema, fresh.citation_candidate = tagged_schema, tagged_candidate
    try:
        for role, prompt in prompts.items():
            writing.composition.PROMPTS[role] = prompt + NOTICE
        yield
    finally:
        fresh.section_schema, fresh.citation_candidate = previous
        writing.composition.PROMPTS.update(prompts)


def common(bundle):
    return {**bundle.context, "candidate_is_untrusted": True, "original_repair_is_mandatory": True,
        "observed_structural_defects": bundle.defects}


def index_proposal(raw, bundle):
    ids = list(bundle.context["assigned_points"])
    schema = prior.section_schema(writing.section_schema(ids, bundle.working),
        bundle.scope["expanded"], bundle.working["pending_source_cards"])
    jsonschema.validate(raw, schema)
    require(raw.get("kind") != "non_teaching", "real_teaching_repair")
    require(not any(prior.defects(raw).values()), "nonempty_distinct_original_body")
    count, proposed, changes = len(raw["paragraphs"]), copy.deepcopy(raw), {}
    for key, row in raw["coverage"].items():
        if any(index >= count for index in row["paragraphs"]):
            changes[key] = {"original_indices": row["paragraphs"], "unverified_search_scope": list(range(count))}
            proposed["coverage"][key]["paragraphs"] = list(range(count))
    require(bool(changes), "actual_out_of_bounds_mapping")
    candidate, citations = prior.citation_candidate(proposed, ids, bundle.working, bundle.inventory, bundle.scope)
    require([p["text"] for p in candidate["paragraphs"]] == [p["text"] for p in raw["paragraphs"]],
        "all_original_prose_unchanged")
    return candidate, {"invalid_mappings": changes, "actual_paragraph_count": count,
        "citation_proposals": citations, "mapping_proposals_are_factual_evidence": False,
        "full_body_is_only_unverified_search_scope": True, "prose_changed": False,
        "whole_original_coverage_still_required": True, "reader_publication_allowed": False}


def failed_repairs(bundle, job, initial):
    name, ids = bundle.prefix + "-repair", list(bundle.context["assigned_points"])
    context = {**common(bundle), "candidate": bundle.candidate, "review": initial}
    schema = prior.section_schema(writing.section_schema(ids, bundle.working),
        bundle.scope["expanded"], bundle.working["pending_source_cards"])
    with prior.variable_format():
        request = fresh.continuation.draft.capture_request(job, name, writing.author.REPAIR_PROMPT,
            schema, context, bundle.scope["expanded"])
    path = bundle.job.root / (name + "-prospective-request.json")
    require(whole.read(path) == {"policy": fresh.POLICY, "execution_sha256": digest(bundle.original), "stage": name,
        "original_schema_sha256": digest(writing.section_schema(ids, bundle.working)),
        "actual_schema_sha256": digest(schema), "original_context_sha256": digest(context),
        "actual_context_sha256": digest(context), "original_packet_sha256": digest(bundle.scope["original"]),
        "actual_packet_sha256": digest(bundle.scope["expanded"]), "original_budgets_reset": False,
        "reader_publication_allowed": False}, "original_repair_prospective_request")
    bundle.paths.add(path)
    paths = sorted((bundle.job.root / "attempts").glob(name + "-[0-9][0-9].json"))
    require([p.name for p in paths] == [name + "-01.json", name + "-02.json"]
        and not (bundle.job.root / (name + ".json")).exists(), "original_repair_exhausted_without_accepted_cache")
    receipts = [(p, whole.read(p)) for p in bundle.job.root.glob("response-*.json")]
    previous, choices = "", []
    for number, path in enumerate(paths, 1):
        row = whole.read(path)
        messages = [{"role": "system", "content": request["prompt"]}, {"role": "user", "content": json.dumps(
            {**context, "previous_validation_failure": previous}, ensure_ascii=False)}]
        require(row["stage"] == name and row["attempt"] == number and not row.get("accepted_structure")
            and row["input_sha256"] == request["binding"] and row["request_sha256"] == digest(messages)
            and row["model_digest"] == job.models["qwen3.5:9b"] and row.get("error_type") == "ValueError",
            "actual_failed_repair_identity_and_request")
        whole.editor.e.verify_response(bundle.job.root, name, row)
        matches = [p for p, receipt in receipts if receipt["stage"] == name
            and receipt["request_sha256"] == row["request_sha256"] and receipt.get("schema_sha256") == digest(schema)
            and receipt["response"]["message"]["content"] == row["raw_response"]
            and receipt["metrics"] == row["model_metrics"]]
        require(len(matches) == 1 and row["model_metrics"]["done_reason"] == "stop", "complete_original_gpu_wire_receipt")
        raw = json.loads(row["raw_response"])
        jsonschema.validate(raw, schema)
        expected = "requires_corpus_structural_mapping_indices_within_actual_array_length_" + str(len(raw["paragraphs"]))
        require(row["validation_error"] == expected, "actual_mapping_error")
        candidate, metadata = index_proposal(raw, bundle)
        choices.append((raw, candidate, metadata, path.name))
        bundle.paths.update([path, *matches])
        previous = row["validation_error"]
    bundle.raw, bundle.proposed, bundle.metadata, bundle.selected = choices[-1]
    bundle.repair_history = [{"attempt": choice[3], "raw_sha256": digest(choice[0]),
        "invalid_mappings": len(choice[2]["invalid_mappings"])} for choice in choices]


def inputs(root, key):
    bundle = prior.load(root, key)
    job = prior.make_job(bundle, read_only=True)
    stage = next(cls.stage for cls in type(job).__mro__ if cls.__name__ == "FreshJob")
    schema = writing.review_schema(bundle.candidate, list(bundle.context["assigned_points"]))
    initial = stage(job, bundle.prefix + "-review", writing.author.REVIEW_PROMPT, schema,
        {**common(bundle), "candidate": bundle.candidate}, lambda row: writing.author.validate_review(row, schema),
        evidence_packet=bundle.scope["original"])
    bundle.paths.update(job.reader.paths)
    bundle.paths.update(p for p in bundle.job.root.glob("response-*.json")
        if whole.read(p)["stage"] == bundle.prefix + "-review")
    failed_repairs(bundle, job, initial)
    bundle.paths.update([bundle.folder / "execution.json", bundle.folder / "old-status.json"])
    bundle.paths.update(bundle.folder.glob("failure-*.json"))
    bundle.paths.add(bundle.folder / "guard-progress.json")
    bundle.prior_folder, bundle.initial = bundle.folder, initial
    bundle.folder = bundle.folder / DIRECTORY
    return bundle


def execution(bundle):
    return {"policy": POLICY, "job": bundle.job.key, "section": bundle.prefix,
        "original_resume_sha256": digest(bundle.contract), "selected_failed_repair": bundle.selected,
        "selected_raw_sha256": digest(bundle.raw), "candidate_sha256": digest(bundle.proposed),
        "initial_review_sha256": digest(bundle.initial), "metadata": bundle.metadata, "history": bundle.repair_history,
        "module_sha256": whole.sha(Path(__file__).read_bytes()),
        "prior_module_sha256": whole.sha(Path(prior.__file__).read_bytes()),
        "notice_sha256": digest(NOTICE), "files": {p.relative_to(bundle.root).as_posix(): whole.sha(p.read_bytes())
            for p in sorted(bundle.paths)}, "only_original_unused_roles": True, "original_budgets_reset": False,
        "new_semantic_repair_allowed": False, "reader_publication_allowed": False, "source_deletion_allowed": False}


def prepare(root, key):
    guard.checkpoint(Path(root).resolve())
    bundle = inputs(root, key)
    with schema_lease(bundle.root / "wiki/textbook-whole-record-evidence" / digest(bundle.inventory) / "authoring-worker.lock", timeout=0):
        old_guard = whole.read(bundle.prior_folder / "guard-progress.json")
        status = whole.read(bundle.job.root / "progress.json")
        require(not writing.identity_live(old_guard) and not writing.identity_live(status)
            and old_guard["state"] == "worker_exited" and old_guard.get("returncode") == 1,
            "original_failed_worker_and_guard_exited")
        names = [bundle.prefix + "-repair-review"]
        names += [unit + "-" + role for unit in bundle.contract["future_units"] for role in fresh.continuation.ROLES]
        require(all(not list((bundle.job.root / "attempts").glob(name + "-[0-9][0-9].json"))
            and not list(bundle.job.root.glob(name + "*.json")) for name in names)
            and not list(bundle.job.root.glob("reading-*.json")), "all_remaining_original_roles_unused")
        require(not (bundle.folder / "execution.json").exists(), "prepare_once_no_budget_refresh")
        fresh.retain(bundle.folder / "execution.json", execution(bundle))
        fresh.retain(bundle.folder / "proposal.json", {"candidate": bundle.proposed, "metadata": bundle.metadata})
    return {"state": "original_unused_corpus_final_review_prepared", "job": key, "model_calls": 0,
        "paragraphs": len(bundle.proposed["paragraphs"]), "invalid_mappings": len(bundle.metadata["invalid_mappings"]),
        "reader_publication_allowed": False}


def load(root, key):
    bundle = inputs(root, key)
    require(whole.read(bundle.folder / "execution.json") == execution(bundle), "same_original_failures_and_frozen_execution")
    require(whole.read(bundle.folder / "proposal.json") == {"candidate": bundle.proposed, "metadata": bundle.metadata},
        "unchanged_private_proposal")
    return bundle


def final_review(bundle, *, read_only):
    job = prior.make_job(bundle, read_only=read_only)
    job.finished_sections, job.section_count = bundle.count, len(bundle.units)
    stage = next(cls.stage for cls in type(job).__mro__ if cls.__name__ == "FreshJob")
    schema = writing.review_schema(bundle.proposed, list(bundle.context["assigned_points"]))
    context = {**common(bundle), "candidate": bundle.proposed, "initial_review": bundle.initial,
        "unverified_index_search_scope": bundle.metadata, "actual_failed_repair_sha256": digest(bundle.raw),
        "full_body_indices_are_search_scope_not_proof": True,
        "original_repair_attempts_exhausted": True, "new_semantic_repair_allowed": False}
    job.proposals[bundle.prefix + "-repair"] = {"candidate": bundle.proposed, "metadata": bundle.metadata,
        "actual_failed_repair_sha256": digest(bundle.raw), "semantic_acceptance": False}
    audit = stage(job, bundle.prefix + "-repair-review", writing.author.REVIEW_PROMPT, schema, context,
        lambda row: writing.author.validate_review(row, schema), evidence_packet=bundle.scope["original"])
    job.reader.paths.update(bundle.paths | {bundle.folder / "execution.json", bundle.folder / "proposal.json"})
    job.reader.paths.update(p for p in job.root.glob("response-*.json")
        if whole.read(p)["stage"] == bundle.prefix + "-repair-review")
    result = {"policy": POLICY, "candidate": bundle.proposed, "metadata": bundle.metadata, "final_review": audit,
        "final_checks_passed": all(writing.author.verdicts(audit)), "whole_original_coverage_required": True,
        "files": {p.relative_to(bundle.root).as_posix(): whole.sha(p.read_bytes()) for p in sorted(job.reader.paths)},
        "reader_publication_allowed": False}
    fresh.retain(bundle.folder / "final-review-result.json", result, read_only=read_only)
    return result


def resumed_type(bundle, result, *, read_only):
    base = prior.original.first_type(bundle.original, bundle.extra, bundle.parent, read_only=read_only)
    original_stage = next(cls.stage for cls in base.__mro__ if cls.__name__ == "FreshJob")

    class ResumedJob(base):
        def __init__(self, *args):
            super().__init__(*args)
            require(self.key == bundle.job.key, "unchanged_writer_identity")
            self.reader.paths.update(bundle.root / p for p in result["files"])
            self.reader.paths.add(bundle.folder / "final-review-result.json")

        def stage(self, name, prompt, schema, context, validate, **kwargs):
            match = re.fullmatch(fresh.PATTERN, name)
            if match and match[1] == bundle.prefix:
                role, candidate, audit = match[2], result["candidate"], result["final_review"]
                expected = dict(bundle.context)
                if role != "write":
                    expected["candidate"] = candidate
                if role == "repair":
                    expected["review"] = audit
                ids = list(bundle.context["assigned_points"])
                expected_prompt = writing.author.WRITE_PROMPT if role == "write" else (
                    writing.author.REPAIR_PROMPT if role == "repair" else writing.author.REVIEW_PROMPT)
                expected_schema = writing.section_schema(ids, bundle.working) if role in {"write", "repair"} else writing.review_schema(candidate, ids)
                require(context == expected and prompt == expected_prompt and schema == expected_schema
                    and kwargs == {"evidence_packet": bundle.scope["original"]}, "exact_original_orchestration")
                value = candidate if role in {"write", "repair"} else audit
                with prior.visible.packet_scope(bundle.scope):
                    validate(value)
                path = bundle.folder / "derived" / (name + ".json")
                fresh.retain(path, {"policy": POLICY, "orchestration_stage": name,
                    "actual_failed_repair": bundle.selected, "actual_final_review": bundle.prefix + "-repair-review",
                    "result_sha256": digest(result), "value_sha256": digest(value), "new_model_calls": 0,
                    "accepted_failed_repair_cache_created": False, "reader_publication_allowed": False}, read_only=read_only)
                self.reader.paths.add(path)
                return value
            if match and match[1] in bundle.contract["future_units"] and match[2] in {"write", "repair"}:
                with future_format():
                    return original_stage(self, name, prompt, schema, context, validate, **kwargs)
            return super().stage(name, prompt, schema, context, validate, **kwargs)

    return ResumedJob


@contextmanager
def runtime_issues(bundle):
    with prior.runtime_issues(bundle):
        parent_issues = writing.final_issues

        def combined(inventory, private, existing):
            return [*parent_issues(inventory, private, existing), {
                "kind": "invalid_index_search_scope_requires_complete_original_coverage", "section": bundle.prefix,
                "point_ids": list(bundle.metadata["invalid_mappings"]), "candidate_sha256": digest(bundle.proposed)}]

        writing.final_issues = combined
        try:
            yield
        finally:
            writing.final_issues = parent_issues


def worker(root, key):
    bundle = load(root, key)
    guard.checkpoint(bundle.root)
    with schema_lease(bundle.folder / "worker.lock", timeout=0):
        require(not (bundle.folder / "started.json").exists(), "worker_once_no_automatic_retry")
        fresh.retain(bundle.folder / "started.json", bundle.job.identity)
        try:
            with schema_lease(bundle.root / "wiki/textbook-whole-record-evidence" / digest(bundle.inventory) / "authoring-worker.lock", timeout=0):
                result = final_review(bundle, read_only=False)
            with runtime_issues(bundle), fresh.installed(resumed_type(bundle, result, read_only=False)):
                output = writing.run(bundle.root, digest(bundle.inventory), bundle.working["job"], bundle.tokenizer)
            fresh.retain(bundle.folder / "result.json", output)
            return output
        except BaseException as exc:
            atomic_json(bundle.folder / ("failure-" + str(time.time_ns()) + ".json"), whole.failure_status(exc))
            raise


def reconstruct(root, key, *, complete=True):
    bundle = load(root, key)
    result = final_review(bundle, read_only=True)
    previous = fresh.job_type
    fresh.job_type = lambda contract, *, read_only: resumed_type(bundle, result, read_only=read_only)
    try:
        with runtime_issues(bundle):
            rebuilt = fresh.reconstruct(bundle.root, digest(bundle.inventory), bundle.working["job"], bundle.tokenizer, complete=complete)
    finally:
        fresh.job_type = previous
    rebuilt["proof"].update(policy=POLICY, index_review_execution_sha256=digest(execution(bundle)),
        index_review_module_sha256=whole.sha(Path(__file__).read_bytes()))
    return rebuilt


def guarded(root, key):
    bundle = load(root, key)
    result = guard.run(bundle.root, bundle.folder, [sys.executable, "-m", __spec__.name,
        "--root", str(bundle.root), "--job", key, "worker"])
    if result["state"] == "private_worker_exited":
        rebuilt = reconstruct(root, key)
        return {**rebuilt["summary"], "snapshot": str(prior.snapshot.save(root, rebuilt))}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("command", choices=["prepare", "load", "worker", "guard", "prefix", "verify"])
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = prepare(args.root, args.job)
        elif args.command == "load":
            bundle = load(args.root, args.job)
            result = {"state": "original_failed_corpus_repairs_authenticated", "job": bundle.job.key,
                "model_calls": 0, "invalid_mappings": len(bundle.metadata["invalid_mappings"])}
        elif args.command in {"prefix", "verify"}:
            rebuilt = reconstruct(args.root, args.job, complete=args.command == "verify")
            result = {**rebuilt["summary"], "snapshot": str(prior.snapshot.save(args.root, rebuilt))}
        else:
            result = (guarded if args.command == "guard" else worker)(args.root, args.job)
        print(json.dumps(result))
    except BaseException as exc:
        print(json.dumps(whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
