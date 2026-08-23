from pathlib import Path

import pytest
from code_wiki_okf.mirror.create import write_new_page
from okf_ext.shape import load_sections
from okf_io import parse

_SECTIONS_DIR = Path(__file__).parents[2] / "src" / "code_wiki_okf" / "assets" / "sections"


_MEMBER = "repositories/acme/files/src/pkg/base.py.md"


def test_write_new_page_creates_file_at_mirrored_path(tmp_path: Path) -> None:
    section_set = load_sections(_SECTIONS_DIR)
    frontmatter = {
        "type": "File",
        "title": "base.py",
        "resource": "file:local/acme/src/pkg/base.py",
        "language": "python",
    }
    write_new_page(tmp_path, _MEMBER, frontmatter, section_set=section_set)
    target = tmp_path / _MEMBER
    assert target.exists()


def test_write_new_page_frontmatter_round_trips(tmp_path: Path) -> None:
    section_set = load_sections(_SECTIONS_DIR)
    frontmatter = {
        "type": "File",
        "title": "base.py",
        "resource": "file:local/acme/src/pkg/base.py",
        "language": "python",
        "package": "pkg",
        "role_flags": ["is_importable"],
        "generated": {"by": "code-wiki-okf", "at": "2026-01-01T00:00:00+00:00"},
        "last_updated_commit": "a" * 40,
    }
    write_new_page(tmp_path, _MEMBER, frontmatter, section_set=section_set)
    target = tmp_path / _MEMBER
    document = parse(target.read_text(encoding="utf-8"))
    assert document.parse_error is None
    assert document.fm.type == "File"
    assert document.fm.resource == "file:local/acme/src/pkg/base.py"
    assert document.fm.extra["language"] == "python"
    assert document.fm.extra["package"] == "pkg"
    assert document.fm.extra["role_flags"] == ["is_importable"]
    assert document.fm.extra["last_updated_commit"] == "a" * 40


def test_write_new_page_drops_keys_outside_key_order(tmp_path: Path) -> None:
    section_set = load_sections(_SECTIONS_DIR)
    frontmatter = {
        "type": "File",
        "title": "base.py",
        "resource": "file:local/acme/src/pkg/base.py",
        "not_a_declared_key": "should never be written",
    }
    write_new_page(tmp_path, _MEMBER, frontmatter, section_set=section_set)
    target = tmp_path / _MEMBER
    text = target.read_text(encoding="utf-8")
    assert "not_a_declared_key" not in text
    document = parse(text)
    assert document.parse_error is None
    assert "not_a_declared_key" not in document.fm.extra


def test_write_new_page_body_carries_every_declared_section(tmp_path: Path) -> None:
    section_set = load_sections(_SECTIONS_DIR)
    frontmatter = {"type": "File", "title": "base.py", "resource": "file:local/acme/src/pkg/base.py"}
    write_new_page(tmp_path, _MEMBER, frontmatter, section_set=section_set)
    target = tmp_path / _MEMBER
    body = target.read_text(encoding="utf-8")
    for heading in ("Notes", "Symbols", "Imports", "Exports", "Imported By"):
        assert f"## {heading}" in body
    assert "not yet generated" in body


def test_write_new_page_refuses_an_existing_target(tmp_path: Path) -> None:
    section_set = load_sections(_SECTIONS_DIR)
    frontmatter = {"type": "File", "title": "base.py", "resource": "file:local/acme/src/pkg/base.py"}
    write_new_page(tmp_path, _MEMBER, frontmatter, section_set=section_set)
    with pytest.raises(FileExistsError):
        write_new_page(tmp_path, _MEMBER, frontmatter, section_set=section_set)
