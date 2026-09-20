"""`run_schema_read`: every schema and section file, parsed as-is, keyed by stem."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from graph_works_core import apply_init, plan_init
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.schema_read import run_schema_read


@pytest.fixture
def layout(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    layout = apply_init(plan_init(repo / ".works", today=date(2026, 9, 19), topic="S")).layout
    (layout.config_dir / "schema").mkdir(exist_ok=True)
    (layout.config_dir / "sections").mkdir(exist_ok=True)
    return layout


def test_keys_are_stems_and_values_are_parsed(layout) -> None:
    (layout.config_dir / "schema" / "Zeta.schema.json").write_text('{"title": "Zeta"}', encoding="utf-8")
    (layout.config_dir / "schema" / "_base.schema.json").write_text('{"$id": "base"}', encoding="utf-8")
    (layout.config_dir / "sections" / "_fragments.demo.yaml").write_text("fragments:\n  a: b\n", encoding="utf-8")
    (layout.config_dir / "schema" / "notes.txt").write_text("ignored", encoding="utf-8")

    read = run_schema_read(layout)

    assert read.schemas["Zeta"] == {"title": "Zeta"}
    assert read.schemas["_base"] == {"$id": "base"}
    assert read.sections["_fragments.demo"] == {"fragments": {"a": "b"}}
    assert "notes" not in read.schemas and "notes.txt" not in read.schemas
    assert list(read.schemas) == sorted(read.schemas)
    assert list(read.sections) == sorted(read.sections)


def test_a_broken_schema_names_the_file(layout) -> None:
    (layout.config_dir / "schema" / "Bad.schema.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(WorkspaceError, match=r"Bad\.schema\.json"):
        run_schema_read(layout)


def test_a_broken_section_file_names_the_file(layout) -> None:
    (layout.config_dir / "sections" / "Bad.yaml").write_text("a: [unclosed\n", encoding="utf-8")

    with pytest.raises(WorkspaceError, match=r"Bad\.yaml"):
        run_schema_read(layout)
