"""Finish private manuscript slots without accepting failed sections.

The original job, evidence, requests and persistent attempt limits stay in
place. Complete failed candidates can be retained privately; oversized review
or repair requests are deferred before inference, never silently shortened.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import jsonschema

from . import course_textbook_authoring as author
from . import course_textbook_composition as composition
from . import course_textbook_provisional_continue as provisional
from . import course_textbook_reviewable_write as reviewable
from .course_knowledge import atomic_json, schema_lease
from .course_library import canonical
from .course_textbook_evidence import sha
from .course_wiki_text_inference import validate_metrics

POLICY = "private-whole-manuscript-continuation-with-retained-failures-v1"
CONTEXT_LIMIT = 35000
CONTRACT = "private-manuscript-continuation-execution.json"


def require(ok, code):
    if not ok:
        raise ValueError("requires_private_manuscript_continuation_" + code)


def not_reviewed(schema):
    properties = schema["properties"]
    if "passed" in properties:
        return {
            "passed": False,
            "reason": "Not reviewed: original-stage execution deferred; editorial verification required.",
        }
    return {key: not_reviewed(value) for key, value in properties.items()}


def protocol_for(root, inventory_key, ledger_job, job_key, legacy_section):
    root = Path(root).resolve()
    base = provisional.protocol_for(
        root, inventory_key, ledger_job, job_key, legacy_section
    )
    inventory, working = composition.load_inputs(root, inventory_key, ledger_job)
    folder = root / "wiki/textbook-composition-jobs" / job_key
    receipts_root = folder / "private-manuscript-continuation"
    receipts_root.mkdir(exist_ok=True)
    allocation = reviewable.repair.saved(folder / "writing-allocation.json")["data"]
    contract_path = folder / CONTRACT
    previous_execution = (
        json.loads(contract_path.read_bytes()) if contract_path.exists() else None
    )
    bounded_sections = (
        previous_execution["bounded_mapping_reason_sections"]
        if previous_execution is not None
        else [
            unit["id"]
            for unit in allocation
            if not any((folder / "attempts").glob(unit["id"] + "-*.json"))
            and not (folder / (unit["id"] + "-write.json")).exists()
        ]
    )
    execution = {
        "policy": POLICY,
        "job": job_key,
        "inventory_sha256": inventory_key,
        "ledger_job": ledger_job,
        "working_sha256": sha(canonical(working)),
        "legacy_section": legacy_section,
        "context_limit_characters": CONTEXT_LIMIT,
        "additional_model_stage_roles": 0,
        "original_stage_attempt_budget": 2,
        "original_semantic_repairs_per_section": 1,
        "original_job_recipe_changed": False,
        "source_or_body_truncation_allowed": False,
        "reader_publication_allowed": False,
        "allocation_sha256": sha(canonical(allocation)),
        "bounded_mapping_reason_sections": bounded_sections,
        "bounded_mapping_reason_characters": reviewable.MAPPING_REASON_CHARACTERS,
        "body_or_teaching_point_limits_changed": False,
        "modules": {
            module.__name__: sha(Path(module.__file__).read_bytes())
            for module in (sys.modules[__name__], provisional, reviewable)
        },
    }
    if contract_path.exists():
        require(
            json.loads(contract_path.read_bytes()) == execution, "unchanged_contract"
        )
    else:
        atomic_json(contract_path, execution)

    receipts = {}
    for path in receipts_root.glob("section-*.json"):
        saved = json.loads(path.read_bytes())
        require(
            saved["section"] == path.stem
            and saved["contract_sha256"] == sha(canonical(execution)),
            "receipt_identity",
        )
        receipts[path.stem] = saved

    bounded_points = {
        frozenset(unit["point_ids"])
        for unit in allocation
        if unit["id"] in bounded_sections
    }

    def section_schema(ids, ledger):
        return (
            reviewable.repair_schema(ids, ledger)
            if frozenset(ids) in bounded_points
            else composition.section_schema(ids, ledger)
        )

    def validate_section(candidate, ids, ledger, inv):
        jsonschema.validate(candidate, section_schema(ids, ledger))
        composition.validate_section(candidate, ids, ledger, inv)

    class ManuscriptJob(base.AuthoringJob):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            require(self.key == job_key, "original_job_identity")

        def verify_receipt(self, receipt):
            require(
                receipt["candidate_sha256"] == sha(canonical(receipt["candidate"]))
                and receipt["context_sha256"] == sha(canonical(receipt["context"])),
                "unchanged_private_candidate_and_context",
            )
            for name, digest in receipt["original_attempts"].items():
                require(
                    sha((folder / "attempts" / name).read_bytes()) == digest,
                    "original_attempts_preserved",
                )
            ids = list(receipt["candidate"]["coverage"])
            reviewable.validate_reviewable(
                receipt["candidate"], ids, working, inventory
            )

        def retain(self, section, context, candidate):
            context = {
                key: value
                for key, value in context.items()
                if key not in {"candidate", "review"}
            }
            ids = list(context["assigned_points"])
            reviewable.validate_reviewable(candidate, ids, working, inventory)
            if section not in receipts:
                receipts[section] = {
                    "section": section,
                    "contract_sha256": sha(canonical(execution)),
                    "context": context,
                    "context_sha256": sha(canonical(context)),
                    "candidate": candidate,
                    "candidate_sha256": sha(canonical(candidate)),
                    "current_body_sha256": sha(canonical(candidate)),
                    "original_attempts": {
                        path.name: sha(path.read_bytes())
                        for path in (folder / "attempts").glob(section + "-*.json")
                    },
                    "deferred_stages": {},
                    "resolved_with_original_roles": False,
                    "reader_publication_allowed": False,
                }
            receipt = receipts[section]
            self.verify_receipt(receipt)
            require(receipt["context"] == context, "same_section_context")
            return receipt

        def save_receipt(self, receipt):
            self.verify_receipt(receipt)
            atomic_json(receipts_root / (receipt["section"] + ".json"), receipt)

        def failed_candidate(self, name, prompt, schema, context, kwargs):
            _, binding = self.stage_input(
                composition.PROMPTS[prompt], schema, context, **kwargs
            )
            previous = ""
            for path in sorted((folder / "attempts").glob(name + "-*.json")):
                record = json.loads(path.read_bytes())
                require(
                    record["stage"] == name
                    and record["input_sha256"] == binding
                    and record["model_digest"] == self.model_digest,
                    "same_failed_write_binding",
                )
                messages = self.messages(prompt, context, previous)
                require(
                    record["request_sha256"] == sha(canonical(messages)),
                    "same_failed_write_request",
                )
                previous = record.get("validation_error", "")
                if (
                    record.get("error_type") != "ValueError"
                    or previous != reviewable.repair.ERROR
                ):
                    continue
                require(
                    isinstance(record.get("model_metrics"), dict),
                    "actual_model_metrics",
                )
                validate_metrics(record["model_metrics"], self.model_digest)
                candidate = json.loads(record["raw_response"])
                reviewable.validate_reviewable(
                    candidate, list(context["assigned_points"]), working, inventory
                )
                return candidate
            raise ValueError(
                "requires_private_manuscript_continuation_complete_failed_write"
            )

        def messages(self, prompt, context, previous):
            return [
                {
                    "role": "system",
                    "content": composition.PROMPTS[prompt] + self.range_notice,
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {**context, "previous_validation_failure": previous},
                        ensure_ascii=False,
                    ),
                },
            ]

        def request_size(self, name, prompt, schema, context, kwargs):
            actual_schema, _ = self.stage_input(
                composition.PROMPTS[prompt], schema, context, **kwargs
            )
            paths = sorted((folder / "attempts").glob(name + "-*.json"))
            previous = (
                json.loads(paths[-1].read_bytes()).get("validation_error", "")
                if paths
                else ""
            )
            return len(
                canonical(
                    {
                        "messages": self.messages(prompt, context, previous),
                        "schema": actual_schema,
                    }
                ).decode("utf-8")
            )

        def defer(self, section, name, prompt, schema, context, kwargs, reason):
            candidate = context.get("candidate")
            require(candidate is not None, "complete_candidate_before_deferral")
            receipt = self.retain(section, context, candidate)
            _, binding = self.stage_input(
                composition.PROMPTS[prompt], schema, context, **kwargs
            )
            value = not_reviewed(schema) if name.endswith("review") else candidate
            deferred = {
                "input_sha256": binding,
                "reason": reason,
                "result": value,
                "result_sha256": sha(canonical(value)),
                "actual_factual_review": False,
                "model_call_performed": False,
            }
            existing = receipt["deferred_stages"].get(name)
            require(existing is None or existing == deferred, "unchanged_deferral")
            receipt["deferred_stages"][name] = deferred
            self.save_receipt(receipt)
            self.progress("retaining_unaccepted_internal_section", name)
            return copy.deepcopy(value)

        def stage(self, name, prompt, schema, context, validate, **kwargs):
            match = re.fullmatch(
                r"(section-[0-9]{4})(-write|-review|-repair|-repair-review)", name
            )
            if match is None or match[1] == legacy_section:
                return super().stage(name, prompt, schema, context, validate, **kwargs)
            section, suffix = match.groups()
            receipt = receipts.get(section)
            if receipt is not None:
                self.verify_receipt(receipt)
                if suffix == "-write":
                    require(
                        context == receipt["context"], "same_retained_write_context"
                    )
                    self.progress("retaining_unaccepted_internal_section", name)
                    return copy.deepcopy(receipt["candidate"])
                if name in receipt["deferred_stages"]:
                    deferred = receipt["deferred_stages"][name]
                    _, binding = self.stage_input(
                        composition.PROMPTS[prompt], schema, context, **kwargs
                    )
                    require(
                        binding == deferred["input_sha256"]
                        and deferred["result_sha256"]
                        == sha(canonical(deferred["result"])),
                        "same_deferred_request_and_result",
                    )
                    self.progress("retaining_unaccepted_internal_section", name)
                    return copy.deepcopy(deferred["result"])
                if (
                    suffix == "-repair-review"
                    and section + "-repair" in receipt["deferred_stages"]
                ):
                    return self.defer(
                        section,
                        name,
                        prompt,
                        schema,
                        context,
                        kwargs,
                        "no_repaired_candidate_because_original_repair_was_deferred",
                    )
            if not (folder / (name + ".json")).exists():
                size = self.request_size(name, prompt, schema, context, kwargs)
                if size > CONTEXT_LIMIT:
                    return self.defer(
                        section,
                        name,
                        prompt,
                        schema,
                        context,
                        kwargs,
                        "complete_original_request_exceeds_character_limit",
                    )
            try:
                result = super().stage(
                    name, prompt, schema, context, validate, **kwargs
                )
            except ValueError as exc:
                if str(exc) != "wiki_stage_attempt_budget_exhausted_" + name:
                    raise
                if suffix == "-write":
                    candidate = self.failed_candidate(
                        name, prompt, schema, context, kwargs
                    )
                    receipt = self.retain(section, context, candidate)
                    self.save_receipt(receipt)
                    self.progress("retaining_unaccepted_internal_section", name)
                    return copy.deepcopy(candidate)
                return self.defer(
                    section,
                    name,
                    prompt,
                    schema,
                    context,
                    kwargs,
                    "original_stage_attempt_budget_exhausted",
                )
            if suffix == "-repair-review":
                if all(author.verdicts(result)):
                    if section in receipts:
                        composition.validate_section(
                            context["candidate"],
                            list(context["assigned_points"]),
                            working,
                            inventory,
                        )
                        receipts[section]["resolved_with_original_roles"] = True
                        receipts[section]["current_body_sha256"] = sha(
                            canonical(context["candidate"])
                        )
                        receipts[section]["passing_original_review_sha256"] = sha(
                            canonical(result)
                        )
                        self.save_receipt(receipts[section])
                else:
                    receipt = self.retain(section, context, context["candidate"])
                    receipt["current_body_sha256"] = sha(
                        canonical(context["candidate"])
                    )
                    self.save_receipt(receipt)
            return result

        def progress(self, state, stage, **extra):
            unaccepted = int(self.finished_sections > int(legacy_section[-4:])) + sum(
                self.finished_sections > int(section[-4:])
                and not receipt["resolved_with_original_roles"]
                for section, receipt in receipts.items()
            )
            composition.AuthoringJob.progress(
                self,
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
        require(
            all(
                sha(canonical(sections[section])) == receipt["current_body_sha256"]
                for section, receipt in receipts.items()
            ),
            "actual_assembled_body_matches_retained_or_reviewed_candidate",
        )
        private = base.assemble(inv, ledger, plan, units, sections)
        private["unaccepted_sections"].update(
            {
                section: {
                    "candidate_sha256": receipt["current_body_sha256"],
                    "initial_candidate_sha256": receipt["candidate_sha256"],
                    "deferred_stages": sorted(receipt["deferred_stages"]),
                    "accepted": False,
                }
                for section, receipt in receipts.items()
                if not receipt["resolved_with_original_roles"]
            }
        )
        return private

    def final_issues(inv, ledger, private):
        return [
            *base.final_issues(inv, ledger, private),
            *[
                {
                    "kind": "unaccepted_body_section_requires_full_manuscript_editorial_repair",
                    "section": section,
                    "candidate_sha256": receipt["current_body_sha256"],
                    "deferred_stages": sorted(receipt["deferred_stages"]),
                    "reader_publication_allowed": False,
                }
                for section, receipt in receipts.items()
                if not receipt["resolved_with_original_roles"]
            ],
        ]

    return SimpleNamespace(
        **{
            **vars(base),
            "AuthoringJob": ManuscriptJob,
            "section_schema": section_schema,
            "validate_section": validate_section,
            "assemble": assemble,
            "final_issues": final_issues,
        }
    )


def run(root, inventory_key, ledger_job, job_key, legacy_section):
    root = Path(root).resolve()
    with schema_lease(
        root / "wiki" / (job_key + "-manuscript-controller.lock"), timeout=0
    ):
        protocol = protocol_for(
            root, inventory_key, ledger_job, job_key, legacy_section
        )
        return author.run(root, inventory_key, ledger_job, protocol=protocol)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("inventory", "ledger-job", "job", "legacy-section"):
        parser.add_argument("--" + flag, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("command", choices=["run", "guard"])
    args = parser.parse_args(argv)
    try:
        if args.command == "guard":
            from .course_archive_gpu import checked_sample
            from .course_gpu_guard import run as guarded

            command = [
                sys.executable,
                "-m",
                __spec__.name,
                "--root",
                str(args.root.resolve()),
            ]
            for flag in ("inventory", "ledger-job", "job", "legacy-section"):
                command += ["--" + flag, getattr(args, flag.replace("-", "_"))]
            guarded(
                args.root,
                [*command, "run"],
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
                        args.legacy_section,
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
