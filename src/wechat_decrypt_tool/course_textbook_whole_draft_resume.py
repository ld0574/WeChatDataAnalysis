"""Review complete failed whole-record drafts using their unused original roles.

Invalid generated diagrams stay in a private quarantine. Citation additions are
explicit proposals, not factual acceptance. Prose, teaching dispositions, failed
responses and all original write/review/repair budgets remain unchanged.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

import jsonschema

from . import course_textbook_diagram_quarantine as quarantine
from . import course_textbook_reviewable_write as reviewable
from . import course_textbook_whole_authoring as writing
from . import course_textbook_whole_plan_resume as plan_resume
from .course_knowledge import atomic_json, schema_lease
from .course_library import canonical

POLICY = "whole-record-unchanged-draft-citation-proposals-diagram-quarantine-v1"
BASE_JOB = writing.AuthoringJob


def require(ok, code):
    if not ok:
        raise ValueError("requires_whole_draft_resume_" + code)


def normalize(candidate, ids, working, inventory):
    jsonschema.validate(candidate, writing.section_schema(ids, working))
    require(candidate.get("kind") != "non_teaching", "complete_authored_candidate")
    scoped, invalid = quarantine.quarantine_graphs(candidate)
    proposed, changes = reviewable.propose_citations(scoped, ids, working, inventory)
    writing.validate_section(proposed, ids, working, inventory)
    require(bool(invalid or changes), "actual_recoverable_structure_defect")
    require({k: v for k, v in proposed.items() if k not in {"paragraphs", "diagrams"}}
        == {k: v for k, v in candidate.items() if k not in {"paragraphs", "diagrams"}}
        and [{k: v for k, v in p.items() if k != "citations"} for p in proposed["paragraphs"]]
        == [{k: v for k, v in p.items() if k != "citations"} for p in candidate["paragraphs"]],
        "authored_content_and_coverage_unchanged")
    return proposed, changes, invalid


def capture_request(job, stage, prompt, schema, context, packet):
    class Captured(BaseException):
        pass

    captured, original = {}, job.stage_input
    overridden = "stage_input" in job.__dict__

    def capture(actual_prompt, actual_schema, actual_context, **kwargs):
        transformed, binding = original(actual_prompt, actual_schema, actual_context, **kwargs)
        captured.update(prompt=actual_prompt, schema=transformed, binding=binding)
        raise Captured

    job.stage_input = capture
    try:
        BASE_JOB.stage(job, stage, prompt, schema, context, lambda value: None, evidence_packet=packet)
    except Captured:
        return captured
    finally:
        if overridden:
            job.stage_input = original
        else:
            del job.stage_input
    raise AssertionError("Original writer did not expose its request")


def candidate_from_history(job, name, schema, context, packet, working, inventory):
    require(re.fullmatch(r"section-[0-9]{4}-write", name) is not None, "original_write_role_only")
    request = capture_request(job, name, writing.author.WRITE_PROMPT, schema, context, packet)
    paths = sorted((job.root / "attempts").glob(name + "-[0-9][0-9].json"))
    require([p.name for p in paths] == [name + "-01.json", name + "-02.json"], "exhausted_two_attempt_write_budget")
    previous, hashes, candidates, rejected = "", {}, [], []
    for number, path in enumerate(paths, 1):
        record = writing.whole.read(path)
        require(record["stage"] == name and record["attempt"] == number
            and record["input_sha256"] == request["binding"]
            and record["model_digest"] == job.models["qwen3.5:9b"]
            and record.get("error_type") == "ValueError" and not record.get("accepted_structure"),
            "original_failed_write_identity")
        messages = [{"role": "system", "content": request["prompt"]}, {"role": "user",
            "content": json.dumps({**context, "previous_validation_failure": previous}, ensure_ascii=False)}]
        require(record["request_sha256"] == writing.whole.sha(canonical(messages)), "actual_original_write_request")
        writing.whole.editor.e.verify_response(job.root, name, record)
        previous = record["validation_error"]
        hashes[path.name] = writing.whole.sha(path.read_bytes())
        try:
            candidate = json.loads(record["raw_response"])
            jsonschema.validate(candidate, request["schema"])
            proposed, changes, invalid = normalize(candidate, list(context["assigned_points"]), working, inventory)
        except (ValueError, jsonschema.ValidationError) as exc:
            rejected.append({"attempt": number, "error_sha256": writing.whole.sha(str(exc).encode())})
        else:
            candidates.append((number, candidate, proposed, changes, invalid))
    require(bool(candidates), "complete_scope_safe_original_candidate")
    number, original, proposed, changes, invalid = candidates[0]
    receipt = {"policy": POLICY, "job": job.key, "stage": name, "input_sha256": request["binding"],
        "original_write_attempts": hashes, "selected_attempt": number,
        "selection": "earliest_complete_scope_safe_candidate_with_only_explicit_metadata_or_graph_defects",
        "rejected_candidates": rejected, "original_candidate_sha256": writing.whole.sha(canonical(original)),
        "citation_proposals": changes, "invalid_generated_diagrams": invalid,
        "prose_and_coverage_unchanged": True, "model_calls": 0, "old_budgets_reset": False,
        "semantic_acceptance": False, "citation_proposals_are_factual_evidence": False,
        "reader_publication_allowed": False, "recovery_module_sha256": writing.whole.sha(Path(__file__).read_bytes()),
        "helper_module_sha256": {m.__name__: writing.whole.sha(Path(m.__file__).read_bytes())
            for m in (quarantine, reviewable, plan_resume)}}
    return {"data": proposed, "data_sha256": writing.whole.sha(canonical(proposed)), "receipt": receipt}


def artifact_path(job, stage):
    return job.root / "draft-resume" / (stage + ".json")


def recover_stage(job, name, schema, context, packet, working, inventory):
    artifact = candidate_from_history(job, name, schema, context, packet, working, inventory)
    require(not (job.root / (name + ".json")).exists(), "failed_write_not_forged_as_model_cache")
    path = artifact_path(job, name)
    if path.exists():
        require(writing.whole.read(path) == artifact, "unchanged_explicit_recovery")
    else:
        prefix = name.removesuffix("-write")
        require(all(not any((job.root / "attempts").glob(prefix + suffix + "-[0-9][0-9].json"))
            for suffix in ("-review", "-repair", "-repair-review")), "original_remaining_roles_unused")
        path.parent.mkdir(exist_ok=True)
        atomic_json(path, artifact)
    backup = Path.home() / "Documents/CourseKnowledgeBackups/whole-draft-resume" / job.key
    for source in [path, Path(__file__), *[(job.root / "attempts" / n) for n in artifact["receipt"]["original_write_attempts"]]]:
        relative = source.relative_to(job.root) if source.is_relative_to(job.root) else Path("code") / source.name
        writing.whole.backup(source, backup / relative)
    return artifact


def saved_data(path):
    value = writing.whole.read(path)
    require(value["data_sha256"] == writing.whole.sha(canonical(value["data"])), "saved_plan_or_allocation_checksum")
    return value["data"]


def prepare(root, inventory_key, ledger_job, tokenizer):
    root = Path(root).resolve()
    inventory, working = writing.load_inputs(root, inventory_key, ledger_job, tokenizer)
    with schema_lease(root / "wiki/textbook-whole-record-evidence" / inventory_key / "authoring-worker.lock", timeout=0):
        job = BASE_JOB(writing.TextbookStore(root), inventory, working, tokenizer)
        status = writing.whole.read(job.root / "progress.json")
        require(not writing.identity_live(status) and status["state"] == "stopped"
            and status["stage"] == "section-0000-write"
            and status.get("reason_code") == "wiki_stage_attempt_budget_exhausted_section-0000-write", "stopped_original_first_unit")
        contract = writing.whole.read(root / "wiki/textbook-keyed-jobs" / ledger_job / "whole-source-authoring-execution.json")
        require(contract == {"inventory": inventory_key, "ledger_sha256": writing.whole.sha(canonical(working)), "job": job.key}, "original_execution_budget")
        plan, units = saved_data(job.root / "concept-plan.json"), saved_data(job.root / "writing-allocation.json")
        require(units == writing.writing_units(plan, working, inventory, job.tokenizer), "actual_original_writing_allocation")
        unit = units[0]
        context = {**writing.writing_context(unit["point_ids"], working, inventory), "chapter": unit["chapter"],
            "concept": unit["concept"], "article_outline": [plan["nodes"][key] for key in plan["chapters"]],
            "diagram_preferred": True, "previous_reading_section": None}
        packet = writing.composition.original_packet(unit["point_ids"], working, inventory)
        artifact = recover_stage(job, unit["id"] + "-write", writing.section_schema(unit["point_ids"], working), context, packet, working, inventory)
        receipt = artifact["receipt"]
        return {"state": "unverified_private_draft_recovery_prepared", "job": job.key, "stage": receipt["stage"],
            "selected_attempt": receipt["selected_attempt"], "paragraph_citation_proposals": len(receipt["citation_proposals"]),
            "invalid_diagrams_quarantined": len(receipt["invalid_generated_diagrams"]), "model_calls": 0,
            "old_budgets_reset": False, "semantic_acceptance": False, "reader_publication_allowed": False}


def worker(root, inventory_key, ledger_job, tokenizer):
    class ResumingJob(BASE_JOB):
        def __init__(self, store, inventory, working, tokenizer):
            super().__init__(store, inventory, working, tokenizer)
            self.resume_inventory, self.resume_working = inventory, working
            require(artifact_path(self, "section-0000-write").exists(), "prepared_initial_recovery_required")

        def stage(self, name, prompt, schema, context, validate, **kwargs):
            if plan_resume.artifact_path(self, name).exists():
                require(prompt == writing.author.GROUP_PROMPT and not kwargs, "planning_only_recovery")
                value = plan_resume.load_recovery(self, self.resume_working, name)
                validate(value)
                return value
            path = artifact_path(self, name)
            if not path.exists():
                try:
                    return super().stage(name, prompt, schema, context, validate, **kwargs)
                except ValueError as exc:
                    if not (name.endswith("-write") and str(exc) == "wiki_stage_attempt_budget_exhausted_" + name):
                        raise
            require(prompt == writing.author.WRITE_PROMPT and set(kwargs) == {"evidence_packet"}, "unchanged_write_scope")
            artifact = recover_stage(self, name, schema, context, kwargs["evidence_packet"], self.resume_working, self.resume_inventory)
            validate(artifact["data"])
            self.progress("reviewing_unverified_private_draft_next", name, citation_proposals_are_unverified=True,
                invalid_diagrams_quarantined=len(artifact["receipt"]["invalid_generated_diagrams"]))
            return artifact["data"]

    writing.AuthoringJob = ResumingJob
    try:
        return writing.run(root, inventory_key, ledger_job, tokenizer)
    finally:
        writing.AuthoringJob = BASE_JOB


def guard(root, inventory_key, ledger_job, tokenizer):
    from .course_archive_gpu import checked_sample
    from .course_gpu_guard import run as guarded

    root = Path(root).resolve()
    writing.TextbookStore(root).checkpoint()
    inventory, working = writing.load_inputs(root, inventory_key, ledger_job, tokenizer)
    job = BASE_JOB(writing.TextbookStore(root), inventory, working, tokenizer)
    require(artifact_path(job, "section-0000-write").exists(), "prepared_initial_recovery_required")
    with schema_lease(root / "wiki/whole-source-authoring-dispatch.lock", timeout=0):
        previous = root / "wiki-revision-thermal-guard.json"
        if previous.exists():
            old = writing.whole.read(previous)
            require(not writing.identity_live(old), "prior_guard_exited")
            writing.whole.backup(previous, root / "wiki/whole-draft-resume-guard-history" / (writing.whole.sha(previous.read_bytes()) + ".json"))
        guarded(root, [sys.executable, "-m", __spec__.name, "--root", str(root), "--inventory", inventory_key,
            "--ledger-job", ledger_job, "--tokenizer", str(Path(tokenizer).resolve()), "worker"],
            maximum_temperature=70, maximum_power=200, guard_name="wiki-revision-thermal-guard", sampler=lambda: checked_sample(root))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--ledger-job", required=True)
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("command", choices=["prepare", "worker", "guard"])
    args = parser.parse_args()
    try:
        result = {"prepare": prepare, "worker": worker, "guard": guard}[args.command](args.root, args.inventory, args.ledger_job, args.tokenizer)
        print(json.dumps(result or {"state": "guard_returned"}))
    except Exception as exc:
        print(json.dumps(writing.whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
