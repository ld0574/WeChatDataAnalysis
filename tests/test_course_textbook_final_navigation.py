from types import SimpleNamespace

from wechat_decrypt_tool import course_textbook_final_navigation as module


def test_navigation_only_changes_heading_without_mutating_original():
    original = {
        "heading": "同一主题",
        "paragraphs": [{"text": "独立解释"}],
        "coverage": {"P0": {"paragraphs": [0]}},
    }
    result = module.disambiguate(original, "section-0002-whole-edit")
    assert result["heading"] == "同一主题（第3节）"
    assert original["heading"] == "同一主题"
    assert result["paragraphs"] == original["paragraphs"]
    assert result["coverage"] == original["coverage"]


def test_distinct_sections_get_distinct_navigation():
    candidate = {"heading": "同一主题"}
    assert (
        module.disambiguate(candidate, "section-0002-whole-edit")["heading"]
        != module.disambiguate(candidate, "section-0003-whole-edit")["heading"]
    )


def test_factual_review_stays_on_original_authentication_path(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        module.mapping,
        "authenticated",
        lambda *args: calls.append(args[1]) or {"passed": False},
    )
    value = module.authenticated(
        SimpleNamespace(root=tmp_path),
        "section-0002-actual-body-review",
        "prompt",
        {},
        {},
        lambda v: None,
        {},
    )
    assert value == {"passed": False}
    assert calls == ["section-0002-actual-body-review"]
