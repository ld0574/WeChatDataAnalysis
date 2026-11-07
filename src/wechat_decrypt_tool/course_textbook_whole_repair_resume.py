"""Audit explicit citation proposals on an actual complete failed repair.

This uses only the original, unused repair-review role. Neither failed repair
attempt is replaced by a successful model cache. Prose, coverage, disposition,
all initial judgments and original model budgets remain unchanged.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import jsonschema

from . import course_textbook_whole_unit_repair as unit
from .course_knowledge import atomic_json, schema_lease

visible, writing, draft, snapshot = unit.visible, unit.writing, unit.draft, unit.snapshot
POLICY = "whole-original-failed-repair-citation-proposal-final-review-v1"
ERROR = "requires_private_composition_primary_original_support_in_body"
SUFFIX = "-whole-repair-proposal-execution.json"
INPUT = "-whole-repair-proposal-input.json"
RESULT = "-whole-repair-proposal-result.json"


def require(ok, code):
    if not ok:
        raise ValueError("requires_whole_repair_resume_" + code)


def digest(value):
    return visible.digest(value)


def propose(candidate, ids, working, inventory, scope):
    require(candidate.get("kind") != "non_teaching", "complete_actual_teaching_repair")
    with visible.packet_scope(scope):
        proposed, changes = draft.reviewable.propose_citations(
            {key: value for key, value in candidate.items() if key != "kind"}, ids, working, inventory)
        if "kind" in candidate:
            proposed["kind"] = candidate["kind"]
        visible.ORIGINAL_VALIDATE(proposed, ids, working, inventory)
    require(bool(changes), "actual_primary_citation_metadata_defect")
    def without_citations(value):
        return {**value, "paragraphs": [{key: row for key, row in paragraph.items() if key != "citations"}
            for paragraph in value["paragraphs"]]}

    require(without_citations(proposed) == without_citations(candidate), "only_paragraph_citation_proposals")
    return proposed, changes


def failed_repair(job, schema, context, packet, working, inventory, scope, prefix):
    name = prefix + "-repair"
    request = draft.capture_request(job, name, writing.author.REPAIR_PROMPT, schema, context, packet)
    paths = sorted((job.root / "attempts").glob(name + "-[0-9][0-9].json"))
    require([p.name for p in paths] == [name + "-01.json", name + "-02.json"], "exact_exhausted_original_repair_budget")
    previous, candidates, hashes = "", [], {}
    for number, path in enumerate(paths, 1):
        row = writing.whole.read(path)
        require(row["stage"] == name and row["attempt"] == number and row["input_sha256"] == request["binding"]
            and row["model_digest"] == job.models["qwen3.5:9b"] and row.get("error_type") == "ValueError"
            and not row.get("accepted_structure") and row.get("validation_error") == ERROR, "actual_failed_repair_identity_and_defect")
        messages = [{"role": "system", "content": request["prompt"]}, {"role": "user", "content": json.dumps(
            {**context, "previous_validation_failure": previous}, ensure_ascii=False)}]
        require(row["request_sha256"] == digest(messages), "actual_failed_repair_request")
        writing.whole.editor.e.verify_response(job.root, name, row)
        require(row["model_metrics"]["model_digest"] == job.models["qwen3.5:9b"], "actual_failed_repair_model")
        candidate = json.loads(row["raw_response"])
        jsonschema.validate(candidate, request["schema"])
        proposed, changes = propose(candidate, list(context["assigned_points"]), working, inventory, scope)
        candidates.append((number, candidate, proposed, changes))
        hashes[path.name] = writing.whole.sha(path.read_bytes())
        previous = row["validation_error"]
    require(not (job.root / (name + ".json")).exists() and not (job.root / (name + ".proof.json")).exists(), "no_forged_repair_model_cache")
    number, candidate, proposed, changes = candidates[0]
    history = {"failed_repair_attempts": hashes, "selected_repair_attempt": number,
        "selection": "earliest_complete_original_repair_with_only_explicit_primary_citation_proposals",
        "actual_repair_input_sha256": request["binding"], "actual_repair_candidate_sha256": digest(candidate),
        "proposed_candidate_sha256": digest(proposed), "citation_proposals": changes,
        "prose_coverage_disposition_and_other_fields_unchanged": True, "citation_proposals_are_factual_evidence": False}
    return candidate, proposed, history


def inputs(root, key, prefix, tokenizer):
    folder = Path(root).resolve() / "wiki/textbook-whole-authoring-jobs" / key
    contract = writing.whole.read(folder / (prefix + unit.SUFFIX))
    args = unit.inputs(root, key, prefix, tokenizer, contract["snapshot_path"])
    job, working, inventory, context, scope, original, defects, history, proof = args
    require(contract == {**unit.execution(job, context, scope, original, defects, history, proof), "section": prefix,
        "snapshot_path": contract["snapshot_path"]}, "original_unit_execution_and_dependencies")
    common = {**context, "structural_defects": defects, "candidate_requires_original_role_repair": True}
    reader = snapshot.ReadOnlyStages(job, inventory, working, visible.Recovery([]))
    schema = writing.review_schema(original, list(context["assigned_points"]))
    initial = reader.stage(prefix + "-review", writing.author.REVIEW_PROMPT, schema, {**common, "candidate": original},
        lambda row: writing.author.validate_review(row, schema), evidence_packet=scope["expanded"])
    repair_context = {**common, "candidate": original, "review": initial}
    repair_schema = unit.grammar.bind(writing.section_schema(list(context["assigned_points"]), working), scope["expanded"])
    raw, candidate, repair_history = failed_repair(job, repair_schema, repair_context, scope["expanded"], working, inventory, scope, prefix)
    final_context = {**common, "candidate": candidate, "original_initial_review": copy.deepcopy(initial),
        "citation_proposals": repair_history["citation_proposals"], "citation_proposals_are_factual_evidence": False,
        "failed_repair_response_is_untrusted_until_independently_reviewed": True}
    final_schema = writing.review_schema(candidate, list(context["assigned_points"]))
    return SimpleNamespace(job=job, working=working, inventory=inventory, context=context, scope=scope,
        raw_candidate=raw, candidate=candidate, initial=initial, repair_history=repair_history, proof=proof,
        final_context=final_context, final_schema=final_schema, reader=reader, original_execution=contract,
        original_history=history, key=key, prefix=prefix)


def execution(bundle):
    return {"policy": POLICY, "job": bundle.key, "section": bundle.prefix, **bundle.repair_history,
        "original_unit_execution_sha256": digest(bundle.original_execution), "prefix_snapshot_sha256": digest(bundle.proof),
        "complete_actual_context_sha256": digest(bundle.context), "actual_visible_packet_sha256": digest(bundle.scope["expanded"]),
        "original_initial_review_sha256": digest(bundle.initial), "actual_final_context_sha256": digest(bundle.final_context),
        "only_unused_original_model_role": bundle.prefix + "-repair-review", "original_stage_attempt_budget": 2,
        "semantic_repairs": 1, "additional_model_roles": 0, "original_budgets_reset": False, "reader_publication_allowed": False,
        "module_sha256": writing.whole.sha(Path(__file__).read_bytes()),
        "helpers_sha256": {m.__name__: writing.whole.sha(Path(m.__file__).read_bytes())
            for m in (unit, unit.grammar, snapshot, visible, draft, draft.reviewable)}}


def prepare(root, key, prefix, tokenizer):
    root = Path(root).resolve()
    folder = root / "wiki/textbook-whole-authoring-jobs" / key
    status = writing.whole.read(folder / "progress.json")
    with schema_lease(root / "wiki/textbook-whole-record-evidence" / status["inventory"] / "authoring-worker.lock", timeout=0):
        require(not writing.identity_live(status), "original_worker_exited")
        bundle = inputs(root, key, prefix, tokenizer)
        contract = execution(bundle)
        target = folder / (prefix + SUFFIX)
        proposal = {"policy": POLICY, "execution_sha256": digest(contract), "candidate": bundle.candidate,
            "candidate_sha256": digest(bundle.candidate), "semantic_acceptance": False, "reader_publication_allowed": False}
        if target.exists():
            require(writing.whole.read(target) == contract
                and writing.whole.read(folder / (prefix + INPUT)) == proposal, "immutable_prepared_proposal")
        else:
            require(status["state"] == "stopped" and status["stage"] == prefix + "-repair"
                and status.get("reason_code") == "wiki_stage_attempt_budget_exhausted_" + prefix + "-repair", "exact_original_failed_repair_terminal_state")
            require(not any((folder / "attempts").glob(prefix + "-repair-review-*.json")), "original_final_review_unused")
            require(not (folder / (prefix + "-repair-review.json")).exists(), "no_original_final_review_cache")
            atomic_json(target, contract)
            atomic_json(folder / (prefix + INPUT), proposal)
        backup(bundle, "inputs")
        return {"state": "actual_failed_repair_proposals_ready_for_original_final_review", "job": key, "section": prefix,
            "selected_repair_attempt": bundle.repair_history["selected_repair_attempt"],
            "citation_proposals": len(bundle.repair_history["citation_proposals"]), "model_calls": 0,
            "prose_and_coverage_unchanged": True, "original_budgets_reset": False, "reader_publication_allowed": False}


def checked_prepared(bundle):
    contract = execution(bundle)
    require(writing.whole.read(bundle.job.root / (bundle.prefix + SUFFIX)) == contract, "original_prepared_proposal_execution")
    require(writing.whole.read(bundle.job.root / (bundle.prefix + INPUT)) == {"policy": POLICY,
        "execution_sha256": digest(contract), "candidate": bundle.candidate, "candidate_sha256": digest(bundle.candidate),
        "semantic_acceptance": False, "reader_publication_allowed": False}, "actual_unaccepted_proposal")
    return contract


def result(bundle, final, contract):
    return {"policy": POLICY, "job": bundle.key, "section": bundle.prefix, "execution_sha256": digest(contract),
        "candidate": bundle.candidate, "candidate_sha256": digest(bundle.candidate),
        "actual_repair_candidate_sha256": digest(bundle.raw_candidate), "citation_proposals": bundle.repair_history["citation_proposals"],
        "original_initial_review": bundle.initial, "actual_final_review": final,
        "factual_checks_passed": all(writing.author.verdicts(final)), "semantic_repairs": 1,
        "all_attempt_files": {p.name: writing.whole.sha(p.read_bytes())
            for p in sorted((bundle.job.root / "attempts").glob(bundle.prefix + "-*.json"))},
        "repair_model_cache_fabricated": False, "whole_writer_resume_required": True, "reader_publication_allowed": False}


def backup(bundle, phase):
    dest = Path.home() / "Documents/CourseKnowledgeBackups/whole-failed-repair-proposals" / bundle.key / bundle.prefix / phase
    paths = [Path(__file__), *bundle.reader.paths]
    paths += [p for p in bundle.job.root.glob(bundle.prefix + "-whole-repair-proposal-*.json")]
    paths += list((bundle.job.root / "attempts").glob(bundle.prefix + "-*.json"))
    paths += [p for p in bundle.job.root.glob("response-*.json") if writing.whole.read(p)["stage"].startswith(bundle.prefix + "-")]
    for path in paths:
        relative = path.relative_to(bundle.job.root) if path.is_relative_to(bundle.job.root) else Path("code") / path.name
        writing.whole.backup(path, dest / relative)


def authenticate(root, key, prefix, tokenizer):
    bundle = inputs(root, key, prefix, tokenizer)
    contract = checked_prepared(bundle)
    final = bundle.reader.stage(prefix + "-repair-review", writing.author.REVIEW_PROMPT, bundle.final_schema, bundle.final_context,
        lambda row: writing.author.validate_review(row, bundle.final_schema), evidence_packet=bundle.scope["expanded"])
    require(writing.whole.read(bundle.job.root / (prefix + RESULT)) == result(bundle, final, contract), "actual_final_review_result")
    return bundle, final, result(bundle, final, contract)


def worker(root, key, prefix, tokenizer):
    root = Path(root).resolve()
    folder = root / "wiki/textbook-whole-authoring-jobs" / key
    status = writing.whole.read(folder / "progress.json")
    with schema_lease(root / "wiki/textbook-whole-record-evidence" / status["inventory"] / "authoring-worker.lock", timeout=0):
        bundle = inputs(root, key, prefix, tokenizer)
        job, contract = bundle.job, checked_prepared(bundle)
        try:
            final = job.stage(prefix + "-repair-review", writing.author.REVIEW_PROMPT, bundle.final_schema, bundle.final_context,
                lambda row: writing.author.validate_review(row, bundle.final_schema), evidence_packet=bundle.scope["expanded"])
            value = result(bundle, final, contract)
            target = folder / (prefix + RESULT)
            require(not target.exists() or writing.whole.read(target) == value, "immutable_completed_proposal_result")
            if not target.exists():
                atomic_json(target, value)
            authenticate(root, key, prefix, tokenizer)
            job.progress("original_repair_response_reviewed_requires_whole_writer_resume", prefix + "-repair-review",
                original_unit_repair_factual_checks_passed=value["factual_checks_passed"])
            backup(bundle, "results")
            return {"state": "original_repair_response_reviewed_requires_whole_writer_resume", "job": key, "section": prefix,
                "factual_checks_passed": value["factual_checks_passed"], "reader_publication_allowed": False}
        except Exception as exc:
            failure = writing.whole.failure_status(exc)
            dest = folder / "whole-repair-proposal-failures"
            dest.mkdir(exist_ok=True)
            atomic_json(dest / (str(time.time_ns()) + ".json"), failure)
            job.progress(failure["state"], job.current, **{k: v for k, v in failure.items() if k != "state"})
            raise


def guard(root, key, prefix, tokenizer):
    from .course_archive_gpu import checked_sample
    from .course_gpu_guard import run as guarded

    root = Path(root).resolve()
    writing.TextbookStore(root).checkpoint()
    with schema_lease(root / "wiki/whole-source-authoring-dispatch.lock", timeout=0):
        previous = root / "wiki-revision-thermal-guard.json"
        old = writing.whole.read(previous)
        require(not writing.identity_live(old), "prior_guard_exited")
        writing.whole.backup(previous, root / "wiki/whole-repair-proposal-guard-history" / (writing.whole.sha(previous.read_bytes()) + ".json"))
        guarded(root, [sys.executable, "-m", __spec__.name, "--root", str(root), "--job", key, "--section", prefix,
            "--tokenizer", str(Path(tokenizer).resolve()), "worker"], maximum_temperature=70, maximum_power=200,
            guard_name="wiki-revision-thermal-guard", sampler=lambda: checked_sample(root))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--job", required=True)
    p.add_argument("--section", required=True)
    p.add_argument("--tokenizer", type=Path, required=True)
    p.add_argument("command", choices=["prepare", "worker", "guard", "authenticate"])
    a = p.parse_args()
    try:
        if a.command == "authenticate":
            _, _, value = authenticate(a.root, a.job, a.section, a.tokenizer)
            output = {"state": "actual_failed_repair_and_final_review_authenticated", "job": a.job,
                "factual_checks_passed": value["factual_checks_passed"], "model_calls": 0, "reader_publication_allowed": False}
        else:
            output = {"prepare": prepare, "worker": worker, "guard": guard}[a.command](a.root, a.job, a.section, a.tokenizer)
        print(json.dumps(output or {"state": "guard_returned"}))
    except Exception as exc:
        print(json.dumps(writing.whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
