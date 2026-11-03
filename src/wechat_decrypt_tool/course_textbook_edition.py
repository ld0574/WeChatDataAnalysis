"""Merge checked additions with exact factual edits, then review that edition.

No earlier article, failure or budget is overwritten. Unchanged paragraph
coverage is transferred by section and exact bytes, not positional IDs alone.
This produces an explicitly unfinished reading export, not a publication.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import time
from pathlib import Path

import jsonschema
import psutil

from . import course_textbook_gap_additions as additions
from . import course_textbook_gap_additions_continue as continuation
from . import course_textbook_precision_edit as precision
from . import course_textbook_unmapped_additions as unmapped
from .course_knowledge import atomic_json, schema_lease
from .course_library import canonical
from .course_textbook_evidence import sha
from .course_textbook_gap_preview import reading_html
from .course_textbook_ledger import TextbookStore
from .course_wiki import object_schema
from .course_wiki_writer import DraftJob

POLICY = "exact-checked-additions-and-factual-edits-edition-v1"
REVIEW = additions.final.PROMPT + """
article 是经过独立审核的补充内容与已接受事实操作合并后的真实新稿，不是旧稿。
pending 是原先未解决的负面意见，失败的局部修订不能当作问题已解决。
核对完整新稿能否实质解决每项问题；只有正文中的实际讲解及真实原文可以支持通过。
不要求只在旧段落内解决，但新稿中仍有错误、关键条件或必要教学遗漏就不能通过。
"""


def require(value, reason):
    if not value:
        raise ValueError("requires_textbook_edition_" + reason)


def read(path):
    return json.loads(path.read_bytes())


def checked_cache(folder, name):
    value = read(folder / (name + ".json"))
    require(value["data_sha256"] == sha(canonical(value["data"])), "stage_cache_integrity")
    accepted = [read(p) for p in sorted((folder / "attempts").glob(name + "-*.json"))
                if read(p).get("accepted_structure")]
    require(len(accepted) == 1 and accepted[0]["input_sha256"] == value["input_sha256"]
            and json.loads(accepted[0]["raw_response"]) == value["data"], "actual_accepted_response")
    verify_response(folder, name, accepted[0])
    return value["data"], value["input_sha256"]


def verify_response(folder, name, record):
    metrics = record["model_metrics"]
    responses = [read(p) for p in folder.glob("response-*.json")]
    require(any(r["stage"] == name and r["request_sha256"] == record["request_sha256"]
                and r["response"]["message"]["content"] == record["raw_response"]
                and r["metrics"] == metrics for r in responses), "actual_model_receipt")
    require(metrics["done_reason"] == "stop" and metrics["size_vram"] > 0
            and metrics["requested_context"] == metrics["measured_context"]
            and metrics["expected_content_tokens"] - 4 <= metrics["prompt_eval_count"]
            <= metrics["expected_content_tokens"] + 256
            and metrics["prompt_eval_count"] + metrics["reserved_output"] < metrics["measured_context"],
            "complete_local_gpu_input_and_output")


def conservative_review(folder, candidate, context, schema, expected_binding):
    """Keep valid paragraph judgments, but make contradictory targets unknown.

    No positive judgment is added. In particular, false/true teaching/coverage
    cannot be converted into a successful exclusion of original teaching.
    """
    files = sorted((folder / "attempts").glob("additions-review-1-*.json"))
    require(len(files) == 2, "failed_final_review_budget_exhausted")
    records = [read(p) for p in files]
    for record in records:
        require(not record.get("accepted_structure") and record.get("validation_error") ==
                "requires_gap_additions_consistent_teaching_coverage"
                and record["input_sha256"] == expected_binding, "only_known_failed_final_review")
        verify_response(folder, "additions-review-1", record)
        jsonschema.validate(json.loads(record["raw_response"]), schema)
    original_review = json.loads(records[0]["raw_response"])
    review = copy.deepcopy(original_review)
    unknown = []
    for ref, row in review["targets"].items():
        if row["covered"] and not row["teaching"]:
            require(not row["addition_ids"], "no_ambiguous_target_mapping")
            # Treat possible teaching as unresolved, never as an exclusion.
            row.update(teaching=True, covered=False, addition_ids=[])
            unknown.append(ref)
    require(bool(unknown), "actual_contradictory_targets")
    additions.check_review(review, candidate, context["targets"], context["complete_sources_for_this_check"])
    require(not set(unknown) & additions.resolved(candidate, review), "no_contradiction_becomes_positive")
    return review, {"selection": "earliest_complete_failed_review", "unknown_target_ids": unknown,
                    "original_review_sha256": sha(canonical(original_review)),
                    "failed_files": {str(p.relative_to(folder)): sha(p.read_bytes()) for p in files},
                    "additional_model_calls": 0}


def paragraph_positions(book):
    result, number = {}, 0
    for chapter in book["chapters"]:
        for section in chapter["sections"]:
            for index, text in enumerate(section["paragraphs"]):
                result[(section["id"], index)] = (f"p{number:03d}", text)
                number += 1
    return result


def merge(book, corrected, candidate, review, old_coverage):
    """Apply only checked prose while preserving every accepted factual edit."""
    good = {k for k, row in review["paragraphs"].items()
            if all(row[v] for v in ["supported", "fully_explained", "fits_section"])}
    selected = [row for row in candidate["additions"] if row["id"] in good]
    result = additions.assemble(corrected, {"additions": selected})
    before, after = paragraph_positions(book), paragraph_positions(result)
    translated = {}
    for key, (new_id, text) in after.items():
        if key in before and before[key][1] == text:
            translated[before[key][0]] = new_id
    # A replaced paragraph is not covered merely because its index survived.
    coverage = copy.deepcopy(old_coverage)
    for ref, row in coverage.items():
        if row["kind"] == "covered":
            ids = row["paragraph_ids"]
            if all(k in translated for k in ids):
                row["paragraph_ids"] = [translated[k] for k in ids]
            else:
                coverage[ref] = {"kind": "missing", "reason": "Previously supporting paragraph changed; needs revalidation"}
    added_positions, counts = {}, {k: len(s["paragraphs"]) for k, s in additions.sections(corrected).items()}
    for row in selected:
        key = (row["section"], counts[row["section"]])
        counts[row["section"]] += 1
        added_positions[row["id"]] = after[key][0]
    for ref in additions.resolved(candidate, review):
        row = review["targets"][ref]
        if row["teaching"]:
            require(all(k in added_positions for k in row["addition_ids"]), "only_applied_paragraphs_claim_coverage")
            coverage[ref] = {"kind": "covered", "reason": row["reason"],
                             "paragraph_ids": [added_positions[k] for k in row["addition_ids"]]}
        else:
            coverage[ref] = {"kind": "non_teaching", "reason": row["reason"]}
    return result, coverage, selected


def inputs_for(root, addition_job, precision_job, tokenizer):
    for key in [addition_job, precision_job]:
        require(re.fullmatch(r"[a-f0-9]{32}", key), "parent_job_ids")
    root = Path(root)
    af = root / "wiki/textbook-gap-addition-jobs" / addition_job
    pf = root / "wiki/textbook-precision-edit-jobs" / precision_job
    is_unmapped = (af / "unmapped-review-binding.json").exists()
    ap = read(af / ("unmapped-review-progress.json" if is_unmapped else "continuation-progress.json"))
    pp = read(pf / "progress.json")
    failed_review = is_unmapped and ap["state"] == "stopped" and ap["stage"] == "additions-review-1" \
        and ap.get("reason_code") == "wiki_stage_attempt_budget_exhausted_additions-review-1"
    require((ap["stage"] == "complete" or failed_review) and pp["stage"] == "complete"
            and not additions.final.writing.planmod.identity_live(ap)
            and not additions.final.writing.planmod.identity_live(pp), "both_parent_workers_finished")
    inputs, book, audit, targets, context, schema, binding = continuation.prepare(root, addition_job, tokenizer)
    require(read(af / "continuation-binding.json") == binding, "frozen_additions_recovery_inputs")
    extra = read(af / "manifest.json")["recipe"]["extra"]
    original_job = continuation.ContinuationJob(TextbookStore(root), inputs, extra["writer"], extra["writer_bindings"], tokenizer)
    if is_unmapped:
        ui, candidate, review_schema, review_context, ub = unmapped.prepare(root, addition_job, tokenizer)
        require(ui == inputs and read(af / "unmapped-review-binding.json") == ub, "same_unmapped_review_inputs")
        rb = original_job.stage_input(additions.REVIEW + unmapped.NOTICE, review_schema, review_context,
            evidence_packet={"sources": review_context["complete_sources_for_this_check"]})[1]
        if failed_review:
            review, conservative = conservative_review(af, candidate, review_context, review_schema, rb)
            addition_files = ["manifest.json", "continuation-binding.json", "unmapped-review-binding.json",
                             "unmapped-review-progress.json", *conservative["failed_files"]]
        else:
            review, cached_binding = checked_cache(af, "additions-review-1")
            require(rb == cached_binding, "actual_scoped_final_review_binding")
        additions.check_review(review, candidate, targets, review_context["complete_sources_for_this_check"])
        if not failed_review:
            require(read(af / "private-unmapped-additions.json") == {"candidate": candidate, "review": review,
                "unassigned_addition_ids": ub["unassigned_addition_ids"], "original_mapping_failure_retained": True},
                "unmapped_candidate_and_failure_unchanged")
            ar = read(af / "unmapped-review-result.json")
            require(ar["job"] == addition_job and ar["state"] == "unmapped_prose_reviewed_internally"
                    and ar["original_mapping_failure_retained"] is True and ar["reader_publication_allowed"] is False,
                    "internal_unmapped_review_only")
            addition_files = ["manifest.json", "continuation-binding.json", "unmapped-review-binding.json",
                              "unmapped-review-result.json", "private-unmapped-additions.json"]
    else:
        candidate, cb = checked_cache(af, "additions-repair")
        additions.check_candidate(candidate, book, targets, inputs["sources"])
        require(original_job.stage_input(additions.WRITE + continuation.NOTICE, schema, context)[1] == cb,
                "original_repair_request_binding")
        proposed = additions.assemble(book, candidate)
        review_context = {"all_original_sources": inputs["sources"], "proposed_article": proposed,
            "candidate": candidate, "targets": targets,
            "target_original_sources": {k: inputs["sources"][k] for k in targets},
            "quote_origin": "Program-supplied source identity; not evidence of a correct classification"}
        review, rb = checked_cache(af, "additions-review-1")
        review_schema = continuation.fixed_quotes(additions.review_schema(candidate, targets, inputs["sources"]), targets, inputs["sources"])
        require(original_job.stage_input(additions.REVIEW, review_schema, review_context)[1] == rb,
                "original_review_request_binding")
        additions.check_review(review, candidate, targets, inputs["sources"])
        require(read(af / "private-additions-continued.json") == {"candidate": candidate, "review": review}
                and read(af / "proposed-reading-continued.json") == proposed
                and read(af / "continuation-result.json")["proposed_article_sha256"] == sha(canonical(proposed)),
                "same_checked_additions")
        addition_files = ["manifest.json", "continuation-binding.json", "continuation-result.json", "private-additions-continued.json"]
    pm = read(pf / "manifest.json")
    require(pm["id"] == precision_job == sha(canonical(pm["recipe"]))[:32], "precision_manifest_integrity")
    pextra = pm["recipe"]["extra"]
    require(pextra["module_sha256"] == sha(Path(precision.__file__).read_bytes())
            and pextra["tokenizer_sha256"] == sha(Path(tokenizer).read_bytes()), "precision_code_and_tokenizer_unchanged")
    pi, pb, tasks, prior = precision.load(root, pextra["parent_bindings"]["review_job"])
    require(pi == inputs and pb == book and prior == pextra["parent_bindings"]
            and read(pf / "all-prior-reviews.json") == prior, "same_article_and_all_prior_reviews")
    operations = read(pf / "private-operations.json")
    for proof in operations:
        require(proof["task"] in tasks and proof["accepted"] and precision.accepted(proof["review"]), "accepted_exact_operation")
        task = proof["task"]["task"]
        cache_name = task + ("-repair" if (pf / (task + "-repair.json")).exists() else "-write")
        actual, _ = checked_cache(pf, cache_name)
        checked, _ = checked_cache(pf, task + ("-review-1" if cache_name.endswith("-repair") else "-review-0"))
        require(actual == proof["candidate"] and checked == proof["review"], "same_accepted_precision_responses")
    corrected = precision.apply_operations(book, operations)
    pr = read(pf / "result.json")
    require(pr["job"] == precision_job and pr["state"] == "precise_edits_require_global_revalidation"
            and read(pf / "provisional-reading.json") == corrected and pr["article_sha256"] == sha(canonical(corrected))
            and pr["edits_applied"] == len(operations)
            and pr["items_still_unresolved"] == len(tasks) - len(operations), "actual_precision_result")
    merged, coverage, selected = merge(book, corrected, candidate, review, audit["source_coverage"])
    require(merged != book, "actual_article_change_before_new_review")
    _, plan, _, _, _, _ = additions.final.inputs_for(root, extra["writer"])
    actual, pending = additions.final.pending_items(inputs, plan, merged, audit["unit_proofs"])
    pending = {k: v for k, v in pending.items() if not prior["all_integrated_reviews"][k]["passed"]}
    parent_files = {str(p.relative_to(root)): sha(p.read_bytes()) for folder, names in [
        (af, addition_files),
        (pf, ["manifest.json", "result.json", "private-operations.json", "all-prior-reviews.json"])] for p in [folder / n for n in names]}
    return inputs, merged, coverage, pending, actual, {"parents": parent_files, "article_sha256": sha(canonical(merged)),
        "accepted_additions": len(selected), "accepted_factual_operations": len(operations), "precision_unresolved": pr["items_still_unresolved"],
        "original_mapping_failure_retained": is_unmapped,
        "conservative_review": conservative if failed_review else None,
        "all_prior_reading_checks": audit["reading"], "all_prior_integrated_checks": prior["all_integrated_reviews"]}


class EditionJob(additions.final.ReviewJob):
    def __init__(self, store, inputs, book, binding, tokenizer):
        from tokenizers import Tokenizer
        self.inputs, self.tokenizer = inputs, Tokenizer.from_file(str(tokenizer))
        DraftJob.__init__(self, store, {"sources": inputs["sources"]}, "qwen3.5:9b", inputs["model_digest"], 21600,
            pipeline=POLICY, job_directory="textbook-edition-jobs", max_attempts=2,
            recipe_extra={"binding": binding, "module_sha256": sha(Path(__file__).read_bytes()),
                "tokenizer_sha256": sha(Path(tokenizer).read_bytes()), "prompts": [REVIEW, additions.final.writing.READ],
                "maximum_semantic_repairs": 0, "old_budgets_reset": False})

    def progress(self, state, stage, **extra):
        self.current = stage
        value = {**self.identity, "job": self.key, "state": state, "stage": stage, "updated": time.time(),
                 "reader_publication_allowed": False, "source_deletion_allowed": False, **extra}
        atomic_json(self.root / "progress.json", value)
        atomic_json(self.store.root / "wiki/textbook-edition-current.json", value)


def export_draft(folder, book, status):
    notice = ('首课工作稿 · 尚未验收。当前仍有 ' + str(status["missing_source_inputs"]) + ' 条来源覆盖疑点、'
              + str(status["remaining_prior_issues"]) + ' 条原审稿问题和 ' + str(status["reading_issues"])
              + ' 项阅读问题。原始资料继续保留。')
    html = reading_html(book).replace('<body><main>', '<body><main><aside role="status" '
        'style="padding:1rem;background:#fff0d4;margin-bottom:2rem">' + notice + '</aside>', 1)
    (folder / "working-reading.html").write_text(html, encoding="utf-8")


def backup_edition(job, binding):
    target = Path.home() / "Documents/CourseKnowledgeBackups/first-lesson-editions" / job.key
    entries = []
    files = [(job.store.root / name, name) for name in binding["parents"]]
    files += [(job.root / name, "edition/" + name) for name in ["manifest.json", "reading.json",
        "private-inherited-coverage.json", "private-prior-checks.json"]]
    files += [(Path(m.__file__), "code/" + Path(m.__file__).name) for m in
              [sys.modules[__name__], continuation, unmapped, additions, precision]]
    for source, name in files:
        output = target / name
        output.parent.mkdir(parents=True, exist_ok=True)
        data = source.read_bytes()
        require(not output.exists() or output.read_bytes() == data, "edition_backup_unchanged")
        if not output.exists():
            output.write_bytes(data)
        require(output.read_bytes() == data, "edition_backup_byte_match")
        entries.append({"path": name, "sha256": sha(data), "bytes": len(data)})
    continuation.bind_once(target / "receipt.json", {"files": entries, "complete_processing_restore": False})


def run(root, addition_job, precision_job, tokenizer):
    store = TextbookStore(root)
    store.checkpoint()
    with schema_lease(store.root / "wiki/textbook-edition.lock", timeout=0):
        inputs, book, coverage, pending, actual, binding = inputs_for(root, addition_job, precision_job, tokenizer)
        job = EditionJob(store, inputs, book, binding, tokenizer)
        continuation.bind_once(store.root / "wiki/textbook-gap-addition-jobs" / addition_job / "edition-binding.json",
            {"job": job.key, "manifest_sha256": sha((job.root / "manifest.json").read_bytes())})
        atomic_json(job.root / "reading.json", book)
        atomic_json(job.root / "private-inherited-coverage.json", coverage)
        atomic_json(job.root / "private-prior-checks.json", {"pending": pending, "binding": binding})
        backup_edition(job, binding)
        export_draft(job.root, book, {"missing_source_inputs": sum(v["kind"] == "missing" for v in coverage.values()),
            "remaining_prior_issues": len(pending),
            "reading_issues": sum(not v["passed"] for v in binding["all_prior_reading_checks"].values())})
        try:
            checks = job.stage("remaining-checks", REVIEW, additions.final.schema_for(pending, inputs["sources"], actual),
                {"all_original_sources": inputs["sources"], "article": book,
                 "actual_article_items": actual, "pending": pending},
                lambda v: additions.final.validate(v, additions.final.schema_for(pending, inputs["sources"], actual)))
            schema = object_schema({k: object_schema({"passed": {"type": "boolean"},
                "reason": {"type": "string", "minLength": 1, "maxLength": 600}})
                for k in ["organisation", "depth", "coherence", "figures", "self_tests"]})
            reading = job.stage("edition-reading", additions.final.writing.READ, schema,
                {"article": book}, lambda v: jsonschema.validate(v, schema))
            result = {"state": "edition_review_finished", "job": job.key,
                "article_sha256": sha(canonical(book)), "source_inputs": len(coverage),
                "missing_source_inputs": sum(v["kind"] == "missing" for v in coverage.values()),
                "remaining_prior_issues": sum(not v["passed"] for v in checks["items"].values()),
                "reading_issues": sum(not v["passed"] for v in reading.values()),
                "accepted_additions": binding["accepted_additions"], "accepted_factual_operations": binding["accepted_factual_operations"],
                "original_mapping_failure_retained": binding["original_mapping_failure_retained"],
                "body_characters": sum(len(v) for v in additions.final.writing.planmod.body_index(book)[0].values()),
                "semantic_acceptance": False, "scope_verified": False, "reader_publication_allowed": False,
                "source_deletion_allowed": False, "goal_complete": False}
            atomic_json(job.root / "result.json", result)
            export_draft(job.root, book, result)
            job.progress(result["state"], "complete")
            return result
        except Exception as exc:
            msg = str(exc)
            job.progress("paused" if isinstance(exc, InterruptedError) else "stopped", job.current,
                error_type=type(exc).__name__, reason_code=msg if re.fullmatch(r"[a-zA-Z0-9_-]{1,180}", msg) else None,
                error_sha256=sha(msg.encode()))
            raise


def dispatch(root, addition_job, precision_job, tokenizer):
    from .course_archive_gpu import checked_sample
    from .course_gpu_guard import run as guarded

    store = TextbookStore(root)
    path = store.root / "wiki/textbook-edition-dispatch.json"
    identity = {"pid": psutil.Process().pid, "created": psutil.Process().create_time()}
    with schema_lease(store.root / "wiki/textbook-edition-dispatch.lock", timeout=0):
        try:
            deadline = time.monotonic() + 7200
            while True:
                store.checkpoint()
                parent = read(store.root / "wiki/textbook-gap-addition-jobs" / addition_job / "unmapped-review-progress.json")
                live = additions.final.writing.planmod.identity_live(parent)
                salvageable = parent["state"] == "stopped" and parent["stage"] == "additions-review-1" \
                    and parent.get("reason_code") == "wiki_stage_attempt_budget_exhausted_additions-review-1"
                if not live and (parent["stage"] == "complete" or salvageable):
                    break
                require(parent["state"] not in {"paused", "stopped"} and live, "parent_review_stopped_no_restart")
                require(time.monotonic() < deadline, "parent_review_wait_timeout")
                atomic_json(path, {**identity, "state": "waiting_parent", "updated": time.time(), "parent": addition_job})
                time.sleep(2)
            inputs_for(root, addition_job, precision_job, tokenizer)
            previous = store.root / "review-experiment-thermal-guard.json"
            if previous.exists():
                data = previous.read_bytes()
                require(not additions.final.writing.planmod.identity_live(json.loads(data)), "edition_guard_slot_free")
                history = store.root / "wiki/textbook-edition-guard-history" / (sha(data) + ".json")
                history.parent.mkdir(parents=True, exist_ok=True)
                require(not history.exists() or history.read_bytes() == data, "edition_guard_history_unchanged")
                if not history.exists():
                    history.write_bytes(data)
            atomic_json(path, {**identity, "state": "guarding_edition", "updated": time.time(), "parent": addition_job})
            guarded(store.root, [sys.executable, "-m", "wechat_decrypt_tool.course_textbook_edition", "--root", str(store.root),
                "--addition-job", addition_job, "--precision-job", precision_job, "--tokenizer", str(tokenizer), "worker"],
                maximum_temperature=70, maximum_power=200, guard_name="review-experiment-thermal-guard",
                sampler=lambda: checked_sample(store.root))
            atomic_json(path, {**identity, "state": "edition_guard_finished", "updated": time.time(), "parent": addition_job})
        except Exception as exc:
            msg = str(exc)
            atomic_json(path, {**identity, "state": "stopped", "updated": time.time(), "error_type": type(exc).__name__,
                "reason_code": msg if re.fullmatch(r"[a-zA-Z0-9_-]{1,180}", msg) else None, "error_sha256": sha(msg.encode())})
            raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--addition-job", required=True)
    p.add_argument("--precision-job", required=True)
    p.add_argument("--tokenizer", type=Path, required=True)
    p.add_argument("command", choices=["preflight", "worker", "guard"])
    args = p.parse_args()
    if args.command == "worker":
        print(json.dumps(run(args.root, args.addition_job, args.precision_job, args.tokenizer)))
    elif args.command == "guard":
        dispatch(args.root, args.addition_job, args.precision_job, args.tokenizer)
    else:
        _, book, coverage, pending, _, binding = inputs_for(args.root, args.addition_job, args.precision_job, args.tokenizer)
        print(json.dumps({"state": "edition_inputs_verified", "article_sha256": sha(canonical(book)),
            "pending_prior_checks": len(pending), "missing_source_inputs": sum(v["kind"] == "missing" for v in coverage.values()),
            "accepted_additions": binding["accepted_additions"]}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        msg = str(exc)
        print(json.dumps({"state": "stopped", "error_type": type(exc).__name__,
            "reason_code": msg if re.fullmatch(r"[a-zA-Z0-9_-]{1,180}", msg) else None, "error_sha256": sha(msg.encode())}))
        raise SystemExit(1) from None
