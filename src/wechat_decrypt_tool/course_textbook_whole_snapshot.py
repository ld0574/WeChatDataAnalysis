"""Authenticate whole-record manuscripts without inference or publication.

Completed prefixes may be inspected while their exact writer remains healthy.
A complete snapshot additionally reassembles the article, exclusions, reading
reviews and every unresolved issue from the original requests and responses.
"""
from __future__ import annotations

import argparse
from contextlib import nullcontext
import copy
import json
from pathlib import Path
import re

from . import course_textbook_visible_context as visible
from .course_knowledge import atomic_json, schema_lease

draft, writing, refs = visible.draft, visible.writing, visible.refs
POLICY = "authenticated-whole-record-manuscript-snapshot-v1"


def require(ok, code):
    if not ok:
        raise ValueError("requires_whole_snapshot_" + code)


def sha(value):
    return visible.digest(value)


class ReadOnlyStages:
    def __init__(self, job, inventory, working, registry):
        self.job, self.inventory, self.working, self.registry = job, inventory, working, registry
        self.paths, self.stages, self.recoveries = set(), [], []

    def saved(self, name):
        path = self.job.root / (name + ".json")
        self.paths.add(path)
        return draft.saved_data(path)

    def authenticate_cache(self, name, prompt, schema, context, packet):
        request = draft.capture_request(self.job, name, prompt, schema, context, packet)
        value, binding = writing.whole.editor.e.checked_cache(self.job.root, name)
        require(binding == request["binding"], "original_cached_input")
        model = "course-defiant:q4" if prompt in {writing.author.REVIEW_PROMPT, writing.author.READ_PROMPT} else "qwen3.5:9b"
        paths = sorted((self.job.root / "attempts").glob(name + "-[0-9][0-9].json"))
        require(0 < len(paths) <= self.job.max_attempts, "original_attempt_budget")
        previous = ""
        for number, path in enumerate(paths, 1):
            record = writing.whole.read(path)
            require(path.name == f"{name}-{number:02d}.json" and record["stage"] == name
                and record["attempt"] == number and record["input_sha256"] == binding
                and record["model_digest"] == self.job.models[model], "actual_stage_attempt_identity")
            messages = [{"role": "system", "content": request["prompt"]}, {"role": "user", "content": json.dumps(
                {**context, "previous_validation_failure": previous}, ensure_ascii=False)}]
            require(record["request_sha256"] == sha(messages), "actual_complete_request")
            if "raw_response" in record:
                writing.whole.editor.e.verify_response(self.job.root, name, record)
                require(record["model_metrics"]["model_digest"] == self.job.models[model], "actual_response_model")
            else:
                require(not record.get("accepted_structure") and bool(record.get("error_type")), "recorded_failed_attempt")
            accepted = bool(record.get("accepted_structure"))
            require(accepted == (number == len(paths)), "one_terminal_accepted_attempt")
            if not accepted:
                previous = record["validation_error"]
            self.paths.add(path)
        proof_path = self.job.root / (name + ".proof.json")
        require(writing.whole.read(proof_path) == {"stage": name, "model": model,
            "model_digest": self.job.models[model], "input_sha256": binding, "data_sha256": sha(value)},
            "actual_frozen_stage_proof")
        self.paths.update([proof_path, self.job.root / (name + ".json")])
        return value

    def stage(self, name, prompt, schema, context, validate, **kwargs):
        plan_path, draft_path = draft.plan_resume.artifact_path(self.job, name), draft.artifact_path(self.job, name)
        if plan_path.exists():
            require(prompt == writing.author.GROUP_PROMPT and not kwargs, "planning_recovery_role")
            value = draft.plan_resume.load_recovery(self.job, self.working, name)
            receipt = writing.whole.read(plan_path)["receipt"]
            self.paths.add(plan_path)
            self.paths.update(self.job.root / "attempts" / key for key in receipt["original_attempts"])
            self.recoveries.append(name)
        elif draft_path.exists():
            require(prompt == writing.author.WRITE_PROMPT and set(kwargs) == {"evidence_packet"}, "draft_recovery_role")
            artifact = self.registry.candidate_from_history(self.job, name, schema, context, kwargs["evidence_packet"],
                self.working, self.inventory)
            require(writing.whole.read(draft_path) == artifact and not (self.job.root / (name + ".json")).exists(),
                "unchanged_explicit_recovery_without_model_cache")
            value = artifact["data"]
            self.paths.add(draft_path)
            self.paths.update(self.job.root / "attempts" / key for key in artifact["receipt"]["original_write_attempts"])
            self.recoveries.append(name)
        else:
            require((self.job.root / (name + ".json")).is_file(), "required_existing_stage_no_generation")
            value = self.authenticate_cache(name, prompt, schema, context, kwargs.get("evidence_packet", self.job.packet))
        validate(value)
        self.stages.append(name)
        return value


