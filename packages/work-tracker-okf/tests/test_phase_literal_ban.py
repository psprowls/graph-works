"""No module in `work_tracker_okf` keeps its own copy of stage knowledge (epic D-017, D-019).

Every phase set and every comparison against one phase name reads
`work_tracker_okf.pipeline`. The two exempt modules are the ones that *define*
the names: `pipeline.py` (the stage table) and `vocabulary.py` (`PHASES`).
There is no allowlist: a phase-spelled string that is not a phase gets a named
constant, as `paths.MANAGED_ARTIFACTS` did.

Flagged shapes: a set/tuple/list literal, or dict keys, holding two or more
phase-name string constants; a comparison with a phase-name string constant as
any operand; a `match` value pattern on one. Strings in messages, f-strings and
docstrings are not flagged.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import work_tracker_okf
from work_tracker_okf.vocabulary import PHASES

_PACKAGE = Path(work_tracker_okf.__file__).parent
_EXEMPT = frozenset({"pipeline.py", "vocabulary.py"})


def _is_phase(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in PHASES


def violations(source: str, filename: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source, filename=filename)):
        shape: str | None = None
        if isinstance(node, (ast.Set, ast.Tuple, ast.List)) and sum(map(_is_phase, node.elts)) >= 2:
            shape = "phase-set literal"
        elif isinstance(node, ast.Dict) and sum(map(_is_phase, node.keys)) >= 2:
            shape = "phase-keyed dict literal"
        elif isinstance(node, ast.Compare) and any(map(_is_phase, (node.left, *node.comparators))):
            shape = "comparison against a phase literal"
        elif isinstance(node, ast.MatchValue) and _is_phase(node.value):
            shape = "match on a phase literal"
        if shape is not None:
            found.append(f"{filename}:{getattr(node, 'lineno', '?')}: {shape}: {ast.unparse(node)[:100]}")
    return found


def _modules() -> list[Path]:
    return sorted(p for p in _PACKAGE.rglob("*.py") if p.name not in _EXEMPT)


def test_no_module_outside_pipeline_and_vocabulary_spells_a_phase_literal() -> None:
    found = [
        hit
        for path in _modules()
        for hit in violations(path.read_text(encoding="utf-8"), str(path.relative_to(_PACKAGE)))
    ]
    assert found == [], "read `work_tracker_okf.pipeline` instead:\n" + "\n".join(found)


def test_the_scan_covers_the_rules_package() -> None:
    names = {path.relative_to(_PACKAGE).as_posix() for path in _modules()}
    assert {"_rules/state.py", "_rules/graph.py", "_rules/decisions.py", "workflow.py"} <= names
    assert not names & _EXEMPT


@pytest.mark.parametrize(
    ("snippet", "shape"),
    [
        ("X = frozenset({'execute', 'finish'})\n", "phase-set literal"),
        ("if item.phase == 'done':\n    pass\n", "comparison against a phase literal"),
        ("match stage:\n    case 'plan':\n        pass\n", "match on a phase literal"),
        ("M = {'design': 1, 'plan': 2}\n", "phase-keyed dict literal"),
    ],
)
def test_each_banned_shape_is_caught(snippet: str, shape: str) -> None:
    hits = violations(snippet, "planted.py")
    assert len(hits) == 1 and shape in hits[0], hits


def test_messages_docstrings_and_single_mentions_are_not_flagged() -> None:
    clean = (
        '"""Runs at design, then plan."""\n'
        "LABEL = 'plan'\n"
        "msg = f\"phase {phase!r} is not 'done'\"\n"
        "ok = {'design-transcript', 'plan-transcript'}\n"
    )
    assert violations(clean, "clean.py") == []
