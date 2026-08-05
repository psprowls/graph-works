"""Walks a fixtures/<lang>/ directory, parses each fixture, compares against
the matching <name>.expected.json.

Comparison is tree-shape-aware: we serialize SourceNode -> dict (kind, name,
language, refs[kind/target_name/target_module], children[recursive]) and
diff that against the expected JSON. Spans are NOT compared (line numbers
shift as fixtures evolve); the projection tests cover them where they
matter.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from code_parser.tree import SourceNode

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_ROOT = PACKAGE_ROOT / "fixtures"


def fixtures_for(language: str, extensions: tuple[str, ...]) -> list[Path]:
    """Return all source-file fixtures for a language (paired with an .expected.json)."""
    base = FIXTURES_ROOT / language
    out: list[Path] = []
    for ext in extensions:
        out.extend(sorted(base.glob(f"*{ext}")))
    return [
        p
        for p in out
        if p.with_suffix(p.suffix + ".expected.json").exists() or p.with_name(p.stem + ".expected.json").exists()
    ]


def expected_path_for(fixture: Path) -> Path:
    """`foo.js` -> `foo.expected.json`."""
    return fixture.with_name(fixture.stem + ".expected.json")


def serialize_tree(node: SourceNode) -> dict[str, Any]:
    return {
        "kind": node.kind,
        "name": node.name,
        "language": node.language,
        "package": node.package,
        "attrs": _scrub_attrs(node.attrs),
        "refs": [
            {
                "kind": r.kind,
                "target_name": r.target_name,
                "target_module": r.target_module,
                "attrs": dict(r.attrs),
            }
            for r in node.refs
        ],
        "children": [serialize_tree(c) for c in node.children],
    }


def _scrub_attrs(attrs: dict[str, Any]) -> dict[str, Any]:
    """Drop noisy fields that legitimately vary across runs (e.g. parse_errors byte offsets)."""
    out = {}
    for k, v in attrs.items():
        if k == "parse_errors":
            out[k] = [{"start_line": e.get("start_line"), "end_line": e.get("end_line")} for e in v]
        else:
            out[k] = v
    return out


def load_expected(fixture: Path) -> dict[str, Any]:
    return json.loads(expected_path_for(fixture).read_text(encoding="utf-8"))


def diff(actual: dict[str, Any], expected: dict[str, Any], path: str = "") -> list[str]:
    """Return a list of human-readable diff lines. Empty list = match."""
    diffs: list[str] = []
    if isinstance(actual, dict) and isinstance(expected, dict):
        for k in sorted(set(actual) | set(expected)):
            sub = f"{path}.{k}" if path else k
            if k not in actual:
                diffs.append(f"missing key in actual: {sub}")
            elif k not in expected:
                diffs.append(f"unexpected key in actual: {sub} (value={actual[k]!r})")
            else:
                diffs.extend(diff(actual[k], expected[k], sub))
    elif isinstance(actual, list) and isinstance(expected, list):
        if len(actual) != len(expected):
            diffs.append(f"length mismatch at {path}: actual={len(actual)} expected={len(expected)}")
        for i, (a, e) in enumerate(zip(actual, expected, strict=False)):
            diffs.extend(diff(a, e, f"{path}[{i}]"))
    else:
        if actual != expected:
            diffs.append(f"mismatch at {path}: actual={actual!r} expected={expected!r}")
    return diffs
