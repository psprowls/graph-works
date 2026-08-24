"""The retired-dialect boundary: no package source may touch it.

Relocated from `test_migration.py` (D-025/D-043): `packages/work-tracker-okf/CLAUDE.md`
states this as a *package* rule, not a property of `migration.py`, so it survives
`migration.py`'s deletion. `test_no_legacy_work_contract.py` matches text; this
matches `ast.Name` identifiers and `Document.set` call arguments, which the text
scan cannot see.
"""

from __future__ import annotations

import ast
from pathlib import Path


def _legacy_boundary_violations(repo_root: Path) -> list[str]:
    packages_root = repo_root / "packages"
    forbidden = {"workflow_status", "parent", "children"}
    violations: list[str] = []
    for path in sorted(packages_root.glob("*/src/**/*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and "DATE_PREFIX" in node.id:
                violations.append(f"{path.name}:{node.lineno}:{node.id}")
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and (r"\d{4}-\d{2}-\d{2}-" in node.value or "YYYY-MM-DD-" in node.value)
            ):
                violations.append(f"{path.name}:{node.lineno}:legacy-date-prefix")
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and "workflow_status" in node.value:
                violations.append(f"{path.name}:{node.lineno}:workflow_status")
            imports_migration = (
                isinstance(node, ast.Import) and any(alias.name == "work_tracker_okf.migration" for alias in node.names)
            ) or (
                isinstance(node, ast.ImportFrom)
                and (
                    node.module == "work_tracker_okf.migration"
                    or (node.module == "work_tracker_okf" and any(alias.name == "migration" for alias in node.names))
                    or (
                        node.level > 0
                        and path.is_relative_to(packages_root / "work-tracker-okf/src/work_tracker_okf")
                        and (
                            node.module == "migration"
                            or (node.module is None and any(alias.name == "migration" for alias in node.names))
                        )
                    )
                )
            )
            if imports_migration:
                violations.append(f"{path.name}:{node.lineno}:legacy-import")
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in {"set", "insert", "__setitem__"} or not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and first.value in forbidden:
                violations.append(f"{path.name}:{node.lineno}:{first.value}")
    return violations


def test_legacy_parsing_and_frontmatter_writes_are_isolated_to_migration_module() -> None:
    repo_root = Path(__file__).parents[3]

    assert _legacy_boundary_violations(repo_root) == []


def test_legacy_boundary_scans_cli_and_plain_imports(tmp_path: Path) -> None:
    rogue = tmp_path / "packages/graph-works-cli/src/graph_works_cli/rogue.py"
    rogue.parent.mkdir(parents=True)
    rogue.write_text("import work_tracker_okf.migration\n", encoding="utf-8")
    second = tmp_path / "packages/code-wiki-okf/src/code_wiki_okf/rogue_from.py"
    second.parent.mkdir(parents=True)
    second.write_text("from work_tracker_okf.migration import plan_migration\n", encoding="utf-8")

    violations = _legacy_boundary_violations(tmp_path)

    assert {"rogue.py:1:legacy-import", "rogue_from.py:1:legacy-import"} <= set(violations)


def test_legacy_boundary_rejects_relative_import_forms(tmp_path: Path) -> None:
    package = tmp_path / "packages/work-tracker-okf/src/work_tracker_okf"
    package.mkdir(parents=True)
    direct = package / "rogue_relative.py"
    direct.write_text("from .migration import plan_migration\n", encoding="utf-8")
    sibling = package / "rogue_sibling.py"
    sibling.write_text("from . import migration\n", encoding="utf-8")

    violations = _legacy_boundary_violations(tmp_path)

    assert {"rogue_relative.py:1:legacy-import", "rogue_sibling.py:1:legacy-import"} <= set(violations)
