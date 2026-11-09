"""Recover a complete original 1..N plan without another planning attempt.

Failed responses remain failed. Later, still unused planning roles declare the
actual group count in their decoding grammar; all original role budgets remain.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import re
import sys
import time
from types import SimpleNamespace

import jsonschema

from . import course_textbook_unused_writer_dispatch as dispatch
from . import course_textbook_plan_cache as numbering
from . import course_textbook_whole_plan_resume as requests
from .course_knowledge import atomic_json, schema_lease

original, fresh, writing, whole = dispatch.original, dispatch.fresh, dispatch.writing, dispatch.whole
guard, reads, digest = dispatch.guard, dispatch.reads, dispatch.digest
POLICY = "original-one-based-plan-recovery-and-unused-count-bound-planning-v1"
DIRECTORY = "one-based-writer-continuations"
PLAN = r"concepts-[0-9]{2}-[0-9]{4}"


def require(ok, code):
    if not ok:
        raise ValueError("requires_one_based_writer_" + code)


def bindings():
    return {"original": original.bindings(), "dispatch": dispatch.bindings(),
        "modules": {Path(m.__file__).stem: whole.sha(Path(m.__file__).read_bytes())
            for m in (sys.modules[__name__], numbering, requests)}}


def folder_for(root, key):
    require(isinstance(key, str) and re.fullmatch(r"[a-f0-9]{32}", key), "writer_id")
    return Path(root).resolve() / "wiki" / DIRECTORY / key


def counted_schema(cards, *, final):
    schema = writing.author.grouping_schema(cards, final=final)
    maximum = schema["properties"]["groups"]["maxItems"]
    branches = []
    for count in range(1, maximum + 1):
        branch = copy.deepcopy(schema)
        branch["properties"] = {"group_count": {"type": "integer", "const": count}, **branch["properties"]}
        branch["required"] = ["group_count", *branch["required"]]
        branch["properties"]["groups"].update(minItems=count, maxItems=count)
        for row in branch["properties"]["assignments"]["properties"].values():
            row["properties"]["group"]["maximum"] = count - 1
        branches.append(branch)
    return {"oneOf": branches}


def compile_counted(raw, cards, *, final):
    jsonschema.validate(raw, counted_schema(cards, final=final))
    value = copy.deepcopy(raw)
    require(value.pop("group_count") == len(value["groups"]), "actual_group_count")
    return fresh.plan_candidate(value, cards, final=final)


def history(job, working, stage):
    cards, final, context = requests.context_for(working, stage)
    request = requests.transformed_request(job, stage, cards, final, context)
    paths = sorted((job.root / "attempts").glob(stage + "-*.json"))
    require([p.name for p in paths] == [stage + "-01.json", stage + "-02.json"], "original_exhausted_two_attempts")
    require(not (job.root / (stage + ".json")).exists(), "no_fabricated_accepted_plan_cache")
    candidates, hashes, rejected, previous = [], {}, [], ""
    receipts = [(p, whole.read(p)) for p in sorted(job.root.glob("response-*.json"))]
    for number, path in enumerate(paths, 1):
        record = whole.read(path)
        require(record["stage"] == stage and record["attempt"] == number
            and record["input_sha256"] == request["binding"]
            and record["model_digest"] == job.models["qwen3.5:9b"]
            and record.get("error_type") == "IndexError" and record.get("validation_error") == "IndexError"
            and not record.get("accepted_structure"), "actual_original_index_failure")
        messages = [{"role": "system", "content": request["prompt"] + job.range_notice},
            {"role": "user", "content": json.dumps({**context, "previous_validation_failure": previous}, ensure_ascii=False)}]
        require(record["request_sha256"] == digest(messages), "exact_original_messages")
        whole.editor.e.verify_response(job.root, stage, record)
        matched = [p for p, row in receipts if row["stage"] == stage
            and row["request_sha256"] == record["request_sha256"]
            and row.get("schema_sha256") == digest(request["schema"])
            and row["response"]["message"]["content"] == record["raw_response"]
            and row["metrics"] == record["model_metrics"]]
        require(len(matched) == 1 and record["model_metrics"]["model_digest"] == job.models["qwen3.5:9b"],
            "actual_unique_wire_schema_response_and_gpu_metrics")
        for selected in [path, *matched]:
            hashes[selected.relative_to(job.root).as_posix()] = whole.sha(selected.read_bytes())
        value = json.loads(record["raw_response"])
        try:
            candidate = numbering.normalize_one_based(value, cards, final=final)
        except (ValueError, jsonschema.ValidationError) as exc:
            rejected.append({"attempt": number, "error_sha256": whole.sha(str(exc).encode())})
        else:
            candidates.append((number, value, candidate))
        previous = "IndexError"
    require(bool(candidates), "complete_unambiguous_original_one_based_plan")
    selected, raw, candidate = candidates[0]
    return {"candidate": candidate, "candidate_sha256": digest(candidate), "receipt": {
        "policy": POLICY, "writer": job.key, "stage": stage, "input_sha256": request["binding"],
        "actual_schema_sha256": digest(request["schema"]), "actual_original_files": hashes,
        "selected_attempt": selected, "selection": "earliest_complete_original_response",
        "rejected_candidates": rejected, "assigned_inputs": len(cards), "groups": len(raw["groups"]),
        "original_response_sha256": digest(raw), "model_calls": 0, "semantic_memberships_changed": False,
        "teaching_text_changed": False, "original_budgets_reset": False,
        "reader_publication_allowed": False}}


def bound(root, key):
    root, prior_folder, dispatch_contract = dispatch.load(root, key)
    with reads.bounded_ledger_reads(writing):
        _, target, inventory, ledger, tokenizer, job, contract, extra, tail = original.writer_bound(root, dispatch_contract["batch"])
    require(job.key == key, "same_original_writer")
    return SimpleNamespace(root=root, prior_folder=prior_folder, dispatch_contract=dispatch_contract,
        target=target, inventory=inventory, ledger=ledger, tokenizer=tokenizer, job=job,
        contract=contract, extra=extra, tail=tail, folder=folder_for(root, key))


def prepare(root, key):
    b = bound(root, key)
    guard.checkpoint(b.root)
    with schema_lease(b.root / "wiki/full-course-batch-worker.lock", timeout=0):
        status = whole.read(b.job.root / "progress.json")
        old_guard = whole.read(b.prior_folder / "guard-progress.json")
        stage = status["stage"]
        require(status["state"] == "stopped" and not writing.identity_live(status)
            and status["reason_code"] == "wiki_stage_attempt_budget_exhausted_" + stage
            and old_guard["state"] == "worker_exited" and old_guard["returncode"] == 1
            and not writing.identity_live(old_guard), "actual_stopped_writer_and_failed_guard")
        working = fresh.continuation.draft.saved_data(b.job.root / "private-working-inputs.json")
        require(digest(working) == b.contract["working_sha256"], "actual_complete_original_working_inputs")
        used = {whole.read(p)["stage"] for p in (b.job.root / "attempts").glob("*.json")}
        require(used == {"concepts-00-0000", stage} and stage == "concepts-00-0001"
            and not list(b.job.root.glob("section-*.json")), "only_original_first_two_plans_attempted")
        job = original.writer_type(b.contract, b.extra, b.tail, read_only=True)(
            writing.TextbookStore(b.root), whole.load(b.root, b.inventory), working, b.tokenizer)
        cards, final, context = requests.context_for(working, "concepts-00-0000")
        job.stage("concepts-00-0000", writing.author.GROUP_PROMPT,
            writing.author.grouping_schema(cards, final=final), context, lambda row: writing.author.grouped(row, cards, final=final))
        artifact = history(job, working, stage)
        require(not b.folder.exists(), "single_explicit_continuation_registration")
        paths = [p for p in b.job.root.rglob("*.json") if p.name != "progress.json"]
        paths.extend([b.prior_folder / "guard-progress.json", b.prior_folder / "guard-failure.json"])
        contract = {"policy": POLICY, "writer": key, "code": bindings(), "batch": b.dispatch_contract["batch"],
            "stage": stage, "recovery_sha256": digest(artifact), "original_status": status,
            "initial_attempt_stages": sorted(used), "original_two_attempt_budget": 2,
            "additional_model_roles": 0, "original_budgets_reset": False,
            "unused_future_plans": "actual_count_and_zero_based_branch_scoped_indices",
            "reader_publication_allowed": False, "source_deletion_allowed": False,
            "original_files": {p.relative_to(b.root).as_posix(): whole.sha(p.read_bytes()) for p in paths}}
        fresh.retain(b.folder / "recovery.json", artifact)
        fresh.retain(b.folder / "execution.json", contract)
        (b.folder / "guard").mkdir()
        return {"state": "original_one_based_writer_continuation_prepared", "writer": key,
            **{k: artifact["receipt"][k] for k in ["stage", "selected_attempt", "assigned_inputs", "groups", "model_calls"]},
            "reader_publication_allowed": False}


def load(root, key):
    b = bound(root, key)
    b.execution = whole.read(b.folder / "execution.json")
    require(b.execution["policy"] == POLICY and b.execution["writer"] == key and b.execution["code"] == bindings(),
        "frozen_same_original_continuation")
    for name, expected in b.execution["original_files"].items():
        path = (b.root / name).resolve()
        require(path.is_relative_to(b.root) and whole.sha(path.read_bytes()) == expected, "original_history_unchanged")
    b.working = fresh.continuation.draft.saved_data(b.job.root / "private-working-inputs.json")
    artifact = history(b.job, b.working, b.execution["stage"])
    require(artifact == whole.read(b.folder / "recovery.json") and digest(artifact) == b.execution["recovery_sha256"],
        "same_authenticated_zero_inference_translation")
    b.recovery = artifact
    return b


def job_type(b, *, read_only):
    base = original.writer_type(b.contract, b.extra, b.tail, read_only=read_only)

    class RecoveredWriter(base):
        def stage(self, name, prompt, schema, context, validate, **kwargs):
            if name == b.execution["stage"]:
                cards, final, expected = requests.context_for(b.working, name)
                require(prompt == writing.author.GROUP_PROMPT and context == expected
                    and schema == writing.author.grouping_schema(cards, final=final) and not kwargs, "original_recovered_role")
                candidate = b.recovery["candidate"]
                validate(candidate)
                self.reader.paths.update([b.folder / "execution.json", b.folder / "recovery.json"])
                self.reader.paths.update(self.root / p for p in b.recovery["receipt"]["actual_original_files"])
                return candidate
            if re.fullmatch(PLAN, name) and name not in b.execution["initial_attempt_stages"]:
                require(prompt == writing.author.GROUP_PROMPT and not kwargs
                    and set(context) == {"items", "mode"}, "original_unused_future_planning_role")
                cards, final = context["items"], context["mode"] == "chapters"
                require(schema == writing.author.grouping_schema(cards, final=final), "same_original_grouping_inputs")
                actual_schema = counted_schema(cards, final=final)
                actual_context = {**context, "group_numbering": "Declare actual group_count, exactly that many groups; assignment indices start at zero."}
                request = {"policy": POLICY, "execution_sha256": digest(b.execution), "stage": name,
                    "original_schema_sha256": digest(schema), "actual_schema_sha256": digest(actual_schema),
                    "original_context_sha256": digest(context), "actual_context_sha256": digest(actual_context),
                    "original_budgets_reset": False}
                path = self.root / (name + "-count-bound-request.json")
                if not path.exists():
                    require(not read_only and not (self.root / (name + ".json")).exists()
                        and not list((self.root / "attempts").glob(name + "-*.json")), "count_grammar_before_first_original_attempt")
                fresh.retain(path, request, read_only=read_only)

                def check(raw):
                    validate(compile_counted(raw, cards, final=final))

                if not read_only and not (self.root / (name + ".json")).exists():
                    fresh.BASE_JOB.stage(self, name, prompt, actual_schema, actual_context, check)
                raw = self.reader.stage(name, prompt, actual_schema, actual_context, check)
                candidate = compile_counted(raw, cards, final=final)
                derivation = self.root / (name + "-count-bound-derived.json")
                fresh.retain(derivation, {"policy": POLICY, "actual_response_sha256": digest(raw),
                    "candidate": candidate, "candidate_sha256": digest(candidate),
                    "semantic_memberships_changed": False, "semantic_acceptance": False}, read_only=read_only)
                self.reader.paths.update([path, derivation, b.folder / "execution.json"])
                return candidate
            return super().stage(name, prompt, schema, context, validate, **kwargs)

    return RecoveredWriter


def worker(root, key):
    b = load(root, key)
    guard.checkpoint(b.root)
    require(whole.read(b.job.root / "progress.json") == b.execution["original_status"], "same_stopped_original_before_continuation")
    require(not (b.folder / "worker-started.json").exists(), "continuation_once_no_automatic_retry")
    fresh.retain(b.folder / "worker-started.json", {"writer": key, "started": time.time()})
    with reads.bounded_ledger_reads(writing), fresh.installed(job_type(b, read_only=False)):
        return writing.run(b.root, b.inventory, b.ledger, b.tokenizer)


def verify(root, key):
    b = load(root, key)
    state = whole.read(b.folder / "guard/guard-progress.json")
    require(state["state"] == "worker_exited" and state["returncode"] == 0 and not writing.identity_live(state),
        "actual_successful_continuation_exit")
    saved = fresh.job_type
    fresh.job_type = lambda value, *, read_only: job_type(b, read_only=read_only)
    try:
        with reads.bounded_ledger_reads(writing):
            result = fresh.reconstruct(b.root, b.inventory, b.ledger, b.tokenizer)
    finally:
        fresh.job_type = saved
    result["proof"].update(one_based_continuation_sha256=digest(b.execution), recovered_model_calls=0)
    return result


def run(root, key):
    b = load(root, key)
    with schema_lease(b.root / "wiki/full-course-batch-worker.lock", timeout=0):
        return guard.run(b.root, b.folder / "guard", [sys.executable, "-m", __spec__.name,
            "--root", str(b.root), "--writer", key, "worker"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--writer", required=True)
    parser.add_argument("command", choices=["prepare", "load", "run", "worker", "verify"])
    args = parser.parse_args()
    try:
        if args.command == "load":
            load(args.root, args.writer)
            result = {"state": "original_one_based_continuation_authenticated", "writer": args.writer, "model_calls": 0}
        elif args.command == "verify":
            result = verify(args.root, args.writer)
            path = fresh.snapshot.save(args.root, result)
            result = {**result["summary"], "snapshot": str(path), "recovered_model_calls": 0}
        else:
            result = globals()[args.command](args.root, args.writer)
        print(json.dumps(result))
    except BaseException as exc:
        failure = whole.failure_status(exc)
        folder = folder_for(args.root, args.writer)
        if folder.exists():
            atomic_json(folder / ("failure-" + str(time.time_ns()) + ".json"), failure)
        print(json.dumps(failure))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
