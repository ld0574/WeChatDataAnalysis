"""Recover only colliding concept order numbers, without another model call."""

from __future__ import annotations

import argparse
import copy
import json
import re
from pathlib import Path

import jsonschema
import psutil

from . import course_textbook_authoring as author
from . import course_textbook_composition as composition
from .course_knowledge import atomic_json, schema_lease
from .course_library import canonical
from .course_pdf_ocr import local_model_digest
from .course_textbook_evidence import sha
from .course_textbook_ledger import TextbookStore
from .course_wiki_text_inference import validate_metrics

ERROR = "missing_textbook_nonempty_ordered_concept_group"
POLICY = "concept-order-collision-stable-input-tie-break-v1"
INDEX_POLICY = "complete-one-based-concept-groups-stable-input-tie-break-v1"


def require(ok, reason):
    if not ok:
        raise ValueError("requires_concept_order_recovery_" + reason)


def normalize(value, cards, *, final):
    """Keep group semantics and strict order comparisons; break equal ranks only."""
    jsonschema.validate(value, author.grouping_schema(cards, final=final))
    require(set(value["assignments"]) == set(cards), "exact_input_assignment")
    headings = [g["heading"].strip().casefold() for g in value["groups"]]
    require(len(headings) == len(set(headings)), "unique_group_headings")
    require(
        all(g["heading"].strip() and g["purpose"].strip() for g in value["groups"]),
        "nonempty_group_semantics",
    )
    require(
        all(
            0 <= row["group"] < len(value["groups"])
            for row in value["assignments"].values()
        ),
        "actual_group_membership",
    )
    order = {key: index for index, key in enumerate(cards)}
    result = copy.deepcopy(value)
    collisions = 0
    for number in range(len(value["groups"])):
        children = [
            key for key, row in value["assignments"].items() if row["group"] == number
        ]
        require(bool(children), "no_empty_groups")
        ranks = [value["assignments"][key]["order"] for key in children]
        if len(ranks) == len(set(ranks)):
            continue
        collisions += len(ranks) - len(set(ranks))
        ranked = sorted(
            children, key=lambda key: (value["assignments"][key]["order"], order[key])
        )
        for rank, key in enumerate(ranked):
            result["assignments"][key]["order"] = rank
    require(collisions > 0, "actual_order_collision")
    author.grouped(result, cards, final=final)
    return result


def normalize_one_based(value, cards, *, final):
    """Translate only a complete 1..N group index; never infer missing groups."""
    jsonschema.validate(value, author.grouping_schema(cards, final=final))
    require(set(value["assignments"]) == set(cards), "exact_input_assignment")
    require(
        {row["group"] for row in value["assignments"].values()}
        == set(range(1, len(value["groups"]) + 1)),
        "complete_unambiguous_one_based_groups",
    )
    result = copy.deepcopy(value)
    for row in result["assignments"].values():
        row["group"] -= 1
    collisions = any(
        len(ranks) != len(set(ranks))
        for number in range(len(result["groups"]))
        for ranks in [
            [
                row["order"]
                for row in result["assignments"].values()
                if row["group"] == number
            ]
        ]
    )
    if collisions:
        result = normalize(result, cards, final=final)
    author.grouped(result, cards, final=final)
    return result


def planning_context(job, working, stage):
    match = re.fullmatch(r"concepts-([0-9]{2})-([0-9]{4})", stage)
    require(match is not None, "planning_stage_identity")
    depth, index = map(int, match.groups())
    cards = {
        key: {field: p[field] for field in ("label", "teaching", "category")}
        for key, p in working["points"].items()
    }
    prompt = composition.PROMPTS[author.GROUP_PROMPT]
    for level in range(depth + 1):
        batches = author.chunks(cards, author.PLAN_CHARACTERS)
        final = len(batches) == 1
        if level == depth:
            require(index < len(batches), "existing_planning_partition")
            batch = batches[index]
            return (
                batch,
                final,
                {"items": batch, "mode": "chapters" if final else "concepts"},
            )
        require(not final, "no_successor_after_complete_plan")
        following = {}
        for position, batch in enumerate(batches):
            name = f"concepts-{level:02d}-{position:04d}"
            path = job.root / (name + ".json")
            require(path.is_file(), "prior_accepted_planning_stage")
            value = json.loads(path.read_bytes())
            context = {"items": batch, "mode": "concepts"}
            _, binding = job.stage_input(
                prompt, author.grouping_schema(batch, final=False), context
            )
            require(
                value["input_sha256"] == binding
                and value["data_sha256"] == sha(canonical(value["data"])),
                "unchanged_prior_planning_cache",
            )
            for number, group in enumerate(
                author.grouped(value["data"], batch, final=False)
            ):
                following[f"N{level:02d}_{position:04d}_{number:02d}"] = {
                    field: group[field] for field in ("heading", "purpose")
                }
        require(len(following) < len(cards), "prior_plan_compression")
        cards = following
    raise AssertionError("unreachable planning context")