def replay_units(reader, units, plan, count):
    sections, exclusions, issues = {}, {}, []
    job, working, inventory = reader.job, reader.working, reader.inventory
    for number, unit in enumerate(units[:count]):
        ids, prefix = unit["point_ids"], unit["id"]
        context = refs.unit_context(unit, number, units, plan, sections, working, inventory)
        packet, schema = visible.ORIGINAL_PACKET(ids, working, inventory), writing.section_schema(ids, working)
        candidate = job.stage(prefix + "-write", writing.author.WRITE_PROMPT, schema, context,
            lambda row: writing.validate_section(row, ids, working, inventory), evidence_packet=packet)

        def review(value, suffix):
            audit_schema = writing.review_schema(value, ids)
            return job.stage(prefix + suffix, writing.author.REVIEW_PROMPT, audit_schema, {**context, "candidate": value},
                lambda row: writing.author.validate_review(row, audit_schema), evidence_packet=packet)

        audit = review(candidate, "-review")
        if not all(writing.author.verdicts(audit)):
            candidate = job.stage(prefix + "-repair", writing.author.REPAIR_PROMPT, schema,
                {**context, "candidate": candidate, "review": audit},
                lambda row: writing.validate_section(row, ids, working, inventory), evidence_packet=packet)
            audit = review(candidate, "-repair-review")
        if not all(writing.author.verdicts(audit)):
            issues.append({"kind": "section_semantic_review", "section": prefix, "review": audit})
        if candidate.get("kind") == "non_teaching":
            exclusions[prefix] = {"candidate": candidate, "audit": audit}
        else:
            sections[prefix] = {key: value for key, value in candidate.items() if key != "kind"}
    return sections, exclusions, issues


def assemble(reader, units, plan, sections, exclusions, issues):
    job, inventory, working = reader.job, reader.inventory, reader.working
    active = [unit for unit in units if unit["id"] in sections]
    require(bool(active), "actual_teaching_content")
    for number, block in enumerate(writing.reading_blocks(active, sections, job.tokenizer)):
        audit = job.stage(f"reading-{number:04d}", writing.author.READ_PROMPT, writing.author.READ_REVIEW,
            {"complete_reading_sections": block}, lambda row: writing.author.validate_review(row, writing.author.READ_REVIEW))
        if not all(writing.author.verdicts(audit)):
            issues.append({"kind": "whole_reading_review", "block": number, "review": audit})
    excluded_keys = {key for unit in units if unit["id"] in exclusions for key in unit["point_ids"]}
    article_ledger = {**working, "points": {key: row for key, row in working["points"].items() if key not in excluded_keys}}
    private = writing.author.assemble(inventory, article_ledger, plan, active, sections)
    require(set(private["private_coverage"]) | excluded_keys == set(working["points"]), "all_targets_accounted_for")
    private.update(private_non_teaching_units=exclusions, parent_ledger_sha256=working["parent_ledger_sha256"],
        inherited_ledger_issues=working["inherited_issues"], quarantined_point_keys=working["quarantined_point_keys"],
        writing_budget_refreshed=False)
    issues = writing.final_issues(inventory, private, issues)
    require(reader.saved("private-article") == private and reader.saved("quality-issues") == issues, "actual_full_private_assembly")
    book = writing.reading_book(private)
    result = {"state": "conceptual_article_requires_actual_source_and_editorial_review", "job": job.key,
        "inventory": sha(inventory), "ledger_job": working["job"], "chapters": len(book["chapters"]), "sections": len(active),
        "knowledge_points": working["confirmed_point_count"], "pending_original_tasks": len(working["pending_source_cards"]),
        "remaining_reviews": len(issues), "article_sha256": sha(book), "reader_article_published": False,
        "source_deletion_allowed": False, "goal_complete": False}
    require(writing.whole.read(job.root / "result.json") == result, "actual_unpublished_result")
    html_bytes = writing.standalone_html(book).encode("utf-8")
    require((job.root / "private-reading.html").read_bytes() in {html_bytes, html_bytes.replace(b"\n", b"\r\n")},
        "exact_private_html")
    reader.paths.update([job.root / "result.json", job.root / "private-reading.html"])
    return private, book, issues


