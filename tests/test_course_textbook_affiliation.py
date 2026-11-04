import copy

import jsonschema
import pytest

from wechat_decrypt_tool.course_textbook_affiliation import finalize, quote_identities


def fixture():
    sources = {
        "L1": {"kind": "speech", "text": "The loop repeats the operation until the condition is false."},
        "D1": {"kind": "document", "text": "Repeat the operation while the condition remains true."},
        "V1": {"kind": "retained_notebook_visual", "text": "A condition controls whether the next iteration runs."},
        "D0": {"kind": "document", "text": ""},
    }
    proposal = {"sources": {"D1": {"kind": "same_lesson_teaching", "reason": "A concrete loop dependency.",
        "pairs": [{"lesson_source": "L1", "source_quote": sources["D1"]["text"],
                   "lesson_quote": sources["L1"]["text"]}]},
        "V1": {"kind": "unresolved", "reason": "No complete correspondence was established."}}}
    return sources, proposal


def test_one_link_cannot_promote_an_entire_notebook_with_an_unresolved_image():
    sources, proposal = fixture()
    identities = quote_identities(proposal, sources, ["D1", "V1"])
    result = finalize(identities, {"D1": {"verdict": "supported"}}, {"D0"}, {"member": ["D1", "V1", "D0"]})
    assert not result["association_verified"]
    assert result["notebooks"]["member"]["relationship"] == "partially_linked"
    assert result["notebooks"]["member"]["unresolved"] == 1


def test_real_quotes_still_need_an_independent_semantic_verdict():
    sources, proposal = fixture()
    identities = quote_identities(proposal, sources, ["D1", "V1"])
    with pytest.raises(ValueError, match="complete_independent_semantic_verdicts"):
        finalize(identities, {}, {"D0"}, {"member": ["D1", "V1", "D0"]})
    result = finalize(identities, {"D1": {"verdict": "unresolved"}}, {"D0"}, {"member": ["D1", "V1", "D0"]})
    assert result["source_dispositions"]["D1"] == "unresolved"


def test_paraphrased_support_is_not_silently_repaired_into_a_real_quote():
    sources, proposal = fixture()
    original = copy.deepcopy(proposal)
    proposal["sources"]["D1"]["pairs"][0]["lesson_quote"] = "A different unsupported reconstruction of the operation."
    identities = quote_identities(proposal, sources, ["D1", "V1"])
    assert identities["D1"]["status"] == "unresolved_invalid_quotes"
    assert proposal["sources"]["D1"]["pairs"] != original["sources"]["D1"]["pairs"]
    assert proposal["sources"]["D1"]["pairs"][0]["lesson_quote"].startswith("A different")


def test_document_identifiers_cannot_be_passed_off_as_lesson_evidence():
    sources, proposal = fixture()
    proposal["sources"]["D1"]["pairs"][0]["lesson_source"] = "V1"
    with pytest.raises(jsonschema.ValidationError):
        quote_identities(proposal, sources, ["D1", "V1"])


def test_whole_scope_must_include_non_teaching_items_and_every_visual_item():
    sources, proposal = fixture()
    identities = quote_identities(proposal, sources, ["D1", "V1"])
    with pytest.raises(ValueError, match="complete_partition_without_exclusion"):
        finalize(identities, {"D1": {"verdict": "supported"}}, {"D0"}, {"member": ["D1", "D0"]})
