import importlib.resources
from datetime import date
from pathlib import Path

import pytest
from code_wiki_okf.init import InitError, init_bundle

_TODAY = date(2026, 1, 1)

_EXPECTED_FILES = {
    "index.md",
    "log.md",
    "_repositories.yaml",
    "_tags.yaml",
    "_schema/Package.schema.json",
    "_schema/App.schema.json",
    "_schema/Dependency.schema.json",
    "_schema/TestSuite.schema.json",
    "_schema/Repository.schema.json",
    "_schema/AgentPlugin.schema.json",
    "_schema/File.schema.json",
    "_sections/Package.yaml",
    "_sections/App.yaml",
    "_sections/Dependency.yaml",
    "_sections/TestSuite.yaml",
    "_sections/Repository.yaml",
    "_sections/AgentPlugin.yaml",
    "_sections/File.yaml",
}


def test_init_bundle_dry_run_writes_nothing(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    result = init_bundle(root, today=_TODAY, dry_run=True)
    assert {f.relative_path for f in result.files} == _EXPECTED_FILES
    assert not root.exists()


def test_init_bundle_writes_expected_files(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    result = init_bundle(root, today=_TODAY, dry_run=False)
    assert {f.relative_path for f in result.files} == _EXPECTED_FILES
    for relative in _EXPECTED_FILES:
        assert (root / relative).exists()
    assert result.changed is True
    assert "+ index.md" in result.diff()


def test_init_bundle_creates_missing_root(tmp_path: Path) -> None:
    root = tmp_path / "nested" / "bundle"
    init_bundle(root, today=_TODAY, dry_run=False)
    assert (root / "index.md").exists()


def test_init_bundle_succeeds_into_preexisting_empty_directory(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    result = init_bundle(root, today=_TODAY, dry_run=False)
    assert {f.relative_path for f in result.files} == _EXPECTED_FILES
    for relative in _EXPECTED_FILES:
        assert (root / relative).exists()


def test_init_bundle_refuses_non_empty_directory(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "existing.txt").write_text("hi", encoding="utf-8")
    with pytest.raises(InitError, match="not empty"):
        init_bundle(root, today=_TODAY, dry_run=False)
    assert [p.name for p in root.iterdir()] == ["existing.txt"]


def test_init_bundle_refuses_root_that_is_a_file(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    root.write_text("x", encoding="utf-8")
    with pytest.raises(InitError, match="not a directory"):
        init_bundle(root, today=_TODAY, dry_run=False)
    assert root.read_text(encoding="utf-8") == "x"


def test_init_bundle_index_md_shape(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    init_bundle(root, today=_TODAY, dry_run=False)
    text = (root / "index.md").read_text(encoding="utf-8")
    assert text.startswith("---\nokf_version: 0.2\n---\n")
    assert f"# {root.name}" in text


def test_init_bundle_log_md_has_todays_entry(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    init_bundle(root, today=_TODAY, dry_run=False)
    text = (root / "log.md").read_text(encoding="utf-8")
    assert "## 2026-01-01" in text
    assert "bundle initialized by code-wiki-okf/" in text


def test_init_bundle_seeds_are_byte_identical_to_assets(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    init_bundle(root, today=_TODAY, dry_run=False)
    assets = importlib.resources.files("code_wiki_okf") / "assets"
    for relative in _EXPECTED_FILES - {"index.md", "log.md"}:
        assert (root / relative).read_bytes() == (assets / relative).read_bytes()
