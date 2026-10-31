"""Conservative sentence citation coverage, independent of model inference.

This checks formatting and reference placement, not semantic entailment. The
answer generator separately checks every reference against retrieved evidence.
"""

from __future__ import annotations

import re

_CLOSERS = frozenset('”’"\'）)】')


def has_sentence_citation_coverage(content, reference):
    """Require a trailing citation for each complete sentence.

    Citations may precede or follow sentence punctuation. Dots inside ASCII
    identifiers/versions such as torch.cuda and 3.11 do not split sentences.
    Mid-sentence or prefix references cannot cover a later uncited claim.
    """
    matches = {match.start(): match for match in reference.finditer(content)}

    def terminal(index):
        char = content[index]
        if char in '。！？!?':
            return True
        if char != '.':
            return False
        before = content[index - 1] if index else ''
        after = content[index + 1] if index + 1 < len(content) else ''
        return not (before and after and before.isascii() and after.isascii()
                    and (before.isalnum() or before == '_')
                    and (after.isalnum() or after == '_'))

    start, index, sentences = 0, 0, 0
    while index < len(content):
        if index in matches:
            index = matches[index].end()
            continue
        if not terminal(index):
            index += 1
            continue

        body = content[start:index].rstrip()
        if not re.search(r'\w', reference.sub('', body)):
            return False
        # An immediately trailing reference before punctuation is accepted.
        suffix = body.rstrip(''.join(_CLOSERS)).rstrip()
        cited = any(match.end() == len(suffix) for match in reference.finditer(suffix))
        index += 1
        # Consume closing quotes, whitespace, punctuation clusters and references
        # attached after this sentence; never borrow a later sentence's citation.
        while index < len(content):
            if index in matches:
                cited = True
                index = matches[index].end()
            elif content[index].isspace() or content[index] in _CLOSERS or terminal(index):
                index += 1
            else:
                break
        if not cited:
            return False
        sentences += 1
        start = index
    return bool(sentences) and not content[start:].strip()
