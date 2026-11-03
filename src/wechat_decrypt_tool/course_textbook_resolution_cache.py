"""Preserve failed facts while binding quotations to their exact original evidence."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import psutil
import jsonschema

from .course_knowledge import atomic_json, schema_lease
from .course_library import canonical
from .course_textbook_evidence import sha
from .course_textbook_ledger import TextbookStore
from .course_textbook_resolution import (
    DECISION,
    PROMPT,
    ResolutionJob,
    affected_sources,
    inputs,
    validate_decision,
)
from .course_wiki_text_inference import validate_metrics

POLICY = "unique-original-whitespace-span-recovery-v1"
SOURCE_BINDING = "earliest-unchanged-candidate-complete-short-source-binding-v1"


def literal_quotations(value, source):
    """Change no non-whitespace character; require a unique original span."""
    result = copy.deepcopy(value)
    text = source["text"]
    positions = [
        index for index, character in enumerate(text) if not character.isspace()
    ]
    compact = "".join(text[index] for index in positions)
    restored = []
    for point_index, point in enumerate(result.get("points", [])):
        for quote_index, quote in enumerate(point["quotations"]):
            if quote in text:
                continue
            normalized = "".join(
                character for character in quote if not character.isspace()
            )
            if not normalized:
                raise ValueError("requires_nonempty_original_quotation")
            matches = [
                match.start()
                for match in re.finditer("(?=" + re.escape(normalized) + ")", compact)
            ]
            if len(matches) != 1:
                raise ValueError("requires_unique_whitespace_only_original_alignment")
            start = positions[matches[0]]
            end = positions[matches[0] + len(normalized) - 1] + 1
            original = text[start:end]
            if "".join(c for c in original if not c.isspace()) != normalized:
                raise ValueError("requires_unchanged_nonwhitespace_quotation")
            point["quotations"][quote_index] = original
            restored.append(
                {
                    "point": point_index,
                    "quotation": quote_index,
                    "start": start,
                    "end": end,
                    "model_quotation_sha256": sha(quote.encode()),
                    "original_span_sha256": sha(original.encode()),
                }
            )
    validate_decision(result, source)
    if not restored:
        raise ValueError("requires_actual_whitespace_quotation_recovery")
    return result, restored


def complete_source_quotations(value, source):
    """Bind unchanged candidate facts to a whole short original for the normal audit.

    This does not claim that the model's proposed quotations were literal or that
    its facts are supported. The existing independent semantic audit decides that.
    """
    jsonschema.validate(value, DECISION)
    text = source["text"]
    if not 8 <= len(text.strip()) <= 2048:
        raise ValueError("requires_short_complete_original_source")
    if value["kind"] != "teaching" or all(
        quotation in text
        for point in value["points"]
        for quotation in point["quotations"]
    ):
        raise ValueError("requires_actual_nonliteral_candidate_quotations")
    result = copy.deepcopy(value)
    spans = []
    for number, point in enumerate(result["points"]):
        spans.append(
            {
                "point": number,
                "start": 0,
                "end": len(text),
                "proposed_quotation_sha256": [
                    sha(quote.encode()) for quote in point["quotations"]
                ],
                "original_span_sha256": sha(text.encode()),
            }
        )
        point["quotations"] = [text]
    validate_decision(result, source)
    return result, spans


def recover(root, job_key):
    return _recover(root, job_key, complete_source=False)


def bind_complete_source(root, job_key):
    """Use exact full-source citations without changing facts or spending retries."""
    return _recover(root, job_key, complete_source=True)


def _recover(root, job_key, *, complete_source):
    root = Path(root).resolve()
    if not re.fullmatch(r"[a-f0-9]{32}", job_key):
        raise ValueError("requires_literal_cache_job_id")
    folder = root / "wiki/textbook-keyed-jobs" / job_key
    progress = json.loads((folder / "progress.json").read_bytes())
    if progress["state"] != "stopped" or not re.fullmatch(
        r"source-\d{4}-resolve", progress["stage"]
    ):
        raise ValueError("requires_stopped_resolution_extract_stage")
    try:
        worker = psutil.Process(progress["pid"])
        if abs(worker.create_time() - progress["created"]) < 0.01:
            raise ValueError("requires_resolution_worker_confirmed_exited")
    except psutil.NoSuchProcess:
        pass
    manifest = json.loads((folder / "manifest.json").read_bytes())
    parent_sha = manifest["recipe"]["extra"]["parent_sha256"]
    # Parent identity comes from the actual bound parent data, not a directory scan.
    parent_job = None
    for path in folder.parent.glob("*/literal-resolution-execution.json"):
        contract = json.loads(path.read_bytes())
        if (
            contract.get("job") == job_key
            and contract.get("parent_sha256") == parent_sha
        ):
            if parent_job is not None:
                raise ValueError("requires_unique_bound_resolution_parent")
            parent_job = path.parent.name
    if parent_job is None:
        raise ValueError("requires_bound_resolution_parent")
    inventory, parent = inputs(root, parent_job)
    if sha(canonical(parent)) != parent_sha:
        raise ValueError("requires_unchanged_literal_cache_parent")
    lock = (
        root
        / "wiki/textbook-evidence"
        / parent["inventory_sha256"]
        / "literal-resolution.lock"
    )
    with schema_lease(lock, timeout=0):
        job = ResolutionJob(
            TextbookStore(root),
            inventory,
            parent,
            manifest["recipe"]["model_digest"],
            0,
        )
        if job.key != job_key:
            raise ValueError("requires_unchanged_resolution_recipe")
        number = int(progress["stage"].split("-")[1])
        ref = affected_sources(parent)[number]
        source = inventory["sources"][ref]
        context = {
            "source": {"kind": source["kind"], "text": source["text"]},
            "parent_disposition": parent["source_dispositions"][ref],
            "parent_points": {
                key: point
                for key, point in parent["points"].items()
                if ref in point["citations"]
            },
        }
        _, binding = job.stage_input(
            PROMPT, DECISION, context, evidence_packet={"sources": {ref: source}}
        )
        target = folder / (progress["stage"] + ".json")
        if target.exists():
            raise ValueError("requires_absent_resolution_checkpoint_before_recovery")
        attempts = sorted((folder / "attempts").glob(progress["stage"] + "-*.json"))
        if len(attempts) != 2:
            raise ValueError("requires_preserved_two_failed_resolution_attempts")
        candidates, originals, proposed = [], {}, []
        for path in attempts:
            raw = path.read_bytes()
            row = json.loads(raw)
            if (
                row["input_sha256"] != binding
                or row.get("validation_error")
                != "missing_resolution_literal_source_support"
                or row.get("accepted_structure")
            ):
                raise ValueError("requires_exact_literal_only_failed_attempt")
            validate_metrics(row["model_metrics"], job.model_digest)
            raw_candidate = json.loads(row["raw_response"])
            proposed.append(sha(canonical(raw_candidate)))
            align = (
                complete_source_quotations if complete_source else literal_quotations
            )
            candidate, spans = align(raw_candidate, source)
            candidates.append((candidate, spans))
            originals[path.name] = sha(raw)
        if not complete_source and canonical(candidates[0][0]) != canonical(
            candidates[1][0]
        ):
            raise ValueError("requires_agreement_of_whitespace_restored_candidates")
        candidate, spans = candidates[0]
        receipt = {
            "policy": SOURCE_BINDING if complete_source else POLICY,
            "job": job_key,
            "stage": progress["stage"],
            "input_sha256": binding,
            "source_reference": ref,
            "source_text_sha256": sha(source["text"].encode()),
            "original_attempt_sha256": originals,
            "original_progress_sha256": sha(canonical(progress)),
            "data_sha256": sha(canonical(candidate)),
            "spans": spans,
            "recovery_module_sha256": sha(Path(__file__).read_bytes()),
            "model_calls": 0,
            "model_attempt_budget_reset": False,
            "independent_source_audit_still_required": True,
        }
        if complete_source:
            receipt.update(
                original_candidate_sha256=proposed[0],
                other_original_candidate_sha256=proposed[1:],
                selection_rule="earliest_original_failed_attempt_no_fact_edits",
                original_candidates_differ=len(set(proposed)) != 1,
                candidate_fact_fields_changed=False,
                model_quotations_claimed_literal=False,
                complete_original_used_as_citation=True,
                semantic_support_claimed=False,
                existing_audit_budget_preserved=True,
            )
        suffix = (
            "-complete-source-binding.json"
            if complete_source
            else "-whitespace-recovery.json"
        )
        receipt_path = folder / (progress["stage"] + suffix)
        if receipt_path.exists() and json.loads(receipt_path.read_bytes()) != receipt:
            raise ValueError("requires_unchanged_literal_recovery_receipt")
        atomic_json(receipt_path, receipt)
        atomic_json(
            target,
            {
                "input_sha256": binding,
                "data_sha256": receipt["data_sha256"],
                "data": candidate,
                "mechanical_recovery_sha256": sha(canonical(receipt)),
            },
        )
        if any(
            sha((folder / "attempts" / name).read_bytes()) != digest
            for name, digest in originals.items()
        ):
            raise ValueError("requires_preserved_original_literal_failures")
        return {
            "policy": receipt["policy"],
            "job": job_key,
            "stage": progress["stage"],
            "source_reference": ref,
            "restored_spans": len(spans),
            "receipt_sha256": sha(canonical(receipt)),
            "model_calls": 0,
            "model_attempt_budget_reset": False,
            "independent_source_audit_still_required": True,
        }
