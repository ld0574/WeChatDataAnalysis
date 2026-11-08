"""Audit an exhausted point-mapped repair without inventing missing prose.

An omitted point receives the entire unchanged body as an explicitly untrusted
search scope. The unused original final review decides factual coverage; a
mandatory unresolved obligation remains for whole-original coverage review.
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

from . import course_textbook_point_mapped_resume as mapped
from .course_knowledge import atomic_json, schema_lease

fresh, writing, whole = mapped.fresh, mapped.writing, mapped.writing.whole
digest, guarder = mapped.digest, mapped.private_guard
POLICY = "original-unused-final-review-explicit-omitted-point-search-scope-v1"
DIRECTORY = "omitted-mapping-review"


def require(ok, code):
    if not ok:
        raise ValueError("requires_omitted_mapping_" + code)


def common(bundle):
    return {**bundle.context, "structural_defects": bundle.defects,
        "candidate_requires_original_role_repair": True, "candidate_is_untrusted": True}


def canonical_mapping(raw, ids, working):
    candidate = copy.deepcopy(raw)
    require(candidate.get("kind") != "non_teaching", "complete_teaching_repair")
    mapping = {key: [] for key in ids}
    for index, paragraph in enumerate(candidate["paragraphs"]):
        for key in paragraph.pop("point_ids"):
            require(key in mapping, "only_assigned_ids")
            mapping[key].append(index)
    missing = []
    for key, row in candidate["coverage"].items():
        row["paragraphs"] = mapping[key]
        if not mapping[key] and (key not in working["pending_source_cards"] or row["teaching"]):
            missing.append(key)
    return candidate, missing


def proposal(raw, bundle):
    ids = list(bundle.context["assigned_points"])
    schema = mapped.point_schema(writing.section_schema(ids, bundle.working), bundle.scope["expanded"], bundle.working["pending_source_cards"])
    jsonschema.validate(raw, schema)
    candidate, missing = canonical_mapping(raw, ids, bundle.working)
    require(bool(missing), "actual_omitted_point_mapping")
    for key in missing:
        candidate["coverage"][key]["paragraphs"] = list(range(len(candidate["paragraphs"])))
    candidate, citation_changes = mapped.ORIGINAL_CANDIDATE(candidate, ids, bundle.working, bundle.inventory, bundle.scope)
    require([row["text"] for row in candidate["paragraphs"]] == [row["text"] for row in raw["paragraphs"]], "all_prose_unchanged")
    return candidate, {"omitted_point_ids": missing,
        "full_body_search_scope": {key: candidate["coverage"][key]["paragraphs"] for key in missing},
        "citation_proposals": citation_changes, "prose_changed": False, "mapping_proposals_are_factual_evidence": False,
        "whole_original_coverage_review_still_required": True, "reader_publication_allowed": False}


def checked_future_candidate(raw, ids, working, inventory, scope):
    if raw.get("kind") != "non_teaching":
        _, missing = canonical_mapping(raw, ids, working)
        if missing:
            raise ValueError("requires_point_mapped_missing_teaching_ids_" + "_".join(missing))
    return mapped.point_candidate(raw, ids, working, inventory, scope)


@contextmanager
def future_format():
    with mapped.mapped_format():
        old = fresh.citation_candidate
        fresh.citation_candidate = checked_future_candidate
        try:
            yield
        finally:
            fresh.citation_candidate = old


def inputs(root, key, tokenizer):
    bundle = mapped.load(root, key, tokenizer)
    job = mapped.ORIGINAL_FACTORY(bundle.original, read_only=True)(writing.TextbookStore(bundle.root),
        bundle.inventory, bundle.working, tokenizer)
    ids, prefix = list(bundle.context["assigned_points"]), bundle.prefix
    initial_schema = writing.review_schema(bundle.original_candidate, ids)
    initial = job.stage(prefix + "-review", writing.author.REVIEW_PROMPT, initial_schema,
        {**common(bundle), "candidate": bundle.original_candidate},
        lambda row: writing.author.validate_review(row, initial_schema), evidence_packet=bundle.scope["original"])
    context = {**common(bundle), "candidate": bundle.original_candidate, "review": initial}
    with mapped.mapped_format():
        schema = mapped.point_schema(writing.section_schema(ids, bundle.working), bundle.scope["expanded"], bundle.working["pending_source_cards"])
        request = fresh.continuation.draft.capture_request(job, prefix + "-repair", writing.author.REPAIR_PROMPT,
            schema, context, bundle.scope["expanded"])
    paths = sorted((bundle.job.root / "attempts").glob(prefix + "-repair-[0-9][0-9].json"))
    require([p.name for p in paths] == [prefix + "-repair-01.json", prefix + "-repair-02.json"], "exhausted_original_two_repair_attempts")
    previous, choices = "", []
    receipts = [(p, whole.read(p)) for p in bundle.job.root.glob("response-*.json")]
    bundle.paths.update(job.reader.paths)
    bundle.paths.add(bundle.job.root / (prefix + "-repair-prospective-request.json"))
    for number, path in enumerate(paths, 1):
        row = whole.read(path)
        require(row["stage"] == prefix + "-repair" and row["attempt"] == number
            and row["input_sha256"] == request["binding"] and row["model_digest"] == job.models["qwen3.5:9b"]
            and row.get("error_type") == "ValidationError" and not row.get("accepted_structure"), "actual_failed_repair_identity")
        messages = [{"role": "system", "content": request["prompt"]}, {"role": "user", "content": json.dumps(
            {**context, "previous_validation_failure": previous}, ensure_ascii=False)}]
        require(row["request_sha256"] == digest(messages), "actual_failed_repair_request")
        whole.editor.e.verify_response(bundle.job.root, prefix + "-repair", row)
        matching = [p for p, receipt in receipts if receipt["stage"] == prefix + "-repair"
            and receipt["request_sha256"] == row["request_sha256"]
            and receipt.get("schema_sha256") == digest(request["schema"])
            and receipt["response"]["message"]["content"] == row["raw_response"]
            and receipt["metrics"] == row["model_metrics"]]
        require(len(matching) == 1, "actual_failed_repair_decoding_schema_and_gpu_receipt")
        raw = json.loads(row["raw_response"])
        jsonschema.validate(raw, request["schema"])
        candidate, metadata = proposal(raw, bundle)
        choices.append((raw, candidate, metadata, path.name))
        bundle.paths.update([path, *matching])
        previous = row["validation_error"]
    bundle.paths.update(bundle.folder.glob("failure-*.json"))
    bundle.paths.add(bundle.folder / "guard-progress.json")
    bundle.raw, bundle.candidate, bundle.metadata, bundle.selected = choices[0]
    bundle.initial = initial
    bundle.folder = bundle.folder / DIRECTORY
    return bundle


def execution(bundle):
    return {"policy": POLICY, "job": bundle.job.key, "section": bundle.prefix,
        "original_resume_execution_sha256": digest(bundle.contract), "selected_failed_repair": bundle.selected,
        "selected_repair_sha256": digest(bundle.raw), "candidate_sha256": digest(bundle.candidate),
        "metadata": bundle.metadata, "initial_review_sha256": digest(bundle.initial),
        "files": {str(p.relative_to(bundle.root)): whole.sha(p.read_bytes()) for p in sorted(bundle.paths)},
        "code": {"module": whole.sha(Path(__file__).read_bytes()), "mapped": whole.sha(Path(mapped.__file__).read_bytes())},
        "only_original_unused_final_review": True, "original_budgets_reset": False,
        "new_write_or_semantic_repair_allowed": False, "reader_publication_allowed": False}


def prepare(root, key, tokenizer):
    bundle = inputs(root, key, tokenizer)
    with schema_lease(bundle.root / "wiki/textbook-whole-record-evidence" / bundle.contract["inventory"] / "authoring-worker.lock", timeout=0):
        status = whole.read(bundle.job.root / "progress.json")
        old_guard = whole.read(bundle.folder.parent / "guard-progress.json")
        require(not writing.identity_live(status) and not writing.identity_live(old_guard)
            and old_guard["state"] == "worker_exited" and old_guard.get("returncode") == 1, "original_failed_worker_and_guard_exited")
        require(not list((bundle.job.root / "attempts").glob(bundle.prefix + "-repair-review-[0-9][0-9].json"))
            and not list(bundle.job.root.glob(bundle.prefix + "-repair-review*.json")), "original_final_review_unused")
        require(not (bundle.folder / "execution.json").exists(), "prepare_once_no_budget_refresh")
        fresh.retain(bundle.folder / "execution.json", execution(bundle))
        fresh.retain(bundle.folder / "proposal.json", {"candidate": bundle.candidate, "metadata": bundle.metadata})
        return {"state": "original_final_review_prepared_for_omitted_point_proposal", "job": key,
            "omitted_points": len(bundle.metadata["omitted_point_ids"]), "paragraphs": len(bundle.candidate["paragraphs"]),
            "model_calls": 0, "original_budgets_reset": False, "reader_publication_allowed": False}


def load(root, key, tokenizer):
    bundle = inputs(root, key, tokenizer)
    require(whole.read(bundle.folder / "execution.json") == execution(bundle), "unchanged_failed_repair_and_proposal")
    require(whole.read(bundle.folder / "proposal.json") == {"candidate": bundle.candidate, "metadata": bundle.metadata}, "unchanged_private_proposal")
    return bundle


def final_review(bundle, tokenizer, *, read_only):
    job = mapped.ORIGINAL_FACTORY(bundle.original, read_only=read_only)(
        writing.TextbookStore(bundle.root), bundle.inventory, bundle.working, tokenizer)
    job.finished_sections, job.section_count = bundle.count, len(bundle.units)
    job.proposals[bundle.prefix + "-repair"] = {"candidate": bundle.candidate, "metadata": bundle.metadata,
        "actual_failed_repair_sha256": digest(bundle.raw), "semantic_acceptance": False}
    schema = writing.review_schema(bundle.candidate, list(bundle.context["assigned_points"]))
    context = {**common(bundle), "candidate": bundle.candidate, "unverified_omitted_point_search_scope": bundle.metadata,
        "full_body_indices_are_search_scope_not_proof": True}
    audit = job.stage(bundle.prefix + "-repair-review", writing.author.REVIEW_PROMPT, schema, context,
        lambda row: writing.author.validate_review(row, schema), evidence_packet=bundle.scope["original"])
    job.reader.paths.update(bundle.paths | {bundle.folder / "execution.json", bundle.folder / "proposal.json"})
    for path in bundle.job.root.glob("response-*.json"):
        if whole.read(path)["stage"] == bundle.prefix + "-repair-review":
            job.reader.paths.add(path)
    result = {"policy": POLICY, "candidate": bundle.candidate, "metadata": bundle.metadata, "final_review": audit,
        "final_checks_passed": all(writing.author.verdicts(audit)), "whole_original_coverage_required": True,
        "files": {str(p.relative_to(bundle.root)): whole.sha(p.read_bytes()) for p in sorted(job.reader.paths)},
        "reader_publication_allowed": False}
    fresh.retain(bundle.folder / "final-review-result.json", result, read_only=read_only)
    return result


def resumed_type(bundle, result, *, read_only):
    base = mapped.ORIGINAL_FACTORY(whole.read(bundle.job.root / fresh.CONTRACT), read_only=read_only)

    class ResumedJob(base):
        def __init__(self, *args):
            super().__init__(*args)
            require(self.key == bundle.job.key, "unchanged_writer_identity")
            self.reader.paths.update(bundle.root / p for p in result["files"])
            self.reader.paths.add(bundle.folder / "final-review-result.json")

        def stage(self, name, prompt, schema, context, validate, **kwargs):
            match = re.fullmatch(fresh.PATTERN, name)
            if match and match[1] == bundle.prefix:
                role = match[2]
                expected = dict(bundle.context)
                if role != "write":
                    expected["candidate"] = bundle.candidate
                if role == "repair":
                    expected["review"] = result["final_review"]
                require(context == expected and kwargs == {"evidence_packet": bundle.scope["original"]}, "exact_original_orchestration_context")
                ids = list(bundle.context["assigned_points"])
                expected_prompt = writing.author.WRITE_PROMPT if role == "write" else (
                    writing.author.REPAIR_PROMPT if role == "repair" else writing.author.REVIEW_PROMPT)
                expected_schema = writing.section_schema(ids, bundle.working) if role in {"write", "repair"} else writing.review_schema(bundle.candidate, ids)
                require(prompt == expected_prompt and schema == expected_schema, "exact_original_orchestration_role")
                value = bundle.candidate if role in {"write", "repair"} else result["final_review"]
                with mapped.visible.packet_scope(bundle.scope):
                    validate(value)
                path = bundle.folder / "derived" / (name + ".json")
                fresh.retain(path, {"policy": POLICY, "orchestration_stage": name,
                    "actual_failed_repair_attempt": bundle.selected, "actual_final_review_stage": bundle.prefix + "-repair-review",
                    "proposal_sha256": digest(bundle.candidate), "final_review_result_sha256": digest(result),
                    "model_write_cache_created": False, "new_model_calls": 0, "reader_publication_allowed": False}, read_only=read_only)
                self.reader.paths.add(path)
                return value
            if match and match[1] in bundle.contract["future_units"] and match[2] in {"write", "repair"}:
                with future_format():
                    return super().stage(name, prompt, schema, context, validate, **kwargs)
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
def retained_obligation(bundle):
    original = writing.final_issues

    def issues(inventory, private, existing):
        require(digest(inventory) == bundle.contract["inventory"], "same_final_inventory")
        return [*original(inventory, private, existing), {"kind": "omitted_point_mapping_proposal_requires_whole_original_coverage",
            "section": bundle.prefix, "point_ids": bundle.metadata["omitted_point_ids"], "candidate_sha256": digest(bundle.candidate)}]

    writing.final_issues = issues
    try:
        yield
    finally:
        writing.final_issues = original


def worker(root, key, tokenizer):
    bundle = load(root, key, tokenizer)
    guarder.checkpoint(bundle.root)
    require(not (bundle.folder / "started.json").exists(), "worker_once_no_retry")
    fresh.retain(bundle.folder / "started.json", bundle.job.identity)
    try:
        with schema_lease(bundle.root / "wiki/textbook-whole-record-evidence" / bundle.contract["inventory"] / "authoring-worker.lock", timeout=0):
            result = final_review(bundle, tokenizer, read_only=False)
        with retained_obligation(bundle), fresh.installed(resumed_type(bundle, result, read_only=False)):
            output = writing.run(bundle.root, bundle.contract["inventory"], bundle.contract["ledger_job"], tokenizer)
        fresh.retain(bundle.folder / "result.json", output)
        return output
    except BaseException as exc:
        atomic_json(bundle.folder / ("failure-" + str(time.time_ns()) + ".json"), whole.failure_status(exc))
        raise


def reconstruct(root, key, tokenizer, *, complete=True):
    bundle = load(root, key, tokenizer)
    result = final_review(bundle, tokenizer, read_only=True)
    original = fresh.job_type
    fresh.job_type = lambda contract, *, read_only: resumed_type(bundle, result, read_only=read_only)
    try:
        with retained_obligation(bundle):
            rebuilt = fresh.reconstruct(root, bundle.contract["inventory"], bundle.contract["ledger_job"], tokenizer, complete=complete)
    finally:
        fresh.job_type = original
    rebuilt["proof"].update(policy=POLICY, omitted_mapping_execution_sha256=digest(execution(bundle)),
        omitted_mapping_adapter_sha256=whole.sha(Path(__file__).read_bytes()))
    return rebuilt


def guard(root, key, tokenizer):
    bundle = load(root, key, tokenizer)
    result = guarder.run(bundle.root, bundle.folder, [sys.executable, "-m", __spec__.name,
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
    parser.add_argument("command", choices=["prepare", "load", "guard", "worker", "verify", "prefix"])
    args = parser.parse_args()
    try:
        if args.command == "load":
            bundle = load(args.root, args.job, args.tokenizer)
            output = {"state": "omitted_mapping_original_inputs_authenticated", "omitted_points": len(bundle.metadata["omitted_point_ids"]), "model_calls": 0}
        elif args.command in {"verify", "prefix"}:
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
