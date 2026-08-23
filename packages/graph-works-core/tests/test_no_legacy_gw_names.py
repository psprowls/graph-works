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


#: Pre-rename names as they appear *in source text* rather than on disk. Each
#: pattern is anchored on a quote or a path separator, so a private helper
#: named `_config` or a `parsers._config` import does not trip the guard.
_LEGACY_TEXT = (
    '"_gw"',
    "'_gw'",
    "_gw/",
    '"_schema"',
    "'_schema'",
    "_schema/",
    '"_sections"',
    "'_sections'",
    "_sections/",
    "_tags.yaml",
    "_config/",
    "_cache/",
)


def test_no_src_tree_still_writes_a_pre_rename_name_in_its_text() -> None:
    """The filesystem guard above misses a name spelled *inside* a file — a
    literal that resolves a directory, or prose describing the old layout.
    `wiki_cli/proposals.py` carried `declarations_dir / "_schema"` past the
    rename because nothing looked at file contents."""
    root = _repo_root()
    offenders: list[str] = []
    for src in sorted(root.glob("packages/*/src")):
        for path in sorted(src.rglob("*")):
            if not path.is_file() or path.suffix in {".pyc", ".db"}:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):  # pragma: no cover -- no binary asset ships today
                continue
            for pattern in _LEGACY_TEXT:
                if pattern in text:
                    offenders.append(f"{path.relative_to(root).as_posix()}: {pattern}")
    assert not offenders, f"pre-rename name(s) still written under packages/*/src: {sorted(offenders)}"
