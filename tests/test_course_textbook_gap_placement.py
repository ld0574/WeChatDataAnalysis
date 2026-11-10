import copy

import jsonschema
import pytest

from wechat_decrypt_tool import course_textbook_gap_placement as m


@pytest.fixture
def packet():
    private = {"chapters": [{"heading": "Synthetic chapter", "sections": [
        {"heading": "Synthetic old section"}]}]}
    body = [
        {"id": "A0", "origin": "parent", "native_path": ["introduction", 0],
            "item": {"content": "Synthetic introduction", "citations": ["S0"]}},
        {"id": "A1", "origin": "parent", "native_path": ["chapters", 0, "sections", 0, "paragraphs", 0],
            "item": {"content": "Synthetic exact duplicate", "citations": ["S0"]}},
        {"id": "B0", "origin": "reviewed_addition", "after": "A1",
            "paragraph": {"text": "Synthetic existing addition", "citations": ["S0"]}},
        {"id": "A2", "origin": "parent", "native_path": ["takeaways", 0],
            "item": {"content": "Synthetic takeaway", "citations": ["S0"]}}]
    themes = {key: {"text": "Synthetic checked explanation " + key, "heading": "Synthetic previous heading",
        "anchor": "new_section", "citations": ["S1"]} for key in ("B0001:N000", "B0001:N001")}
    book = {"parent_private": private, "ordered_body_blocks": body,
        "summary": {"remaining_source_conflicts": 3}, "inherited_parent_issues": [{"kind": "synthetic_parent_issue"}],
        "retained_addition_ids": [*themes, "B0002:N000", "B0003:N000"], "all_reviewed_additions": []}
    context = {"article": body, "section_slots": m.section_slots(book), "theme_additions": themes,
        "exact_text_aliases": {"B0002:N000": {"exact_body_item_ids": ["A1"],
            "original_paragraph": {"text": "Synthetic exact duplicate", "citations": ["S1"]},
            "same_citations_asserted": False, "semantic_coverage_asserted": False}},
        "placement_groups": {
            "B0001:G0000": {"clauses": {"C1": {"reference": "S1", "text": "Synthetic source"}},
                "article_items": [*themes, "A1"]},
            "B0002:G0000": {"clauses": {"C2": {"reference": "S1", "text": "Synthetic source"}},
                "article_items": ["B0002:N000"]}},
        "sources": {"S1": {"text": "Synthetic source"}}, "context_sources": {}}
    data = {"book": book, "context": context, "unaffected_unresolved_ids": ["U1", "U2"],
        "placement_dependency_ids": ["C1", "C2"]}
    layout = {"sections": [{"after_section": "S0000", "heading": "Synthetic new theme", "addition_ids": list(themes)}],
        "retained_ids": []}
    audit = {"placements": {key: {"passed": True, "reason": "Synthetic placement check"} for key in themes},
        "groups": {key: {"passed": True, "reason": "Synthetic full member check"} for key in context["placement_groups"]}}
    return data, layout, audit


def test_exact_reviewed_layout_preserves_every_old_block_and_all_obligations(packet):
    before = copy.deepcopy(packet)
    data, layout, audit = packet
    result = m.finish(*packet)
    assert [b["id"] for b in result["ordered_body_blocks"]] == ["A0", "A1", "B0", "B0001:N000", "B0001:N001", "A2"]
    assert result["ordered_body_blocks"][:3] == data["book"]["ordered_body_blocks"][:3]
    assert result["original_private_assembly"] == data["book"]
    assert result["new_sections"][0]["paragraphs"][0]["paragraph"] == data["context"]["theme_additions"]["B0001:N000"]
    assert result["unestablished_coverage_obligation_ids"] == ["U1", "U2"]
    assert result["summary"]["source_conflicts_retained"] == 3
    assert result["summary"]["parent_issues_retained"] == 1
    assert result["still_retained_addition_ids"] == ["B0002:N000", "B0003:N000"]
    assert result["summary"]["semantic_acceptance"] is result["summary"]["reader_publication_allowed"] is False
    assert result["summary"]["source_deletion_allowed"] is False
    assert packet == before


