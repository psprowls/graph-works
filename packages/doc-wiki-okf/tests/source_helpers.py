"""Synthetic bundles for the sources subpackage's tests.

`build_bundle` loads with the **shipped** `doc_wiki_okf.cli.IGNORE` rather than
a local copy, so the round-trip assertion in `test_sources.py` measures the
constant the command actually uses.
"""

from __future__ import annotations

import importlib.resources
from collections.abc import Mapping
from datetime import UTC, date, datetime
from pathlib import Path

from doc_wiki_okf.cli import IGNORE
from doc_wiki_okf.resources import seed_files
from okf_ext.schemas import SchemaSet, load_schemas
from okf_ext.shape import SectionSet, load_sections
from okf_io import Bundle, load_bundle

TODAY = date(2026, 8, 12)
AT = datetime(2026, 8, 12, 9, 0, tzinfo=UTC)
BY = "agent:test"


def schema_set() -> SchemaSet:
    return load_schemas(str(importlib.resources.files("doc_wiki_okf") / "assets" / "_schema"))


def section_set() -> SectionSet:
    return load_sections(str(importlib.resources.files("doc_wiki_okf") / "assets" / "_sections"))


def build_bundle(root: Path, members: Mapping[str, str] | None = None) -> Bundle:
    """*root* seeded with an index and the twelve declarations, plus *members*.

    *members* is keyed by bundle-relative path **with** its suffix, because this
    suite writes non-markdown members too.
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.md").write_text("---\nokf_version: 0.2\n---\n\n# bundle\n", encoding="utf-8")
    for relative, text in seed_files().items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    for relative, text in (members or {}).items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return load_bundle(root, ignore=IGNORE)


def material(tmp_path: Path, name: str = "notes.md", text: str = "# Notes\n\nBody.\n") -> tuple[Path, str]:
    """A file outside any bundle, plus its text -- what the CLI hands the writer."""
    outside = tmp_path / "outside"
    outside.mkdir(parents=True, exist_ok=True)
    path = outside / name
    path.write_text(text, encoding="utf-8")
    return path, text
