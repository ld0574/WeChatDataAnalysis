import pytest

from wechat_decrypt_tool.course_answer_validation import has_sentence_citation_coverage
from wechat_decrypt_tool.course_knowledge import ANSWER_REFERENCE


@pytest.mark.parametrize('text', [
    'Python 需要配置。[证据 S1, S2]',
    'Fixture answer [证据 S1]。',
    'First [S1]. Second. [S2]',
    'First。[S1] Second [S2]。',
    'Use torch.cuda with Python 3.11 [S1].',
    'Run `torch.cuda.is_available()`。[证据 S1]',
    '“他说可以。”[证据 S1]',
    '“他说可以[证据 S1]。”',
    'First [S1]... Next [S2]!',
    'One [S1]?!\nTwo? [S2]',
    '见 config.json。[S1][S2]',
])
def test_complete_sentences_allow_citations_on_either_side_of_punctuation(text):
    assert has_sentence_citation_coverage(text, ANSWER_REFERENCE)


@pytest.mark.parametrize('text', [
    '', '[S1]', 'One. [S1] Incomplete', 'One [S1]',
    'One.[S1] Two.', 'One. Two.[S1]', '[S1] One.',
    'One [S1], then an unsupported claim.',
    'One [S1]. Uncited? Three [S2].',
    'One [S1]. [not a reference]',
    'First。[S1]\nSecond。',
    'One [S1]. “Uncited.”',
])
def test_uncited_or_incomplete_text_is_rejected(text):
    assert not has_sentence_citation_coverage(text, ANSWER_REFERENCE)