def reconstruct(root, key, tokenizer, *, complete):
    root = Path(root).resolve()
    require(re.fullmatch(r"[a-f0-9]{32}", key) is not None, "authoring_job_id")
    folder = root / "wiki/textbook-whole-authoring-jobs" / key
    status = writing.whole.read(folder / "progress.json")
    if complete:
        require(status["stage"] == "private_article_assembled"
            and status["state"] == "conceptual_article_requires_actual_source_and_editorial_review"
            and not writing.identity_live(status), "assembled_exited_writer")
    lease = schema_lease(root / "wiki/textbook-whole-record-evidence" / status["inventory"] / "authoring-worker.lock", timeout=0) if complete else nullcontext()
    with lease:
        return reconstruct_bound(root, key, tokenizer, status, complete=complete)


def reconstruct_bound(root, key, tokenizer, status, *, complete):
    folder = root / "wiki/textbook-whole-authoring-jobs" / key
    require(not complete or writing.whole.read(folder / "progress.json") == status, "stable_finished_writer")
    inventory, working = writing.load_inputs(root, status["inventory"], status["ledger_job"], tokenizer)
    job = visible.ORIGINAL_JOB(writing.TextbookStore(root), inventory, working, tokenizer)
    require(job.key == key == status["job"] and folder == job.root, "original_authoring_identity")
    contract = root / "wiki/textbook-keyed-jobs" / working["job"] / "whole-source-authoring-execution.json"
    require(writing.whole.read(contract) == {"inventory": sha(inventory), "ledger_sha256": sha(working), "job": key},
        "original_authoring_budget")
    registry = visible.Recovery([])
    reader = ReadOnlyStages(job, inventory, working, registry)
    reader.paths.update([contract, folder / "manifest.json"])
    require(reader.saved("private-working-inputs") == working, "original_working_inputs")
    job.stage = reader.stage
    with registry.hooks():
        plan = writing.organize(job, working)
        units = writing.writing_units(plan, working, inventory, job.tokenizer)
        require(reader.saved("concept-plan") == plan and reader.saved("writing-allocation") == units, "original_plan_allocation")
        count = len(units) if complete else status["completed_sections"]
        require(type(count) is int and 0 < count <= len(units), "actual_completed_prefix")
        sections, exclusions, issues = replay_units(reader, units, plan, count)
        private, book = None, None
        if complete:
            private, book, issues = assemble(reader, units, plan, sections, exclusions, issues)
    for path in folder.glob("response-*.json"):
        if writing.whole.read(path)["stage"] in reader.stages:
            reader.paths.add(path)
    files = {str(path.relative_to(root)): writing.whole.sha(path.read_bytes()) for path in sorted(reader.paths)}
    summary = {"state": "whole_manuscript_authenticated_for_coverage_review" if complete else "completed_whole_manuscript_prefix_authenticated",
        "job": key, "inventory": sha(inventory), "verified_units": count, "total_units": len(units),
        "retained_teaching_sections": len(sections), "non_teaching_units": len(exclusions),
        "unresolved_issues": len(issues), "actual_model_stages": len(reader.stages) - len(reader.recoveries),
        "explicit_recoveries": len(reader.recoveries), "model_calls": 0,
        "original_budgets_reset": False, "semantic_acceptance": False, "reader_publication_allowed": False}
    proof = {"policy": POLICY, **summary, "files": files,
        "adapter_sha256": writing.whole.sha(Path(__file__).read_bytes()),
        "helper_sha256": {m.__name__: writing.whole.sha(Path(m.__file__).read_bytes()) for m in (visible, refs, draft)},
        "article_sha256": sha(book) if book else None, "private_article_sha256": sha(private) if private else None,
        "scope": "all_whole_record_sources_and_original_teaching_obligations_still_require_coverage_review"}
    return {"summary": summary, "proof": proof, "inventory": inventory, "working": working,
        "private": private, "book": book, "issues": copy.deepcopy(issues)}


def save(root, snapshot):
    proof = snapshot["proof"]
    path = Path(root).resolve() / "wiki/whole-manuscript-snapshots" / (sha(proof) + ".json")
    require(not path.exists() or writing.whole.read(path) == proof, "immutable_snapshot")
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(path, proof)
    return path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--job", required=True)
    p.add_argument("--tokenizer", type=Path, required=True)
    p.add_argument("command", choices=["prefix", "complete"])
    a = p.parse_args()
    try:
        result = reconstruct(a.root, a.job, a.tokenizer, complete=a.command == "complete")
        path = save(a.root, result)
        print(json.dumps({**result["summary"], "snapshot": str(path)}))
    except Exception as exc:
        print(json.dumps(writing.whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
