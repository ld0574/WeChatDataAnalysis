"""Retain invalid generated diagrams privately and continue original slots."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import psutil

from . import course_textbook_authoring as author
from . import course_textbook_composition as composition
from . import course_textbook_manuscript_continue as continuation
from . import course_textbook_reviewable_write as reviewable
from .course_knowledge import atomic_json, schema_lease
from .course_library import canonical
from .course_textbook_article import validate_graph
from .course_textbook_evidence import sha
from .course_wiki_text_inference import validate_metrics

POLICY = "private-invalid-generated-diagram-quarantine-with-original-budgets-v1"
CONTRACT = "diagram-quarantine-continuation-execution.json"


def require(ok, reason):
    if not ok:
        raise ValueError("requires_diagram_quarantine_" + reason)


def quarantine_graphs(candidate):
    result = copy.deepcopy(candidate)
    result["diagrams"] = []
    invalid = []
    for index, graph in enumerate(candidate["diagrams"]):
        try:
            validate_graph(
                {key: value for key, value in graph.items() if key != "citations"}
            )
        except ValueError as exc:
            invalid.append(
                {"index": index, "graph": graph, "error_sha256": sha(str(exc).encode())}
            )
        else:
            result["diagrams"].append(graph)
    require(
        {key: value for key, value in result.items() if key != "diagrams"}
        == {key: value for key, value in candidate.items() if key != "diagrams"},
        "body_mapping_and_all_other_fields_unchanged",
    )
    return result, invalid


def protocol_for(root, inventory_key, ledger_job, job_key, legacy_section):
    root = Path(root).resolve()
    folder = root / "wiki/textbook-composition-jobs" / job_key
    state = json.loads((folder / "progress.json").read_bytes())
    try:
        alive = (
            abs(psutil.Process(state["pid"]).create_time() - state["created"]) < 0.001
        )
    except psutil.NoSuchProcess:
        alive = False
    require(not alive, "previous_writer_exited")
    base = continuation.protocol_for(
        root, inventory_key, ledger_job, job_key, legacy_section
    )
    inventory, working = composition.load_inputs(root, inventory_key, ledger_job)
    execution = {
        "policy": POLICY,
        "job": job_key,
        "inventory_sha256": inventory_key,
        "ledger_job": ledger_job,
        "legacy_section": legacy_section,
        "module_sha256": sha(Path(__file__).read_bytes()),
        "controller_sha256": sha(Path(continuation.__file__).read_bytes()),
        "previous_contract_sha256": sha((folder / continuation.CONTRACT).read_bytes()),
        "additional_model_stage_roles": 0,
        "original_stage_attempt_budget": 2,
        "semantic_body_or_mapping_changes": False,
        "reader_publication_allowed": False,
    }
    path = folder / CONTRACT
    if path.exists():
        require(json.loads(path.read_bytes()) == execution, "unchanged_execution")
    else:
        require(
            state["state"] == "stopped" and state["stage"].endswith("-write"),
            "terminal_write_parent",
        )
        atomic_json(path, execution)
    quarantine_root = folder / "private-diagram-quarantines"
    quarantine_root.mkdir(exist_ok=True)
    for path in quarantine_root.glob("section-*.json"):
        proof = json.loads(path.read_bytes())
        require(
            proof["execution_sha256"] == sha(canonical(execution)),
            "same_quarantine_execution",
        )
        original = json.loads((folder / "attempts" / proof["attempt"]).read_bytes())
        require(
            sha((folder / "attempts" / proof["attempt"]).read_bytes())
            == proof["attempt_sha256"],
            "original_attempt_preserved",
        )
        candidate, graphs = quarantine_graphs(json.loads(original["raw_response"]))
        require(
            sha(canonical(candidate)) == proof["candidate_sha256"]
            and graphs == proof["invalid_diagrams"],
            "exact_private_diagram_quarantine",
        )

    class QuarantineJob(base.AuthoringJob):
        def failed_candidate(self, name, prompt, schema, context, kwargs):
            _, binding = self.stage_input(
                composition.PROMPTS[prompt], schema, context, **kwargs
            )
            previous, candidates = "", []
            for attempt in sorted((folder / "attempts").glob(name + "-*.json")):
                record = json.loads(attempt.read_bytes())
                require(
                    record["stage"] == name
                    and record["input_sha256"] == binding
                    and record["model_digest"] == self.model_digest
                    and record["request_sha256"]
                    == sha(canonical(self.messages(prompt, context, previous))),
                    "original_failed_write_identity",
                )
                previous = record.get("validation_error", "")
                if (
                    record.get("error_type") != "ValueError"
                    or previous != reviewable.repair.ERROR
                ):
                    continue
                validate_metrics(record["model_metrics"], self.model_digest)
                candidate = json.loads(record["raw_response"])
                try:
                    reviewable.validate_reviewable(
                        candidate, list(context["assigned_points"]), working, inventory
                    )
                except ValueError:
                    candidates.append((attempt, candidate))
                else:
                    return candidate
            for attempt, candidate in candidates:
                revised, invalid = quarantine_graphs(candidate)
                if not invalid:
                    continue
                try:
                    reviewable.validate_reviewable(
                        revised, list(context["assigned_points"]), working, inventory
                    )
                except ValueError:
                    continue
                proof = {
                    "policy": POLICY,
                    "section": name.removesuffix("-write"),
                    "execution_sha256": sha(canonical(execution)),
                    "attempt": attempt.name,
                    "attempt_sha256": sha(attempt.read_bytes()),
                    "candidate_sha256": sha(canonical(revised)),
                    "invalid_diagrams": invalid,
                    "body_or_mapping_changed": False,
                    "original_attempts_removed": False,
                    "model_calls_performed": 0,
                    "candidate_accepted": False,
                    "reader_publication_allowed": False,
                }
                path = quarantine_root / (proof["section"] + ".json")
                if path.exists():
                    require(
                        json.loads(path.read_bytes()) == proof,
                        "unchanged_quarantine_proof",
                    )
                else:
                    atomic_json(path, proof)
                return revised
            raise ValueError(
                "requires_diagram_quarantine_complete_body_with_valid_mapping"
            )

    return SimpleNamespace(**{**vars(base), "AuthoringJob": QuarantineJob})


def run(root, inventory_key, ledger_job, job_key, legacy_section):
    root = Path(root).resolve()
    with schema_lease(
        root / "wiki" / (job_key + "-diagram-quarantine-controller.lock"), timeout=0
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

            parameters = ["--root", str(args.root.resolve())]
            for flag in ("inventory", "ledger-job", "job", "legacy-section"):
                parameters += ["--" + flag, getattr(args, flag.replace("-", "_"))]
            guarded(
                args.root,
                [sys.executable, "-m", __spec__.name, *parameters, "run"],
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
