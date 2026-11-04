"""Project an actual independently reviewed revision as an unfinished draft.

This checks the saved model receipt and revision bindings without invoking a
model. Courseware affiliation and final course acceptance remain separate.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import jsonschema

from .course_library import canonical
from .course_textbook_article import validate_book
from .course_textbook_edition import checked_cache
from .course_textbook_edition_editor import apply, labelled_article, operations
from .course_textbook_evidence import sha
from .course_textbook_gap_plan import body_index

STAGE = "retained-visual-independent-review"
ROLE = "retained-three-visual-paragraph-independent-review-only"
ID = re.compile(r"[a-f0-9]{32}")


def require(condition, code):
    if not condition:
        raise ValueError("textbook_reviewed_draft_" + code)


def read(path):
    return json.loads(Path(path).read_bytes())


def load(root, job):
    """Return only a clean book and a text-free publication receipt."""
    require(isinstance(job, str) and ID.fullmatch(job), "invalid_job")
    wiki = Path(root).resolve() / "wiki"
    folder = wiki / "lesson-contextual-repair-jobs" / job
    manifest = read(folder / "manifest.json")
    recipe = manifest["recipe"]
    digest = sha(canonical(recipe))
    require(manifest["id"] == job == digest[:32]
            and manifest["recipe_sha256"] == digest, "manifest_changed")
    require(recipe["extra"]["role"] == ROLE, "unsupported_review")
    result = read(folder / "result.json")
    require(result["job"] == job
            and result["state"] == "retained_visual_additions_independently_checked"
            and result["all_operations_accepted"] is True
            and result["new_writing_calls"] == 0
            and result["source_deletion_allowed"] is False
            and result["semantic_acceptance"] is False
            and result["scope_verified"] is False
            and result["reader_publication_allowed"] is False
            and all(result[key] == 0 for key in (
                "missing_source_inputs", "remaining_prior_issues", "reading_issues"
            )), "not_an_accepted_reading_draft")
    require(all(ID.fullmatch(result[key]) for key in ("writer", "parent")),
            "invalid_revision_chain")
    writer = wiki / "lesson-contextual-repair-jobs" / result["writer"]
    bound = read(writer / "retained-actual-unique-prose-position-mapped-review-binding.json")
    require(bound["reviewer"] == job and bound["new_writing_calls"] == 0
            and bound["manifest_sha256"] == sha((folder / "manifest.json").read_bytes()),
            "review_binding_changed")
    proof = read(folder / "private-final-checks.json")
    inputs = read(folder / "private-inputs.json")
    proposal = read(folder / "private-reviewed-proposal.json")
    binding = recipe["extra"]["binding"]
    require(binding == proof["binding"] == inputs["binding"]
            == proposal["selection_binding"], "input_binding_changed")
    for name, checksum in binding.items():
        path = Path(name)
        if path.is_absolute():
            require(sha(path.read_bytes()) == checksum, "bound_source_changed")
    require(binding["original_writer"] == result["writer"], "writer_changed")
    for name, checksum in binding["original_writer_files"].items():
        require(Path(name).name == name or name == "attempts/focused-visual-write-01.json",
                "invalid_writer_artifact")
        require(sha((writer / name).read_bytes()) == checksum, "writer_artifact_changed")
    review, input_digest = checked_cache(folder, STAGE)
    jsonschema.validate(review, inputs["schema"])
    require(review == proposal["review"]
            and review["editorial"] == proof["review"], "actual_review_changed")
    editorial = review["editorial"]
    require(bool(editorial["operations"])
            and all(v["verdict"] == "accepted" for v in editorial["operations"].values())
            and all(v["verdict"] == "resolved" for v in editorial["prior_issues"].values())
            and all(v["verdict"] == "passed" for v in editorial["reading"].values()),
            "review_has_remaining_issues")
    book = validate_book(read(folder / "reading.json"))
    require(sha(canonical(book)) == result["article_sha256"]
            == binding["proposed_article_sha256"]
            and book == proposal["proposed_article"]
            and labelled_article(book) == inputs["context"]["proposed_article"],
            "reviewed_article_changed")
    parent_path = wiki / "lesson-retained-addition-jobs" / result["parent"] / "reading.json"
    require(str(parent_path) in binding, "parent_not_bound")
    parent = validate_book(read(parent_path))
    candidate = proposal["candidate"]
    require(candidate["quiz_changes"] == []
            and all(row["kind"] == "append" for row in candidate["paragraph_changes"])
            and apply(parent, candidate) == book
            and operations(candidate) == inputs["context"]["operations"]
            and set(operations(candidate)) == set(editorial["operations"]),
            "reviewed_operations_changed")
    coverage = proof["source_coverage"]
    checked = review["source_check"]["sources"]
    require(set(coverage) == set(recipe["packet"]["sources"])
            and set(checked) == set(inputs["context"]["source_check_targets"])
            and result["audited_source_inputs"] == len(checked)
            and all(coverage[key] == value for key, value in checked.items()),
            "coverage_binding_changed")
    body = body_index(book)[0]
    require(all(row["kind"] in {"covered", "non_teaching"} for row in coverage.values())
            and all(set(row.get("paragraph_ids", [])) <= set(body) for row in coverage.values()),
            "coverage_has_unresolved_inputs")
    require(result["body_characters"] == sum(map(len, body.values()))
            and result["chapters"] == len(book["chapters"])
            and result["self_tests"] == len(book["quiz"])
            and result["actual_added_paragraphs"] == len(candidate["paragraph_changes"])
            and result["actual_added_characters"] == sum(
                len(row["text"]) for row in candidate["paragraph_changes"]),
            "revision_counts_changed")
    return book, {
        "review_job": job,
        "manifest_sha256": sha((folder / "manifest.json").read_bytes()),
        "input_sha256": input_digest,
        "article_sha256": result["article_sha256"],
        "state": "review_draft",
        "semantic_acceptance": False,
        "scope_verified": False,
        "source_deletion_allowed": False,
    }
