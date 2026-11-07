"""Resume a stopped complete-record article through a separately proved plan repair.

Only unused concept groups and tied order numbers may change. Failed responses
remain failed; no accepted-model cache is fabricated and no attempt is reset.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import re
import sys

import jsonschema

from . import course_textbook_authoring as author
from . import course_textbook_whole_authoring as writing
from .course_knowledge import atomic_json, schema_lease
from .course_library import canonical

POLICY = "whole-record-unused-plan-group-stable-order-recovery-v1"
ERROR = "missing_textbook_nonempty_ordered_concept_group"
BASE_JOB = writing.AuthoringJob


def require(ok, reason):
    if not ok:
        raise ValueError("requires_whole_plan_resume_" + reason)


def normalize(value, cards, *, final=False):
    """Discard no input and move no input to a different semantic group."""
    jsonschema.validate(value, author.grouping_schema(cards, final=final))
    require(set(value["assignments"]) == set(cards), "all_original_inputs")
    groups = value["groups"]
    require(all(group["heading"].strip() and group["purpose"].strip() for group in groups), "nonempty_group_text")
    require(len({g["heading"].strip().casefold() for g in groups}) == len(groups), "unique_headings")
    used = sorted({row["group"] for row in value["assignments"].values()})
    require(set(used) <= set(range(len(groups))), "all_group_indices_in_range")
    removed = sorted(set(range(len(groups))) - set(used))
    require(bool(removed), "actual_unused_group")
    result = copy.deepcopy(value)
    result["groups"] = [copy.deepcopy(groups[index]) for index in used]
    mapping = {old: new for new, old in enumerate(used)}
    input_order = {key: index for index, key in enumerate(cards)}
    for old, new in mapping.items():
        children = [key for key, row in value["assignments"].items() if row["group"] == old]
        ranks = [value["assignments"][key]["order"] for key in children]
        ranked = sorted(children, key=lambda key: (value["assignments"][key]["order"], input_order[key]))
        for rank, key in enumerate(ranked):
            result["assignments"][key]["group"] = new
            if len(ranks) != len(set(ranks)):
                result["assignments"][key]["order"] = rank
    author.grouped(result, cards, final=final)
    require(result["title"] == value["title"], "original_title")
    for key, row in result["assignments"].items():
        require(result["groups"][row["group"]] == groups[value["assignments"][key]["group"]], "original_group_meaning")
    return result


def context_for(working, stage):
    # Higher-level plans require replaying their predecessor tree; this rule is
    # deliberately limited to original-point grouping, not arbitrary tree edits.
    match = re.fullmatch(r"concepts-00-([0-9]{4})", stage)
    require(match is not None, "original_point_grouping_stage")
    cards = {key: {field: point[field] for field in ("label", "teaching", "category")}
        for key, point in working["points"].items()}
    batches = author.chunks(cards, writing.PLAN_CHARACTERS)
    index = int(match.group(1))
    require(index < len(batches), "existing_partition")
    batch, final = batches[index], len(batches) == 1
    return batch, final, {"items": batch, "mode": "chapters" if final else "concepts"}


def transformed_request(job, stage, cards, final, context):
    """Capture the original implementation's prompt before it accesses a stage."""
    class Captured(BaseException):
        pass

    captured = {}
    original = job.stage_input

    def capture(prompt, schema, supplied, **kwargs):
        result = original(prompt, schema, supplied, **kwargs)
        captured.update(prompt=prompt, schema=result[0], binding=result[1])
        raise Captured

    job.stage_input = capture
    try:
        BASE_JOB.stage(job, stage, author.GROUP_PROMPT, author.grouping_schema(cards, final=final),
            context, lambda value: author.grouped(value, cards, final=final))
    except Captured:
        return captured
    finally:
        del job.stage_input
    raise AssertionError("original stage did not expose its request")


