"""Retrieve literal lesson sources for bounded, independently reviewed links.

Retrieval is only candidate generation. A score, a real ID or a matching term
never establishes affiliation, and a failed local search cannot prove absence.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter

from .course_wiki import object_schema


def features(text):
    text = unicodedata.normalize("NFKC", text).casefold()
    result = Counter(re.findall(r"[a-z_][a-z_0-9]{2,}", text))
    for run in re.findall(r"[\u3400-\u9fff]{2,}", text):
        for size in (2, 3):
            result.update(run[i:i + size] for i in range(len(run) - size + 1))
    return result


def scope(sources, target, *, candidates=12, neighbors=1):
    if target not in sources or sources[target]["kind"] in {"speech", "frame"}:
        raise ValueError("affiliation_scope_courseware_target_required")
    if type(candidates) is not int or not 1 <= candidates <= 24 or neighbors not in (0, 1):
        raise ValueError("affiliation_scope_finite_candidate_budget_required")
    lesson = {k: v for k, v in sources.items() if v["kind"] in {"speech", "frame"}}
    if not lesson:
        raise ValueError("affiliation_scope_actual_lesson_sources_required")
    vectors = {k: features(v["text"]) for k, v in lesson.items()}
    query = features(sources[target]["text"])
    frequency = Counter(term for vector in vectors.values() for term in vector)
    count = len(vectors)
    weights = {term: math.log(1 + count / (1 + frequency[term])) for term in query}
    scores = {}
    for key, vector in vectors.items():
        overlap = sum(weights[term] * min(amount, 3) * min(vector[term], 3)
                      for term, amount in query.items() if term in vector)
        scores[key] = overlap / math.sqrt(max(1, sum(vector.values())))
    ranking = sorted(lesson, key=lambda key: (-scores[key], key))
    seeds = [key for key in ranking if scores[key] > 0][:candidates]
    selected = set(seeds)
    if neighbors:
        for kind in {row["kind"] for row in lesson.values()}:
            ordered = sorted(key for key, row in lesson.items() if row["kind"] == kind)
            for index, key in enumerate(ordered):
                if key in seeds:
                    selected.update(ordered[max(0, index - 1):index + 2])
    return {
        "target": target,
        "courseware_source": sources[target],
        "lesson_sources": {key: lesson[key] for key in sorted(selected)},
        "retrieval": {"searched_lesson_sources": len(lesson), "seed_ids": seeds,
                      "candidate_count": len(selected), "ranking_is_affiliation_evidence": False,
                      "unretrieved_sources_are_excluded": False},
    }


def selection_schema(context):
    ids = sorted(context["lesson_sources"])
    reason = {"type": "string", "minLength": 1, "maxLength": 120}
    unresolved = object_schema({"kind": {"const": "unresolved"}, "reason": reason})
    if not ids:
        return unresolved
    return {"anyOf": [
        unresolved,
        object_schema({"kind": {"const": "candidate_link"}, "reason": reason,
                       "lesson_sources": {"type": "array", "items": {"enum": ids},
                                          "uniqueItems": True, "minItems": 1, "maxItems": 6}}),
    ]}


def review_schema():
    return object_schema({
        "whole_item_supported": {"type": "boolean"},
        "specific_teaching_not_just_terminology": {"type": "boolean"},
        "context_sufficient": {"type": "boolean"},
        "reason": {"type": "string", "minLength": 1, "maxLength": 120},
    })


def accepted(review):
    return all(review[key] is True for key in (
        "whole_item_supported", "specific_teaching_not_just_terminology", "context_sufficient"
    ))