def test_exact_text_witness_does_not_automatically_pass_semantic_coverage(packet):
    packet[2]["groups"]["B0002:G0000"]["passed"] = False
    result = m.finish(*packet)
    assert result["unestablished_coverage_obligation_ids"] == ["C2", "U1", "U2"]
    assert result["exact_text_alias_witnesses"]["B0002:N000"]["same_citations_asserted"] is False


def test_one_negative_placement_cannot_create_an_unreviewed_partial_layout(packet):
    data, layout, audit = packet
    layout["sections"].append({"after_section": "S0000", "heading": "Synthetic second theme",
        "addition_ids": [layout["sections"][0]["addition_ids"].pop()]})
    audit["placements"]["B0001:N001"]["passed"] = False
    result = m.finish(*packet)
    assert result["ordered_body_blocks"] == data["book"]["ordered_body_blocks"]
    assert result["placed_addition_ids"] == []
    assert result["summary"]["unestablished_coverage_obligations"] == 3
    assert result["still_retained_addition_ids"] == data["book"]["retained_addition_ids"]


def test_group_requires_every_actual_body_dependency_even_with_positive_review(packet):
    data, layout, audit = packet
    key = layout["sections"][0]["addition_ids"].pop()
    layout["retained_ids"].append(key)
    audit["placements"][key]["passed"] = False
    result = m.finish(*packet)
    assert "C1" in result["unestablished_coverage_obligation_ids"]
    assert key in result["still_retained_addition_ids"]


@pytest.mark.parametrize("change", ["omit", "repeat", "invent", "unknown_section", "blank_heading"])
def test_invalid_layout_cannot_drop_repeat_or_invent_material(packet, change):
    data, layout, _ = packet
    ids = layout["sections"][0]["addition_ids"]
    if change == "omit":
        ids.pop()
    elif change == "repeat":
        layout["retained_ids"].append(ids[0])
    elif change == "invent":
        ids[0] = "B9999:N999"
    elif change == "unknown_section":
        layout["sections"][0]["after_section"] = "S9999"
    else:
        layout["sections"][0]["heading"] = " "
    with pytest.raises((ValueError, jsonschema.ValidationError)):
        m.check_layout(layout, data["context"])


def test_retained_paragraph_cannot_be_accepted_as_actually_placed(packet):
    data, layout, audit = packet
    layout["retained_ids"] = layout["sections"][0]["addition_ids"]
    layout["sections"] = []
    with pytest.raises(ValueError, match="retained_theme_cannot_pass_placement"):
        m.finish(*packet)


def test_changed_duplicate_witness_is_rejected_before_any_coverage_is_cleared(packet):
    packet[0]["book"]["ordered_body_blocks"][1]["item"]["content"] += " changed"
    with pytest.raises(ValueError, match="unchanged_literal_body_witnesses"):
        m.finish(*packet)


def test_read_only_role_never_acquires_gpu_or_calls_model(tmp_path):
    job = object.__new__(m.Job)
    job.root, job.read_only, job.models = tmp_path, True, {"qwen3.5:9b": "synthetic"}
    with pytest.raises(ValueError, match="existing_role_without_inference"):
        job.stage("theme-layout", m.LAYOUT, {}, {}, lambda v: None)
    with pytest.raises(ValueError, match="inference_forbidden_in_reconstruction"):
        job.generate_response([], schema={})
    assert list(tmp_path.iterdir()) == []


def test_unrecognized_roles_cannot_renew_original_edit_or_review_budget(tmp_path):
    job = object.__new__(m.Job)
    with pytest.raises(ValueError, match="only_two_distinct_layout_roles"):
        job.stage("batch-0010-edit", m.original.EDIT, {}, {}, lambda v: None)


def test_rewritten_original_source_snapshot_cannot_load(tmp_path, monkeypatch):
    parent, key = "b" * 32, "a" * 32
    folder = m.folder_for(tmp_path, key)
    m.retained(folder / "recipe.json", {"policy": m.POLICY, "inputs_sha256": "synthetic", "code": m.bindings()})
    m.retained(folder / "inputs.json", {"tampered": True})
    with pytest.raises(ValueError, match="same_frozen_inputs_code_and_role_limits"):
        m.load(tmp_path, key)
    assert not (tmp_path / "wiki/gap-theme-placement-registrations" / (parent + ".json")).exists()