def candidate_from_history(job, working, stage):
    cards, final, context = context_for(working, stage)
    request = transformed_request(job, stage, cards, final, context)
    paths = sorted((job.root / "attempts").glob(stage + "-*.json"))
    require([p.name for p in paths] == [stage + "-01.json", stage + "-02.json"], "original_two_attempt_budget")
    candidates, hashes, rejected, previous = [], {}, [], ""
    for number, path in enumerate(paths, 1):
        raw = path.read_bytes()
        record = json.loads(raw)
        require(record["stage"] == stage and record["attempt"] == number
            and record["input_sha256"] == request["binding"]
            and record["model_digest"] == job.models["qwen3.5:9b"]
            and record["error_type"] == "ValueError" and record["validation_error"] == ERROR
            and not record.get("accepted_structure"), "original_failed_structural_request")
        messages = [{"role": "system", "content": request["prompt"]},
            {"role": "user", "content": json.dumps({**context, "previous_validation_failure": previous}, ensure_ascii=False)}]
        require(record["request_sha256"] == writing.whole.sha(canonical(messages)), "exact_original_request")
        writing.whole.editor.e.verify_response(job.root, stage, record)
        require(record["model_metrics"]["model_digest"] == job.models["qwen3.5:9b"], "actual_model_revision")
        previous = ERROR
        hashes[path.name] = writing.whole.sha(raw)
        value = json.loads(record["raw_response"])
        try:
            repaired = normalize(value, cards, final=final)
        except (ValueError, jsonschema.ValidationError) as exc:
            rejected.append({"attempt": number, "error_sha256": writing.whole.sha(str(exc).encode())})
        else:
            candidates.append((number, value, repaired))
    require(bool(candidates), "complete_original_candidate")
    selected, original, repaired = candidates[0]
    changes = {key: {"before": original["assignments"][key], "after": row}
        for key, row in repaired["assignments"].items() if row != original["assignments"][key]}
    metadata = {"policy": POLICY, "stage": stage, "job": job.key, "input_sha256": request["binding"],
        "original_attempts": hashes, "selected_attempt": selected, "candidate_selection": "earliest_complete_original_response",
        "rejected_candidates": rejected, "unused_groups_removed": len(original["groups"]) - len(repaired["groups"]),
        "assignment_number_changes": changes, "assigned_inputs": len(cards), "semantic_group_memberships_changed": False,
        "teaching_text_changed": False, "model_calls": 0, "old_budgets_reset": False,
        "semantic_acceptance": False, "reader_publication_allowed": False,
        "recovery_module_sha256": writing.whole.sha(Path(__file__).read_bytes())}
    return repaired, metadata


def artifact_path(job, stage):
    return job.root / "structural-resume" / (stage + ".json")


def load_recovery(job, working, stage):
    candidate, receipt = candidate_from_history(job, working, stage)
    expected = {"data": candidate, "data_sha256": writing.whole.sha(canonical(candidate)), "receipt": receipt}
    require(writing.whole.read(artifact_path(job, stage)) == expected, "unchanged_explicit_recovery")
    require(not (job.root / (stage + ".json")).exists(), "failed_stage_not_forged_as_model_cache")
    return candidate


