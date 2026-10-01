from __future__ import annotations

import importlib.resources
import os
import sys
from datetime import date
from pathlib import Path

import pytest
from okf_ext.bundle import SCAFFOLD_MEMBERS
from repositories_okf.init import InitError, install_bundle
from repositories_okf.resources import SEED_RELATIVE_PATHS

_TODAY = date(2026, 9, 29)
_ALL = {"index.md", "log.md", "tags.yaml", *SEED_RELATIVE_PATHS}
_IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


def test_this_package_ships_none_of_the_scaffold_members() -> None:
    assert not set(SEED_RELATIVE_PATHS) & set(SCAFFOLD_MEMBERS)


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    result = install_bundle(root, today=_TODAY)
    assert set(result.scaffold.written) | set(result.install.written) == _ALL
    assert result.logged is None and result.vocabulary is None
    assert not root.exists()


def test_install_writes_every_file_merges_tags_and_logs_once(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    result = install_bundle(root, today=_TODAY, dry_run=False)
    assert result.ok and result.changed
    for relative in _ALL:
        assert (root / relative).is_file()
    tags = (root / "tags.yaml").read_text(encoding="utf-8")
    assert "repository" in tags and "upstream" in tags
    log = (root / "log.md").read_text(encoding="utf-8")
    assert log.count("installed by repositories-okf/0.1.3") == 1
    assert "+ schema/ManagedRepository.schema.json" in result.diff()


def test_a_second_install_changes_nothing(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    install_bundle(root, today=_TODAY, dry_run=False)
    before = {relative: (root / relative).read_bytes() for relative in _ALL}
    again = install_bundle(root, today=_TODAY, dry_run=False)
    assert again.ok and not again.changed
    assert again.logged is None
    assert {relative: (root / relative).read_bytes() for relative in _ALL} == before


def test_seeds_are_byte_identical_to_the_packages_assets(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    install_bundle(root, today=_TODAY, dry_run=False)
    assets = importlib.resources.files("repositories_okf") / "assets"
    for relative in SEED_RELATIVE_PATHS:
        assert (root / relative).read_bytes() == (assets / relative).read_bytes()


def test_a_modified_owned_file_is_refused_by_name(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    (root / "schema").mkdir(parents=True)
    (root / "schema/ReferenceRepository.schema.json").write_text('{"mine": true}\n', encoding="utf-8", newline="")
    result = install_bundle(root, today=_TODAY, dry_run=False)
    assert not result.ok
    assert [f.path for f in result.install.failed] == ["schema/ReferenceRepository.schema.json"]
    assert result.install.failed[0].kind == "foreign-content"


def test_declarations_route_to_a_separate_directory(tmp_path: Path) -> None:
    root, declarations = tmp_path / "bundle", tmp_path / "decl"
    result = install_bundle(root, today=_TODAY, declarations_dir=declarations, dry_run=False)
    assert result.ok
    assert (declarations / "schema/ManagedRepository.schema.json").is_file()
    assert not (root / "schema").exists()


def test_a_root_that_is_a_file_is_caller_error(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    root.write_text("x", encoding="utf-8", newline="")
    with pytest.raises(InitError, match="not a directory"):
        install_bundle(root, today=_TODAY, dry_run=False)
    assert root.read_text(encoding="utf-8") == "x"


@pytest.mark.parametrize("sibling", ["work_tracker_okf", "doc_wiki_okf", "code_wiki_okf"])
def test_coexists_with_every_sibling_installer(tmp_path: Path, sibling: str) -> None:
    module = pytest.importorskip(f"{sibling}.init", reason="sibling package, not a dependency of this one")
    root = tmp_path / "bundle"
    module.install_bundle(root, today=_TODAY, dry_run=False)
    result = install_bundle(root, today=_TODAY, dry_run=False)
    assert result.ok
    assert (root / "schema/_base-repository.schema.json").is_file()


def test_log_md_records_the_scaffold_and_then_the_install(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    result = install_bundle(root, today=_TODAY, dry_run=False)
    assert result.log_failure is None
    text = (root / "log.md").read_text(encoding="utf-8")
    assert "## 2026-09-29" in text
    assert "bundle scaffold created" in text
    assert text.count("installed by repositories-okf/") == 1


def test_an_unparseable_log_md_is_refused_by_name_not_raised(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    corrupt = "---\nfoo: [1, 2\n---\n"
    (root / "log.md").write_text(corrupt, encoding="utf-8", newline="")

    result = install_bundle(root, today=_TODAY, dry_run=False)
    assert not result.ok
    assert [f.path for f in result.scaffold.failed] == ["log.md"]
    assert result.logged is None
    assert result.log_failure is None
    for relative in SEED_RELATIVE_PATHS:
        assert (root / relative).is_file()
    assert (root / "log.md").read_text(encoding="utf-8") == corrupt


def test_a_log_md_that_is_a_directory_is_refused_by_name_not_raised(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    (root / "log.md").mkdir(parents=True)

    result = install_bundle(root, today=_TODAY, dry_run=False)
    assert not result.ok
    assert [f.path for f in result.scaffold.failed] == ["log.md"]
    assert result.logged is None
    assert result.log_failure is None
    assert (root / "log.md").is_dir()


def test_an_out_of_order_log_md_is_reported_as_log_failure_not_raised(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    disordered = "## 2026-01-01\n\n- first\n\n## 2026-01-03\n\n- third\n"
    (root / "log.md").write_text(disordered, encoding="utf-8", newline="")

    result = install_bundle(root, today=date(2026, 1, 2), dry_run=False)
    assert not result.ok
    assert result.scaffold.failed == ()
    assert result.logged is None
    assert result.log_failure is not None
    assert result.log_failure.kind == "foreign-content"
    assert "! log.md:" in result.diff()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
@pytest.mark.skipif(_IS_ROOT, reason="root bypasses permission bits")
def test_a_read_only_log_md_is_reported_as_a_commit_error(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    original = "## 2026-01-01\n\n- first\n"
    log_path = root / "log.md"
    log_path.write_text(original, encoding="utf-8", newline="")
    log_path.chmod(0o444)
    try:
        result = install_bundle(root, today=_TODAY, dry_run=False)
    finally:
        log_path.chmod(0o644)

    assert not result.ok
    assert result.scaffold.failed == ()
    assert result.logged is None
    assert result.log_failure is not None
    assert result.log_failure.kind == "commit-error"
    assert log_path.read_text(encoding="utf-8") == original


def test_a_second_install_merges_nothing_and_reports_unchanged(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    install_bundle(root, today=_TODAY, dry_run=False)
    again = install_bundle(root, today=_TODAY, dry_run=False)
    assert again.vocabulary is not None
    assert again.vocabulary.added == ()
    assert again.vocabulary.unchanged == ("repository", "upstream")


def test_a_hand_deprecated_tag_refuses_that_tag_alone_and_the_file_is_untouched(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    install_bundle(root, today=_TODAY, dry_run=False)
    target = root / "tags.yaml"
    text = target.read_text(encoding="utf-8")
    marker = "  - name: upstream\n"
    assert marker in text
    lines = text.split(marker)[1].split("\n")
    assert lines[0].startswith("    description:")
    target.write_text(
        text.replace(marker + lines[0] + "\n", marker + lines[0] + "\n    deprecated: true\n"),
        encoding="utf-8",
        newline="",
    )
    edited = target.read_bytes()

    result = install_bundle(root, today=_TODAY, dry_run=False)
    assert not result.ok
    assert [f.path for f in result.vocabulary_problems] == ["upstream"]
    assert result.vocabulary_problems[0].kind == "foreign-content"
    assert target.read_bytes() == edited


def test_a_reworded_description_is_reported_as_drift_and_the_install_still_succeeds(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    install_bundle(root, today=_TODAY, dry_run=False)
    target = root / "tags.yaml"
    target.write_text(
        target.read_text(encoding="utf-8").replace(
            "A repository the workspace reads but does not own.", "Somebody else's code."
        ),
        encoding="utf-8",
        newline="",
    )
    result = install_bundle(root, today=_TODAY, dry_run=False)
    assert result.ok
    assert result.vocabulary is not None
    assert [d.name for d in result.vocabulary.drift] == ["upstream"]
    assert "~ tags.yaml: upstream" in result.diff()
