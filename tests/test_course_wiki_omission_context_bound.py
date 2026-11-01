import copy

import pytest

from wechat_decrypt_tool.course_library import canonical
from wechat_decrypt_tool.course_wiki_completeness import stages


def value(width=2200):
    return {
        'article': {
            'title': 'Synthetic bounded review',
            'overview': {'text': 'Synthetic overview', 'citations': ['a']},
            'prerequisites': [],
            'pitfalls': [],
            'quiz': [],
            'sections': [{
                'heading': 'Synthetic chapter',
                'paragraphs': [
                    {'text': 'Synthetic explanation. ' * (width // 23), 'citations': [ref]}
                    for ref in ['a', 'b'] * 4
                ],
                'code': '',
                'code_citations': [],
            }],
        },
        'packet': {
            'sources': {'a': {'text': 'Synthetic original A.\n' * 25}, 'b': {'text': 'Synthetic original B.\n' * 25}},
            'chapter_scopes': [['a', 'b']],
        },
    }


def test_focused_requests_fit_while_parent_is_large_without_losing_originals():
    original = value()
    before = canonical(original)
    with pytest.raises(ValueError, match='omissions_chapter_context_exceeds_bound'):
        list(stages(original))
    focused = list(stages(original, per_source=True))
    assert len(focused) == 2
    for name, context in focused:
        assert len(canonical(context).decode()) <= 16000
        assert context['sources'] == original['packet']['sources']
        ref = context['source_focus']
        assert ''.join(w['quote'] for w in context['windows'].values()) == context['sources'][ref]['text']
        assert all(ref in target['citations'] for target in context['article_targets'].values())
    assert canonical(original) == before


def test_focused_request_still_obeys_original_context_limit():
    with pytest.raises(ValueError, match='omissions_chapter_context_exceeds_bound'):
        list(stages(value(width=6000), per_source=True))


def test_original_text_limit_is_not_relaxed_by_source_partition():
    original = value()
    original['packet']['sources']['b']['text'] = 'b' * 3201
    with pytest.raises(ValueError, match='omissions_chapter_originals_exceed_bound'):
        list(stages(original, per_source=True))


def test_small_existing_requests_and_all_character_windows_stay_equal():
    original = value(width=100)
    before = copy.deepcopy(original)
    parent = list(stages(original))[0][1]
    merged = {}
    for name, focused in stages(original, per_source=True):
        assert focused['sources'] == parent['sources']
        assert not merged.keys() & focused['windows'].keys()
        merged.update(focused['windows'])
    assert merged == parent['windows']
    assert original == before
