"""A mechanical guard: no package's `src/` tree still names a directory or
file after the pre-rename control-plane convention.

Scoped to whole path *components* — a fragment filename like
`_base-diataxis.schema.json` legitimately keeps its leading underscore (the
fragment rule, orthogonal to this rename) and must not trip this guard.
"""

from __future__ import annotations

from pathlib import Path

_LEGACY_COMPONENTS = frozenset({"_gw", "_schema", "_sections"})
_LEGACY_FILENAME = "_tags.yaml"


def _repo_root() -> Path:
    # packages/graph-works-core/tests/test_no_legacy_gw_names.py -> repo root
    return Path(__file__).resolve().parents[3]


def test_no_src_tree_still_names_a_pre_rename_directory_or_file() -> None:
    root = _repo_root()
    offenders: list[str] = []
    for src in sorted(root.glob("packages/*/src")):
        for path in src.rglob("*"):
            relative = path.relative_to(root).as_posix()
            if any(part in _LEGACY_COMPONENTS for part in path.parts) or path.name == _LEGACY_FILENAME:
                offenders.append(relative)
    assert not offenders, f"pre-rename name(s) still present under packages/*/src: {sorted(set(offenders))}"
