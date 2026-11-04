import copy

import pytest

from wechat_decrypt_tool import course_textbook_contextual_affiliation as module


def fixture():
    sources = {
        "D0": {"kind": "document", "text": "table = input"},
        "D1": {"kind": "document", "text": ".drop_duplicates(subset=['account'])"},
        "V0": {"kind": "retained_notebook_visual", "text": "Unique accounts table"},
        "S0": {"kind": "speech", "text": "drop_duplicates removes repeated account rows"},
        "S1": {"kind": "speech", "text": "Adjacent classroom source"},
        "S2": {"kind": "speech", "text": "Transport protocols"},
    }
    context = {
        "target": "D0", "target_source": {**sources["D0"], "origin": {"cell": 1}},
        "complete_textual_originals": {"0": {
            "source_ids": ["D0", "D1"],
            "record": {"text": sources["D0"]["text"] + sources["D1"]["text"]},
        }},
        "visual_text_context": {"V0": sources["V0"]},
        "literal_source_ids": ["D0", "D1", "V0"],
        "does_not_establish_lesson_affiliation": True,
        "does_not_reclassify_teaching_or_coverage": True,
    }
    return sources, context


def test_complete_record_finds_candidates_missing_from_the_literal_fragment():
    sources, context = fixture()
    before = copy.deepcopy((sources, context))
    result = module.scope(sources, "D0", context, candidates=1)
    assert result["retrieval"]["literal_target_seed_ids"] == []
    assert result["retrieval"]["complete_cell_seed_ids"] == ["S0"]
    assert set(result["lesson_sources"]) == {"S0", "S1"}
    assert result["courseware_source"] == sources["D0"]
    assert result["complete_notebook_context"] == context
    assert not result["retrieval"]["ranking_is_affiliation_evidence"]
    assert not result["retrieval"]["unretrieved_sources_are_excluded"]
    assert (sources, context) == before


@pytest.mark.parametrize("mutation", ["target", "fragment", "visual", "declared_ids", "verdict"])
def test_wrong_or_incomplete_context_is_rejected_before_retrieval(mutation):
    sources, context = fixture()
    if mutation == "target":
        context["target"] = "D1"
    elif mutation == "fragment":
        context["complete_textual_originals"]["0"]["source_ids"].pop()
    elif mutation == "visual":
        context["visual_text_context"] = {"V0": {"kind": "retained_notebook_visual", "text": "Changed"}}
    elif mutation == "declared_ids":
        context["literal_source_ids"].pop()
    else:
        context["does_not_establish_lesson_affiliation"] = False
    with pytest.raises(ValueError):
        module.scope(sources, "D0", context)
