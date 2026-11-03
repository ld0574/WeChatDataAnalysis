"""Recover an omitted source disposition from explicit retained model outputs.

Only mechanical composition is permitted: borrow an existing non-teaching
classification for an omitted reference. Never invent a classification, merge
new teaching claims, replenish attempts, or skip the subsequent full review.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

import psutil

from .course_knowledge import atomic_json, schema_lease
from .course_library import canonical
from .course_textbook_evidence import checked_sha, load, sha
from .course_textbook_ledger import (
    EXTRACT,
    LEDGER,
    LedgerJob,
    TextbookStore,
    validate_ledger,
)
from .course_wiki_text_inference import validate_metrics

POLICY = "textbook-explicit-cached-exclusion-composition-v1"


def compose(candidates, sources):
    """Use only explicit, unopposed exclusions already proposed for that source."""
    if len(candidates) != 2:
        raise ValueError("textbook_cache_requires_two_preserved_candidates")
    known, teaching = set(sources), set()
    for candidate in candidates:
        refs = {ref for point in candidate["points"] for ref in point["citations"]} | {
            row["reference"] for row in candidate["excluded_sources"]
        }
        if refs - known:
            raise ValueError("textbook_cache_candidate_has_unknown_source")
        validate_ledger(candidate, {ref: sources[ref] for ref in refs})
        teaching.update(
            ref for point in candidate["points"] for ref in point["citations"]
        )
    for base_index in reversed(range(len(candidates))):
        result = copy.deepcopy(candidates[base_index])
        covered = {ref for point in result["points"] for ref in point["citations"]} | {
            row["reference"] for row in result["excluded_sources"]
        }
        missing = known - covered
        if not missing:
            raise ValueError("textbook_cache_complete_candidate_needs_no_recovery")
        if missing.intersection(teaching):
            continue
        borrowed = []
        for ref in sorted(missing):
            choices = [
                (index, row)
                for index, candidate in enumerate(candidates)
                if index != base_index
                for row in candidate["excluded_sources"]
                if row["reference"] == ref
            ]
            if not choices:
                break
            donor, row = choices[-1]
            result["excluded_sources"].append(copy.deepcopy(row))
            borrowed.append({"reference": ref, "candidate": donor})
        else:
            validate_ledger(result, sources)
            return result, {"base_candidate": base_index, "borrowed": borrowed}
    raise ValueError("textbook_cache_no_unambiguous_explicit_exclusion_for_omission")


def recover(root, inventory_key, job_key, page):
    root = Path(root).resolve()
    checked_sha(inventory_key)
    if not isinstance(job_key, str) or len(job_key) != 32:
        raise ValueError("textbook_cache_invalid_job")
    if any(ch not in "0123456789abcdef" for ch in job_key):
        raise ValueError("textbook_cache_invalid_job")
    inventory = load(root, inventory_key)
    if type(page) is not int or not 0 <= page < len(inventory["pages"]):
        raise ValueError("textbook_cache_invalid_page")
    folder = root / "wiki" / "textbook-ledger-jobs" / job_key
    parent = json.loads((folder / "manifest.json").read_bytes())
    prefix = f"page-{page:04d}-extract"
    progress = json.loads((folder / "progress.json").read_bytes())
    if progress["state"] != "stopped" or progress["stage"] != prefix:
        raise ValueError("textbook_cache_requires_exact_terminal_stage")
    try:
        process = psutil.Process(progress["pid"])
        if abs(process.create_time() - progress["created"]) < 0.01:
            raise ValueError("textbook_cache_worker_still_live")
    except psutil.NoSuchProcess:
        pass
    lock = root / "wiki" / "textbook-evidence" / inventory_key / "ledger-worker.lock"
    with schema_lease(lock, timeout=0):
        store = TextbookStore(root)
        store.checkpoint()
        job = LedgerJob(
            store, inventory, "qwen3.5:9b", parent["recipe"]["model_digest"], 0
        )
        if job.key != job_key:
            raise ValueError("textbook_cache_original_recipe_changed")
        target = folder / (prefix + ".json")
        if target.exists():
            raise ValueError("textbook_cache_existing_checkpoint_preserved")
        packet = {
            "sources": {
                ref: inventory["sources"][ref] for ref in inventory["pages"][page]
            }
        }
        context = {
            "sources": {
                ref: {"kind": row["kind"], "text": row["text"]}
                for ref, row in packet["sources"].items()
            },
            "scope": "complete_bounded_originals_only_not_article_structure",
        }
        _, binding = job.stage_input(EXTRACT, LEDGER, context, evidence_packet=packet)
        paths = sorted((folder / "attempts").glob(prefix + "-*.json"))
        if len(paths) != 2:
            raise ValueError("textbook_cache_original_attempt_budget_not_terminal")
        candidates, attempts = [], []
        for index, path in enumerate(paths):
            raw = path.read_bytes()
            attempt = json.loads(raw)
            if (
                attempt.get("input_sha256") != binding
                or attempt.get("attempt") != index + 1
                or attempt.get("model_digest") != job.model_digest
                or attempt.get("error_type") != "ValueError"
                or attempt.get("validation_error")
                != "missing_textbook_exact_source_dispositions"
                or attempt.get("accepted_structure")
            ):
                raise ValueError("textbook_cache_attempt_not_eligible")
            validate_metrics(attempt["model_metrics"], job.model_digest)
            candidates.append(json.loads(attempt["raw_response"]))
            attempts.append(
                {"path": path.relative_to(root).as_posix(), "sha256": sha(raw)}
            )
        result, provenance = compose(candidates, packet["sources"])
        receipt = {
            "policy": POLICY,
            "inventory_sha256": inventory_key,
            "job": job_key,
            "page": page,
            "stage": prefix,
            "input_sha256": binding,
            "data_sha256": sha(canonical(result)),
            "attempts": attempts,
            "composition": provenance,
            "recovery_module_sha256": sha(Path(__file__).read_bytes()),
            "model_calls": 0,
            "original_attempt_budget_unchanged": True,
            "original_failure_records_unchanged": True,
            "full_source_review_still_required": True,
            "semantic_accuracy_verified": False,
            "reader_article_published": False,
        }
        receipt_key = sha(canonical(receipt))
        receipt_path = (
            root
            / "wiki"
            / "textbook-ledger-cache-recoveries"
            / receipt_key
            / "receipt.json"
        )
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        if receipt_path.exists() and json.loads(receipt_path.read_bytes()) != receipt:
            raise ValueError("textbook_cache_recovery_receipt_changed")
        atomic_json(receipt_path, receipt)
        for attempt in attempts:
            if sha((root / attempt["path"]).read_bytes()) != attempt["sha256"]:
                raise ValueError("textbook_cache_original_failure_changed")
        atomic_json(
            target,
            {
                "input_sha256": binding,
                "data_sha256": receipt["data_sha256"],
                "data": result,
                "recovery": {
                    "receipt": receipt_path.relative_to(root).as_posix(),
                    "sha256": receipt_key,
                },
            },
        )
        saved = json.loads(target.read_bytes())
        validate_ledger(saved["data"], packet["sources"])
        if sha(canonical(saved["data"])) != saved["data_sha256"]:
            raise ValueError("textbook_cache_saved_checkpoint_changed")
        return receipt_key, receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("--page", type=int, required=True)
    args = parser.parse_args(argv)
    key, receipt = recover(args.root, args.inventory, args.job, args.page)
    print(
        json.dumps(
            {
                "recovery": key,
                "job": receipt["job"],
                "page": receipt["page"],
                "model_calls": 0,
                "borrowed_references": [
                    row["reference"] for row in receipt["composition"]["borrowed"]
                ],
                "full_source_review_still_required": True,
            }
        )
    )


if __name__ == "__main__":
    main()