def prepare(root, inventory_key, ledger_job, tokenizer):
    root = Path(root).resolve()
    inventory, working = writing.load_inputs(root, inventory_key, ledger_job, tokenizer)
    status = writing.whole.read(root / "wiki/whole-source-authoring-current.json")
    require(status["state"] == "stopped" and not writing.identity_live(status), "stopped_original_worker")
    stage = status["stage"]
    require(status.get("reason_code") == "wiki_stage_attempt_budget_exhausted_" + stage, "actual_exhausted_stage")
    with schema_lease(root / "wiki/textbook-whole-record-evidence" / inventory_key / "authoring-worker.lock", timeout=0):
        job = BASE_JOB(writing.TextbookStore(root), inventory, working, tokenizer)
        require(job.key == status["job"], "original_bound_job")
        contract = writing.whole.read(root / "wiki/textbook-keyed-jobs" / ledger_job / "whole-source-authoring-execution.json")
        require(contract == {"inventory": inventory_key, "ledger_sha256": writing.whole.sha(canonical(working)), "job": job.key}, "original_execution_contract")
        candidate, receipt = candidate_from_history(job, working, stage)
        artifact = {"data": candidate, "data_sha256": writing.whole.sha(canonical(candidate)), "receipt": receipt}
        path = artifact_path(job, stage)
        path.parent.mkdir(exist_ok=True)
        require(not path.exists() or writing.whole.read(path) == artifact, "immutable_recovery")
        if not path.exists():
            atomic_json(path, artifact)
        destination = Path.home() / "Documents/CourseKnowledgeBackups/whole-plan-resume" / job.key
        for source in [*job.root.rglob("*.json"), Path(__file__)]:
            relative = source.relative_to(job.root) if source.is_relative_to(job.root) else Path("code") / source.name
            writing.whole.backup(source, destination / relative)
        return {"state": "whole_plan_structural_recovery_prepared", "job": job.key, "stage": stage,
            "assigned_inputs": receipt["assigned_inputs"], "unused_groups_removed": receipt["unused_groups_removed"],
            "selected_attempt": receipt["selected_attempt"], "model_calls": 0, "old_budgets_reset": False,
            "reader_publication_allowed": False, "recovery_sha256": writing.whole.sha(path.read_bytes())}


def worker(root, inventory_key, ledger_job, tokenizer):
    class ResumingJob(BASE_JOB):
        def __init__(self, store, inventory, working, tokenizer):
            super().__init__(store, inventory, working, tokenizer)
            self.resume_working = working

        def stage(self, name, prompt, schema, context, validate, **kwargs):
            if artifact_path(self, name).exists():
                require(prompt == author.GROUP_PROMPT and not kwargs, "planning_only_recovery")
                value = load_recovery(self, self.resume_working, name)
                validate(value)
                self.progress("resuming_original_concept_plan", name, zero_model_recovery_used=True)
                return value
            return super().stage(name, prompt, schema, context, validate, **kwargs)

    writing.AuthoringJob = ResumingJob
    try:
        return writing.run(root, inventory_key, ledger_job, tokenizer)
    finally:
        writing.AuthoringJob = BASE_JOB


def guard(root, inventory_key, ledger_job, tokenizer):
    from .course_archive_gpu import checked_sample
    from .course_gpu_guard import run as guarded

    root = Path(root).resolve()
    inventory, working = writing.load_inputs(root, inventory_key, ledger_job, tokenizer)
    job = BASE_JOB(writing.TextbookStore(root), inventory, working, tokenizer)
    artifacts = list((job.root / "structural-resume").glob("concepts-*.json"))
    require(bool(artifacts), "prepared_recovery_exists")
    for path in artifacts:
        load_recovery(job, working, path.stem)
    with schema_lease(root / "wiki/whole-source-authoring-dispatch.lock", timeout=0):
        previous = root / "wiki-revision-thermal-guard.json"
        if previous.exists():
            old = writing.whole.read(previous)
            require(not writing.identity_live(old), "prior_guard_exited")
            writing.whole.backup(previous, root / "wiki/whole-plan-resume-guard-history" / (writing.whole.sha(previous.read_bytes()) + ".json"))
        guarded(root, [sys.executable, "-m", __spec__.name, "--root", str(root),
            "--inventory", inventory_key, "--ledger-job", ledger_job, "--tokenizer", str(Path(tokenizer).resolve()), "worker"],
            maximum_temperature=70, maximum_power=200, guard_name="wiki-revision-thermal-guard",
            sampler=lambda: checked_sample(root))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--ledger-job", required=True)
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("command", choices=["prepare", "worker", "guard"])
    args = parser.parse_args()
    function = {"prepare": prepare, "worker": worker, "guard": guard}[args.command]
    try:
        result = function(args.root, args.inventory, args.ledger_job, args.tokenizer)
        print(json.dumps(result or {"state": "guard_returned"}))
    except Exception as exc:
        print(json.dumps(writing.whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
