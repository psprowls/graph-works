import importlib.resources
import os
import sys
from datetime import date
from pathlib import Path

import pytest
from doc_wiki_okf.init import BundleInstall, InitError, install_bundle, plan_install
from doc_wiki_okf.resources import SEED_RELATIVE_PATHS, seed_files
from okf_ext.bundle import SCAFFOLD_MEMBERS

_TODAY = date(2026, 1, 1)
_IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0

_SCAFFOLD_FILES = {"index.md", "log.md", "_tags.yaml"}
_INSTALL_FILES = set(SEED_RELATIVE_PATHS)
_ALL_FILES = _SCAFFOLD_FILES | _INSTALL_FILES


def test_the_package_owns_exactly_twelve_files() -> None:
    assert len(SEED_RELATIVE_PATHS) == 12
    assert len(set(SEED_RELATIVE_PATHS)) == 12


def test_this_package_ships_none_of_the_scaffold_members() -> None:
    """Tier 2 refuses a member it owns and `write_all`'s create probe is
    all-or-nothing, so an overlap here would take every sibling write down with
    it. Asserted from this side because okf-ext must not import a tier-3
    package to check it from the other."""
    assert not set(SEED_RELATIVE_PATHS) & set(SCAFFOLD_MEMBERS)


def test_install_bundle_dry_run_writes_nothing(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    result = install_bundle(root, today=_TODAY)
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
    assert "+ _schema/Tutorial.schema.json" in result.diff()


def test_a_second_install_writes_nothing_and_refuses_nothing(tmp_path: Path) -> None:
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
    assert "= _schema/Source.schema.json" in again.diff()


def test_a_modified_seed_is_refused_by_name_and_its_neighbours_still_land(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    (root / "_schema").mkdir(parents=True)
    (root / "_schema/HowTo.schema.json").write_text('{"mine": true}\n', encoding="utf-8")

    result = install_bundle(root, today=_TODAY, dry_run=False)
    assert not result.ok
    assert [f.path for f in result.install.failed] == ["_schema/HowTo.schema.json"]
    assert result.install.failed[0].kind == "foreign-content"
    assert (root / "_schema/HowTo.schema.json").read_text(encoding="utf-8") == '{"mine": true}\n'
    for relative in SEED_RELATIVE_PATHS:
        if relative != "_schema/HowTo.schema.json":
            assert (root / relative).is_file()


def test_install_is_additive_over_a_bundle_code_wiki_okf_already_created(tmp_path: Path) -> None:
    """Multiple tier-3 packages are designed to share one bundle. Neither
    package's members are disturbed by the other's install.

    Skipped under `--package doc-wiki-okf`, where the sibling is not in the
    dependency closure -- deliberately, since depending on it would break the
    two-dependency boundary this package is held to."""
    pytest.importorskip("code_wiki_okf", reason="sibling tier-3 package, not a dependency of this one")
    from code_wiki_okf.init import install_bundle as install_code_wiki

    root = tmp_path / "bundle"
    install_code_wiki(root, today=_TODAY, dry_run=False)
    sibling = (root / "_schema/Package.schema.json").read_bytes()

    result = install_bundle(root, today=_TODAY, dry_run=False)
    assert result.ok
    assert result.scaffold.written == ()  # the scaffold was already there
    assert set(result.install.written) == _INSTALL_FILES
    assert (root / "_schema/Package.schema.json").read_bytes() == sibling
    assert (root / "_schema/Tutorial.schema.json").is_file()


def test_work_tracker_okf_and_doc_wiki_okf_coexist(tmp_path: Path) -> None:
    """The collision this module used to trigger is gone: `work_tracker_okf`
    and `doc_wiki_okf` now ship their fragment files under distinct
    bundle-relative paths (`_fragments.work_tracker.yaml` and
    `_fragments.doc_wiki.yaml`), so `plan_install`'s byte-ownership model never
    sees the two packages disagree about one path. Installing both into the
    same root succeeds regardless of order."""
    pytest.importorskip("work_tracker_okf", reason="sibling tier-3 package, not a dependency of this one")
    from work_tracker_okf.init import install_bundle as install_work_tracker

    root = tmp_path / "bundle"
    install_work_tracker(root, today=_TODAY, dry_run=False)

    result = install_bundle(root, today=_TODAY, dry_run=False)
    assert result.ok
    assert (root / "_sections/_fragments.work_tracker.yaml").is_file()
    assert (root / "_sections/_fragments.doc_wiki.yaml").is_file()


def test_install_bundle_succeeds_into_a_directory_that_is_not_empty(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "someone-elses.md").write_text("hi", encoding="utf-8")

    result = install_bundle(root, today=_TODAY, dry_run=False)
    assert result.ok
    assert (root / "someone-elses.md").read_text(encoding="utf-8") == "hi"


def test_install_bundle_refuses_a_root_that_is_a_file(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    root.write_text("x", encoding="utf-8")
    with pytest.raises(InitError, match="not a directory"):
        install_bundle(root, today=_TODAY, dry_run=False)
    assert root.read_text(encoding="utf-8") == "x"


def test_log_md_records_the_scaffold_and_then_the_install(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    result = install_bundle(root, today=_TODAY, dry_run=False)
    assert result.log_failure is None
    text = (root / "log.md").read_text(encoding="utf-8")
    assert "## 2026-01-01" in text
    assert "bundle scaffold created" in text
    assert text.count("installed by doc-wiki-okf/") == 1


def test_an_unparseable_log_md_is_refused_by_name_not_raised(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    corrupt = "---\nfoo: [1, 2\n---\n"
    (root / "log.md").write_text(corrupt, encoding="utf-8")

    result = install_bundle(root, today=_TODAY, dry_run=False)
    assert not result.ok
    assert [f.path for f in result.scaffold.failed] == ["log.md"]
    assert result.logged is None
    assert result.log_failure is None
    for relative in SEED_RELATIVE_PATHS:
        assert (root / relative).is_file()
    assert (root / "log.md").read_text(encoding="utf-8") == corrupt


def test_a_log_md_that_is_a_directory_is_refused_by_name_not_raised(tmp_path: Path) -> None:
    """A directory where `log.md` belongs is skipped by okf-io's walk, so
    `bundle.logs.get("")` is `None` -- the branch that must not become an
    `AttributeError`."""
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
    """`log.md` can parse cleanly and still refuse the append: two dated
    sections that are not newest-first are a structural problem
    `append_log_entry` only discovers by attempting the write. Reported through
    `log_failure`, not `scaffold.failed` -- the scaffold's validity check
    (parses? yes) would never catch it."""
    root = tmp_path / "bundle"
    root.mkdir()
    disordered = "## 2026-01-01\n\n- first\n\n## 2026-01-03\n\n- third\n"
    (root / "log.md").write_text(disordered, encoding="utf-8")

    result = install_bundle(root, today=date(2026, 1, 2), dry_run=False)
    assert not result.ok
    assert result.scaffold.failed == ()
    assert result.logged is None
    assert result.log_failure is not None
    # Content, not `commit-error`: re-running against the same disordered log
    # would fail identically forever -- the file needs a human.
    assert result.log_failure.kind == "foreign-content"
    assert "! log.md:" in result.diff()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
@pytest.mark.skipif(_IS_ROOT, reason="root bypasses permission bits")
def test_a_read_only_log_md_is_reported_as_a_commit_error(tmp_path: Path) -> None:
    """The other side of the `FailureKind` fork. An I/O failure *is*
    retry-worthy: fix the mode, re-run, it works."""
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
    assert result.log_failure.kind == "commit-error"
    assert log_path.read_text(encoding="utf-8") == original


def test_seeds_are_byte_identical_to_the_packages_assets(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    install_bundle(root, today=_TODAY, dry_run=False)
    assets = importlib.resources.files("doc_wiki_okf") / "assets"
    for relative in SEED_RELATIVE_PATHS:
        assert (root / relative).read_bytes() == (assets / relative).read_bytes()


def test_declarations_dir_relocates_the_declarations(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    elsewhere = tmp_path / "declarations"
    elsewhere.mkdir()
    install_bundle(root, today=_TODAY, declarations_dir=elsewhere, dry_run=False)

    assert (elsewhere / "_schema/Tutorial.schema.json").is_file()
    assert (elsewhere / "_sections/_fragments.doc_wiki.yaml").is_file()
    assert not (root / "_schema").exists()


def test_plan_install_writes_nothing(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    plan = plan_install(root)
    assert [planned.member for planned in plan.writes] == list(SEED_RELATIVE_PATHS)
    assert not root.exists()


def test_seed_files_reads_every_owned_path(tmp_path: Path) -> None:
    files = seed_files()
    assert tuple(files) == SEED_RELATIVE_PATHS
    assert all(text for text in files.values())


def test_bundle_install_is_frozen() -> None:
    assert BundleInstall.__dataclass_params__.frozen is True
