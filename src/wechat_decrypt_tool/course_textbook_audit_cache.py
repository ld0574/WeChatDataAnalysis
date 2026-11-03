"""Fail closed on contradictory audit verdicts without generating new content."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import jsonschema
import psutil

from . import course_textbook_resolution as audit
from . import course_textbook_source_revision as revision
from .course_knowledge import atomic_json, schema_lease
from .course_library import canonical
from .course_textbook_evidence import sha
from .course_textbook_ledger import TextbookStore
from .course_wiki_text_inference import validate_metrics

POLICY = "contradictory-source-audit-conservative-downgrade-v1"


def conservative_verdict(value):
    jsonschema.validate(value, audit.AUDIT)
    if (
        value["verdict"] != "supported"
        or not value["missing_information"]
        or not value["reason"].strip()
        or any(not item.strip() for item in value["missing_information"])
    ):
        raise ValueError("requires_supported_audit_with_explicit_missing_information")
    result = copy.deepcopy(value)
    result["verdict"] = "needs_changes"
    audit.validate_audit(result)
    return result


def require_exited(progress):
    try:
        process = psutil.Process(progress["pid"])
        if abs(process.create_time() - progress["created"]) < 0.01:
            raise ValueError("requires_audit_worker_confirmed_exited")
    except psutil.NoSuchProcess:
        pass


def recover(root, job_key):
    root = Path(root).resolve()
    store = TextbookStore(root)
    store.checkpoint()
    if not re.fullmatch(r"[a-f0-9]{32}", job_key):
        raise ValueError("requires_audit_cache_job_id")
    folder = root / "wiki/textbook-keyed-jobs" / job_key
    raw_progress = (folder / "progress.json").read_bytes()
    progress = json.loads(raw_progress)
    if progress["state"] != "stopped" or not re.fullmatch(
        r"source-\d{4}-audit", progress["stage"]
    ):
        raise ValueError("requires_stopped_source_revision_audit")
    require_exited(progress)
    manifest = json.loads((folder / "manifest.json").read_bytes())
    parent_sha = manifest["recipe"]["extra"]["parent_sha256"]
    matches = []
    for path in folder.parent.glob("*/source-revision-execution.json"):
        row = json.loads(path.read_bytes())
        if row.get("job") == job_key and row.get("parent_sha256") == parent_sha:
            matches.append(path.parent.name)
    if len(matches) != 1:
        raise ValueError("requires_unique_bound_source_revision_parent")
    inventory, parent, old, old_audits = revision.inputs(root, matches[0])
    if sha(canonical(parent)) != parent_sha:
        raise ValueError("requires_unchanged_audit_revision_parent")
    lock = (
        root
        / "wiki/textbook-evidence"
        / parent["inventory_sha256"]
        / "literal-resolution.lock"
    )
    with schema_lease(lock, timeout=0):
        require_exited(progress)
        if (folder / "progress.json").read_bytes() != raw_progress:
            raise ValueError("requires_unchanged_stopped_audit_progress")
        job = revision.SourceRevisionJob(
            store, inventory, parent, manifest["recipe"]["model_digest"], 0
        )
        if job.key != job_key:
            raise ValueError("requires_unchanged_source_revision_recipe")
        number = int(progress["stage"].split("-")[1])
        if number >= len(old):
            raise ValueError("requires_known_source_revision_audit")
        ref = sorted(old)[number]
        source = inventory["sources"][ref]
        packet = {"sources": {ref: source}}
        context = {
            "source": {"kind": source["kind"], "text": source["text"]},
            "candidate": old[ref],
            "prior_audit": old_audits[ref],
        }
        _, body_binding = job.stage_input(
            revision.PROMPT, revision.BODY, context, evidence_packet=packet
        )
        body = json.loads((folder / f"source-{number:04d}-revise.json").read_bytes())
        if body["input_sha256"] != body_binding or body["data_sha256"] != sha(
            canonical(body["data"])
        ):
            raise ValueError("requires_unchanged_original_revision_candidate")
        candidate = revision.source_decision(body["data"], source)
        _, binding = job.stage_input(
            audit.REVIEW,
            audit.AUDIT,
            {**context, "candidate": candidate},
            evidence_packet=packet,
        )
        target = folder / (progress["stage"] + ".json")
        if target.exists():
            raise ValueError("requires_absent_audit_checkpoint_before_downgrade")
        attempts = sorted((folder / "attempts").glob(progress["stage"] + "-*.json"))
        if len(attempts) != 2:
            raise ValueError("requires_preserved_two_failed_audit_attempts")
        candidates, originals = [], {}
        for path in attempts:
            raw = path.read_bytes()
            row = json.loads(raw)
            if (
                row["input_sha256"] != binding
                or row.get("validation_error")
                != "requires_resolution_consistent_audit_verdict"
                or row.get("accepted_structure")
            ):
                raise ValueError("requires_exact_contradictory_audit_failure")
            validate_metrics(row["model_metrics"], job.model_digest)
            candidates.append(conservative_verdict(json.loads(row["raw_response"])))
            originals[path.name] = sha(raw)
        # Keep the first complete review intact, changing only its positive verdict.
        result = candidates[0]
        receipt = {
            "policy": POLICY,
            "job": job_key,
            "stage": progress["stage"],
            "input_sha256": binding,
            "data_sha256": sha(canonical(result)),
            "source_reference": ref,
            "source_sha256": sha(canonical(source)),
            "candidate_sha256": sha(canonical(candidate)),
            "parent_sha256": parent_sha,
            "original_progress_sha256": sha(raw_progress),
            "original_attempt_sha256": originals,
            "changed_fields": ["verdict"],
            "selected_original_attempt": attempts[0].name,
            "model_calls": 0,
            "model_attempt_budget_reset": False,
            "missing_information_removed": False,
            "semantic_support_claimed": False,
            "recovery_module_sha256": sha(Path(__file__).read_bytes()),
        }
        receipt_path = folder / (progress["stage"] + "-conservative-verdict.json")
        if receipt_path.exists() and json.loads(receipt_path.read_bytes()) != receipt:
            raise ValueError("requires_unchanged_conservative_audit_receipt")
        if any(
            sha((folder / "attempts" / name).read_bytes()) != digest
            for name, digest in originals.items()
        ):
            raise ValueError("requires_preserved_original_audit_failures")
        atomic_json(receipt_path, receipt)
        atomic_json(
            target,
            {
                "input_sha256": binding,
                "data_sha256": receipt["data_sha256"],
                "data": result,
                "mechanical_recovery_sha256": sha(canonical(receipt)),
            },
        )
        return {
            "job": job_key,
            "stage": progress["stage"],
            "source_reference": ref,
            "verdict": "needs_changes",
            "missing_information_count": len(result["missing_information"]),
            "model_calls": 0,
            "model_attempt_budget_reset": False,
            "receipt_sha256": sha(canonical(receipt)),
        }
