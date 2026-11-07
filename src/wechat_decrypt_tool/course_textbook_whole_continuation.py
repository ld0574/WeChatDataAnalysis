"""Continue a whole manuscript from authenticated, actually used unit roles.

The orchestration explicitly reuses an actual repair and its independent final
review. Later unused roles opt into branch-aware citation schemas. Old caches,
requests, judgments, failures and per-role budgets remain untouched.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, nullcontext
import copy
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace

import jsonschema

from . import course_textbook_whole_unit_repair as repair
from . import course_textbook_whole_repair_resume as proposal
from .course_knowledge import atomic_json, schema_lease

snapshot, grammar, visible = repair.snapshot, repair.grammar, repair.visible
draft, writing = repair.draft, repair.writing
POLICY = "authenticated-original-unit-repair-whole-continuation-v1"
CONTRACT = "whole-original-unit-continuation-execution.json"
ROLES = ("write", "review", "repair", "repair-review")
PATTERN = r"(section-[0-9]{4})-(write|review|repair|repair-review)"


def require(ok, code):
    if not ok:
        raise ValueError("requires_whole_continuation_" + code)


def digest(value):
    return visible.digest(value)


def checked_cache(folder, name):
    value = writing.whole.read(folder / (name + ".json"))
    require(value["data_sha256"] == digest(value["data"]), "stage_cache_integrity")
    accepted = [writing.whole.read(path) for path in (folder / "attempts").glob(name + "-[0-9][0-9].json")
        if writing.whole.read(path).get("accepted_structure")]
    require(len(accepted) == 1 and accepted[0]["input_sha256"] == value["input_sha256"]
        and json.loads(accepted[0]["raw_response"]) == value["data"], "actual_accepted_response")
    writing.whole.editor.e.verify_response(folder, name, accepted[0])
    return value["data"], value["input_sha256"]


@contextmanager
def exact_role_cache():
    previous = writing.whole.editor.e.checked_cache
    writing.whole.editor.e.checked_cache = checked_cache
    try:
        yield
    finally:
        writing.whole.editor.e.checked_cache = previous


class StrictReader(snapshot.ReadOnlyStages):
    def authenticate_cache(self, name, prompt, schema, context, packet):
        with exact_role_cache():
            return super().authenticate_cache(name, prompt, schema, context, packet)


def scope_for(ids, working, inventory, context, packet):
    require(packet == visible.ORIGINAL_PACKET(ids, working, inventory), "original_unit_packet")
    expected = writing.whole.context_for(inventory, list(packet["sources"]))
    require(all(context.get(key) == expected[key] for key in ("sources", "context_sources", "scope"))
        and context["assigned_points"] == {key: working["points"][key] for key in ids}, "complete_actually_supplied_originals")
    refs = [*packet["sources"], *context["context_sources"]]
    return {"ids": tuple(ids), "working": working, "inventory": inventory,
        "context": copy.deepcopy(context), "original": copy.deepcopy(packet),
        "expanded": {"sources": {key: inventory["sources"][key] for key in refs}}}


def load_bundle(root, key, prefix, tokenizer):
    root = Path(root).resolve()
    folder = root / "wiki/textbook-whole-authoring-jobs" / key
    execution_path = folder / (prefix + repair.SUFFIX)
    if (folder / (prefix + proposal.RESULT)).is_file():
        recovered, final, result = proposal.authenticate(root, key, prefix, tokenizer)
        paths = {root / name for name in recovered.proof["files"]} | recovered.reader.paths
        paths.update([execution_path, Path(recovered.original_execution["snapshot_path"])])
        paths.update(folder / (prefix + suffix) for suffix in (proposal.SUFFIX, proposal.INPUT, proposal.RESULT))
        paths.update(folder / "attempts" / name for name in result["all_attempt_files"])
        for path in folder.glob("response-*.json"):
            if writing.whole.read(path)["stage"].startswith(prefix + "-"):
                paths.add(path)
        return SimpleNamespace(root=root, key=key, prefix=prefix, tokenizer=tokenizer, job=recovered.job,
            working=recovered.working, inventory=recovered.inventory, context=recovered.context, scope=recovered.scope,
            candidate=recovered.candidate, initial=recovered.initial, final=final, result=result, proof=recovered.proof,
            plan=draft.saved_data(folder / "concept-plan.json"), units=draft.saved_data(folder / "writing-allocation.json"),
            paths=paths, actual_stages=recovered.reader.stages, uses_proposal=True, raw_candidate=recovered.raw_candidate,
            repair_binding=recovered.repair_history["actual_repair_input_sha256"])
    contract = writing.whole.read(execution_path)
    args = repair.inputs(root, key, prefix, tokenizer, contract["snapshot_path"])
    job, working, inventory, context, scope, original, defects, history, proof = args
    require(contract == {**repair.execution(job, context, scope, original, defects, history, proof),
        "section": prefix, "snapshot_path": contract["snapshot_path"]}, "unchanged_original_role_execution")
    reader = StrictReader(job, inventory, working, visible.Recovery([]))
    job.stage = reader.stage
    candidate, initial, final = repair.review_and_repair(job, prefix, working, inventory, context, scope, original, defects)
    result_path = folder / (prefix + "-whole-original-role-repair-result.json")
    result = {"policy": repair.POLICY, "job": key, "section": prefix, "execution_sha256": digest(contract),
        "original_candidate_sha256": digest(original), "candidate": candidate, "candidate_sha256": digest(candidate),
        "original_initial_review": initial, "actual_final_review": final,
        "factual_checks_passed": all(writing.author.verdicts(final)),
        "all_attempt_files": {p.name: writing.whole.sha(p.read_bytes())
            for p in sorted((folder / "attempts").glob(prefix + "-*.json"))},
        "semantic_repairs": 1, "whole_writer_resume_required": True, "reader_publication_allowed": False}
    require(writing.whole.read(result_path) == result, "actual_completed_original_role_result")
    plan, units = draft.saved_data(folder / "concept-plan.json"), draft.saved_data(folder / "writing-allocation.json")
    paths = {root / name for name in proof["files"]} | reader.paths | {execution_path, result_path, Path(contract["snapshot_path"])}
    paths.update(folder / "attempts" / name for name in history["original_write_attempts"])
    for path in folder.glob("response-*.json"):
        if writing.whole.read(path)["stage"].startswith(prefix + "-"):
            paths.add(path)
    return SimpleNamespace(root=root, key=key, prefix=prefix, tokenizer=tokenizer, job=job, working=working,
        inventory=inventory, context=context, scope=scope, candidate=candidate, initial=initial, final=final,
        result=result, proof=proof, plan=plan, units=units, paths=paths, actual_stages=reader.stages)


def execution(bundle):
    future = [unit["id"] for unit in bundle.units[bundle.proof["verified_units"] + 1:]]
    actual_repair = "repair-proposal" if getattr(bundle, "uses_proposal", False) else "repair"
    return {"policy": POLICY, "job": bundle.key, "section": bundle.prefix,
        "inventory": digest(bundle.inventory), "working_sha256": digest(bundle.working),
        "prefix_snapshot_sha256": digest(bundle.proof), "actual_repair_result_sha256": digest(bundle.result),
        "original_role_mapping": {"write": actual_repair, "review": "repair-review",
            "repair": actual_repair, "repair-review": "repair-review"},
        "future_units": future, "future_roles": list(ROLES), "future_roles_unused_at_preparation": True,
        "future_schema_policy": grammar.POLICY, "future_scope": "only_complete_originals_actually_supplied_in_context",
        "future_primary_citation_gate": "explicit_unverified_metadata_proposal_followed_by_original_independent_review",
        "future_candidate_text_and_coverage_changed_by_adapter": False,
        "original_stage_attempt_budget": 2, "semantic_repairs_per_unit": 1, "original_budgets_reset": False,
        "additional_model_roles": 0, "negative_judgments_preserved": True, "reader_publication_allowed": False,
        "adapter_sha256": writing.whole.sha(Path(__file__).read_bytes()),
        "helpers_sha256": {m.__name__: writing.whole.sha(Path(m.__file__).read_bytes())
            for m in (repair, proposal, snapshot, grammar, visible, visible.refs, draft, draft.reviewable, writing)},
        "authenticated_input_files": {str(path.relative_to(bundle.root)): writing.whole.sha(path.read_bytes())
            for path in sorted(bundle.paths)}}


def unused_future(folder, units):
    return all(not any((folder / "attempts").glob(unit + "-*.json"))
        and all(not (folder / (unit + "-" + role + suffix)).exists()
            for role in ROLES for suffix in (".json", ".proof.json", "-continuation-request.json", "-whole-future-citation-proposal.json")) for unit in units)


def prepare(root, key, prefix, tokenizer):
    folder = Path(root).resolve() / "wiki/textbook-whole-authoring-jobs" / key
    status = writing.whole.read(folder / "progress.json")
    with schema_lease(Path(root).resolve() / "wiki/textbook-whole-record-evidence" / status["inventory"] / "authoring-worker.lock", timeout=0):
        require(not writing.identity_live(status), "original_unit_worker_exited")
        bundle = load_bundle(root, key, prefix, tokenizer)
        contract = execution(bundle)
        target = folder / CONTRACT
        if target.exists():
            require(writing.whole.read(target) == contract, "immutable_continuation_execution")
        else:
            require(status["state"] in {"original_unit_roles_finished_requires_whole_writer_resume",
                    "original_repair_response_reviewed_requires_whole_writer_resume"}
                and status["stage"] == prefix + "-repair-review"
                and status["completed_sections"] == bundle.proof["verified_units"], "exact_finite_original_role_terminal_state")
            require(unused_future(folder, contract["future_units"]), "all_future_roles_still_unused")
            atomic_json(target, contract)
        dest = Path.home() / "Documents/CourseKnowledgeBackups/whole-unit-continuation" / key / "inputs"
        for path in [target, Path(__file__), *sorted(bundle.paths)]:
            relative = path.relative_to(bundle.root) if path.is_relative_to(bundle.root) else Path("code") / path.name
            writing.whole.backup(path, dest / relative)
        return {"state": "original_role_result_authenticated_for_whole_continuation", "job": key, "section": prefix,
            "verified_prior_units": bundle.proof["verified_units"], "remaining_unused_units": len(contract["future_units"]),
            "repaired_unit_factual_checks_passed": bundle.result["factual_checks_passed"],
            "model_calls": 0, "original_budgets_reset": False, "reader_publication_allowed": False}


def retain(path, value, *, read_only):
    if path.exists():
        require(writing.whole.read(path) == value, "unchanged_explicit_stage_mapping")
    else:
        require(not read_only, "required_existing_stage_mapping")
        atomic_json(path, value)


def derived_stage(bundle, contract, name, prompt, schema, context, validate, kwargs, *, read_only):
    match = re.fullmatch(PATTERN, name)
    require(match is not None and match[1] == bundle.prefix, "exact_derived_unit_role")
    role = match[2]
    candidate, final = bundle.candidate, bundle.final
    expected_context = dict(bundle.context)
    if role != "write":
        expected_context["candidate"] = candidate
    if role == "repair":
        expected_context["review"] = final
    expected_prompt = writing.author.WRITE_PROMPT if role == "write" else (
        writing.author.REPAIR_PROMPT if role == "repair" else writing.author.REVIEW_PROMPT)
    ids = list(bundle.context["assigned_points"])
    expected_schema = writing.section_schema(ids, bundle.working) if role in {"write", "repair"} else writing.review_schema(candidate, ids)
    require(context == expected_context and prompt == expected_prompt and schema == expected_schema
        and kwargs == {"evidence_packet": bundle.scope["original"]}, "exact_original_orchestration_request")
    value = candidate if role in {"write", "repair"} else final
    with visible.packet_scope(bundle.scope):
        validate(value)
    actual_role = contract["original_role_mapping"][role]
    actual_model_role = actual_role
    if actual_role == "repair-proposal":
        require(getattr(bundle, "uses_proposal", False) and bundle.result["candidate"] == value
            and bundle.result["actual_repair_candidate_sha256"] == digest(bundle.raw_candidate), "authenticated_explicit_repair_proposal")
        actual_model_role, binding = "repair", bundle.repair_binding
        raw_value = bundle.raw_candidate
    else:
        raw_value, binding = checked_cache(bundle.job.root, bundle.prefix + "-" + actual_role)
        require(raw_value == value, "exact_actual_role_value")
    receipt = {"policy": POLICY, "execution_sha256": digest(contract), "orchestration_stage": name,
        "actual_model_stage": bundle.prefix + "-" + actual_model_role, "actual_input_sha256": binding,
        "actual_data_sha256": digest(raw_value), "derived_data_sha256": digest(value),
        "explicit_citation_proposal_derivation": actual_role == "repair-proposal", "initial_review_sha256": digest(bundle.initial),
        "final_review_sha256": digest(final), "repaired_candidate_sha256": digest(candidate),
        "actual_original_role_result_sha256": digest(bundle.result), "new_model_calls": 0,
        "model_write_cache_fabricated": False, "negative_judgments_preserved": True, "reader_publication_allowed": False}
    path = bundle.job.root / (name + "-continuation-request.json")
    retain(path, receipt, read_only=read_only)
    return value, path


def future_candidate(folder, name, raw, scope, bundle, contract, *, read_only):
    path = folder / (name + "-whole-future-citation-proposal.json")
    try:
        with visible.packet_scope(scope):
            visible.ORIGINAL_VALIDATE(raw, list(scope["ids"]), bundle.working, bundle.inventory)
    except ValueError as exc:
        require(str(exc) == proposal.ERROR, "only_future_primary_citation_gate_can_be_deferred")
        candidate, changes = proposal.propose(raw, list(scope["ids"]), bundle.working, bundle.inventory, scope)
    else:
        require(not path.exists(), "no_stale_future_citation_proposal")
        return raw, None
    actual, binding = checked_cache(folder, name)
    require(actual == raw, "actual_future_response_before_proposal")
    artifact = {"policy": POLICY, "execution_sha256": digest(contract), "stage": name,
        "actual_input_sha256": binding, "actual_candidate_sha256": digest(raw), "candidate": candidate,
        "candidate_sha256": digest(candidate), "citation_proposals": changes,
        "prose_coverage_and_disposition_unchanged": True, "citation_proposals_are_factual_evidence": False,
        "semantic_acceptance": False, "reader_publication_allowed": False}
    retain(path, artifact, read_only=read_only)
    return candidate, (path, artifact)


def job_type(bundle, contract, registry, *, read_only):
    class ContinuationJob(visible.ORIGINAL_JOB):
        def __init__(self, store, inventory, working, tokenizer):
            super().__init__(store, inventory, working, tokenizer)
            require(self.key == bundle.key and inventory == bundle.inventory and working == bundle.working,
                "same_original_writer_identity")
            self.reader = StrictReader(self, inventory, working, registry)
            self.continuation_paths = set(bundle.paths) | {self.root / CONTRACT}
            self.derived_stages = []
            self.future_proposals = {}

        def progress(self, state, stage, **extra):
            old_count, old_total = self.finished_sections, self.section_count
            self.finished_sections = max(old_count, bundle.proof["verified_units"])
            self.section_count = max(old_total, len(bundle.units))
            try:
                super().progress(state, stage, **extra)
            finally:
                self.finished_sections, self.section_count = old_count, old_total

        def stage(self, name, prompt, schema, context, validate, **kwargs):
            match = re.fullmatch(PATTERN, name)
            if match and match[1] == bundle.prefix:
                value, path = derived_stage(bundle, contract, name, prompt, schema, context, validate, kwargs, read_only=read_only)
                self.continuation_paths.add(path)
                self.derived_stages.append(name)
                return value
            if match and match[1] in contract["future_units"]:
                unit = next(unit for unit in bundle.units if unit["id"] == match[1])
                role = match[2]
                expected_prompt = writing.author.WRITE_PROMPT if role == "write" else (
                    writing.author.REPAIR_PROMPT if role == "repair" else writing.author.REVIEW_PROMPT)
                expected_schema = writing.section_schema(unit["point_ids"], bundle.working) if role in {"write", "repair"} else (
                    writing.review_schema(context["candidate"], unit["point_ids"]))
                require(prompt == expected_prompt and schema == expected_schema, "declared_future_original_role")
                base_context = {k: v for k, v in context.items() if k not in {"candidate", "review"}}
                require(set(kwargs) == {"evidence_packet"}, "complete_future_evidence_packet")
                scope = scope_for(unit["point_ids"], bundle.working, bundle.inventory, base_context, kwargs["evidence_packet"])
                scoped_schema = grammar.bind(schema, scope["expanded"])
                matching = {stage: row["citation_proposals"] for stage, row in self.future_proposals.items()
                    if row["candidate"] == context.get("candidate")}
                actual_context = {**context, "unverified_citation_proposals": matching,
                    "citation_proposals_are_factual_evidence": False} if matching else context
                receipt = {"policy": POLICY, "execution_sha256": digest(contract), "stage": name,
                    "original_schema_sha256": digest(schema), "actual_schema_sha256": digest(scoped_schema),
                    "original_context_sha256": digest(context), "actual_context_sha256": digest(actual_context),
                    "original_packet_sha256": digest(scope["original"]),
                    "actual_visible_packet_sha256": digest(scope["expanded"]),
                    "actual_visible_sources": len(scope["expanded"]["sources"]), "original_budgets_reset": False,
                    "only_primary_citation_metadata_gate_deferred": role in {"write", "repair"}}
                path = self.root / (name + "-continuation-request.json")
                retain(path, receipt, read_only=read_only)
                self.continuation_paths.add(path)

                def scoped_validate(value):
                    jsonschema.validate(value, scoped_schema)
                    with visible.packet_scope(scope):
                        if role in {"write", "repair"} and value.get("kind") != "non_teaching":
                            draft.reviewable.validate_reviewable({k: v for k, v in value.items() if k != "kind"},
                                unit["point_ids"], bundle.working, bundle.inventory)
                        else:
                            validate(value)

                actual_kwargs = {"evidence_packet": scope["expanded"]}
                if not read_only and not (self.root / (name + ".json")).exists():
                    value = visible.ORIGINAL_JOB.stage(self, name, prompt, scoped_schema, actual_context, scoped_validate, **actual_kwargs)
                    actual = self.reader.stage(name, prompt, scoped_schema, actual_context, scoped_validate, **actual_kwargs)
                    require(actual == value, "actual_new_stage_authenticated")
                else:
                    value = self.reader.stage(name, prompt, scoped_schema, actual_context, scoped_validate, **actual_kwargs)
                if role in {"write", "repair"}:
                    value, derivation = future_candidate(self.root, name, value, scope, bundle, contract, read_only=read_only)
                    if derivation:
                        self.continuation_paths.add(derivation[0])
                        self.future_proposals[name] = derivation[1]
                return value
            if match or prompt == writing.author.GROUP_PROMPT or read_only:
                require(not match or match[1] < bundle.prefix, "declared_original_role_scope")
                return self.reader.stage(name, prompt, schema, context, validate, **kwargs)
            require(prompt == writing.author.READ_PROMPT and re.fullmatch(r"reading-[0-9]{4}", name), "original_whole_reading_role")
            return visible.ORIGINAL_JOB.stage(self, name, prompt, schema, context, validate, **kwargs)

    return ContinuationJob


def bound_bundle(root, key, prefix, tokenizer):
    bundle = load_bundle(root, key, prefix, tokenizer)
    contract = writing.whole.read(bundle.job.root / CONTRACT)
    require(contract == execution(bundle), "same_prepared_continuation_code_and_inputs")
    return bundle, contract


def worker(root, key, prefix, tokenizer):
    bundle, contract = bound_bundle(root, key, prefix, tokenizer)
    registry = visible.Recovery([])
    cls = job_type(bundle, contract, registry, read_only=False)
    with registry.hooks():
        previous = writing.AuthoringJob
        writing.AuthoringJob = cls
        try:
            result = writing.run(root, digest(bundle.inventory), bundle.working["job"], tokenizer)
        finally:
            writing.AuthoringJob = previous
    dest = Path.home() / "Documents/CourseKnowledgeBackups/whole-unit-continuation" / key / "results"
    for source in [bundle.job.root / CONTRACT, *bundle.job.root.glob("*-continuation-request.json"),
            *bundle.job.root.glob("*-whole-future-citation-proposal.json")]:
        writing.whole.backup(source, dest / source.name)
    return result


def reconstruct(root, key, prefix, tokenizer, *, complete):
    root = Path(root).resolve()
    folder = root / "wiki/textbook-whole-authoring-jobs" / key
    status = writing.whole.read(folder / "progress.json")
    if complete:
        require(status["stage"] == "private_article_assembled"
            and status["state"] == "conceptual_article_requires_actual_source_and_editorial_review"
            and not writing.identity_live(status), "assembled_exited_writer")
    lease = schema_lease(root / "wiki/textbook-whole-record-evidence" / status["inventory"] / "authoring-worker.lock", timeout=0) if complete else nullcontext()
    with lease:
        bundle, contract = bound_bundle(root, key, prefix, tokenizer)
        registry = visible.Recovery([])
        job = job_type(bundle, contract, registry, read_only=True)(writing.TextbookStore(root), bundle.inventory, bundle.working, tokenizer)
        reader = job.reader
        require(reader.saved("private-working-inputs") == bundle.working, "original_working_inputs")
        with registry.hooks():
            plan = writing.organize(job, bundle.working)
            units = writing.writing_units(plan, bundle.working, bundle.inventory, job.tokenizer)
            require(reader.saved("concept-plan") == plan == bundle.plan
                and reader.saved("writing-allocation") == units == bundle.units, "original_plan_and_allocation")
            count = len(units) if complete else status["completed_sections"]
            require(type(count) is int and bundle.proof["verified_units"] < count <= len(units), "completed_continued_prefix")
            sections, exclusions, issues = snapshot.replay_units(reader, units, plan, count)
            private, book = None, None
            if complete:
                private, book, issues = snapshot.assemble(reader, units, plan, sections, exclusions, issues)
        if complete:
            require(writing.whole.read(folder / "progress.json") == status, "stable_finished_writer")
        paths = reader.paths | job.continuation_paths
        for path in folder.glob("response-*.json"):
            if writing.whole.read(path)["stage"] in reader.stages:
                paths.add(path)
        summary = {"state": "continued_whole_manuscript_authenticated_for_coverage_review" if complete else "continued_whole_prefix_authenticated",
            "job": key, "inventory": digest(bundle.inventory), "verified_units": count, "total_units": len(units),
            "retained_teaching_sections": len(sections), "non_teaching_units": len(exclusions), "unresolved_issues": len(issues),
            "explicit_derived_orchestration_stages": len(job.derived_stages), "model_calls": 0,
            "original_budgets_reset": False, "semantic_acceptance": False, "reader_publication_allowed": False}
        proof = {"policy": POLICY, **summary, "execution_sha256": digest(contract),
            "adapter_sha256": writing.whole.sha(Path(__file__).read_bytes()),
            "files": {str(path.relative_to(root)): writing.whole.sha(path.read_bytes()) for path in sorted(paths)},
            "article_sha256": digest(book) if book else None, "private_article_sha256": digest(private) if private else None,
            "scope": "complete_original_source_coverage_and_editorial_acceptance_still_required"}
        return {"summary": summary, "proof": proof, "inventory": bundle.inventory, "working": bundle.working,
            "private": private, "book": book, "issues": copy.deepcopy(issues)}


def guard(root, key, prefix, tokenizer):
    from .course_archive_gpu import checked_sample
    from .course_gpu_guard import run as guarded

    root = Path(root).resolve()
    bound_bundle(root, key, prefix, tokenizer)
    writing.TextbookStore(root).checkpoint()
    with schema_lease(root / "wiki/whole-source-authoring-dispatch.lock", timeout=0):
        previous = root / "wiki-revision-thermal-guard.json"
        old = writing.whole.read(previous)
        require(not writing.identity_live(old) and old["state"] == "worker_exited" and old.get("returncode") == 0,
            "prior_guard_cleanly_exited")
        writing.whole.backup(previous, root / "wiki/whole-continuation-guard-history" / (writing.whole.sha(previous.read_bytes()) + ".json"))
        guarded(root, [sys.executable, "-m", __spec__.name, "--root", str(root), "--job", key, "--section", prefix,
            "--tokenizer", str(Path(tokenizer).resolve()), "worker"], maximum_temperature=70, maximum_power=200,
            guard_name="wiki-revision-thermal-guard", sampler=lambda: checked_sample(root))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--job", required=True)
    p.add_argument("--section", required=True)
    p.add_argument("--tokenizer", type=Path, required=True)
    p.add_argument("command", choices=["prepare", "worker", "guard", "prefix", "complete"])
    a = p.parse_args()
    try:
        if a.command in {"prefix", "complete"}:
            result = reconstruct(a.root, a.job, a.section, a.tokenizer, complete=a.command == "complete")
            path = snapshot.save(a.root, result)
            output = {**result["summary"], "snapshot": str(path)}
        else:
            output = {"prepare": prepare, "worker": worker, "guard": guard}[a.command](a.root, a.job, a.section, a.tokenizer)
        print(json.dumps(output or {"state": "guard_returned"}))
    except Exception as exc:
        print(json.dumps(writing.whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
