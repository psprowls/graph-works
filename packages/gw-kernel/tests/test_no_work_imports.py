"""Neither kernel source nor its tests depend on the work slice."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def work_imports(source: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            targets = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            target = node.module or ""
            targets = [target, *(f"{target}.{a.name}" if target else a.name for a in node.names)]
        else:
            continue
        found.extend(t for t in targets if t == "gw_work" or t.startswith("gw_work."))
    return found


@pytest.mark.parametrize(
    "source",
    [
        "import gw_work",
        "import gw_work.commands as work",
        "from gw_work import commands",
        "if TYPE_CHECKING:\n    from gw_work.archive import commands",
        "from .gw_work import commands",
        "from . import gw_work",
    ],
)
def test_reverse_guard_rejects_import_forms(source):
    assert work_imports(source)


def test_reverse_guard_ignores_comments():
    assert work_imports("# import gw_work\ntext = 'gw_work'") == []


def test_kernel_source_and_tests_do_not_import_work():
    roots = (ROOT / "src/gw_kernel", ROOT / "tests")
    assert all(root.is_dir() for root in roots)
    found = [
        f"{path}:{target}"
        for root in roots
        for path in sorted(root.rglob("*.py"))
        for target in work_imports(path.read_text(encoding="utf-8"))
    ]
    assert found == []
