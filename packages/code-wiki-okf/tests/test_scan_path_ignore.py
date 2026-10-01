"""Every bundle load on scan's path honours the caller's `ignore=`.

Band 2 cannot import the lane that names the clone glob, so the caller supplies it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "code_wiki_okf"
SCAN_PATH = ("sync/run.py", "entities/sync.py", "entities/catalog.py", "mirror/apply.py")


def _bare_loads(path: Path) -> list[int]:
    lines: list[int] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "load_bundle":
            lines.extend([node.lineno] if not any(keyword.arg == "ignore" for keyword in node.keywords) else [])
    return lines


@pytest.mark.parametrize("relative", SCAN_PATH)
def test_no_scan_path_load_drops_the_callers_ignore(relative: str) -> None:
    assert _bare_loads(SRC / relative) == []