def recover(root, inventory_key, ledger_job, job_key, stage, *, one_based=False):
    root = Path(root).resolve()
    require(re.fullmatch(r"[a-f0-9]{32}", job_key) is not None, "valid_job_identity")
    require(
        re.fullmatch(r"concepts-[0-9]{2}-[0-9]{4}", stage) is not None,
        "planning_stage_identity",
    )
    inventory, working = composition.load_inputs(root, inventory_key, ledger_job)
    TextbookStore(root).checkpoint()
    folder = root / "wiki/textbook-composition-jobs" / job_key
    progress = json.loads((folder / "progress.json").read_bytes())
    require(
        progress["state"] == "stopped" and progress["stage"] == stage,
        "exact_stopped_planning_stage",
    )
    try:
        alive = (
            abs(psutil.Process(progress["pid"]).create_time() - progress["created"])
            < 0.001
        )
    except psutil.NoSuchProcess:
        alive = False
    require(not alive, "worker_exited")
    with schema_lease(
        root / "wiki/textbook-evidence" / inventory_key / "authoring-worker.lock",
        timeout=0,
    ):
        job = composition.AuthoringJob(
            TextbookStore(root), inventory, working, local_model_digest("qwen3.5:9b"), 0
        )
        require(job.key == job_key, "unchanged_bound_job")
        contract = json.loads(
            (
                root / "wiki/textbook-keyed-jobs" / ledger_job / composition.CONTRACT
            ).read_bytes()
        )
        require(
            contract
            == {
                "inventory_sha256": inventory_key,
                "ledger_sha256": sha(canonical(working)),
                "job": job_key,
            },
            "unchanged_execution_contract",
        )
        cache = folder / (stage + ".json")
        receipt = folder / (
            stage + ("-index-recovery.json" if one_based else "-order-recovery.json")
        )
        require(not cache.exists() and not receipt.exists(), "new_cache_only")
        cards, final, context = planning_context(job, working, stage)
        prompt = composition.PROMPTS[author.GROUP_PROMPT]
        schema, binding = job.stage_input(
            prompt, author.grouping_schema(cards, final=final), context
        )
        attempts = sorted((folder / "attempts").glob(stage + "-*.json"))
        require(
            [p.name for p in attempts] == [stage + "-01.json", stage + "-02.json"],
            "original_exhausted_two_attempt_budget",
        )
        raw_files, candidates, originals, prior_failure = {}, [], [], ""
        rejected = []
        for number, path in enumerate(attempts):
            raw = path.read_bytes()
            record = json.loads(raw)
            require(
                record["input_sha256"] == binding
                and record["attempt"] == number + 1
                and record["stage"] == stage
                and record["model_digest"] == job.model_digest
                and record["error_type"] == "ValueError"
                and record["validation_error"] == ERROR,
                "only_original_order_collision_failures",
            )
            validate_metrics(record["model_metrics"], job.model_digest)
            messages = [
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": json.dumps(
                        {**context, "previous_validation_failure": prior_failure},
                        ensure_ascii=False,
                    ),
                },
            ]
            require(
                record["request_sha256"] == sha(canonical(messages)),
                "exact_original_request",
            )
            prior_failure = record["validation_error"]
            value = json.loads(record["raw_response"])
            originals.append(value)
            raw_files[path.name] = sha(raw)
            if one_based:
                try:
                    candidate = normalize_one_based(value, cards, final=final)
                except (ValueError, jsonschema.ValidationError) as exc:
                    rejected.append(
                        {"attempt": number + 1, "error_sha256": sha(str(exc).encode())}
                    )
                    continue
            else:
                candidate = normalize(value, cards, final=final)
            candidates.append((number, candidate))
        # No comparison selects a preferred meaning: always preserve the first
        # complete candidate, including all its original group memberships.
        require(bool(candidates), "complete_structural_candidate")
        selected, value = candidates[0]
        original = originals[selected]
        changes = {
            key: {"old": original["assignments"][key]["order"], "new": row["order"]}
            for key, row in value["assignments"].items()
            if row["order"] != original["assignments"][key]["order"]
        }
        proof = {
            "policy": INDEX_POLICY if one_based else POLICY,
            "job": job_key,
            "stage": stage,
            "input_sha256": binding,
            "data_sha256": sha(canonical(value)),
            "model_calls": 0,
            "original_attempts": raw_files,
            "candidate_selection": "earliest_complete_original_response",
            "selected_attempt": selected + 1,
            "rejected_structural_candidates": rejected,
            "original_group_index_base": 1 if one_based else 0,
            "group_numbers_changed": len(cards) if one_based else 0,
            "tie_break": "original_input_key_order",
            "order_changes": changes,
            "group_memberships_changed": False,
            "semantic_acceptance": False,
            "reader_publication_allowed": False,
            "module_sha256": sha(Path(__file__).read_bytes()),
        }
        atomic_json(receipt, proof)
        atomic_json(
            cache,
            {
                "input_sha256": binding,
                "data_sha256": proof["data_sha256"],
                "data": value,
                "recovery_sha256": sha(receipt.read_bytes()),
            },
        )
        require(
            all(
                sha((folder / "attempts" / name).read_bytes()) == digest
                for name, digest in raw_files.items()
            ),
            "preserved_original_attempts",
        )
        return {
            "state": (
                "concept_index_cache_recovered"
                if one_based
                else "concept_order_cache_recovered"
            ),
            "job": job_key,
            "stage": stage,
            "model_calls": 0,
            "changed_order_numbers": len(changes),
            "changed_group_numbers": len(cards) if one_based else 0,
            "selected_attempt": selected + 1,
            "receipt_sha256": sha(receipt.read_bytes()),
            "reader_publication_allowed": False,
        }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("inventory", "ledger-job", "job", "stage"):
        parser.add_argument("--" + flag, required=True)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--one-based-groups", action="store_true")
    args = parser.parse_args(argv)
    try:
        print(
            json.dumps(
                recover(
                    args.root,
                    args.inventory,
                    args.ledger_job,
                    args.job,
                    args.stage,
                    one_based=args.one_based_groups,
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
