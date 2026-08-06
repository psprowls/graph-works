"""Tokenization and snippet extraction, ported from the reference implementation.

The token pattern, the stopword list and the snippet arithmetic are unchanged
from the reference (spec §5.3). Two mechanical adaptations, neither of which
touches behaviour: both functions return frozen data, and `snippet`'s `width`
is keyword-only.

`tokenize`'s `len(t) > 1` guard is redundant — `TOKEN_RE` already requires two
characters — and is kept anyway. A redundant guard is not a bug, and removing
it is not this item's work.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

#: Two characters minimum, and hyphens, underscores and apostrophes are kept
#: *inside* a token (spec §4.3): `headline-metric` is one token, so a text
#: search for `metric` does not reach it. Splitting on hyphens would change
#: term frequencies, which is exactly what the parity golden exists to freeze,
#: and `Filters.tags` already covers the case.
#:
#: **Known limitation: the character classes are ASCII-only**, and that is
#: inherited from the reference rather than chosen here. Three consequences,
#: in descending order of how much they bite:
#:
#: - **A document in a non-Latin script indexes to an empty `tf`** and is
#:   unreachable by any text query. `数据质量` yields no tokens at all, so the
#:   document carrying it can be found by filters and by the §5.5 browse, and
#:   by nothing else.
#: - **Accented Latin is mangled, but symmetrically.** `Müller` tokenizes to
#:   `ller` at index time *and* at query time, so searching the accented
#:   spelling works. Searching the unaccented one a reader would actually
#:   type — `muller` — does not.
#: - **Splitting on an accent leaves junk in the index.** `naïve` becomes
#:   `na` and `ve`, both of which are real tokens a query can match.
#:
#: Widening the classes is not a local edit: it changes tokenization, so it
#: changes term frequencies, so it changes every score. The parity golden
#: exists to make exactly that kind of change loud rather than silent. If a
#: corpus ever needs it, the change belongs with a new golden and a note about
#: what the old rankings were.
TOKEN_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_\-']+")

#: The reference's list verbatim. `a` and `i` can never be produced by
#: `TOKEN_RE` and are kept regardless: this is a port, not a tidy-up.
STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "if",
        "then",
        "so",
        "to",
        "of",
        "in",
        "on",
        "at",
        "for",
        "by",
        "with",
        "from",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "as",
        "we",
        "you",
        "they",
        "their",
        "our",
        "us",
        "i",
        "not",
        "no",
        "yes",
        "do",
        "does",
        "did",
        "will",
        "would",
        "can",
        "could",
        "should",
        "about",
        "into",
        "than",
        "out",
        "up",
        "down",
        "over",
        "under",
        "also",
    }
)


def tokenize(text: str) -> tuple[str, ...]:
    """Lowercase tokens of *text*, stopwords removed.

    Returns `()` for text `TOKEN_RE` cannot represent — see its ASCII-only
    limitation above. A caller who needs to tell "this query has no usable
    terms" from "this query matched nothing" must check this, because
    `search()` cannot tell them apart afterwards.
    """
    return tuple(t.lower() for t in TOKEN_RE.findall(text) if t.lower() not in STOPWORDS and len(t) > 1)


def snippet(text: str, query: Sequence[str], *, width: int = 220) -> str:
    """An excerpt of *text* around the first term of *query* it contains.

    Falls back to the head of *text* when no term matches — which is also what
    an empty *query* gets, and what the empty-query browse (§5.5) relies on.
    Ellipses mark a truncated end and only a truncated end.

    *query* terms are expected lowercase, as `tokenize` returns them; the
    search is over a lowercased copy of *text*.
    """
    lower = text.lower()
    for term in query:
        idx = lower.find(term)
        if idx >= 0:
            start = max(0, idx - width // 3)
            end = min(len(text), start + width)
            excerpt = text[start:end].replace("\n", " ")
            return ("…" if start > 0 else "") + excerpt + ("…" if end < len(text) else "")
    return text[:width].replace("\n", " ") + ("…" if len(text) > width else "")
