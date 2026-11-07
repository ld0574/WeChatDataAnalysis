"""Review explicit non-teaching mapping proposals using original unused roles.

Failed model outputs, prose, teaching judgments and role budgets are retained.
This adapter never invents an accepted model write cache or an exclusion verdict.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import copy
import json
from pathlib import Path
import re
import sys

import jsonschema

from . import course_textbook_whole_continuation as continuation
from .course_knowledge import atomic_json, schema_lease

writing, visible, draft = continuation.writing, continuation.visible, continuation.draft
POLICY = "explicit-non-teaching-empty-mapping-proposal-before-original-review-v1"
ERROR = "requires_private_composition_explicit_non_teaching_disposition"
ORIGINAL_JOB_TYPE, ORIGINAL_RECOVERY = continuation.job_type, visible.Recovery


def require(ok, code):
    if not ok:
        raise ValueError("requires_disposition_mapping_" + code)


def digest(value):
    return continuation.digest(value)


def bindings():
    return {m.__name__: writing.whole.sha(Path(m.__file__).read_bytes())
        for m in (continuation, visible, continuation.grammar, continuation.proposal)}


def propose(value, ids, working, inventory, scope):
    require(value.get("kind") != "non_teaching", "original_complete_teaching_candidate")
    candidate, changes = copy.deepcopy(value), []
    for key, row in candidate["coverage"].items():
        if key in working["pending_source_cards"] and row.get("teaching") is False and row["paragraphs"]:
            changes.append({"point": key, "original_paragraphs": list(row["paragraphs"]), "proposed_paragraphs": []})
            row["paragraphs"] = []
    require(bool(changes), "actual_explicit_false_mapping_conflict")
    citations = []
    try:
        with visible.packet_scope(scope):
            visible.ORIGINAL_VALIDATE(candidate, ids, working, inventory)
    except ValueError as exc:
        require(str(exc) == continuation.proposal.ERROR, "only_additional_primary_citation_metadata_defect")
        candidate, citations = continuation.proposal.propose(candidate, ids, working, inventory, scope)
    require({k: v for k, v in candidate.items() if k not in {"coverage", "paragraphs"}}
        == {k: v for k, v in value.items() if k not in {"coverage", "paragraphs"}}
        and [{k: v for k, v in p.items() if k != "citations"} for p in candidate["paragraphs"]]
        == [{k: v for k, v in p.items() if k != "citations"} for p in value["paragraphs"]], "actual_prose_unchanged")
    changed = {row["point"] for row in changes}
    require(all(({k: v for k, v in row.items() if k != "paragraphs"}
        == {k: v for k, v in value["coverage"][key].items() if k != "paragraphs"})
        and (key in changed or row == value["coverage"][key]) for key, row in candidate["coverage"].items()),
        "all_dispositions_and_other_mapping_fields_unchanged")
    return candidate, changes, citations


def artifact(job, name, schema, context, packet, working, inventory, proof, snapshot_path):
    require(re.fullmatch(r"section-[0-9]{4}-write", name) is not None, "original_write_role")
    ids = list(context["assigned_points"])
    scope = continuation.scope_for(ids, working, inventory, context, visible.ORIGINAL_PACKET(ids, working, inventory))
    expected_schema = continuation.grammar.bind(writing.section_schema(ids, working), scope["expanded"])
    require(packet == scope["expanded"] and schema == expected_schema, "actual_scoped_original_request")
    request = draft.capture_request(job, name, writing.author.WRITE_PROMPT, schema, context, packet)
    descriptor_path = job.root / (name + "-continuation-request.json")
    descriptor = writing.whole.read(descriptor_path)
    require(descriptor["actual_context_sha256"] == digest(context)
        and descriptor["actual_schema_sha256"] == digest(schema)
        and descriptor["actual_visible_packet_sha256"] == digest(packet), "actual_original_role_descriptor")
    paths = sorted((job.root / "attempts").glob(name + "-[0-9][0-9].json"))
    require([p.name for p in paths] == [name + "-01.json", name + "-02.json"], "exhausted_two_original_write_attempts")
    previous, hashes, candidates = "", {}, []
    for number, path in enumerate(paths, 1):
        row = writing.whole.read(path)
        messages = [{"role": "system", "content": request["prompt"]}, {"role": "user", "content": json.dumps(
            {**context, "previous_validation_failure": previous}, ensure_ascii=False)}]
        require(row["stage"] == name and row["attempt"] == number and row["input_sha256"] == request["binding"]
            and row["request_sha256"] == digest(messages) and row["model_digest"] == job.models["qwen3.5:9b"]
            and row.get("error_type") == "ValueError" and row.get("validation_error") == ERROR
            and not row.get("accepted_structure"), "actual_failed_request_and_original_budget")
        writing.whole.editor.e.verify_response(job.root, name, row)
        receipts = [writing.whole.read(p) for p in job.root.glob("response-*.json")]
        require(any(r["stage"] == name and r["request_sha256"] == row["request_sha256"]
            and r.get("schema_sha256") == digest(request["schema"])
            and r["response"]["message"]["content"] == row["raw_response"] and r["metrics"] == row["model_metrics"]
            for r in receipts), "actual_decoding_schema_and_gpu_receipt")
        value = json.loads(row["raw_response"])
        jsonschema.validate(value, request["schema"])
        proposed, changes, citations = propose(value, ids, working, inventory, scope)
        candidates.append((number, value, proposed, changes, citations))
        hashes[path.name] = writing.whole.sha(path.read_bytes())
        previous = row["validation_error"]
    number, original, proposed, changes, citations = candidates[0]
    receipt = {"policy": POLICY, "job": job.key, "stage": name, "input_sha256": request["binding"],
        "original_write_attempts": hashes, "selected_attempt": number,
        "selection": "earliest_complete_original_candidate_no_response_merging",
        "original_candidate_sha256": digest(original), "mapping_proposals": changes, "citation_proposals": citations,
        "prefix_snapshot_path": str(Path(snapshot_path).resolve()), "prefix_snapshot_sha256": digest(proof),
        "prefix_verified_units": proof["verified_units"], "descriptor_sha256": digest(descriptor),
        "module_sha256": writing.whole.sha(Path(__file__).read_bytes()), "helpers_sha256": bindings(),
        "prose_and_teaching_dispositions_unchanged": True, "mapping_exclusions_independently_verified": False,
        "model_calls": 0, "original_budgets_reset": False, "reader_publication_allowed": False}
    return {"data": proposed, "data_sha256": digest(proposed), "receipt": receipt}


class Recovery(ORIGINAL_RECOVERY):
    def candidate_from_history(self, job, name, schema, context, packet, working, inventory):
        path = draft.artifact_path(job, name)
        if not path.exists() or writing.whole.read(path)["receipt"].get("policy") != POLICY:
            return super().candidate_from_history(job, name, schema, context, packet, working, inventory)
        saved = writing.whole.read(path)
        snapshot_path = Path(saved["receipt"]["prefix_snapshot_path"])
        proof = writing.whole.read(snapshot_path)
        require(digest(proof) == saved["receipt"]["prefix_snapshot_sha256"], "unchanged_authenticated_prefix")
        require(all(writing.whole.sha((job.store.root / relative).read_bytes()) == sha
            for relative, sha in proof["files"].items()), "unchanged_prefix_files")
        actual = artifact(job, name, schema, context, packet, working, inventory, proof, snapshot_path)
        require(actual == saved and not (job.root / (name + ".json")).exists(), "explicit_proposal_without_fabricated_model_cache")
        return actual


def job_type(bundle, contract, registry, *, read_only):
    base = ORIGINAL_JOB_TYPE(bundle, contract, registry, read_only=read_only)
    lookup = ORIGINAL_JOB_TYPE(bundle, contract, registry, read_only=True)

    class MappingJob(base):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.mapping_targets = {}
            for path in (self.root / "draft-resume").glob("section-*-write.json"):
                value = writing.whole.read(path)
                if value["receipt"].get("policy") == POLICY:
                    require(value["receipt"]["module_sha256"] == writing.whole.sha(Path(__file__).read_bytes())
                        and value["receipt"]["helpers_sha256"] == bindings(), "same_bound_mapping_adapter")
                    self.mapping_targets[path.stem.removesuffix("-write")] = value
                    self.continuation_paths.add(path)
                    self.continuation_paths.add(Path(value["receipt"]["prefix_snapshot_path"]))

        def progress(self, state, stage, **extra):
            previous = self.finished_sections
            self.finished_sections = max([previous, *(v["receipt"]["prefix_verified_units"] for v in self.mapping_targets.values())])
            try:
                return super().progress(state, stage, **extra)
            finally:
                self.finished_sections = previous

        def stage(self, name, prompt, schema, context, validate, **kwargs):
            match = re.fullmatch(continuation.PATTERN, name)
            target = self.mapping_targets.get(match[1]) if match else None
            if target and match[2] == "write":
                value = lookup.stage(self, name, prompt, schema, context, validate, **kwargs)
                if target["receipt"]["citation_proposals"]:
                    self.future_proposals[name] = {"candidate": value, "citation_proposals": target["receipt"]["citation_proposals"]}
                return value
            if target:
                context = {**context, "original_failed_write_mapping_proposals": target["receipt"]["mapping_proposals"],
                    "mapping_proposals_are_factual_evidence": False}
            return super().stage(name, prompt, schema, context, validate, **kwargs)

    return MappingJob


@contextmanager
def hooks():
    previous = continuation.job_type, visible.Recovery
    continuation.job_type, visible.Recovery = job_type, Recovery
    try:
        yield
    finally:
        continuation.job_type, visible.Recovery = previous


def prior_sections(root, folder, units, proof):
    def retained(path):
        require(path.relative_to(root).as_posix() in {Path(p).as_posix() for p in proof["files"]}, "authenticated_prior_section_file")
        return writing.whole.read(path)

    sections = {}
    for unit in units[:proof["verified_units"]]:
        prefix = unit["id"]
        initial = retained(folder / (prefix + "-review.json"))["data"]
        name = prefix + ("-write" if all(writing.author.verdicts(initial)) else "-repair")
        derived = folder / (name + "-whole-future-citation-proposal.json")
        repair = folder / (prefix + continuation.proposal.RESULT)
        if derived.exists():
            candidate = retained(derived)["candidate"]
        elif (folder / (name + ".json")).exists():
            candidate = retained(folder / (name + ".json"))["data"]
        elif name.endswith("-repair") and repair.exists():
            candidate = retained(repair)["candidate"]
        else:
            candidate = retained(draft.artifact_path(type("JobPath", (), {"root": folder})(), name))["data"]
        if candidate.get("kind") != "non_teaching":
            sections[prefix] = {k: v for k, v in candidate.items() if k != "kind"}
    return sections


def prepare(root, key, parent_section, tokenizer):
    root = Path(root).resolve()
    result = continuation.reconstruct(root, key, parent_section, tokenizer, complete=False)
    proof, working, inventory = result["proof"], result["working"], result["inventory"]
    snapshot_path = continuation.snapshot.save(root, result)
    folder = root / "wiki/textbook-whole-authoring-jobs" / key
    status = writing.whole.read(folder / "progress.json")
    require(not writing.identity_live(status) and status["state"] == "stopped", "stopped_original_writer")
    units, plan = draft.saved_data(folder / "writing-allocation.json"), draft.saved_data(folder / "concept-plan.json")
    number = proof["verified_units"]
    require(number < len(units) and status["stage"] == units[number]["id"] + "-write", "exact_next_failed_write")
    with schema_lease(root / "wiki/textbook-whole-record-evidence" / digest(inventory) / "authoring-worker.lock", timeout=0):
        require(writing.whole.read(folder / "progress.json") == status, "stable_stopped_original_writer")
        context = visible.refs.unit_context(units[number], number, units, plan,
            prior_sections(root, folder, units, proof), working, inventory)
        ids = units[number]["point_ids"]
        scope = continuation.scope_for(ids, working, inventory, context, visible.ORIGINAL_PACKET(ids, working, inventory))
        job = visible.ORIGINAL_JOB(writing.TextbookStore(root), inventory, working, tokenizer)
        name = status["stage"]
        value = artifact(job, name, continuation.grammar.bind(writing.section_schema(ids, working), scope["expanded"]),
            context, scope["expanded"], working, inventory, proof, snapshot_path)
        path = draft.artifact_path(job, name)
        require(not path.exists() or writing.whole.read(path) == value, "immutable_mapping_proposal")
        if not path.exists():
            require(all(not any((folder / "attempts").glob(units[number]["id"] + suffix + "-[0-9][0-9].json"))
                for suffix in ("-review", "-repair", "-repair-review")), "original_followup_roles_unused")
            atomic_json(path, value)
        return {"state": "explicit_disposition_mapping_prepared_for_original_independent_review", "job": key,
            "section": units[number]["id"], "verified_prefix": number, "mapping_proposals": len(value["receipt"]["mapping_proposals"]),
            "citation_proposals": len(value["receipt"]["citation_proposals"]), "model_calls": 0, "reader_publication_allowed": False}


def guard(root, key, section, tokenizer):
    from .course_archive_gpu import checked_sample
    from .course_gpu_guard import run as guarded

    root = Path(root).resolve()
    writing.TextbookStore(root).checkpoint()
    require(not (root / "supervisor-stop.flag").exists(), "supervisor_stop_preserved")
    with schema_lease(root / "wiki/whole-source-authoring-dispatch.lock", timeout=0):
        path = root / "wiki-revision-thermal-guard.json"
        old = writing.whole.read(path)
        require(not writing.identity_live(old) and old["state"] == "worker_exited", "prior_guard_actually_exited")
        writing.whole.backup(path, root / "wiki/disposition-mapping-guard-history" / (writing.whole.sha(path.read_bytes()) + ".json"))
        guarded(root, [sys.executable, "-m", __spec__.name, "--root", str(root), "--job", key, "--section", section,
            "--tokenizer", str(Path(tokenizer).resolve()), "worker"], maximum_temperature=70, maximum_power=200,
            guard_name="wiki-revision-thermal-guard", sampler=lambda: checked_sample(root))


def coverage_guard(root, key, tokenizer, adapter, section):
    from .course_archive_gpu import checked_sample
    from .course_gpu_guard import run as guarded
    from . import course_textbook_whole_coverage as coverage

    root = Path(root).resolve()
    require(adapter == "continuation", "original_continued_coverage_adapter")
    coverage.checkpoint(writing.TextbookStore(root))
    with schema_lease(root / "wiki/whole-source-authoring-dispatch.lock", timeout=0):
        path = root / "wiki-revision-thermal-guard.json"
        old = writing.whole.read(path)
        require(not writing.identity_live(old) and old["state"] == "worker_exited" and old.get("returncode") == 0,
            "coverage_requires_clean_completed_writer_guard")
        writing.whole.backup(path, root / "wiki/disposition-mapping-coverage-guard-history" / (writing.whole.sha(path.read_bytes()) + ".json"))
        guarded(root, [sys.executable, "-m", __spec__.name, "--root", str(root), "--job", key, "--section", section,
            "--tokenizer", str(Path(tokenizer).resolve()), "coverage-worker"], maximum_temperature=70, maximum_power=200,
            guard_name="wiki-revision-thermal-guard", sampler=lambda: checked_sample(root))


def after_writer(root, key, tokenizer, section, writer, guard_identity):
    from . import course_textbook_whole_coverage as coverage

    root = Path(root).resolve()
    path = root / "wiki" / (key + "-whole-coverage-after-writer.json")
    if path.exists():
        old = writing.whole.read(path)
        require(not writing.identity_live(old), "previous_coverage_observer_actually_exited")
        writing.whole.backup(path, root / "wiki/disposition-mapping-observer-history" / (writing.whole.sha(path.read_bytes()) + ".json"))
    previous = coverage.guard
    coverage.guard = coverage_guard
    try:
        with hooks():
            return coverage.after_writer(root, key, tokenizer, "continuation", section, writer, guard_identity)
    finally:
        coverage.guard = previous


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("--section", default="section-0007")
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("--writer-pid", type=int)
    parser.add_argument("--writer-created", type=float)
    parser.add_argument("--guard-pid", type=int)
    parser.add_argument("--guard-created", type=float)
    parser.add_argument("command", choices=["prepare", "worker", "guard", "prefix", "complete", "coverage-after-writer",
        "coverage-worker", "verify-coverage"])
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = prepare(args.root, args.job, args.section, args.tokenizer)
        elif args.command == "guard":
            result = guard(args.root, args.job, args.section, args.tokenizer)
        elif args.command == "coverage-after-writer":
            require(all(value is not None for value in [args.writer_pid, args.writer_created, args.guard_pid, args.guard_created]),
                "exact_new_writer_and_guard_identities_required")
            result = after_writer(args.root, args.job, args.tokenizer, args.section,
                {"pid": args.writer_pid, "created": args.writer_created}, {"pid": args.guard_pid, "created": args.guard_created})
        else:
            with hooks():
                if args.command == "worker":
                    result = continuation.worker(args.root, args.job, args.section, args.tokenizer)
                elif args.command == "coverage-worker":
                    from . import course_textbook_whole_coverage as coverage

                    result = coverage.run(args.root, args.job, args.tokenizer, "continuation", args.section)
                elif args.command == "verify-coverage":
                    from . import course_textbook_whole_coverage_snapshot as checked

                    value = checked.reconstruct(args.root, args.job, args.tokenizer, "continuation", args.section)
                    result = {**value["summary"], "snapshot": str(checked.save(args.root, value))}
                else:
                    value = continuation.reconstruct(args.root, args.job, args.section, args.tokenizer, complete=args.command == "complete")
                    path = continuation.snapshot.save(args.root, value)
                    result = {**value["summary"], "snapshot": str(path)}
        print(json.dumps(result or {"state": "guard_returned"}))
    except Exception as exc:
        print(json.dumps(writing.whole.failure_status(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
