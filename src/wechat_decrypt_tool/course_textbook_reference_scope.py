"""Check a courseware supplement without claiming its whole text was spoken.

Lecture affiliation and source coverage remain separate, immutable findings.
This receipt establishes only an item-specific instructional reference relation.
"""
from __future__ import annotations

import jsonschema

from .course_wiki import object_schema

CHECKS = (
    "specific_instructional_connection",
    "more_than_shared_terminology",
    "whole_target_relevant_as_reference",
    "reference_details_supported_by_courseware",
    "article_integration_coherent",
    "classroom_delivery_not_misrepresented",
    "context_sufficient",
)


def schema(lesson_sources, article_items):
    if not lesson_sources or not article_items:
        raise ValueError("reference_scope_actual_sources_and_article_required")
    return object_schema({
        **{key: {"type": "boolean"} for key in CHECKS},
        "coverage_contradiction_found": {"type": "boolean"},
        "lesson_source_ids": {"type": "array", "uniqueItems": True,
            "items": {"enum": sorted(lesson_sources)}, "maxItems": 8},
        "article_item_ids": {"type": "array", "uniqueItems": True,
            "items": {"enum": sorted(article_items)}, "maxItems": 8},
        "reason": {"type": "string", "minLength": 1, "maxLength": 200},
    })


def assess(value, lesson_sources, article_items):
    jsonschema.validate(value, schema(lesson_sources, article_items))
    accepted = (all(value[key] is True for key in CHECKS)
        and value["coverage_contradiction_found"] is False
        and bool(value["lesson_source_ids"]) and bool(value["article_item_ids"]))
    return {"reference_relation": "supported_supplement" if accepted else "unresolved",
        "same_lesson_teaching_established": False,
        "whole_notebook_affiliation_established": False,
        "source_coverage_rechecked": False,
        "semantic_acceptance": False, "source_deletion_allowed": False}
