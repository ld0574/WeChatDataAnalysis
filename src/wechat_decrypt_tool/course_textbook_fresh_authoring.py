"""Opt in before a fresh whole-record writer spends any model attempt.

Branch-scoped citation grammars prevent avoidable structural retries. Original
responses remain immutable; deterministic plan and citation metadata proposals
are separate artifacts, and factual review still uses the original role budget.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, nullcontext
import copy
import json
from pathlib import Path
import re
import sys

import jsonschema

from . import course_textbook_whole_continuation as continuation
from .course_knowledge import atomic_json, schema_lease

writing, visible, snapshot = continuation.writing, continuation.visible, continuation.snapshot
grammar = continuation.grammar
BASE_JOB = visible.ORIGINAL_JOB
POLICY = "prospective-complete-record-authoring-scoped-grammar-v1"
CONTRACT = "prospective-authoring-execution.json"
PATTERN = continuation.PATTERN


def require(ok, code):
    if not ok:
        raise ValueError("requires_fresh_authoring_" + code)


def digest(value):
    return visible.digest(value)


def retain(path, value, *, read_only=False):
    if path.exists():
        require(writing.whole.read(path) == value, "immutable_existing_artifact")
    else:
        require(not read_only, "existing_artifact_without_inference")
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(path, value)


def plan_candidate(value, cards, *, final):
    """Remove unused groups and stably resolve ties, preserving memberships."""
    jsonschema.validate(value, writing.author.grouping_schema(cards, final=final))
    require(set(value["assignments"]) == set(cards), "every_planning_input")
    result = copy.deepcopy(value)
    used = sorted({row["group"] for row in value["assignments"].values()})
    require(bool(used), "nonempty_conceptual_plan")
    result["groups"] = [copy.deepcopy(value["groups"][i]) for i in used]
    input_order = {key: i for i, key in enumerate(cards)}
    for new, old in enumerate(used):
        children = [key for key, row in value["assignments"].items() if row["group"] == old]
        ranks = [value["assignments"][key]["order"] for key in children]
        children.sort(key=lambda key: (value["assignments"][key]["order"], input_order[key]))
        for rank, key in enumerate(children):
            result["assignments"][key]["group"] = new
            if len(ranks) != len(set(ranks)):
                result["assignments"][key]["order"] = rank
    writing.author.grouped(result, cards, final=final)
    require(result["title"] == value["title"] and all(
        result["groups"][row["group"]] == value["groups"][value["assignments"][key]["group"]]
        for key, row in result["assignments"].items()), "original_semantic_group_memberships")
    return result


def section_schema(schema, packet, pending):
    result = grammar.bind(schema, packet)

    def visit(node):
        if not isinstance(node, dict):
            return
        for name, child in node.get("properties", {}).items():
            if name in {"citations", "code_citations"}:
                child["uniqueItems"] = True
            if name == "coverage":
                for key, row in child.get("properties", {}).items():
                    if key in pending and row.get("properties", {}).get("teaching") == {"type": "boolean"}:
                        positive, negative = copy.deepcopy(row), copy.deepcopy(row)
                        positive["properties"]["teaching"] = {"const": True}
                        positive["properties"]["paragraphs"]["minItems"] = 1
                        negative["properties"]["teaching"] = {"const": False}
                        negative["properties"]["paragraphs"]["maxItems"] = 0
                        child["properties"][key] = {"oneOf": [positive, negative]}
        for key in grammar.MAPPINGS:
            for child in node.get(key, {}).values():
                visit(child)
        for key in grammar.SINGLES:
            child = node.get(key)
            for value in child if isinstance(child, list) else [child]:
                visit(value)
        for key in grammar.BRANCHES:
            for child in node.get(key, []):
                visit(child)

    visit(result)
    return result


def citation_candidate(raw, ids, working, inventory, scope):
    with visible.packet_scope(scope):
        try:
            visible.ORIGINAL_VALIDATE(raw, ids, working, inventory)
        except ValueError as exc:
            require(str(exc) == continuation.proposal.ERROR, "only_primary_citation_metadata_proposed")
            candidate, changes = continuation.proposal.propose(raw, ids, working, inventory, scope)
            return candidate, changes
    return raw, {}


class StrictReader(continuation.StrictReader):
    def authenticate_cache(self, name, prompt, schema, context, packet):
        value = super().authenticate_cache(name, prompt, schema, context, packet)
        request = continuation.draft.capture_request(self.job, name, prompt, schema, context, packet)
        receipts = [writing.whole.read(path) for path in self.job.root.glob("response-*.json")]
        for path in (self.job.root / "attempts").glob(name + "-[0-9][0-9].json"):
            row = writing.whole.read(path)
            if "raw_response" in row:
                require(any(receipt["stage"] == name and receipt["request_sha256"] == row["request_sha256"]
                    and receipt.get("schema_sha256") == digest(request["schema"])
                    and receipt["response"]["message"]["content"] == row["raw_response"]
                    and receipt["metrics"] == row["model_metrics"] for receipt in receipts),
                    "actual_decoding_schema_and_gpu_receipt")
        return value


def execution(job, inventory, working, tokenizer):
    helpers = (continuation, grammar, visible, snapshot, continuation.proposal,
               continuation.draft, continuation.draft.reviewable)
    return {"policy": POLICY, "job": job.key, "inventory": digest(inventory), "ledger_job": working["job"],
        "working_sha256": digest(working), "module_sha256": writing.whole.sha(Path(__file__).read_bytes()),
        "helpers_sha256": {m.__name__: writing.whole.sha(Path(m.__file__).read_bytes()) for m in helpers},
        "writer_sha256": writing.bindings(), "tokenizer_sha256": writing.whole.sha(Path(tokenizer).read_bytes()),
        "models": job.models, "original_stage_attempt_budget": 2, "semantic_repairs_per_unit": 1,
        "roles_unused_at_preparation": True, "old_budgets_reset": False, "additional_model_roles": 0,
        "citation_scope": "only_complete_originals_actually_supplied",
        "plan_transform": "unused_groups_and_stable_ties_only_semantic_memberships_unchanged",
        "prose_and_teaching_dispositions_changed_by_adapter": False,
        "citation_proposals_are_factual_evidence": False, "reader_publication_allowed": False}


def inputs(root, inventory_key, ledger_job, tokenizer):
    root = Path(root).resolve()
    inventory, working = writing.load_inputs(root, inventory_key, ledger_job, tokenizer)
    job = BASE_JOB(writing.TextbookStore(root), inventory, working, tokenizer)
    return root, inventory, working, job


def prepare(root, inventory_key, ledger_job, tokenizer):
    root, inventory, working, job = inputs(root, inventory_key, ledger_job, tokenizer)
    with schema_lease(root / "wiki/textbook-whole-record-evidence" / inventory_key / "authoring-worker.lock", timeout=0):
        contract = execution(job, inventory, working, tokenizer)
        path = job.root / CONTRACT
        if not path.exists():
            require(not (job.root / "progress.json").exists()
                and not list((job.root / "attempts").glob("*.json"))
                and all(p.name == "manifest.json" for p in job.root.glob("*.json")),
                "fresh_writer_before_any_model_role")
        retain(path, contract)
        backup = Path.home() / "Documents/CourseKnowledgeBackups/fresh-whole-authoring" / job.key / "inputs"
        for source in [path, job.root / "manifest.json", Path(__file__), Path(tokenizer),
                       root / "wiki/textbook-whole-record-evidence" / inventory_key / "manifest.json"]:
            relative = source.relative_to(root) if source.is_relative_to(root) else Path("support") / source.name
            writing.whole.backup(source, backup / relative)
    return {"state": "fresh_writer_prepared_before_first_role", "job": job.key, "inventory": inventory_key,
        "records": len(inventory["sources"]), "knowledge_points": working["confirmed_point_count"],
        "pending_original_tasks": len(working["pending_source_cards"]), "model_calls": 0,
        "original_budgets_reset": False, "reader_publication_allowed": False}


def bound(root, inventory_key, ledger_job, tokenizer):
    root, inventory, working, job = inputs(root, inventory_key, ledger_job, tokenizer)
    contract = writing.whole.read(job.root / CONTRACT)
    require(contract == execution(job, inventory, working, tokenizer), "original_prepared_execution_and_budgets")
    return root, inventory, working, job, contract


def job_type(contract, *, read_only):
    class FreshJob(BASE_JOB):
        def __init__(self, store, inventory, working, tokenizer):
            super().__init__(store, inventory, working, tokenizer)
            require(execution(self, inventory, working, tokenizer) == contract, "same_prepared_writer")
            self.inventory, self.working = inventory, working
            self.reader = StrictReader(self, inventory, working, visible.Recovery([]))
            self.reader.paths.add(self.root / CONTRACT)
            self.proposals = {}

        def generate_response(self, *args, **kwargs):
            require(not read_only, "inference_forbidden_in_reconstruction")
            return super().generate_response(*args, **kwargs)

        def slot(self, *args, **kwargs):
            require(not read_only, "gpu_acquisition_forbidden_in_reconstruction")
            return super().slot(*args, **kwargs)

        def progress(self, state, stage, **extra):
            if read_only:
                self.current = stage
            else:
                super().progress(state, stage, **extra)

        def stage(self, name, prompt, schema, context, validate, **kwargs):
            actual_schema, actual_context, actual_kwargs = schema, context, kwargs

            def derive(value):
                return value, {}

            match = re.fullmatch(PATTERN, name)
            if match:
                role = match[2]
                ids = list(context["assigned_points"])
                expected_prompt = writing.author.WRITE_PROMPT if role == "write" else (
                    writing.author.REPAIR_PROMPT if role == "repair" else writing.author.REVIEW_PROMPT)
                expected_schema = writing.section_schema(ids, self.working) if role in {"write", "repair"} else (
                    writing.review_schema(context["candidate"], ids))
                require(prompt == expected_prompt and schema == expected_schema
                    and set(kwargs) == {"evidence_packet"}, "declared_original_unit_role")
                scope = continuation.scope_for(ids, self.working, self.inventory,
                    {k: v for k, v in context.items() if k not in {"candidate", "review"}}, kwargs["evidence_packet"])
                actual_schema = section_schema(schema, scope["expanded"], self.working["pending_source_cards"])
                actual_kwargs = {"evidence_packet": scope["expanded"]}
                matching = {stage: row for stage, row in self.proposals.items()
                    if row["candidate"] == context.get("candidate")}
                if matching:
                    actual_context = {**context, "unverified_citation_proposals": matching,
                        "citation_proposals_are_factual_evidence": False}
                if role in {"write", "repair"}:
                    def derive(value):
                        return citation_candidate(value, ids, self.working, self.inventory, scope)

                    def actual_validate(value):
                        jsonschema.validate(value, actual_schema)
                        derive(value)
                else:
                    actual_validate = validate
            elif prompt == writing.author.GROUP_PROMPT and re.fullmatch(r"concepts-[0-9]{2}-[0-9]{4}", name):
                require(not kwargs and set(context) == {"items", "mode"}, "original_planning_role")

                def derive(value):
                    return plan_candidate(value, context["items"], final=context["mode"] == "chapters"), {}

                def actual_validate(value):
                    derive(value)
            else:
                require(prompt == writing.author.READ_PROMPT and re.fullmatch(r"reading-[0-9]{4}", name)
                    and not kwargs, "original_reading_role")
                actual_validate = validate
            receipt = {"policy": POLICY, "execution_sha256": digest(contract), "stage": name,
                "original_schema_sha256": digest(schema), "actual_schema_sha256": digest(actual_schema),
                "original_context_sha256": digest(context), "actual_context_sha256": digest(actual_context),
                "original_packet_sha256": digest(kwargs.get("evidence_packet", self.packet)),
                "actual_packet_sha256": digest(actual_kwargs.get("evidence_packet", self.packet)),
                "original_budgets_reset": False, "reader_publication_allowed": False}
            request_path = self.root / (name + "-prospective-request.json")
            retain(request_path, receipt, read_only=read_only)
            self.reader.paths.add(request_path)
            if not read_only and not (self.root / (name + ".json")).exists():
                BASE_JOB.stage(self, name, prompt, actual_schema, actual_context, actual_validate, **actual_kwargs)
            value = self.reader.stage(name, prompt, actual_schema, actual_context, actual_validate, **actual_kwargs)
            candidate, changes = derive(value)
            if candidate != value:
                artifact = {"policy": POLICY, "stage": name, "actual_response_sha256": digest(value),
                    "candidate": candidate, "candidate_sha256": digest(candidate), "citation_proposals": changes,
                    "prose_and_teaching_dispositions_unchanged": True,
                    "citation_proposals_are_factual_evidence": False, "semantic_acceptance": False}
                path = self.root / (name + "-prospective-derived.json")
                retain(path, artifact, read_only=read_only)
                self.reader.paths.add(path)
                if match:
                    self.proposals[name] = artifact
            if match:
                with visible.packet_scope(scope):
                    validate(candidate)
            else:
                validate(candidate)
            return candidate

    return FreshJob


@contextmanager
def installed(cls):
    previous = writing.AuthoringJob
    writing.AuthoringJob = cls
    try:
        yield
    finally:
        writing.AuthoringJob = previous


def worker(root, inventory_key, ledger_job, tokenizer):
    root, _, _, job, contract = bound(root, inventory_key, ledger_job, tokenizer)
    require(not (job.root / "progress.json").exists(), "first_worker_only_no_automatic_retry")
    with installed(job_type(contract, read_only=False)):
        result = writing.run(root, inventory_key, ledger_job, tokenizer)
    for source in [job.root / CONTRACT, *job.root.glob("*-prospective-*.json"), Path(__file__)]:
        writing.whole.backup(source, Path.home() / "Documents/CourseKnowledgeBackups/fresh-whole-authoring"
            / job.key / "results" / source.name)
    return result


def reconstruct(root, inventory_key, ledger_job, tokenizer, *, complete=True):
    root, inventory, working, base, contract = bound(root, inventory_key, ledger_job, tokenizer)
    status = writing.whole.read(base.root / "progress.json")
    if complete:
        require(status["stage"] == "private_article_assembled"
            and status["state"] == "conceptual_article_requires_actual_source_and_editorial_review"
            and not writing.identity_live(status), "actually_finished_writer")
    lease = schema_lease(root / "wiki/textbook-whole-record-evidence" / inventory_key / "authoring-worker.lock", timeout=0) if complete else nullcontext()
    with lease:
        job = job_type(contract, read_only=True)(writing.TextbookStore(root), inventory, working, tokenizer)
        reader = job.reader
        require(reader.saved("private-working-inputs") == working, "unchanged_working_inputs")
        original_contract = root / "wiki/textbook-keyed-jobs" / ledger_job / "whole-source-authoring-execution.json"
        require(writing.whole.read(original_contract) == {"inventory": inventory_key,
            "ledger_sha256": digest(working), "job": job.key}, "original_writer_budget")
        reader.paths.update([original_contract, job.root / "manifest.json"])
        plan = writing.organize(job, working)
        units = writing.writing_units(plan, working, inventory, job.tokenizer)
        require(reader.saved("concept-plan") == plan and reader.saved("writing-allocation") == units,
            "same_complete_plan_and_allocations")
        count = len(units) if complete else status["completed_sections"]
        require(type(count) is int and 0 < count <= len(units), "actual_completed_units")
        sections, exclusions, issues = snapshot.replay_units(reader, units, plan, count)
        private, book = None, None
        if complete:
            private, book, issues = snapshot.assemble(reader, units, plan, sections, exclusions, issues)
            require(writing.whole.read(job.root / "progress.json") == status, "stable_finished_writer")
        for path in job.root.glob("response-*.json"):
            if writing.whole.read(path)["stage"] in reader.stages:
                reader.paths.add(path)
        summary = {"state": "fresh_whole_manuscript_reconstructed" if complete else "fresh_whole_prefix_reconstructed",
            "job": job.key, "inventory": inventory_key, "verified_units": count, "total_units": len(units),
            "unresolved_issues": len(issues), "model_calls": 0, "reader_publication_allowed": False}
        proof = {"policy": POLICY, **summary, "execution_sha256": digest(contract),
            "files": {str(path.relative_to(root)): writing.whole.sha(path.read_bytes()) for path in sorted(reader.paths)},
            "article_sha256": digest(book) if book else None, "private_article_sha256": digest(private) if private else None}
        return {"summary": summary, "proof": proof, "inventory": inventory, "working": working,
            "private": private, "book": book, "issues": copy.deepcopy(issues)}


def guard(root, inventory_key, ledger_job, tokenizer):
    from .course_archive_gpu import checked_sample
    from .course_gpu_guard import run as guarded

    root, _, _, _, _ = bound(root, inventory_key, ledger_job, tokenizer)
    writing.TextbookStore(root).checkpoint()
    require(not (root / "supervisor-stop.flag").exists(), "supervisor_stop_preserved")
    with schema_lease(root / "wiki/fresh-authoring-dispatch.lock", timeout=0):
        previous = root / "review-experiment-thermal-guard.json"
        if previous.exists():
            old = writing.whole.read(previous)
            require(not writing.identity_live(old) and old["state"] == "worker_exited" and old.get("returncode") == 0,
                "prior_review_guard_successfully_exited")
            writing.whole.backup(previous, root / "wiki/fresh-authoring-guard-history" / (writing.whole.sha(previous.read_bytes()) + ".json"))
        guarded(root, [sys.executable, "-m", __spec__.name, "--root", str(root), "--inventory", inventory_key,
            "--ledger-job", ledger_job, "--tokenizer", str(Path(tokenizer).resolve()), "worker"],
            maximum_temperature=70, maximum_power=200, guard_name="review-experiment-thermal-guard",
            sampler=lambda: checked_sample(root))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--ledger-job", required=True)
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("command", choices=["prepare", "worker", "guard", "prefix", "verify"])
    args = parser.parse_args()
    try:
        values = (args.root, args.inventory, args.ledger_job, args.tokenizer)
        if args.command in {"prefix", "verify"}:
            result = reconstruct(*values, complete=args.command == "verify")
            path = snapshot.save(args.root, result)
            output = {**result["summary"], "snapshot": str(path)}
        else:
            output = {"prepare": prepare, "worker": worker, "guard": guard}[args.command](*values)
        print(json.dumps(output or {"state": "fresh_authoring_guard_returned"}))
    except Exception as exc:
        print(json.dumps(writing.whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
