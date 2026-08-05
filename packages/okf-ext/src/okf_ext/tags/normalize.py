"""Canonical tag forms. Pure, no I/O.

**Computing a canonical form never rewrites anything.** It feeds the
normalization clusters in `inventory` and the plans in `rename`; writing
happens only through `rename.apply`.
"""

from __future__ import annotations

import re
import unicodedata

from okf_ext.context import DEFAULT_NORMALIZATION, NormalizationPolicy

#: A run of whitespace or underscores is one separator, however long.
_SEPARATOR_RUN = re.compile(r"[\s_]+")

#: The same characters, where they pad the ends. `str.strip()` would remove
#: only the whitespace, leaving underscore padding to be collapsed into a
#: dangling separator (`__legacy__` -> `-legacy-`, `___` -> `-`).
_PADDING = re.compile(r"^[\s_]+|[\s_]+$")


def canonical(tag: str, policy: NormalizationPolicy = DEFAULT_NORMALIZATION) -> str:
    """The canonical form of *tag* under *policy*.

    The order is not arbitrary. Unicode normalization runs first because NFKC
    can change which characters are even present; stripping second, so padding
    removed there never becomes a separator; case third; separator collapse
    last, over whatever the earlier steps produced.

    Existing separators of the target kind are left alone — only whitespace and
    underscores collapse — so `e-commerce` survives a round trip unchanged.

    The replacement is a lambda, not a format string, to treat the configured
    separator as a literal string. Otherwise, backslashes or group references
    in an unconstrained `str` field could crash (`re.error`) or silently
    produce wrong output.

    Padding is stripped by regex, not `str.strip()`, because `_SEPARATOR_RUN`
    matches both whitespace and underscores. Using `str.strip()` alone leaves
    underscore padding, which then gets collapsed into a dangling separator
    (`__legacy__` -> `-legacy-`, `___` -> `-`), breaking both correctness and
    idempotency when `separator` is whitespace.
    """
    text = tag
    if policy.unicode_form != "none":
        text = unicodedata.normalize(policy.unicode_form, text)
    if policy.strip:
        text = _PADDING.sub("", text)
    if policy.case == "lower":
        text = text.lower()
    return _SEPARATOR_RUN.sub(lambda _match: policy.separator, text)