@pytest.fixture
def predecessor_packet(packet):
    data, _, _ = packet
    context, book = data["context"], data["book"]
    book["all_reviewed_additions"] = [{"id": key, "paragraph": paragraph, "reason_codes": [m.NEW_THEME]}
        for key, paragraph in context["theme_additions"].items()]
    book["all_reviewed_additions"].append({"id": "B0002:N000", "reason_codes": ["exact_parent_text_duplicate"],
        "paragraph": context["exact_text_aliases"]["B0002:N000"]["original_paragraph"]})
    records, reported = [], []
    for n, cid, new_ids in ((1, "C1", ["N000", "N001"]), (2, "C2", ["N000"])):
        group = {"kind": "covered", "clauses": [cid], "article_items": new_ids}
        records.append({"batch": n, "candidate": {"groups": [group], "additions": dict.fromkeys(new_ids)},
            "context": {"target_clauses": {cid: {"reference": "S1", "text": "Synthetic source"}},
                "sources": context["sources"], "context_sources": {"S9": {"text": "Complete synthetic context " * 100}}},
            "audit": {"groups": {"G0000": {"passed": True, "reason": "Synthetic previous check"}}},
            "outcome": {"resolved_target_clauses": [cid]}})
        reported.append({"batch": n, "group": "G0000", "clauses": [cid],
            "absent_body_items": [f"B{n:04d}:{key}" for key in new_ids]})
    old = {"original_unresolved": ["U1", "U2", "C1"]}
    report = {"draft_sha256": m.digest(book), "affected_groups": reported, "original_candidate_unresolved": 2,
        "target_clauses_with_absent_body_dependencies": 2, "original_candidate_unresolved_plus_newly_unestablished": 4}
    return book, old, records, report


def test_snapshot_preserves_complete_records_members_and_unaffected_obligations(predecessor_packet):
    before = copy.deepcopy(predecessor_packet)
    result = m.snapshot(*predecessor_packet)
    assert result["unaffected_unresolved_ids"] == ["U1", "U2"]
    assert result["placement_dependency_ids"] == ["C1", "C2"]
    assert result["context"]["context_sources"]["S9"]["text"] == "Complete synthetic context " * 100
    assert len(result["context"]["theme_additions"]) == 2
    assert result["context"]["exact_text_aliases"]["B0002:N000"]["semantic_coverage_asserted"] is False
    assert predecessor_packet == before


def test_snapshot_cannot_promote_an_originally_rejected_or_unmapped_addition(predecessor_packet):
    book, old, records, report = predecessor_packet
    book["all_reviewed_additions"][0]["reason_codes"].append("independent_addition_review_failed")
    report["draft_sha256"] = m.digest(book)
    with pytest.raises(ValueError, match="no_failed_or_unmapped_paragraph_promoted"):
        m.snapshot(book, old, records, report)


def test_review_transmits_one_complete_article_with_every_source_without_duplicate_old_body(packet):
    data, layout, _ = packet
    value = m.review_context(data, layout)
    assert "article" not in value
    original_ids = {row["id"] for row in data["book"]["ordered_body_blocks"]}
    projected = value["proposed_article"]["ordered_body_blocks"]
    assert [row for row in projected if row["id"] in original_ids] == data["book"]["ordered_body_blocks"]
    assert value["sources"] == data["context"]["sources"]
    assert value["context_sources"] == data["context"]["context_sources"]
    assert value["theme_additions"] == data["context"]["theme_additions"]
    assert value["placement_groups"] == data["context"]["placement_groups"]
    for section in value["proposed_article"]["new_sections"]:
        assert "paragraphs" not in section
        for key in section["addition_ids"]:
            block = next(row for row in projected if row["id"] == key)
            assert block["paragraph"] == data["context"]["theme_additions"][key]
