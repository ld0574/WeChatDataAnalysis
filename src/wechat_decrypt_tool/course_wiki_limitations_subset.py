"""Select only whole generated claims whose witnesses are all literal."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import course_wiki_limitations_repair as limits
from . import course_wiki_synthesis as synthesis
from . import course_wiki_synthesis_repair as criteria
from .course_knowledge import atomic_json, schema_lease
from .course_library import canonical
from .course_lesson_exports import digest
from .course_video_visual_audit import ReadOnlyArchive
from .course_wiki import Wiki, publish_draft, validate_article
from .course_wiki_writer import evidence_schema

POLICY = "whole-claim-literal-limitations-subset-v1"


def selection(root, parent):
    folder, recipe = criteria.manifest(root, parent)
    limits.validate_lineage(root, recipe)
    saved = recipe["extra"]["limitations_repair"]
    spec, _, candidate = limits.repair_spec(root, saved["parent_job"], saved["chapter"])
    scoped, schema, context = limits.stage_input(recipe["packet"], candidate, spec)
    if (folder / (limits.STAGE + ".json")).exists():
        raise ValueError("limitations_subset_requires_failed_call")
    attempts = sorted((folder / "attempts").glob(limits.STAGE + "-*.json"))
    if [p.name for p in attempts] != [limits.STAGE + "-01.json"]:
        raise ValueError("limitations_subset_requires_exactly_one_call")
    record = json.loads(attempts[0].read_bytes())
    prompt = limits.PROMPT + recipe.get("original_range_notice", "")
    binding = digest(
        canonical(
            {
                "prompt": prompt,
                "schema": evidence_schema(schema, scoped),
                "context": context,
                "model_digest": recipe["model_digest"],
            }
        )
    )
    messages = [
        {"role": "system", "content": prompt},
        {
            "role": "user",
            "content": json.dumps(
                {**context, "previous_validation_failure": ""}, ensure_ascii=False
            ),
        },
    ]
    if (
        record.get("stage") != limits.STAGE
        or record.get("attempt") != 1
        or record.get("model_digest") != recipe["model_digest"]
        or record.get("input_sha256") != binding
        or record.get("request_sha256") != digest(canonical(messages))
        or record.get("error_type") != "ValueError"
        or record.get("accepted_structure")
    ):
        raise ValueError("limitations_subset_original_call_binding_changed")
    value = json.loads(record["raw_response"])
    try:
        limits.validate_result(value, scoped, schema)
    except ValueError as exc:
        if str(exc) != "limitations_repair_witness_not_literal":
            raise ValueError(
                "limitations_subset_requires_only_nonliteral_witness_failure"
            ) from None
    else:
        raise ValueError("limitations_subset_requires_failed_call")
    selected, rejected = [], []
    for n, item in enumerate(value["pitfalls"]):
        try:
            limits.validate_result({**value, "pitfalls": [item]}, scoped, schema)
        except ValueError as exc:
            if str(exc) != "limitations_repair_witness_not_literal":
                raise ValueError(
                    "limitations_subset_requires_only_nonliteral_witness_failure"
                ) from None
            rejected.append(n)
        else:
            selected.append(n)
    if not selected or not rejected:
        raise ValueError(
            "limitations_subset_requires_nonempty_strict_whole_claim_subset"
        )
    result = {**value, "pitfalls": [value["pitfalls"][n] for n in selected]}
    limits.validate_result(result, scoped, schema)
    article = limits.completed_article(candidate, result)
    validate_article(article, recipe["packet"])
    return (
        {
            "policy": POLICY,
            "parent_job": parent,
            "parent_files": criteria.inventory(folder),
            "source_attempt_sha256": digest(attempts[0].read_bytes()),
            "selected_whole_claim_indices": selected,
            "rejected_whole_claim_indices": rejected,
            "additional_model_calls": 0,
        },
        recipe,
        result,
        article,
    )


def child_recipe(parent, spec):
    return {
        **parent,
        "extra": {
            "unchanged_body_job": parent["extra"]["unchanged_body_job"],
            "unchanged_assessment_job": parent["extra"]["unchanged_assessment_job"],
            "failed_limitations_job": spec["parent_job"],
            "literal_limitations_subset": spec,
        },
    }


def validate_lineage(root, recipe, *, complete=False, job=None, publication=None):
    spec, parent, result, article = selection(
        root, recipe["extra"]["failed_limitations_job"]
    )
    if recipe != child_recipe(parent, spec):
        raise ValueError("limitations_subset_lineage_changed")
    if complete:
        key = job or digest(canonical(recipe))[:32]
        binding = (
            Path(root)
            / "wiki/limitations-subset-bindings"
            / (spec["parent_job"] + ".json")
        )
        if json.loads(binding.read_bytes()) != {
            "policy": POLICY,
            "parent_job": spec["parent_job"],
            "child_job": key,
        }:
            raise ValueError("limitations_subset_singleton_binding_changed")
        folder = Path(root) / "wiki/jobs" / key
        if json.loads((folder / "selected-limitations.json").read_bytes()) != {
            "source_attempt_sha256": spec["source_attempt_sha256"],
            "result": result,
        }:
            raise ValueError("limitations_subset_selected_claim_changed")
        if json.loads((folder / "assembled.json").read_bytes())["article"] != article:
            raise ValueError("limitations_subset_assembled_article_changed")
        if publication is not None and publication != article:
            raise ValueError("limitations_subset_publication_changed")
    return spec


def repair(store, parent, model="qwen3.5:9b", *, prepare_only=False):
    with schema_lease(store.root / "wiki-build.lock", timeout=0):
        store.checkpoint()
        spec, original, result, article = selection(store.root, parent)
        recipe = child_recipe(original, spec)
        sha = digest(canonical(recipe))
        key = sha[:32]
        folder = store.root / "wiki/jobs" / key
        folder.mkdir(parents=True, exist_ok=True)
        manifest = {"id": key, "recipe_sha256": sha, "recipe": recipe}
        path = folder / "manifest.json"
        if path.exists() and json.loads(path.read_bytes()) != manifest:
            raise ValueError("limitations_subset_manifest_changed")
        if not path.exists():
            atomic_json(path, manifest)
        binding = store.root / "wiki/limitations-subset-bindings" / (parent + ".json")
        value = {"policy": POLICY, "parent_job": parent, "child_job": key}
        if binding.exists() and json.loads(binding.read_bytes()) != value:
            raise ValueError("limitations_subset_singleton_binding_changed")
        if not binding.exists():
            binding.parent.mkdir(parents=True, exist_ok=True)
            atomic_json(binding, value)
        validate_lineage(store.root, recipe)
        if prepare_only:
            return {
                "id": key,
                "state": "prepared_no_model_calls",
                "additional_model_calls": 0,
                "selected_whole_claims": len(spec["selected_whole_claim_indices"]),
            }
        if any(row["id"] == key for row in Wiki(store.root).catalog()["articles"]):
            publication = Wiki(store.root).article(key)
            validate_lineage(
                store.root,
                recipe,
                complete=True,
                job=key,
                publication=publication["article"],
            )
            return {
                "id": key,
                "state": "already_published",
                "additional_model_calls": 0,
            }
        atomic_json(
            folder / "selected-limitations.json",
            {"source_attempt_sha256": spec["source_attempt_sha256"], "result": result},
        )
        atomic_json(
            folder / "assembled.json",
            {"article": article, "checks": validate_article(article, recipe["packet"])},
        )
        validate_lineage(store.root, recipe, complete=True, job=key)
        published = publish_draft(
            store,
            article,
            recipe["packet"],
            key,
            model,
            recipe["model_digest"],
            pipeline=synthesis.POLICY,
        )
        return {**published, "additional_model_calls": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--failed-job", required=True)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            repair(
                ReadOnlyArchive(args.root),
                args.failed_job,
                prepare_only=args.prepare_only,
            )
        )
    )


if __name__ == "__main__":
    main()
