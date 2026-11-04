"""Check typed courseware links without turning quote identity into meaning."""

from __future__ import annotations

import jsonschema

from .course_wiki import object_schema


def require(value, reason):
    if not value:
        raise ValueError("requires_courseware_affiliation_" + reason)


def normal(text):
    return "".join(text.split())


def schema_for(sources, targets):
    require(bool(targets) and set(targets) <= set(sources), "real_courseware_targets")
    lesson = [key for key, row in sources.items() if row["kind"] in {"speech", "frame"}]
    require(bool(lesson) and not set(targets).intersection(lesson), "separate_typed_source_roles")
    quote = {"type": "string", "minLength": 1, "maxLength": 180}
    reason = {"type": "string", "minLength": 1, "maxLength": 80}
    pair = object_schema({"lesson_source": {"enum": lesson}, "source_quote": quote, "lesson_quote": quote})
    return object_schema({"sources": object_schema({key: {"anyOf": [
        object_schema({"kind": {"const": "same_lesson_teaching"}, "reason": reason,
            "pairs": {"type": "array", "minItems": 1, "maxItems": 3, "items": pair}}),
        object_schema({"kind": {"const": "unresolved"}, "reason": reason}),
    ]} for key in targets})})


def quote_identities(value, sources, targets):
    """Keep malformed support unresolved, without rewriting the model's quotes."""
    jsonschema.validate(value, schema_for(sources, targets))
    checked = {}
    for key, row in value["sources"].items():
        if row["kind"] == "unresolved":
            checked[key] = {"status": "unresolved"}
            continue
        errors = []
        for index, pair in enumerate(row["pairs"]):
            for name, ref in [("source_quote", key), ("lesson_quote", pair["lesson_source"])]:
                text, quote = normal(sources[ref]["text"]), normal(pair[name])
                minimum = min(10, len(text))
                if not text or len(quote) < minimum or quote not in text:
                    errors.append({"pair": index, "field": name, "code": "not_an_actual_source_quote"})
        checked[key] = ({"status": "unresolved_invalid_quotes", "errors": errors} if errors
                        else {"status": "awaiting_semantic_review"})
    return checked


def finalize(identities, semantic, non_teaching, members):
    """A whole notebook passes only when every teaching item has passed."""
    eligible = {key for key, row in identities.items() if row["status"] == "awaiting_semantic_review"}
    require(set(semantic) == eligible, "complete_independent_semantic_verdicts")
    require(all(row["verdict"] in {"supported", "unresolved"} for row in semantic.values()), "known_semantic_verdicts")
    grouped = [key for values in members.values() for key in values]
    require(len(grouped) == len(set(grouped)), "unique_courseware_membership")
    require(not set(identities).intersection(non_teaching)
            and set(grouped) == set(identities) | set(non_teaching), "complete_partition_without_exclusion")
    dispositions = {key: "non_teaching_context" for key in non_teaching}
    for key in identities:
        dispositions[key] = ("same_lesson_teaching" if key in eligible and semantic[key]["verdict"] == "supported"
                             else "unresolved")
    notebooks = {}
    for member, keys in members.items():
        kinds = [dispositions[key] for key in keys]
        unresolved = kinds.count("unresolved")
        linked = kinds.count("same_lesson_teaching")
        notebooks[member] = {"items": len(keys), "linked_teaching": linked,
            "non_teaching": kinds.count("non_teaching_context"), "unresolved": unresolved,
            "association_verified": unresolved == 0,
            "relationship": ("same_lesson_teaching" if linked and not unresolved else
                             "non_teaching_context" if not linked and not unresolved else
                             "partially_linked" if linked else "unresolved")}
    return {"source_dispositions": dispositions, "notebooks": notebooks,
            "association_verified": all(row["association_verified"] for row in notebooks.values())}
