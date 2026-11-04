import copy

import pytest

from wechat_decrypt_tool import course_textbook_notebook_context as module
from wechat_decrypt_tool.course_textbook_evidence import inventory


def fixture():
    member = "a" * 64
    def document(text, cell):
        return {"kind": "document", "text": text,
                "origin": {"member_sha256": member, "cell": cell, "component": "source"}}
    value = inventory(
        {"id": "b" * 24, "source_export_sha256": "c" * 64,
         "evidence": {"S0": {"kind": "speech", "text": "Synthetic lesson", "start": 0, "end": 1}}},
        lesson_artifact="d" * 64,
        supplements=[document("prefix " * 300 + "essential final condition", 5),
                     document("Neighbor context only", 6)],
    )
    refs = [key for key, row in value["sources"].items()
            if row["kind"] == "document" and row["origin"]["cell"] == 5]
    visual = {"V0": {"kind": "retained_notebook_visual", "text": "Synthetic chart",
                     "origin": {"image_sha256": "e" * 64, "document_id": "fixture-doc"}}}
    links = {"links": {"V0": {"unique_direct_link": True, "candidates": [{
        "notebook_sha256": member, "cell": 5, "direct_text_source_ids": refs,
        "image_sha256": "e" * 64, "document_id": "fixture-doc",
    }]}}}
    return value, refs, visual, links


def test_split_target_keeps_original_identity_and_includes_the_entire_code_record():
    value, refs, visual, links = fixture()
    before = copy.deepcopy(value)
    assert len(refs) == 2
    context = module.build(value, visual, links, refs[1])
    assert context["target_source"] == value["sources"][refs[1]]
    assert context["target_source"]["text"] != value["originals"][1]["text"]
    original = context["complete_textual_originals"]["1"]
    assert original["source_ids"] == refs
    assert original["record"] == value["originals"][1]
    assert original["record"]["text"].endswith("essential final condition")
    assert original["role"] == "target_complete_original"
    assert value == before
    assert context["does_not_establish_lesson_affiliation"]


def test_visual_receives_its_actual_code_cell_and_explicit_neighbor_context():
    value, refs, visual, links = fixture()
    context = module.build(value, visual, links, "V0")
    assert set(refs) <= set(context["literal_source_ids"])
    assert context["visual_text_context"]["V0"] == visual["V0"]
    assert context["complete_textual_originals"]["1"]["role"] == "same_cell_context"
    assert context["complete_textual_originals"]["2"]["role"] == "neighbor_cell_context"
    assert context["does_not_reclassify_teaching_or_coverage"]
    narrow = module.build(value, visual, links, "V0", neighbors=0)
    assert set(narrow["complete_textual_originals"]) == {"1"}


@pytest.mark.parametrize("mutation", ["changed_text", "wrong_cell", "wrong_image", "ambiguous_origin"])
def test_changed_or_ambiguous_private_evidence_cannot_be_projected(mutation):
    value, refs, visual, links = fixture()
    if mutation == "changed_text":
        value["sources"][refs[0]]["text"] = "Changed original text"
    elif mutation == "wrong_cell":
        links["links"]["V0"]["candidates"][0]["cell"] = 6
    elif mutation == "wrong_image":
        visual["V0"]["origin"]["image_sha256"] = "f" * 64
    else:
        links["links"]["V0"]["candidates"] *= 2
    with pytest.raises(ValueError):
        module.build(value, visual, links, "V0")
