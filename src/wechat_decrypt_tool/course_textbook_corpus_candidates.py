"""Batch retrieval across retained lessons, never an affiliation verdict.

Literal sources and timestamps stay in the caller's immutable corpus. Sparse
postings are shared across queries so a corpus is tokenized only once per call.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict

from .course_textbook_affiliation_scope import features


def require(value, code):
    if not value:
        raise ValueError("corpus_candidates_" + code)


def rank(lessons, queries, *, limit=2, seeds_per_lesson=3, always_include=()):
    """Find literal classroom candidates using separate named query variants."""
    require(type(limit) is int and 1 <= limit <= 4, "bounded_lesson_limit")
    require(type(seeds_per_lesson) is int and 1 <= seeds_per_lesson <= 6,
            "bounded_source_seed_limit")
    require(bool(lessons) and bool(queries) and set(always_include) <= set(lessons),
            "actual_corpus_and_queries")
    require(len(set(always_include)) == len(always_include), "unique_required_lessons")
    vectors = {}
    for key, variants in queries.items():
        require(isinstance(variants, dict) and bool(variants)
                and all(isinstance(text, str) for text in variants.values()), "text_query_variants")
        vectors[key] = {name: features(text) for name, text in variants.items()}
    terms = {term for variants in vectors.values() for vector in variants.values() for term in vector}
    postings, frequency, rows, ordered = defaultdict(list), Counter(), [], {}
    for lesson, value in sorted(lessons.items()):
        require(isinstance(value, dict) and bool(value.get("sources")), "nonempty_lesson_sources")
        kinds = defaultdict(list)
        for ref, row in sorted(value["sources"].items()):
            require(row["kind"] in {"speech", "frame"} and isinstance(row["text"], str),
                    "literal_classroom_sources_only")
            start, end = row["start"], row["end"]
            require(type(start) in (int, float) and type(end) in (int, float)
                    and math.isfinite(start) and math.isfinite(end) and 0 <= start <= end,
                    "finite_original_timestamps")
            vector = features(row["text"])
            norm = math.sqrt(max(1, sum(vector.values())))
            index = len(rows)
            rows.append((lesson, ref, row["kind"]))
            kinds[row["kind"]].append((start, end, ref))
            for term in terms.intersection(vector):
                postings[term].append((index, min(vector[term], 3) / norm))
                frequency[term] += 1
        ordered[lesson] = {kind: [ref for _, _, ref in sorted(values)] for kind, values in kinds.items()}
    result = {}
    for key, variants in vectors.items():
        per_lesson, combined = defaultdict(dict), defaultdict(dict)
        for name, query in variants.items():
            scores = defaultdict(float)
            query_norm = math.sqrt(max(1, sum(min(amount, 3) ** 2 for amount in query.values())))
            for term, amount in query.items():
                weight = math.log(1 + len(rows) / (1 + frequency[term])) * min(amount, 3) / query_norm
                for index, tf in postings.get(term, ()):
                    scores[index] += weight * tf
            grouped = defaultdict(list)
            for index, score in scores.items():
                lesson, ref, _ = rows[index]
                if score > 0:
                    grouped[lesson].append((score, ref))
            for lesson, matches in grouped.items():
                seeds = sorted(matches, key=lambda row: (-row[0], row[1]))[:seeds_per_lesson]
                per_lesson[lesson][name] = {"seed_ids": [ref for _, ref in seeds],
                                          "score": seeds[0][0] + sum(s for s, _ in seeds) / 4}
                for score, ref in seeds:
                    combined[lesson][ref] = max(combined[lesson].get(ref, 0), score)
        ranking = sorted(per_lesson, key=lambda lesson: (
            -max(row["score"] for row in per_lesson[lesson].values()), lesson))
        selected = list(dict.fromkeys([*always_include, *ranking[:limit]]))
        courses = []
        for lesson in selected:
            seeds = set(combined.get(lesson, {}))
            refs = set(seeds)
            for values in ordered[lesson].values():
                for index, ref in enumerate(values):
                    if ref in seeds:
                        refs.update(values[max(0, index - 1):index + 2])
            courses.append({"lesson": lesson, "artifact": lessons[lesson]["artifact"],
                "seed_ids": sorted(seeds), "source_ids": sorted(refs),
                "variant_rankings": per_lesson.get(lesson, {}),
                "selected_only_because_required": lesson not in ranking[:limit]})
        result[key] = {"courses": courses, "searched_lesson_count": len(lessons),
            "searched_source_count": len(rows), "matched_lesson_count": len(per_lesson),
            "ranking_establishes_affiliation": False, "unretrieved_sources_are_excluded": False,
            "semantic_acceptance": False}
    return result
