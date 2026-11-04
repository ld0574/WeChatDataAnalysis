import copy

import pytest

from wechat_decrypt_tool import course_textbook_corpus_candidates as module


def fixture():
    def row(text, start, kind="speech"):
        return {"kind": kind, "text": text, "start": start, "end": start + 1}
    return {
        "intro": {"artifact": "a" * 64, "sources": {"1": row("Course overview", 0)}},
        "database": {"artifact": "b" * 64, "sources": {
            "10": row("Transaction before", 10), "2": row("isolation transaction rollback", 20),
            "30": row("Transaction after", 30), "99": row("Transport unrelated", 99),
        }},
        "network": {"artifact": "c" * 64, "sources": {"8": row("Transport protocol routing", 0)}},
    }


def test_sparse_batch_retrieval_preserves_originals_and_uses_temporal_neighbors():
    corpus = fixture()
    before = copy.deepcopy(corpus)
    result = module.rank(corpus, {"D0": {"literal": "isolation rollback"}},
                         limit=1, seeds_per_lesson=1, always_include=["intro"])["D0"]
    assert [row["lesson"] for row in result["courses"]] == ["intro", "database"]
    assert result["courses"][1]["seed_ids"] == ["2"]
    assert set(result["courses"][1]["source_ids"]) == {"10", "2", "30"}
    assert result["courses"][0]["source_ids"] == []
    assert result["searched_source_count"] == 6
    assert not result["ranking_establishes_affiliation"]
    assert not result["unretrieved_sources_are_excluded"]
    assert not result["semantic_acceptance"]
    assert corpus == before


def test_named_full_cell_variant_and_literal_variant_are_both_retained():
    result = module.rank(fixture(), {"D0": {"literal": "rollback", "whole_cell": "protocol routing"}},
                         limit=2, seeds_per_lesson=1)["D0"]
    by_id = {row["lesson"]: row for row in result["courses"]}
    assert set(by_id) == {"database", "network"}
    assert by_id["database"]["variant_rankings"]["literal"]["seed_ids"] == ["2"]
    assert by_id["network"]["variant_rankings"]["whole_cell"]["seed_ids"] == ["8"]


def test_no_lexical_match_does_not_become_an_exclusion_or_drop_a_required_lesson():
    result = module.rank(fixture(), {"D0": {"literal": "unseen_identifier"}},
                         always_include=["intro"])["D0"]
    assert result["matched_lesson_count"] == 0
    assert result["courses"][0]["lesson"] == "intro"
    assert result["courses"][0]["source_ids"] == []
    assert not result["unretrieved_sources_are_excluded"]


@pytest.mark.parametrize("mutation", ["kind", "clock", "limit"])
def test_invalid_sources_or_unbounded_requests_are_rejected(mutation):
    corpus = fixture()
    kwargs = {}
    if mutation == "kind":
        corpus["intro"]["sources"]["1"]["kind"] = "document"
    elif mutation == "clock":
        corpus["intro"]["sources"]["1"]["start"] = float("nan")
    else:
        kwargs["limit"] = 0
    with pytest.raises(ValueError):
        module.rank(corpus, {"D0": {"literal": "routing"}}, **kwargs)
