"""No rule module may reach documents except through `_common.concepts`.

`RuleContext.scope` is only enforceable because there is exactly one
per-document iteration chokepoint in this package. A rule that walked
`ctx.bundle.concepts` directly would ignore the scope, restoring the
whole-corpus cost the seam exists to remove -- or, if it is a cross-document
rule, would be scoped when it must not be. grimp cannot see this shape, so it
is a hand-written AST assertion, in the style of the workspace's other
boundary tests.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_RULES_DIR = Path(__file__).resolve().parent.parent / "src" / "okf_io" / "_rules"

#: Modules allowed to touch a document mapping directly, each with the reason.
#: Adding a name here is a reviewed decision, not a formality.
_EXEMPT: dict[str, str] = {
    "_common.py": "defines the chokepoint",
    # `reserved` walks `bundle.indexes` / `bundle.logs`, which are not concepts
    # and are not per-document-expensive; it never touches `bundle.concepts`.
}

#: Attribute chains that reach a document mapping. `bundle.concepts` is the one
#: that matters; the others are listed so a future rename is caught too.
_DOCUMENT_MAPPINGS = frozenset({"concepts"})


def _rule_modules() -> list[Path]:
    return sorted(p for p in _RULES_DIR.glob("*.py") if p.name != "__init__.py")


def test_there_are_rule_modules_to_check():
    """A glob that silently matches nothing would make this test vacuous."""
    assert len(_rule_modules()) >= 9


@pytest.mark.parametrize("module", _rule_modules(), ids=lambda p: p.name)
def test_no_rule_module_iterates_documents_outside_the_chokepoint(module: Path):
    if module.name in _EXEMPT:
        pytest.skip(_EXEMPT[module.name])
    tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
    offenders = [
        f"{module.name}:{node.lineno}: .{node.attr}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in _DOCUMENT_MAPPINGS
    ]
    assert not offenders, (
        "reach documents through `okf_io._rules._common.concepts(ctx)` so "
        f"`RuleContext.scope` is honoured, or add a reviewed exemption: {offenders}"
    )


def test_the_chokepoint_itself_consults_the_scope():
    source = (_RULES_DIR / "_common.py").read_text(encoding="utf-8")
    assert "in_scope(ctx, member_path(concept_id))" in source
