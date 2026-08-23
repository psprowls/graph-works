"""tree-sitter grammar loading via tree-sitter-language-pack, with a
standalone-package fallback for languages the pack doesn't ship."""

from __future__ import annotations

from functools import cache
from typing import cast

import tree_sitter
import tree_sitter_c_sharp
from tree_sitter_language_pack import SupportedLanguage
from tree_sitter_language_pack import get_language as _pack_get_language

from code_graph_io.parser.errors import UnsupportedLanguageError

# `tsx` is the TypeScript-with-JSX grammar. The plain `typescript` grammar
# cannot parse JSX, so `.tsx` files must be routed here (see TypeScriptParser).
_KNOWN: frozenset[str] = frozenset({"python", "javascript", "typescript", "tsx", "csharp"})

# Languages tree_sitter_language_pack does not ship (verified against its
# SupportedLanguage literal: it has `fsharp` but no `csharp`/`c_sharp`).
# For these, a lookup failure falls through to a standalone grammar binding
# instead of raising.
_FALLBACK: frozenset[str] = frozenset({"csharp"})


def _fallback_language(name: str) -> tree_sitter.Language:
    if name == "csharp":
        return tree_sitter.Language(tree_sitter_c_sharp.language())
    raise UnsupportedLanguageError(
        f"No fallback grammar registered for {name!r}",
        path=None,
        extension=None,
    )


@cache
def get_language(name: str) -> tree_sitter.Language:
    """Return the tree-sitter Language for a language name. Cached."""
    if name not in _KNOWN:
        raise UnsupportedLanguageError(
            f"Unknown grammar name: {name!r}. Known: {sorted(_KNOWN)}",
            path=None,
            extension=None,
        )
    try:
        return _pack_get_language(cast(SupportedLanguage, name))
    except Exception as exc:
        if name in _FALLBACK:
            return _fallback_language(name)
        raise UnsupportedLanguageError(
            f"Failed to load grammar for {name!r}: {exc}",
            path=None,
            extension=None,
        ) from exc
