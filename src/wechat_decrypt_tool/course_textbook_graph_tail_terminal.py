"""Authenticate an exhausted original graph repair without accepting its draft.

This reads the actual prefix, original accepted initial review and both failed
repair requests and responses. It never infers, invents a cache or spends the
unused final review, later units, or a new semantic repair budget.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

import jsonschema

from . import course_textbook_graph_tail_roles as tail

prior, fresh, writing, whole, digest = tail.prior, tail.fresh, tail.writing, tail.whole, tail.digest
POLICY = "authenticate-terminal-original-tail-graph-repair-without-acceptance-v1"


def require(ok, code):
    if not ok:
        raise ValueError("requires_graph_tail_terminal_" + code)


def bindings():
    return {"module_sha256": whole.sha(Path(__file__).read_bytes()), "tail": tail.bindings()}


def repair_failures(bundle, job, initial):
    name, ids = bundle.prefix + "-repair", list(bundle.context["assigned_points"])
    common = {**bundle.context, "candidate_is_untrusted": True, "original_repair_is_mandatory": True,
        "observed_structural_defects": bundle.defects, "adapter_graph_changes": 0}
    context = {**common, "candidate": bundle.candidate, "review": initial}
    schema = prior.indexed.tagged_schema(writing.section_schema(ids, bundle.working),
        bundle.scope["expanded"], bundle.working["pending_source_cards"])
    with prior.indexed.future_format():
        request = fresh.continuation.draft.capture_request(job, name, writing.author.REPAIR_PROMPT,
            schema, context, bundle.scope["expanded"])
    prospective = job.root / (name + "-prospective-request.json")
    require(whole.read(prospective) == {"policy": fresh.POLICY,
        "execution_sha256": digest(bundle.base.contract), "stage": name,
        "original_schema_sha256": digest(writing.section_schema(ids, bundle.working)),
        "actual_schema_sha256": digest(schema), "original_context_sha256": digest(context),
        "actual_context_sha256": digest(context), "original_packet_sha256": digest(bundle.scope["original"]),
        "actual_packet_sha256": digest(bundle.scope["expanded"]), "original_budgets_reset": False,
        "reader_publication_allowed": False}, "exact_original_repair_request")
    paths = sorted((job.root / "attempts").glob(name + "-[0-9][0-9].json"))
    require([p.name for p in paths] == [name + "-01.json", name + "-02.json"]
        and not (job.root / (name + ".json")).exists(), "two_exhausted_repairs_without_accepted_cache")
    receipts = [(p, whole.read(p)) for p in job.root.glob("response-*.json")]
    history, previous = [], ""
    bundle.paths.add(prospective)
    for number, path in enumerate(paths, 1):
        row = whole.read(path)
        messages = [{"role": "system", "content": request["prompt"]}, {"role": "user", "content": json.dumps(
            {**context, "previous_validation_failure": previous}, ensure_ascii=False)}]
        require(row["stage"] == name and row["attempt"] == number and not row.get("accepted_structure")
            and row["input_sha256"] == request["binding"] and row["request_sha256"] == digest(messages)
            and row["model_digest"] == job.models["qwen3.5:9b"]
            and row.get("error_type") == "ValueError" and row["validation_error"] == "ValueError",
            "actual_original_failed_repair_identity")
        whole.editor.e.verify_response(job.root, name, row)
        matches = [p for p, receipt in receipts if receipt["stage"] == name
            and receipt["request_sha256"] == row["request_sha256"] and receipt.get("schema_sha256") == digest(schema)
            and receipt["response"]["message"]["content"] == row["raw_response"]
            and receipt["metrics"] == row["model_metrics"]]
        require(len(matches) == 1 and row["model_metrics"]["done_reason"] == "stop", "actual_complete_repair_wire_response")
        raw = json.loads(row["raw_response"])
        jsonschema.validate(raw, schema)
        candidate = copy.deepcopy(raw)
        require(candidate.pop("paragraph_count") == len(candidate["paragraphs"]), "actual_original_repair_paragraph_count")
        defects = prior.graph_defects(candidate)
        try:
            prior.indexed.tagged_candidate(raw, ids, bundle.working, bundle.inventory, bundle.scope)
        except ValueError as exc:
            require(str(exc) == prior.GRAPH_ERROR, "same_actual_terminal_graph_failure")
        else:
            require(False, "failed_repair_still_rejected")
        history.append({"attempt": path.name, "raw_sha256": whole.sha(row["raw_response"].encode()),
            "candidate_sha256": digest(candidate), "observed_graph_defects": defects,
            "empty_mappings": sum(not v["paragraphs"] for v in candidate["coverage"].values())})
        bundle.paths.update([path, *matches])
        previous = row["validation_error"]
    return history


def inspect(root, key):
    base = tail.load(root, key)
    protected_path = base.folder / "guard/guard-progress.json"
    status_path = base.job.root / "progress.json"
    protected, status = whole.read(protected_path), whole.read(status_path)
    require(protected["state"] == "worker_exited" and protected["returncode"] == 1
        and status["state"] == "stopped" and not writing.identity_live(protected) and not writing.identity_live(status)
        and status["reason_code"] == "wiki_stage_attempt_budget_exhausted_" + base.prefix + "-repair"
        and status["completed_sections"] == base.count and status["total_sections"] == len(base.units),
        "actual_terminal_original_repair_and_guard_exited")
    base.job.reader.paths.add(base.folder / "execution.json")
    bundle = tail.unit_bundle(base, base.job, base.count, base.context, {"evidence_packet": base.scope["original"]})
    contract = tail.unit_execution(base, bundle)
    require(whole.read(bundle.folder / "execution.json") == contract, "same_actual_original_unit_registration")
    bundle.paths.add(bundle.folder / "execution.json")
    cls = tail.parent_type(base, read_only=True)
    job = cls(writing.TextbookStore(base.root), base.inventory, base.working, base.tokenizer)
    job.finished_sections, job.section_count = bundle.count, len(base.units)
    stage = next(c.stage for c in cls.__mro__ if c.__name__ == "FreshJob")
    common = {**bundle.context, "candidate_is_untrusted": True, "original_repair_is_mandatory": True,
        "observed_structural_defects": bundle.defects, "adapter_graph_changes": 0}
    schema = writing.review_schema(bundle.candidate, list(bundle.context["assigned_points"]))
    initial = stage(job, bundle.prefix + "-review", writing.author.REVIEW_PROMPT, schema,
        {**common, "candidate": bundle.candidate}, lambda value: writing.author.validate_review(value, schema),
        evidence_packet=bundle.scope["original"])
    bundle.paths.update(job.reader.paths)
    history = repair_failures(bundle, job, initial)
    remaining = [bundle.prefix + "-repair-review"]
    remaining += [unit["id"] + "-" + role for unit in base.units[base.count + 1:] for role in fresh.continuation.ROLES]
    require(all(not list((job.root / "attempts").glob(name + "-[0-9][0-9].json"))
        and not list(job.root.glob(name + "*.json")) for name in remaining)
        and not list(job.root.glob("reading-*.json")), "remaining_final_review_and_later_original_roles_unused")
    bundle.paths.update([protected_path, status_path, base.folder / "started.json"])
    bundle.paths.update(base.folder.glob("failure-*.json"))
    bundle.paths.update(job.root.glob("failures/*.json"))
    verdicts = list(writing.author.verdicts(initial))
    return {"policy": POLICY, "state": "original_graph_tail_terminal_authenticated_not_a_complete_manuscript",
        "writer": key, "code": bindings(), "original_tail_execution_sha256": digest(base.contract),
        "verified_prefix_units": base.count, "total_units": len(base.units), "unresolved_prefix_issues": len(base.issues),
        "original_accepted_initial_review_checks": len(verdicts), "initial_review_passed_checks": sum(verdicts),
        "original_failed_repairs": history, "original_final_review_and_later_roles_unused": True,
        "files": {p.relative_to(base.root).as_posix(): whole.sha(p.read_bytes()) for p in sorted(bundle.paths)},
        "model_calls": 0, "original_budgets_reset": False, "semantic_acceptance": False,
        "complete_manuscript": False, "reader_publication_allowed": False, "source_deletion_allowed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--writer", required=True)
    args = parser.parse_args()
    try:
        result = inspect(args.root, args.writer)
        print(json.dumps({k: v for k, v in result.items() if k not in {"files", "code", "original_failed_repairs"}}))
    except BaseException as exc:
        print(json.dumps(whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
