import copy

import pytest

from wechat_decrypt_tool import course_textbook_gap_assembly as m


@pytest.fixture
def sample():
    private = {"introduction": [{"text": "Synthetic introduction", "citations": ["S0"]}],
        "prerequisites": [], "chapters": [{"heading": "Synthetic chapter", "sections": [
            {"heading": "Synthetic section", "paragraphs": [{"text": "Synthetic original paragraph", "citations": ["S0"]}],
                "diagrams": []}]}], "takeaways": [{"text": "Synthetic takeaway", "citations": ["S0"]}],
        "private_coverage": {"point": "retained"}, "quiz": [{"private": "retained"}]}
    items, _ = m.parent_items(private)
    candidate = {"additions": {"N000": {"text": "Synthetic independently checked paragraph", "heading": "Synthetic heading",
        "anchor": "A000001", "citations": ["S0"]}}, "groups": [{"kind": "covered", "clauses": ["C0"], "article_items": ["N000"]}]}
    audit = {"groups": {"G0000": {"passed": True, "reason": "Synthetic check"}},
        "additions": {"N000": {"passed": True, "reason": "Synthetic check"}}, "source_conflicts": {}}
    context = {"article": items, "source_conflicts": {}, "target_clauses": {"C0": {"reference": "S0"}}}
    record = {"batch": 0, "candidate": candidate, "audit": audit, "context": context}
    status = {"reviewed_batches": 1, "retained_unresolved_batches": 1, "remaining_original_unresolved": 7,
        "remaining_source_conflicts": 2}
    return private, items, [record], status, [{"kind": "synthetic_unresolved_parent_issue"}]


def compose(sample):
    for row in sample[2]:
        row["outcome"] = {**m.original.assess(row["candidate"], row["audit"], row["context"]),
            "candidate_sha256": m.remaining.digest(row["candidate"]), "audit_sha256": m.remaining.digest(row["audit"])}
    return m.compose(*sample)


def test_exact_anchor_insertion_preserves_parent_all_metadata_and_pending_obligations(sample):
    before = copy.deepcopy(sample)
    value = compose(sample)
    assert value["parent_private"] == before[0]
    assert [row["id"] for row in value["ordered_body_blocks"]] == ["A000000", "A000001", "B0000:N000", "A000002"]
    assert value["ordered_body_blocks"][2]["paragraph"] == before[2][0]["candidate"]["additions"]["N000"]
    assert value["ordered_body_blocks"][1]["native_path"] == ["chapters", 0, "sections", 0, "paragraphs", 0]
    assert value["summary"]["placed_paragraphs"] == 1 and value["summary"]["retained_paragraphs"] == 0
    assert value["summary"]["remaining_original_unresolved"] == 7
    assert value["summary"]["retained_unresolved_batches"] == 1
    assert value["inherited_parent_issues"] == before[4]
    assert value["summary"]["semantic_acceptance"] is value["summary"]["reader_publication_allowed"] is False
    assert sample[0] == before[0] and sample[2][0]["candidate"] == before[2][0]["candidate"]


@pytest.mark.parametrize("failure,reason", [
    ("unmapped", "unmapped_addition"), ("negative_addition", "independent_addition_review_failed"),
    ("negative_group", "no_independently_resolved_covered_group"), ("new_section", "new_section_requires_editorial_placement"),
    ("parent_duplicate", "exact_parent_text_duplicate")])
def test_no_failed_unmapped_or_unplaced_addition_enters_body_but_full_original_stays(sample, failure, reason):
    row = sample[2][0]
    if failure == "unmapped":
        row["candidate"]["groups"][0]["article_items"] = ["A000001"]
    elif failure == "negative_addition":
        row["audit"]["additions"]["N000"]["passed"] = False
    elif failure == "negative_group":
        row["audit"]["groups"]["G0000"]["passed"] = False
    elif failure == "new_section":
        row["candidate"]["additions"]["N000"]["anchor"] = "new_section"
    else:
        row["candidate"]["additions"]["N000"]["text"] = sample[0]["chapters"][0]["sections"][0]["paragraphs"][0]["text"]
    value = compose(sample)
    assert value["summary"]["placed_paragraphs"] == 0 and value["summary"]["retained_paragraphs"] == 1
    assert all(block["origin"] == "parent" for block in value["ordered_body_blocks"])
    assert value["all_reviewed_additions"][0]["paragraph"] == row["candidate"]["additions"]["N000"]
    assert reason in value["all_reviewed_additions"][0]["reason_codes"]


def test_exact_cross_batch_duplicate_keeps_both_reviews_and_one_body_paragraph(sample):
    sample[2].append(copy.deepcopy(sample[2][0]))
    sample[2][1]["batch"] = 2
    sample[3]["reviewed_batches"] = 2
    value = compose(sample)
    assert value["summary"]["candidate_paragraphs"] == 2 and value["summary"]["placed_paragraphs"] == 1
    assert value["all_reviewed_additions"][1]["exact_duplicate_of"] == "B0000:N000"
    assert value["retained_addition_ids"] == ["B0002:N000"]


def test_unknown_anchor_and_modified_parent_are_rejected(sample):
    sample[2][0]["candidate"]["additions"]["N000"]["anchor"] = "A999999"
    with pytest.raises(ValueError, match="known_exact_original_anchor"):
        compose(sample)
    sample[2][0]["candidate"]["additions"]["N000"]["anchor"] = "A000001"
    sample[0]["introduction"][0]["text"] = "Changed synthetic parent"
    with pytest.raises(ValueError, match="same_entire_parent_article"):
        compose(sample)


def test_reviewed_batch_count_and_changed_audit_cannot_be_made_complete(sample):
    compose(sample)
    sample[3]["reviewed_batches"] = 2
    with pytest.raises(ValueError, match="all_actual_reviewed_batches_once"):
        m.compose(*sample)
    sample[3]["reviewed_batches"] = 1
    sample[2][0]["audit"]["additions"]["N000"]["passed"] = False
    with pytest.raises(ValueError, match="same_independently_checked_batch_disposition"):
        m.compose(*sample)
