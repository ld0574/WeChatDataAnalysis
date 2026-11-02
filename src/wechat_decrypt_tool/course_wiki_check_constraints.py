"""Versioned opt-in evidence grammar; frozen review recipes stay unchanged.

These schemas constrain evidence selection, never semantic correctness. Keep
the normal source/witness validators after decoding every response.
"""

from __future__ import annotations

import json

from .course_wiki import object_schema

POLICY = "verdict-dependent-evidence-grammar-experiment-v1"
GENERATION_POLICY = "verdict-dependent-evidence-grammar-v1"
KEYED_REVERSE_POLICY = "keyed-window-verdict-dependent-evidence-grammar-v1"
BOUNDED_KEYED_REVERSE_POLICY = (
    "keyed-window-batches-verdict-dependent-evidence-grammar-v1"
)
LARGE_CONTEXT_REVERSE_POLICY = "keyed-window-batches-gpu-context16384-output4000-v1"


def generation_policy(value, *, reverse=False):
    allowed = (
        (
            None,
            GENERATION_POLICY,
            KEYED_REVERSE_POLICY,
            BOUNDED_KEYED_REVERSE_POLICY,
            LARGE_CONTEXT_REVERSE_POLICY,
        )
        if reverse
        else (None, GENERATION_POLICY)
    )
    if value not in allowed:
        raise ValueError("check_unknown_generation_constraints")
    return value is not None


def retain_generation_policy(wiki, directory, article_sha, policy):
    """Changing a schema cannot give an existing article a fresh attempt budget."""
    generation_policy(policy, reverse=directory == "completeness-jobs")
    for path in (wiki / directory).glob("*/manifest.json"):
        saved = json.loads(path.read_bytes())
        saved = saved if directory == "review-jobs" else saved["recipe"]["extra"]
        if (
            saved.get("article_sha256") == article_sha
            and saved.get("generation_constraints") != policy
        ):
            raise ValueError("check_constraint_change_requires_new_article")


def _array(ids, minimum=0):
    if not ids:
        return {"type": "array", "maxItems": 0}
    return {
        "type": "array",
        "minItems": minimum,
        "items": {"type": "string", "enum": sorted(ids)},
    }


def _reviews(branches, count):
    return object_schema(
        {
            "reviews": {
                "type": "array",
                "minItems": count,
                "maxItems": count,
                "items": {"oneOf": branches},
            }
        }
    )


def forward(request):
    branches = []
    for target in request["targets"]:
        ids = sorted(
            key
            for key, witness in request["witnesses"].items()
            if witness["source"] in target["citations"]
        )
        for verdict in ("supported", "partial", "unsupported", "unclear"):
            if verdict in {"supported", "partial"} and not ids:
                continue
            evidence = {"type": "array", "maxItems": 0}
            if ids:
                evidence = {
                    "type": "array",
                    "minItems": int(verdict in {"supported", "partial"}),
                    "items": object_schema(
                        {"witness": {"type": "string", "enum": ids}}
                    ),
                }
            branches.append(
                object_schema(
                    {
                        "target": {"type": "string", "enum": [target["target"]]},
                        "verdict": {"type": "string", "enum": [verdict]},
                        "reason": {"type": "string", "minLength": 1},
                        "evidence": evidence,
                    }
                )
            )
    return _reviews(branches, len(request["targets"]))


def reverse(context):
    branches = []
    for key, window in context["windows"].items():
        ids = {
            ref
            for ref, target in context["article_targets"].items()
            if window["source"] in target["citations"]
        }
        for verdict in ("covered", "partial", "missing", "not_essential", "unclear"):
            if verdict in {"covered", "partial"} and not ids:
                continue
            if verdict == "covered" and not any(
                not ref.endswith("-code") for ref in ids
            ):
                continue
            evidence = {"type": "array", "maxItems": 0}
            if verdict in {"covered", "partial", "unclear"}:
                evidence = _array(ids, int(verdict in {"covered", "partial"}))
            branches.append(
                object_schema(
                    {
                        "window": {"type": "string", "enum": [key]},
                        "coverage": {"type": "string", "enum": [verdict]},
                        "reason": {"type": "string", "minLength": 1, "maxLength": 700},
                        "article_evidence": evidence,
                    }
                )
            )
    return _reviews(branches, len(context["windows"]))


def reverse_keyed(context):
    """Each required window has its own result slot, never an array of IDs."""
    properties = {key: [] for key in context["windows"]}
    for branch in reverse(context)["properties"]["reviews"]["items"]["oneOf"]:
        key = branch["properties"].pop("window")["enum"][0]
        branch["required"].remove("window")
        properties[key].append(branch)
    return object_schema(
        {
            "reviews": object_schema(
                {key: {"oneOf": branches} for key, branches in properties.items()}
            )
        }
    )
