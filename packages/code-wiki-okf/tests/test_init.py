import importlib.resources
import os
import sys
from datetime import date
from pathlib import Path

import pytest
from code_wiki_okf.init import SEED_RELATIVE_PATHS, InitError, install_bundle, plan_install
from okf_ext.bundle import SCAFFOLD_MEMBERS

_TODAY = date(2026, 1, 1)
_IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0

_SCAFFOLD_FILES = {"index.md", "log.md", "tags.yaml"}
_INSTALL_FILES = set(SEED_RELATIVE_PATHS)
_ALL_FILES = _SCAFFOLD_FILES | _INSTALL_FILES


def test_install_bundle_dry_run_writes_nothing(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    result = install_bundle(root, today=_TODAY, dry_run=True)
    assert set(result.scaffold.written) | set(result.install.written) == _ALL_FILES
    assert result.logged is None
    assert not root.exists()


def test_install_bundle_writes_every_file(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    result = install_bundle(root, today=_TODAY, dry_run=False)
    assert result.ok
    assert set(result.scaffold.written) | set(result.install.written) == _ALL_FILES
    for relative in _ALL_FILES:
        assert (root / relative).is_file()
    assert result.changed is True
    assert "+ index.md" in result.diff()


def test_install_bundle_creates_missing_root(tmp_path: Path) -> None:
    root = tmp_path / "nested" / "bundle"
    install_bundle(root, today=_TODAY, dry_run=False)
    assert (root / "index.md").is_file()


def test_a_second_install_writes_nothing_and_refuses_nothing(tmp_path: Path) -> None:
    """The done-when, in miniature: re-running against a bundle that already
    has everything is a no-op, not a refusal."""
    root = tmp_path / "bundle"
    install_bundle(root, today=_TODAY, dry_run=False)
    before = {relative: (root / relative).read_bytes() for relative in _ALL_FILES}

    again = install_bundle(root, today=_TODAY, dry_run=False)
    assert again.ok
    assert again.scaffold.written == ()
    assert again.install.written == ()
    assert again.logged is None
    assert again.changed is False
    assert {relative: (root / relative).read_bytes() for relative in _ALL_FILES} == before


def test_install_bundle_succeeds_into_a_directory_that_is_not_empty(tmp_path: Path) -> None:
    """The narrowed contract. "Not empty" used to be a refusal; the whole
    point of this item is that it is not one any more."""
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "someone-elses.md").write_text("hi", encoding="utf-8")

    result = install_bundle(root, today=_TODAY, dry_run=False)
    assert result.ok
    assert (root / "index.md").is_file()
    assert (root / "someone-elses.md").read_text(encoding="utf-8") == "hi"


def test_a_modified_seed_is_refused_by_name_and_its_neighbours_still_land(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    (root / "schema").mkdir(parents=True)
    (root / "schema/File.schema.json").write_text('{"mine": true}\n', encoding="utf-8")

    result = install_bundle(root, today=_TODAY, dry_run=False)
    assert not result.ok
    assert [f.path for f in result.install.failed] == ["schema/File.schema.json"]
    assert result.install.failed[0].kind == "foreign-content"
    assert (root / "schema/File.schema.json").read_text(encoding="utf-8") == '{"mine": true}\n'
    assert (root / "schema/Package.schema.json").is_file()


def test_install_bundle_never_writes_a_repositories_yaml(tmp_path: Path) -> None:
    """`workspace.yaml` is `load_config`'s only source. A bundle's own
    `_repositories.yaml`, if one exists, is not one of this package's
    members: neither written, compared, nor refused."""
    root = tmp_path / "bundle"
    result = install_bundle(root, today=_TODAY, dry_run=False)
    assert result.ok
    assert not (root / "_repositories.yaml").exists()


def test_install_bundle_refuses_a_root_that_is_a_file(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    root.write_text("x", encoding="utf-8")
    with pytest.raises(InitError, match="not a directory"):
        install_bundle(root, today=_TODAY, dry_run=False)
    assert root.read_text(encoding="utf-8") == "x"


def test_index_md_shape(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    install_bundle(root, today=_TODAY, dry_run=False)
    text = (root / "index.md").read_text(encoding="utf-8")
    assert text.startswith("---\nokf_version: 0.2\n---\n")
    assert f"# {root.name}" in text


def test_log_md_records_the_scaffold_and_then_the_install(tmp_path: Path) -> None:
    """Two acts, two lines. In a bundle three packages share, the log reads as
    a record of each arrival."""
    root = tmp_path / "bundle"
    result = install_bundle(root, today=_TODAY, dry_run=False)
    assert result.log_failure is None
    text = (root / "log.md").read_text(encoding="utf-8")
    assert "## 2026-01-01" in text
    assert "bundle scaffold created" in text
    assert "installed by code-wiki-okf/" in text


def test_a_normal_install_appends_exactly_one_installed_by_line(tmp_path: Path) -> None:
    """The positive case behind the refusal tests below: nothing about
    guarding a broken `log.md` should cost the happy path its log line."""
    root = tmp_path / "bundle"
    result = install_bundle(root, today=_TODAY, dry_run=False)
    assert result.ok
    assert result.logged is not None
    assert result.log_failure is None
    text = (root / "log.md").read_text(encoding="utf-8")
    assert text.count("installed by code-wiki-okf/") == 1


def test_an_unparseable_log_md_is_refused_by_name_not_raised(tmp_path: Path) -> None:
    """A `log.md` that fails to parse is content, not caller error: the
    scaffold refuses it by name, no log line is appended, and the other
    fourteen files still land -- `install_bundle()` never raises for a
    content reason. Reported through `scaffold.failed`, not `log_failure`:
    the scaffold's own validity check is what caught this one, before the
    log append was ever attempted."""
    root = tmp_path / "bundle"
    root.mkdir()
    corrupt = "---\nfoo: [1, 2\n---\n"
    (root / "log.md").write_text(corrupt, encoding="utf-8")

    result = install_bundle(root, today=_TODAY, dry_run=False)
    assert not result.ok
    assert [f.path for f in result.scaffold.failed] == ["log.md"]
    assert result.logged is None
    assert result.log_failure is None
    assert "+ log.md:" not in result.diff()
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
    for relative in SEED_RELATIVE_PATHS:
        assert (root / relative).is_file()
    assert (root / "log.md").is_dir()


def test_an_out_of_order_log_md_is_reported_as_log_failure_not_raised(tmp_path: Path) -> None:
    """`log.md` can parse cleanly and still refuse the append itself: two
    dated sections that are not newest-first are a structural problem
    `append_log_entry` only discovers by attempting the write, not something
    the scaffold's validity check (parses? yes) would ever catch. Reported
    through `log_failure`, not `scaffold.failed` -- the distinction the two
    tests above exercise from the other side."""
    root = tmp_path / "bundle"
    root.mkdir()
    disordered = "## 2026-01-01\n\n- first\n\n## 2026-01-03\n\n- third\n"
    (root / "log.md").write_text(disordered, encoding="utf-8")

    result = install_bundle(root, today=date(2026, 1, 2), dry_run=False)
    assert not result.ok
    assert result.scaffold.failed == ()
    assert result.logged is None
    assert result.log_failure is not None
    assert result.log_failure.path == "log.md"
    # Content, not `commit-error`: re-running against the same disordered log
    # would fail identically, so this must not read as retry-worthy.
    assert result.log_failure.kind == "foreign-content"
    assert "! log.md:" in result.diff()
    for relative in SEED_RELATIVE_PATHS:
        assert (root / relative).is_file()
    assert (root / "log.md").read_text(encoding="utf-8") == disordered


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
@pytest.mark.skipif(_IS_ROOT, reason="root bypasses permission bits")
def test_a_read_only_log_md_is_reported_as_log_failure_not_raised(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    original = "## 2026-01-01\n\n- first\n"
    log_path = root / "log.md"
    log_path.write_text(original, encoding="utf-8")
    log_path.chmod(0o444)
    try:
        result = install_bundle(root, today=_TODAY, dry_run=False)
    finally:
        log_path.chmod(0o644)

    assert not result.ok
    assert result.scaffold.failed == ()
    assert result.logged is None
    assert result.log_failure is not None
    assert result.log_failure.path == "log.md"
    # An I/O failure *is* retry-worthy: fix the mode, re-run, it works.
    assert result.log_failure.kind == "commit-error"
    for relative in SEED_RELATIVE_PATHS:
        assert (root / relative).is_file()
    assert log_path.read_text(encoding="utf-8") == original


def test_seeds_are_byte_identical_to_the_packages_assets(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    install_bundle(root, today=_TODAY, dry_run=False)
    assets = importlib.resources.files("code_wiki_okf") / "assets"
    for relative in SEED_RELATIVE_PATHS:
        assert (root / relative).read_bytes() == (assets / relative).read_bytes()


def test_the_package_no_longer_seeds_a_tags_vocabulary() -> None:
    assert "tags.yaml" not in SEED_RELATIVE_PATHS


def test_this_package_ships_none_of_the_scaffold_members() -> None:
    """Tier 2 refuses a member it owns, and `write_all`'s create probe is
    all-or-nothing, so an overlap here would not refuse one file -- it would
    take every sibling write down with it. Asserted from this side because
    okf-ext must not import a tier-3 package to check it from the other."""
    assert not set(SEED_RELATIVE_PATHS) & set(SCAFFOLD_MEMBERS)


def test_declarations_dir_relocates_the_declarations(tmp_path: Path) -> None:
    """`declarations_dir` still routes `schema/`/`sections/`/`tags.yaml`
    elsewhere -- `plan_scaffold` (tier 2) still takes the parameter, so
    `install_bundle` keeps threading it through. It is no longer stamped
    anywhere: there is no config file left for `init` to write it into, so a
    caller who wants every later command to agree passes `--config-path` at
    `workspace.yaml`'s own address instead."""
    root = tmp_path / "bundle"
    elsewhere = tmp_path / "declarations"
    elsewhere.mkdir()
    install_bundle(root, today=_TODAY, declarations_dir=elsewhere, dry_run=False)

    assert (elsewhere / "schema/File.schema.json").is_file()
    assert (elsewhere / "tags.yaml").is_file()
    assert not (root / "schema").exists()


def test_plan_install_writes_nothing(tmp_path: Path) -> None:
    """Tier 2 returns plans and takes no `dry_run`; this wrapper inherits that."""
    root = tmp_path / "bundle"
    plan = plan_install(root)
    assert [planned.member for planned in plan.writes] == list(SEED_RELATIVE_PATHS)
    assert not root.exists()
