"""Continue unused writing slots while retaining one explicitly failed draft.

This never accepts that section's prose, reruns exhausted calls or publishes a
reader. The complete private manuscript remains subject to editorial repair.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import psutil

from . import course_textbook_authoring as author
from . import course_textbook_composition as composition
from . import course_textbook_section_repair as repair
from .course_knowledge import atomic_json, schema_lease
from .course_library import canonical
from .course_textbook_evidence import sha
from .course_textbook_ledger import TextbookStore
from .course_wiki_text_inference import validate_metrics

POLICY = "private-composition-continue-with-explicitly-unaccepted-section-v1"


def require(ok, reason):
    if not ok:
        raise ValueError("requires_private_provisional_continuation_" + reason)


def prepare(root, inventory_key, ledger_job, job_key, section):
    from .course_pdf_ocr import local_model_digest

    require(re.fullmatch(r"[a-f0-9]{32}", job_key) is not None, "job_identity")
    require(re.fullmatch(r"section-[0-9]{4}", section) is not None, "section_identity")
    root = Path(root).resolve()
    inventory, working = composition.load_inputs(root, inventory_key, ledger_job)
    folder = root / "wiki/textbook-composition-jobs" / job_key
    state = json.loads((folder / "progress.json").read_bytes())
    try:
        alive = (
            abs(psutil.Process(state["pid"]).create_time() - state["created"]) < 0.001
        )
    except psutil.NoSuchProcess:
        alive = False
    require(not alive, "previous_writer_exited")
    execution_path = folder / (section + "-provisional-continuation.json")
    if not execution_path.exists():
        require(
            state["state"] == "stopped"
            and state["stage"] == section + "-repair-review",
            "failed_repair_review",
        )
    job = composition.AuthoringJob(
        TextbookStore(root), inventory, working, local_model_digest("qwen3.5:9b"), 0
    )
    require(job.key == job_key, "unchanged_original_job")
    plan = repair.saved(folder / "concept-plan.json")["data"]
    units = repair.saved(folder / "writing-allocation.json")["data"]
    number = int(section.removeprefix("section-"))
    require(
        number < len(units) and units[number]["id"] == section,
        "same_writing_allocation",
    )
    ids = units[number]["point_ids"]
    context = repair.context_for_unit(folder, number, units, plan, working, inventory)
    packet = composition.original_packet(ids, working, inventory)
    require(
        not (folder / (section + "-write.json")).exists()
        and not (folder / (section + "-repair.json")).exists(),
        "failed_draft_still_unaccepted",
    )
    files = sorted((folder / "attempts").glob(section + "-repair-0*.json"))
    require(
        [p.name for p in files]
        == [section + "-repair-01.json", section + "-repair-02.json"],
        "exhausted_rewrite_budget",
    )
    review = repair.saved(folder / (section + "-repair-review.json"))
    audit = review["data"]
    require(not all(author.verdicts(audit)), "actual_semantic_review_failure")
    candidate = None
    attempts = {}
    for path in files:
        record = json.loads(path.read_bytes())
        attempts[path.name] = sha(path.read_bytes())
        if (
            record.get("error_type") != "ValueError"
            or record.get("validation_error") != repair.ERROR
        ):
            continue
        validate_metrics(record["model_metrics"], job.model_digest)
        value = json.loads(record["raw_response"])
        repair.machine_audit(value, ids, working, inventory)
        _, binding = job.stage_input(
            composition.PROMPTS[author.REVIEW_PROMPT],
            author.review_schema(value, ids),
            {**context, "candidate": value},
            evidence_packet=packet,
        )
        if binding == review["input_sha256"] and candidate is None:
            candidate = value
    require(candidate is not None, "exact_reviewed_failed_candidate")
    _, write_binding = job.stage_input(
        composition.PROMPTS[author.WRITE_PROMPT],
        composition.section_schema(ids, working),
        context,
        evidence_packet=packet,
    )
    # This new controller freezes its behavior separately while every model
    # stage keeps the original job identity and its original persistent budget.
    proof = {
        "policy": POLICY,
        "job": job_key,
        "section": section,
        "candidate_sha256": sha(canonical(candidate)),
        "failed_review_sha256": sha(canonical(audit)),
        "failed_review_input_sha256": review["input_sha256"],
        "write_input_sha256": write_binding,
        "original_repair_attempts": attempts,
        "additional_calls_for_failed_section": 0,
        "original_failed_section_accepted": False,
        "reader_publication_allowed": False,
        "source_deletion_allowed": False,
        "module_sha256": sha(Path(__file__).read_bytes()),
        "repair_helper_sha256": sha(Path(repair.__file__).read_bytes()),
    }
    if execution_path.exists():
        require(
            json.loads(execution_path.read_bytes()) == proof,
            "unchanged_continuation_contract",
        )
    else:
        atomic_json(execution_path, proof)
    return inventory, working, context, candidate, audit, proof


def protocol_for(root, inventory_key, ledger_job, job_key, section):
    inventory, working, base, candidate, audit, proof = prepare(
        root, inventory_key, ledger_job, job_key, section
    )
    number = int(section.removeprefix("section-"))

    class ProvisionalJob(composition.AuthoringJob):
        def stage(self, name, prompt, schema, context, validate, **kwargs):
            if name not in {
                section + suffix
                for suffix in ("-write", "-review", "-repair", "-repair-review")
            }:
                return super().stage(name, prompt, schema, context, validate, **kwargs)
            suffix = name.removeprefix(section)
            if suffix == "-write":
                require(
                    context == base and prompt == author.WRITE_PROMPT,
                    "same_unaccepted_write_context",
                )
                expected_binding = proof["write_input_sha256"]
            elif suffix == "-repair":
                require(
                    context == {**base, "candidate": candidate, "review": audit}
                    and prompt == author.REPAIR_PROMPT,
                    "same_unaccepted_repair_context",
                )
                expected_binding = None
            else:
                require(
                    context == {**base, "candidate": candidate}
                    and prompt == author.REVIEW_PROMPT,
                    "same_failed_review_context",
                )
                expected_binding = proof["failed_review_input_sha256"]
            _, binding = self.stage_input(
                composition.PROMPTS[prompt], schema, context, **kwargs
            )
            require(
                expected_binding is None or binding == expected_binding,
                "same_frozen_request",
            )
            self.progress("retaining_unaccepted_internal_section", name)
            # No accepted cache is written and the validation callback is not
            # represented as passed. The failed audit reaches assemble/issues.
            return copy.deepcopy(
                audit if suffix in {"-review", "-repair-review"} else candidate
            )

        def progress(self, state, stage, **extra):
            unaccepted = int(self.finished_sections > number)
            super().progress(
                state,
                stage,
                **{
                    "draft_sections_processed": self.finished_sections,
                    "sections_retained_unaccepted": unaccepted,
                    "completed_sections": max(0, self.finished_sections - unaccepted),
                    **extra,
                },
            )

    def assemble(inv, ledger, plan, units, sections):
        value = composition.assemble(inv, ledger, plan, units, sections)
        value["unaccepted_sections"] = {
            section: {
                "candidate_sha256": proof["candidate_sha256"],
                "failed_review_sha256": proof["failed_review_sha256"],
                "accepted": False,
            }
        }
        return value

    def final_issues(inv, ledger, private):
        return [
            *composition.final_issues(inv, ledger, private),
            {
                "kind": "unaccepted_body_section_requires_full_manuscript_editorial_repair",
                "section": section,
                "retained_failed_review_sha256": proof["failed_review_sha256"],
                "original_budget_still_exhausted": True,
                "reader_publication_allowed": False,
            },
        ]

    return SimpleNamespace(
        POLICY=composition.POLICY,
        CONTRACT=composition.CONTRACT,
        load_inputs=composition.load_inputs,
        AuthoringJob=ProvisionalJob,
        organize=composition.organize,
        writing_units=composition.writing_units,
        original_packet=composition.original_packet,
        writing_context=composition.writing_context,
        section_schema=composition.section_schema,
        validate_section=composition.validate_section,
        assemble=assemble,
        final_issues=final_issues,
    )


def run(root, inventory_key, ledger_job, job_key, section):
    root = Path(root).resolve()
    with schema_lease(
        root / "wiki" / (job_key + "-provisional-controller.lock"), timeout=0
    ):
        protocol = protocol_for(root, inventory_key, ledger_job, job_key, section)
        return author.run(root, inventory_key, ledger_job, protocol=protocol)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("inventory", "ledger-job", "job", "section"):
        parser.add_argument("--" + flag, required=True)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("command", choices=["run", "guard"])
    args = parser.parse_args(argv)
    try:
        if args.command == "guard":
            from .course_archive_gpu import checked_sample
            from .course_gpu_guard import run as guarded

            guarded(
                args.root,
                [
                    sys.executable,
                    "-m",
                    __spec__.name,
                    "--root",
                    str(args.root.resolve()),
                    "--inventory",
                    args.inventory,
                    "--ledger-job",
                    args.ledger_job,
                    "--job",
                    args.job,
                    "--section",
                    args.section,
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
                        args.inventory,
                        args.ledger_job,
                        args.job,
                        args.section,
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
