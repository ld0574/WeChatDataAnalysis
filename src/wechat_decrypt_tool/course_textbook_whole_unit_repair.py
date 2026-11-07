"""Use a stopped whole-record unit's unused original review and repair roles.

The unmodified failed draft is an untrusted audit input, never a successful
writer cache. One mandatory repair fixes actual structure before an independent
final audit. This finite operation does not assemble or publish an article.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import time

import jsonschema

from . import course_textbook_schema_scope as grammar
from . import course_textbook_structural_repair as structural
from . import course_textbook_whole_snapshot as snapshot
from .course_knowledge import atomic_json, schema_lease

visible, writing, draft = snapshot.visible, snapshot.writing, snapshot.draft
POLICY = "whole-record-unused-original-unit-roles-mandatory-structural-repair-v1"
SUFFIX = "-whole-original-role-repair-execution.json"


def require(ok, code):
    if not ok:
        raise ValueError("requires_whole_unit_repair_" + code)


def digest(value):
    return visible.digest(value)


def original_candidate(job, prefix, schema, context, packet, inventory):
    name = prefix + "-write"
    request = draft.capture_request(job, name, writing.author.WRITE_PROMPT, schema, context, packet)
    paths = sorted((job.root / "attempts").glob(name + "-[0-9][0-9].json"))
    require([path.name for path in paths] == [name + "-01.json", name + "-02.json"], "original_exhausted_two_attempt_write")
    previous, hashes, candidates = "", {}, []
    for number, path in enumerate(paths, 1):
        row = writing.whole.read(path)
        require(row["stage"] == name and row["attempt"] == number and row["input_sha256"] == request["binding"]
            and row["model_digest"] == job.models["qwen3.5:9b"] and row.get("error_type") == "ValueError"
            and not row.get("accepted_structure"), "actual_failed_write_identity")
        messages = [{"role": "system", "content": request["prompt"]}, {"role": "user", "content": json.dumps(
            {**context, "previous_validation_failure": previous}, ensure_ascii=False)}]
        require(row["request_sha256"] == digest(messages), "actual_failed_write_request")
        writing.whole.editor.e.verify_response(job.root, name, row)
        require(row["model_metrics"]["model_digest"] == job.models["qwen3.5:9b"], "actual_failed_response_model")
        previous = row["validation_error"]
        hashes[path.name] = writing.whole.sha(path.read_bytes())
        value = json.loads(row["raw_response"])
        try:
            jsonschema.validate(value, request["schema"])
            require(value.get("kind") != "non_teaching", "complete_original_teaching_candidate")
            rows = [value["overview"], value["takeaway"], value["quiz"], *value["paragraphs"], *value["diagrams"]]
            require(all(set(v["citations"]) <= set(inventory["sources"]) for v in rows), "known_inventory_references_only")
        except (ValueError, jsonschema.ValidationError):
            continue
        candidates.append((number, value))
    require(bool(candidates), "complete_original_candidate_exists")
    number, value = candidates[0]
    return value, {"original_write_attempts": hashes, "selected_attempt": number,
        "selection": "earliest_complete_unmodified_original_teaching_candidate_for_untrusted_audit",
        "original_write_input_sha256": request["binding"], "candidate_sha256": digest(value)}


def inputs(root, key, prefix, tokenizer, snapshot_path):
    root = Path(root).resolve()
    require(re.fullmatch(r"[a-f0-9]{32}", key) and re.fullmatch(r"section-[0-9]{4}", prefix), "original_job_and_unit")
    folder = root / "wiki/textbook-whole-authoring-jobs" / key
    status = writing.whole.read(folder / "progress.json")
    proof = writing.whole.read(snapshot_path)
    require(proof["policy"] == snapshot.POLICY and proof["job"] == key
        and proof["state"] == "completed_whole_manuscript_prefix_authenticated"
        and proof["adapter_sha256"] == writing.whole.sha(Path(snapshot.__file__).read_bytes()), "authenticated_prefix_snapshot")
    require(all((root / path).is_file() and writing.whole.sha((root / path).read_bytes()) == digest_
        for path, digest_ in proof["files"].items()), "unchanged_authenticated_prefix_files")
    inventory, working = writing.load_inputs(root, proof["inventory"], status["ledger_job"], tokenizer)
    job = visible.ORIGINAL_JOB(writing.TextbookStore(root), inventory, working, tokenizer)
    require(job.key == key and draft.saved_data(folder / "private-working-inputs.json") == working, "same_original_writer")
    plan, units = draft.saved_data(folder / "concept-plan.json"), draft.saved_data(folder / "writing-allocation.json")
    number = proof["verified_units"]
    require(0 < number < len(units) and units[number]["id"] == prefix, "exact_next_unit_after_verified_prefix")
    sections = {}
    for unit in units[:number]:
        stem = unit["id"]
        audit, _ = writing.whole.editor.e.checked_cache(folder, stem + "-review")
        stage = stem + ("-write" if all(writing.author.verdicts(audit)) else "-repair")
        if (folder / (stage + ".json")).exists():
            value, _ = writing.whole.editor.e.checked_cache(folder, stage)
        else:
            value = draft.saved_data(folder / "draft-resume" / (stage + ".json"))
        if value.get("kind") != "non_teaching":
            sections[stem] = {key_: row for key_, row in value.items() if key_ != "kind"}
    unit = units[number]
    context = visible.refs.unit_context(unit, number, units, plan, sections, working, inventory)
    original_packet = visible.ORIGINAL_PACKET(unit["point_ids"], working, inventory)
    scope = visible.visible_scope(unit["point_ids"], working, inventory, context, original_packet)
    candidate, history = original_candidate(job, prefix, writing.section_schema(unit["point_ids"], working), context, original_packet, inventory)
    defects = structural.defects(candidate, scope["expanded"])
    job.finished_sections, job.section_count = number, len(units)
    return job, working, inventory, context, scope, candidate, defects, history, proof


def execution(job, context, scope, candidate, defects, history, proof):
    return {"policy": POLICY, "job": job.key,
        **history, "prefix_snapshot_sha256": digest(proof), "actual_context_sha256": digest(context),
        "original_packet_sha256": digest(scope["original"]), "actual_visible_packet_sha256": digest(scope["expanded"]),
        "structural_defects": defects, "candidate_is_untrusted_audit_input": True,
        "original_candidate_changed": False, "original_stage_attempt_budget": 2,
        "semantic_repairs": 1, "mandatory_structural_repair_even_if_initial_factual_review_passes": True,
        "additional_stage_roles": 0, "old_budgets_reset": False, "reader_publication_allowed": False,
        "module_sha256": writing.whole.sha(Path(__file__).read_bytes()),
        "helpers_sha256": {m.__name__: writing.whole.sha(Path(m.__file__).read_bytes()) for m in (grammar, structural, snapshot, visible)}}


def prepare(root, key, prefix, tokenizer, snapshot_path):
    job, working, inventory, context, scope, candidate, defects, history, proof = inputs(root, key, prefix, tokenizer, snapshot_path)
    with schema_lease(Path(root).resolve() / "wiki/textbook-whole-record-evidence" / digest(inventory) / "authoring-worker.lock", timeout=0):
        status = writing.whole.read(job.root / "progress.json")
        require(status["state"] == "stopped" and status["stage"] == prefix + "-write" and not writing.identity_live(status),
            "stopped_original_write_before_preparation")
        contract = {**execution(job, context, scope, candidate, defects, history, proof), "section": prefix,
            "snapshot_path": str(Path(snapshot_path).resolve())}
        path = job.root / (prefix + SUFFIX)
        if path.exists():
            require(writing.whole.read(path) == contract, "immutable_original_role_contract")
        else:
            require(all(not any((job.root / "attempts").glob(prefix + suffix + "-*.json"))
                for suffix in ("-review", "-repair", "-repair-review")), "original_three_roles_unused")
            atomic_json(path, contract)
        backup = Path.home() / "Documents/CourseKnowledgeBackups/whole-original-unit-repair" / key / prefix / "inputs"
        paths = [path, Path(__file__), Path(grammar.__file__), Path(snapshot_path),
            job.root / "private-working-inputs.json", job.root / "concept-plan.json", job.root / "writing-allocation.json"]
        paths += [job.root / "attempts" / name for name in history["original_write_attempts"]]
        for source in paths:
            relative = source.relative_to(job.root) if source.is_relative_to(job.root) else Path("support") / source.name
            writing.whole.backup(source, backup / relative)
        return {"state": "original_unused_unit_roles_prepared", "job": key, "section": prefix,
            "structural_defects": len(defects), "original_sources": len(scope["original"]["sources"]),
            "actual_context_sources": len(scope["expanded"]["sources"]) - len(scope["original"]["sources"]),
            "model_calls": 0, "old_budgets_reset": False, "reader_publication_allowed": False}


def review_and_repair(job, prefix, working, inventory, context, scope, candidate, defects):
    ids, packet = list(context["assigned_points"]), scope["expanded"]
    common = {**context, "structural_defects": defects, "candidate_requires_original_role_repair": True}
    audit_schema = writing.review_schema(candidate, ids)
    initial = job.stage(prefix + "-review", writing.author.REVIEW_PROMPT, audit_schema, {**common, "candidate": candidate},
        lambda row: writing.author.validate_review(row, audit_schema), evidence_packet=packet)
    repair_schema = grammar.bind(writing.section_schema(ids, working), packet)

    def validate_repaired(row):
        jsonschema.validate(row, repair_schema)
        with visible.packet_scope(scope):
            visible.ORIGINAL_VALIDATE(row, ids, working, inventory)

    repaired = job.stage(prefix + "-repair", writing.author.REPAIR_PROMPT, repair_schema,
        {**common, "candidate": candidate, "review": initial}, validate_repaired, evidence_packet=packet)
    final_schema = writing.review_schema(repaired, ids)
    final = job.stage(prefix + "-repair-review", writing.author.REVIEW_PROMPT, final_schema,
        {**common, "candidate": repaired}, lambda row: writing.author.validate_review(row, final_schema), evidence_packet=packet)
    return repaired, initial, final


def worker(root, key, prefix, tokenizer):
    root = Path(root).resolve()
    folder = root / "wiki/textbook-whole-authoring-jobs" / key
    contract = writing.whole.read(folder / (prefix + SUFFIX))
    with schema_lease(root / "wiki/textbook-whole-record-evidence" / writing.whole.read(folder / "progress.json")["inventory"] / "authoring-worker.lock", timeout=0):
        job, working, inventory, context, scope, candidate, defects, history, proof = inputs(root, key, prefix, tokenizer, contract["snapshot_path"])
        require(contract == {**execution(job, context, scope, candidate, defects, history, proof), "section": prefix,
            "snapshot_path": contract["snapshot_path"]}, "original_prepared_role_contract")
        try:
            repaired, initial, final = review_and_repair(job, prefix, working, inventory, context, scope, candidate, defects)
            result = {"policy": POLICY, "job": key, "section": prefix, "execution_sha256": digest(contract),
                "original_candidate_sha256": digest(candidate), "candidate": repaired, "candidate_sha256": digest(repaired),
                "original_initial_review": initial, "actual_final_review": final, "factual_checks_passed": all(writing.author.verdicts(final)),
                "all_attempt_files": {p.name: writing.whole.sha(p.read_bytes()) for p in sorted((job.root / "attempts").glob(prefix + "-*.json"))},
                "semantic_repairs": 1, "whole_writer_resume_required": True, "reader_publication_allowed": False}
            target = job.root / (prefix + "-whole-original-role-repair-result.json")
            require(not target.exists() or writing.whole.read(target) == result, "immutable_completed_unit_result")
            if not target.exists():
                atomic_json(target, result)
            job.progress("original_unit_roles_finished_requires_whole_writer_resume", prefix + "-repair-review",
                original_unit_repair_factual_checks_passed=result["factual_checks_passed"])
            dest = Path.home() / "Documents/CourseKnowledgeBackups/whole-original-unit-repair" / key / prefix / "results"
            for source in [target, *[job.root / "attempts" / name for name in result["all_attempt_files"]],
                    *[p for p in job.root.glob("response-*.json") if writing.whole.read(p)["stage"].startswith(prefix)]]:
                writing.whole.backup(source, dest / source.relative_to(job.root))
            return {"state": "original_unit_roles_finished_requires_whole_writer_resume", "job": key, "section": prefix,
                "factual_checks_passed": result["factual_checks_passed"], "reader_publication_allowed": False}
        except Exception as exc:
            failure = writing.whole.failure_status(exc)
            directory = job.root / "whole-unit-repair-failures"
            directory.mkdir(exist_ok=True)
            atomic_json(directory / (str(time.time_ns()) + ".json"), failure)
            job.progress(failure["state"], job.current, **{k: v for k, v in failure.items() if k != "state"})
            raise


def guard(root, key, prefix, tokenizer):
    from .course_archive_gpu import checked_sample
    from .course_gpu_guard import run as guarded

    root = Path(root).resolve()
    writing.TextbookStore(root).checkpoint()
    with schema_lease(root / "wiki/whole-source-authoring-dispatch.lock", timeout=0):
        previous = root / "wiki-revision-thermal-guard.json"
        if previous.exists():
            old = writing.whole.read(previous)
            require(not writing.identity_live(old), "prior_guard_exited")
            writing.whole.backup(previous, root / "wiki/whole-unit-repair-guard-history" / (writing.whole.sha(previous.read_bytes()) + ".json"))
        guarded(root, [sys.executable, "-m", __spec__.name, "--root", str(root), "--job", key, "--section", prefix,
            "--tokenizer", str(Path(tokenizer).resolve()), "worker"], maximum_temperature=70, maximum_power=200,
            guard_name="wiki-revision-thermal-guard", sampler=lambda: checked_sample(root))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--job", required=True)
    p.add_argument("--section", required=True)
    p.add_argument("--tokenizer", type=Path, required=True)
    p.add_argument("--snapshot", type=Path)
    p.add_argument("command", choices=["prepare", "worker", "guard"])
    a = p.parse_args()
    try:
        if a.command == "prepare":
            require(a.snapshot is not None, "authenticated_prefix_snapshot_required")
            result = prepare(a.root, a.job, a.section, a.tokenizer, a.snapshot)
        else:
            result = {"worker": worker, "guard": guard}[a.command](a.root, a.job, a.section, a.tokenizer)
        print(json.dumps(result or {"state": "guard_returned"}))
    except Exception as exc:
        print(json.dumps(writing.whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
