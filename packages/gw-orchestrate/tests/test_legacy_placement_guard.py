"""Only the allowlisted modules may read the retired placement stamps or write placement keys."""

from __future__ import annotations

import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_PACKAGES = _ROOT / "packages"

_READERS = {
    "work-tracker-okf/src/work_tracker_okf/items.py",
    "work-tracker-okf/src/work_tracker_okf/_rules/state.py",
    "gw-pipeline/src/gw_pipeline/placement.py",
    "gw-kernel/src/gw_kernel/read_session/snapshot.py",
    "gw-work/src/gw_work/migrate_placement.py",
}
#: No module records a placement stamp: computed placement retired the code stamps and
#: workspace writes land on the primary checkout.
_WRITERS: set[str] = set()
_READ_NAMES = {"legacy_placement", "repo_stamps", "LegacyPlacement"}


def _sources() -> list[tuple[str, ast.AST]]:
    return [
        (path.relative_to(_PACKAGES).as_posix(), ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        for path in sorted(_PACKAGES.glob("*/src/**/*.py"))
    ]


def _first_str(call: ast.Call) -> str | None:
    if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
        return call.args[0].value
    return None


def test_only_allowlisted_modules_read_legacy_placement() -> None:
    readers = set()
    for rel, tree in _sources():
        for node in ast.walk(tree):
            if (isinstance(node, ast.Attribute) and node.attr in _READ_NAMES) or (
                isinstance(node, ast.Name) and node.id in _READ_NAMES
            ):
                readers.add(rel)
    # An allowlist is a ceiling: state.py and migrate_placement.py work on raw frontmatter and read none of these names.
    assert readers <= _READERS, sorted(readers - _READERS)


def test_no_module_writes_placement_keys() -> None:
    offenders = []
    for rel, tree in _sources():
        if rel in _WRITERS:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name == "set" and _first_str(node) in {"worktree", "branch", "repo_stamps"}:
                offenders.append(f"{rel}:{node.lineno}")
            if name == "FieldChange" and _first_str(node) in {"worktree", "branch"}:
                offenders.append(f"{rel}:{node.lineno}")
    assert offenders == []
