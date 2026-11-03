"""Finite, resumable original-to-manuscript coverage; never publish a reader."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import psutil

from . import course_textbook_source_coverage as coverage
from .course_knowledge import atomic_json, schema_lease
from .course_library import canonical
from .course_textbook_article import reading_book
from .course_textbook_evidence import checked_sha, load, sha
from .course_textbook_ledger import TextbookStore
from .course_wiki_text_inference import chat, profile
from .course_wiki_writer import DraftJob

POLICY = "complete-original-to-actual-manuscript-coverage-v1"
MAX_CLAUSES = 12
CONTEXT_CHARACTERS = 10000
CONTRACT = "manuscript-coverage-execution.json"


def require(ok, reason):
    if not ok:
        raise ValueError("requires_manuscript_coverage_" + reason)


def read_saved(path):
    value = json.loads(path.read_bytes())
    require(
        value["data_sha256"] == sha(canonical(value["data"])), "unchanged_saved_data"
    )
    return value["data"]


def inputs(root, composition_job):
    require(re.fullmatch(r"[a-f0-9]{32}", composition_job) is not None, "parent_job")
    folder = Path(root) / "wiki/textbook-composition-jobs" / composition_job
    state = json.loads((folder / "progress.json").read_bytes())
    require(state["stage"] == "private_article_assembled", "assembled_parent_article")
    try:
        alive = (
            abs(psutil.Process(state["pid"]).create_time() - state["created"]) < 0.001
        )
    except psutil.NoSuchProcess:
        alive = False
    require(not alive, "parent_worker_exited")
    private = read_saved(folder / "private-article.json")
    inventory = load(root, checked_sha(private["inventory_sha256"]))
    require(private["course_id"] == inventory["lesson"], "matching_course")
    result = read_saved(folder / "result.json")
    require(
        result["job"] == composition_job
        and result["inventory_sha256"] == private["inventory_sha256"]
        and result["reader_article_published"] is False,
        "matching_unpublished_parent",
    )
    return inventory, private


def paragraphs(private, inventory):
    """Index the actual reading body; quizzes cannot replace body coverage."""
    reading_book(private)
    require(
        private["inventory_sha256"] == sha(canonical(inventory)), "article_inventory"
    )
    result = {}
    for chapter in private["chapters"]:
        for section in chapter["sections"]:
            for row in section["paragraphs"]:
                require(
                    bool(row["citations"])
                    and set(row["citations"]) <= set(inventory["sources"]),
                    "known_body_citations",
                )
                result[f"P{len(result):06d}"] = {
                    "text": row["text"],
                    "chapter": chapter["heading"],
                    "section": section["heading"],
                    "section_id": section["id"],
                    "citations": list(row["citations"]),
                }
    require(bool(result), "actual_article_body")
    return result


def neighbors(inventory, ref):
    source = inventory["sources"][ref]
    if source["kind"] != "speech":
        return {}
    refs = [key for key, row in inventory["sources"].items() if row["kind"] == "speech"]
    index = refs.index(ref)
    return {
        key: inventory["sources"][key]["text"]
        for key in refs[max(0, index - 1) : index] + refs[index + 1 : index + 2]
    }


def base_context(inventory, ref, units, ids):
    text = inventory["sources"][ref]["text"]
    return {
        "target_reference": ref,
        "target": text,
        "context": neighbors(inventory, ref),
        "clauses": {key: text[units[key]["start"] : units[key]["end"]] for key in ids},
        "paragraphs": {},
        "article_structure": {},
    }


def with_paragraphs(context, records, ids):
    return {
        **context,
        "paragraphs": {key: records[key]["text"] for key in ids},
        "article_structure": {
            key: {field: records[key][field] for field in ("chapter", "section")}
            for key in ids
        },
    }


def context_size(context):
    return len(coverage.PROMPT) + len(canonical(context).decode("utf-8"))


def article_blocks(context, records, ref):
    """Try cited paragraphs first; every other body paragraph remains available."""
    ordered = sorted(records, key=lambda key: ref not in records[key]["citations"])
    blocks, current = [], []
    require(context_size(context) < CONTEXT_CHARACTERS, "complete_source_fits_context")
    for key in ordered:
        proposed = [*current, key]
        if (
            context_size(with_paragraphs(context, records, proposed))
            > CONTEXT_CHARACTERS
        ):
            require(bool(current), "complete_single_body_paragraph_fits_context")
            blocks.append(current)
            current = [key]
            require(
                context_size(with_paragraphs(context, records, current))
                <= CONTEXT_CHARACTERS,
                "complete_single_body_paragraph_fits_context",
            )
        else:
            current = proposed
    if current:
        blocks.append(current)
    require(
        len([key for block in blocks for key in block]) == len(records)
        and {key for block in blocks for key in block} == set(records),
        "complete_article_search_space",
    )
    return blocks


def known_teaching_sources(root, private):
    from .course_textbook_resolution import inputs as ledger_inputs

    inventory, ledger = ledger_inputs(root, private["ledger_job"])
    require(
        sha(canonical(inventory)) == private["inventory_sha256"],
        "same_source_ledger_inventory",
    )
    if "parent_ledger_sha256" in private:
        require(
            sha(canonical(ledger)) == private["parent_ledger_sha256"],
            "same_source_ledger",
        )
    excluded = set(private.get("quarantined_point_keys", []))
    confirmed = {
        key: row for key, row in ledger["points"].items() if key not in excluded
    }
    require(
        set(confirmed) <= set(private["private_coverage"]), "confirmed_points_retained"
    )
    refs = {ref for row in confirmed.values() for ref in row["citations"]}
    for key, card in private.get("pending_source_cards", {}).items():
        if private["private_coverage"][key].get("teaching") is True:
            refs.add(card["reference"])
    return sorted(refs)


def make_plan(inventory, private, *, known_teaching_source_ids=()):
    require(
        len(set(known_teaching_source_ids)) == len(known_teaching_source_ids)
        and set(known_teaching_source_ids) <= set(inventory["sources"]),
        "known_teaching_source_scope",
    )
    source_plan = coverage.plan(inventory)
    records = paragraphs(private, inventory)
    sources = {}
    for ref, units in source_plan["units"].items():
        keys = list(units)
        partitions = []
        for start in range(0, len(keys), MAX_CLAUSES):
            ids = keys[start : start + MAX_CLAUSES]
            context = base_context(inventory, ref, units, ids)
            partitions.append(
                {
                    "clauses": ids,
                    "article_blocks": article_blocks(context, records, ref),
                }
            )
        require(
            [key for part in partitions for key in part["clauses"]] == keys,
            "complete_original_clause_partitions",
        )
        sources[ref] = partitions
    return {
        "policy": POLICY,
        "inventory_sha256": sha(canonical(inventory)),
        "article_sha256": sha(canonical(private)),
        "original_clause_plan_sha256": sha(canonical(source_plan)),
        "paragraphs_sha256": sha(canonical(records)),
        "source_count": len(sources),
        "clause_count": source_plan["clauses"],
        "paragraph_count": len(records),
        "known_teaching_source_ids": list(known_teaching_source_ids),
        "sources": sources,
        "maximum_clauses_per_request": MAX_CLAUSES,
        "maximum_context_characters": CONTEXT_CHARACTERS,
        "reader_publication_allowed": False,
        "source_deletion_allowed": False,
    }


class ReviewJob(DraftJob):
    def __init__(self, store, inventory, private, plan, wait_seconds):
        from .course_pdf_ocr import local_model_digest

        DraftJob.__init__(
            self,
            store,
            {"sources": inventory["sources"]},
            "qwen3.5:9b",
            local_model_digest("qwen3.5:9b"),
            wait_seconds,
            pipeline=POLICY,
            job_directory="manuscript-coverage-jobs",
            max_attempts=2,
            recipe_extra={
                "inventory_sha256": sha(canonical(inventory)),
                "article_sha256": sha(canonical(private)),
                "plan_sha256": sha(canonical(plan)),
                "prompt": coverage.PROMPT,
                "text_profile": profile(),
                "semantic_repair_calls": 0,
                "module_sha256": {
                    name: sha(Path(__file__).with_name(name + ".py").read_bytes())
                    for name in (
                        "course_textbook_manuscript_review",
                        "course_textbook_source_coverage",
                        "course_textbook_article",
                        "course_wiki_text_inference",
                        "course_wiki_writer",
                    )
                },
            },
        )
        self.source_count = plan["source_count"]
        self.finished_sources = 0
        self.clause_count = plan["clause_count"]
        self.finished_clauses = 0

    def generate_response(self, messages, *, schema):
        return chat(self.model, messages, schema=schema)

    def progress(self, state, stage, **extra):
        self.current = stage
        value = {
            **self.identity,
            "job": self.key,
            "state": state,
            "stage": stage,
            "updated": time.time(),
            "completed_stages": self.completed,
            "completed_sources": self.finished_sources,
            "total_sources": self.source_count,
            "completed_clauses": self.finished_clauses,
            "total_clauses": self.clause_count,
            "reader_publication_allowed": False,
            "source_deletion_allowed": False,
            **extra,
        }
        atomic_json(self.root / "progress.json", value)
        atomic_json(self.store.root / "wiki/manuscript-coverage-last-build.json", value)


def review_partition(job, inventory, records, ref, units, part, prefix):
    unresolved = list(part["clauses"])
    result = {}
    for number, block in enumerate(part["article_blocks"]):
        if not unresolved:
            break
        context = with_paragraphs(
            base_context(inventory, ref, units, unresolved), records, block
        )
        require(context_size(context) <= CONTEXT_CHARACTERS, "complete_request_context")
        stage = f"{prefix}-body-{number:04d}"
        job.current = stage
        ids = list(unresolved)
        packet = {
            "sources": {
                key: inventory["sources"][key] for key in [ref, *context["context"]]
            }
        }
        value = job.stage(
            stage,
            coverage.PROMPT,
            coverage.review_schema(ids, block),
            context,
            lambda row, ids=ids, block=block: coverage.validate_review(row, ids, block),
            evidence_packet=packet,
        )
        following = []
        for key, row in value["clauses"].items():
            prior = result.get(key)
            # A later search window cannot erase an already detected teaching
            # obligation merely by relabeling it as non-teaching.
            if prior is not None and prior["teaching"] and not row["teaching"]:
                row = {**prior, "assessments": [*prior["assessments"], stage]}
            else:
                row = {
                    **row,
                    "assessments": [*(prior or {}).get("assessments", []), stage],
                }
            result[key] = row
            if row["teaching"] and not row["covered"]:
                following.append(key)
        unresolved = following
    require(set(result) == set(part["clauses"]), "every_clause_reviewed")
    return result


def run(root, composition_job, *, wait_seconds=3600):
    require(type(wait_seconds) is int and 0 <= wait_seconds <= 3600, "finite_gpu_wait")
    root = Path(root).resolve()
    inventory, private = inputs(root, composition_job)
    plan = make_plan(
        inventory,
        private,
        known_teaching_source_ids=known_teaching_sources(root, private),
    )
    records = paragraphs(private, inventory)
    original = coverage.plan(inventory)
    store = TextbookStore(root)
    store.checkpoint()
    with schema_lease(
        root
        / "wiki/textbook-evidence"
        / plan["inventory_sha256"]
        / "manuscript-coverage-worker.lock",
        timeout=0,
    ):
        job = ReviewJob(store, inventory, private, plan, wait_seconds)
        binding = {
            "job": job.key,
            "plan_sha256": sha(canonical(plan)),
            "article_sha256": plan["article_sha256"],
        }
        contract = root / "wiki/textbook-composition-jobs" / composition_job / CONTRACT
        if contract.exists():
            require(
                json.loads(contract.read_bytes()) == binding,
                "preserve_bound_review_budget",
            )
        else:
            atomic_json(contract, binding)
        atomic_json(job.root / "review-plan.json", plan)
        results = {}
        try:
            for number, (ref, parts) in enumerate(plan["sources"].items()):
                rows = {}
                for index, part in enumerate(parts):
                    rows.update(
                        review_partition(
                            job,
                            inventory,
                            records,
                            ref,
                            original["units"][ref],
                            part,
                            f"source-{number:04d}-clauses-{index:04d}",
                        )
                    )
                require(
                    set(rows) == set(original["units"][ref]), "complete_reviewed_source"
                )
                results[ref] = rows
                job.finished_sources += 1
                job.finished_clauses += len(rows)
                job.progress("reviewing_complete_originals", f"source-{number:04d}")
            missing = {
                ref: [
                    key
                    for key, row in rows.items()
                    if row["teaching"] and not row["covered"]
                ]
                for ref, rows in results.items()
            }
            missing = {ref: ids for ref, ids in missing.items() if ids}
            conflicts = [
                ref
                for ref in plan["known_teaching_source_ids"]
                if not any(row["teaching"] for row in results[ref].values())
            ]
            require(
                set(results) == set(inventory["sources"]),
                "all_original_sources_reviewed",
            )
            result = {
                "policy": POLICY,
                "job": job.key,
                "composition_job": composition_job,
                **binding,
                "inventory_sha256": plan["inventory_sha256"],
                "sources": job.finished_sources,
                "clauses": job.finished_clauses,
                "missing_clauses": sum(map(len, missing.values())),
                "sources_with_missing_teaching": len(missing),
                "sources_with_classification_conflicts": len(conflicts),
                "classification_conflicts": conflicts,
                "coverage": results,
                "missing": missing,
                "automatic_original_coverage_complete": not missing and not conflicts,
                "semantic_accuracy_verified": False,
                "reader_publication_allowed": False,
                "source_deletion_allowed": False,
                "state": "automatic_original_coverage_complete"
                if not missing and not conflicts
                else "manuscript_requires_original_coverage_repair",
            }
            atomic_json(job.root / "coverage-result.json", result)
            job.progress(
                result["state"],
                "complete",
                missing_clauses=result["missing_clauses"],
                classification_conflicts=len(conflicts),
            )
            return {
                key: value
                for key, value in result.items()
                if key not in {"coverage", "missing"}
            }
        except BaseException as exc:
            job.progress(
                "paused" if isinstance(exc, InterruptedError) else "stopped",
                job.current,
                error_type=type(exc).__name__,
            )
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--composition-job", required=True)
    parser.add_argument("--wait-gpu-seconds", default=3600, type=int)
    parser.add_argument("command", choices=["run", "guard"])
    args = parser.parse_args(argv)
    try:
        if args.command == "guard":
            from .course_archive_gpu import checked_sample
            from .course_gpu_guard import run as guarded

            inputs(args.root, args.composition_job)
            guarded(
                args.root,
                [
                    sys.executable,
                    "-m",
                    __spec__.name,
                    "--root",
                    str(args.root.resolve()),
                    "--composition-job",
                    args.composition_job,
                    "--wait-gpu-seconds",
                    str(args.wait_gpu_seconds),
                    "run",
                ],
                maximum_power=200,
                maximum_temperature=70,
                guard_name="wiki-revision-thermal-guard",
                sampler=lambda: checked_sample(args.root),
            )
        else:
            print(
                json.dumps(
                    run(
                        args.root,
                        args.composition_job,
                        wait_seconds=args.wait_gpu_seconds,
                    )
                )
            )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "state": "stopped",
                    "error_type": type(exc).__name__,
                    "error_sha256": sha(str(exc).encode()),
                }
            )
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
