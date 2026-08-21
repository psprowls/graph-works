"""Every exit code code-graph-io's README marks "reserved" must have zero
producers in graph-works-core, the only package that returns these codes.
Guards against the README claiming "reserved" for a code this package has
since started producing (see the 2026-08-05 tech-debt item this test
originated from).
"""

from __future__ import annotations

import re
from pathlib import Path

_README = Path(__file__).resolve().parents[3] / "code-graph-io" / "README.md"
_SRC = Path(__file__).resolve().parents[2] / "src" / "graph_works_core"

_TABLE_ROW = re.compile(r"^\|\s*\d+\s*\|\s*`([A-Z_]+)`\s*\|(?P<meaning>.*)\|\s*$", re.MULTILINE)


def _reserved_code_names() -> list[str]:
    table = _README.read_text(encoding="utf-8")
    return [name for name, meaning in _TABLE_ROW.findall(table) if "reserved" in meaning.lower()]


def test_reserved_codes_have_no_producer_in_graph_works_core() -> None:
    reserved = _reserved_code_names()
    assert reserved, "expected at least one exit code marked reserved in the README"
    for name in reserved:
        producers = [path for path in _SRC.rglob("*.py") if f"exit_codes.{name}" in path.read_text(encoding="utf-8")]
        assert not producers, (
            f"README marks {name} reserved, but graph-works-core produces it in: {[str(p) for p in producers]}"
        )
