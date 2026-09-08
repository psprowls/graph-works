"""Every okf-ext rule module that walks documents has a recorded disposition.

okf-io funnels every rule through one chokepoint; okf-ext cannot, because some
of its rules are legitimately cross-document. The enforceable property is that
the set of modules walking `bundle.concepts` is closed and each member's
scoped/unscoped choice is reviewed -- a new rule module must not join silently
and inherit whichever behaviour it happens to get.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src" / "okf_ext"

#: module path -> disposition. "scoped": honours `RuleContext.scope` because it
#: is per-document. "unscoped": deliberately whole-corpus, with the reason.
_DISPOSITION: dict[str, str] = {
    "render/rule.py": "scoped",
    "sections/rule.py": "scoped",
    "schemas/rule.py": "scoped",
    "placement/rule.py": "unscoped: resource-collision accumulates first_claim across documents",
    "health/rule.py": "unscoped: duplicate-titles groups across documents; uncited reads backlinks",
    "tags/vocabulary.py": "unscoped: not in any gate rule set; narrowing it buys nothing",
}


def _reads_context_bundle(tree: ast.AST) -> bool:
    """`context.bundle` accessed anywhere -- the mark of a `RuleContext`-shaped
    rule function, as opposed to a capability that takes a `Bundle` directly.
    """
    return any(
        isinstance(node, ast.Attribute)
        and node.attr == "bundle"
        and isinstance(node.value, ast.Name)
        and node.value.id == "context"
        for node in ast.walk(tree)
    )


def _reads_dot_concepts(tree: ast.AST) -> bool:
    """`<anything>.concepts` accessed anywhere."""
    return any(isinstance(node, ast.Attribute) and node.attr == "concepts" for node in ast.walk(tree))


def _modules_walking_concepts() -> set[str]:
    """Rule modules that walk `bundle.concepts` where `bundle` traces back to
    `context.bundle`.

    Matching the direct chain `context.bundle.concepts` alone misses
    `render/rule.py`, which threads `context.bundle` through a helper's plain
    `bundle: Bundle` parameter before indexing `.concepts` -- so a module
    counts if it both reads `context.bundle` *and* reads `.concepts` somewhere,
    rather than requiring the two in one expression. Requiring `context.bundle`
    is what keeps this from also catching the many non-rule capabilities
    (`moves`, `tables`, `search`, `generators`, `proposals`, `tags/rename`,
    `tags/inventory`, `sections/scaffold`) that take a `Bundle` argument
    directly and never see a `RuleContext`.
    """
    found: set[str] = set()
    for path in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if _reads_context_bundle(tree) and _reads_dot_concepts(tree):
            found.add(path.relative_to(_SRC).as_posix())
    return found


def test_the_set_of_rule_modules_walking_documents_is_closed():
    assert _modules_walking_concepts() == set(_DISPOSITION), (
        "a module started or stopped walking `context.bundle.concepts`; decide "
        "whether it is per-document (scope it) or cross-document (record why "
        "not) and update _DISPOSITION"
    )


def test_every_scoped_module_actually_consults_the_scope():
    for module, disposition in _DISPOSITION.items():
        source = (_SRC / module).read_text(encoding="utf-8")
        consults = "context.scope" in source
        assert consults is disposition.startswith("scoped"), f"{module}: {disposition}"
