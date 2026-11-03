import copy

import pytest

from wechat_decrypt_tool import course_textbook_gap_final_review as m
from test_course_textbook_gap_write import candidate, packet, review


def test_unit_negatives_stay_bound_to_actual_integrated_article():
    inputs, plan = packet()
    candidates = {u["unit"]: candidate(u) for u in plan["units"]}
    book = m.writing.assemble(inputs, plan, candidates)
    proofs = {u["unit"]: {"candidate": candidates[u["unit"]],
              "review": review(candidates[u["unit"]], u, passed=False)} for u in plan["units"]}
    before = copy.deepcopy(proofs)
    actual, pending = m.pending_items(inputs, plan, book, proofs)
    assert pending
    assert {v["kind"] for v in pending.values()} == {"facts", "coverage", "retention"}
    for value in pending.values():
        assert not value["original_negative_review"]["passed"]
        if value["kind"] == "facts":
            assert value["actual_article_items"]
            assert all(actual[k] == value["actual_content"] for k in value["actual_article_items"])
    assert proofs == before
    book["chapters"][0]["sections"][0]["paragraphs"][0] = "Changed actual text"
    with pytest.raises(ValueError, match="pending_fact_exists"):
        m.pending_items(inputs, plan, book, proofs)


def test_integrated_positive_needs_actual_article_and_original_support():
    schema = m.schema_for(["pending000"], {"original": {}}, {"p000": "Paragraph"})
    value = {"items": {"pending000": {"passed": True, "reason": "Synthetic", "source_keys": ["original"], "article_items": []}}}
    with pytest.raises(ValueError, match="integrated_positive_actual"):
        m.validate(value, schema)
    value["items"]["pending000"]["article_items"] = ["p000"]
    m.validate(value, schema)
