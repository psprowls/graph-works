import shutil
from pathlib import Path

import pytest
from okf_ext.shape import SectionSet, load_sections
from okf_io import Bundle, load_bundle
from work_helpers import CONFORMANT_ROOT, CONFORMANT_TODAY
from work_tracker_okf.init import install_bundle
from work_tracker_okf.resources import assets_root

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "minimal"
PATH_NATIVE_ROOT = Path(__file__).parent / "fixtures" / "path_native"


@pytest.fixture
def minimal_root() -> Path:
    """The fixture vault's root. A `Path`, not a `str` -- `load_bundle` calls
    `root.iterdir()` and a `str` raises `AttributeError`."""
    return FIXTURE_ROOT


@pytest.fixture
def minimal_bundle() -> Bundle:
    """The fixture vault loaded with **no** `ignore=`, so `test_items` sees the
    `references/` pages too and can prove they are not items on their own
    terms rather than because they were filtered out first."""
    return load_bundle(FIXTURE_ROOT)


@pytest.fixture
def path_native_root(tmp_path: Path) -> Path:
    """A writable copy of the representative nested release tree."""
    root = tmp_path / "path-native"
    shutil.copytree(PATH_NATIVE_ROOT, root)
    return root


@pytest.fixture
def path_native_bundle() -> Bundle:
    """The path-native vault without ignore rules so layout tests see every member."""
    return load_bundle(PATH_NATIVE_ROOT)


@pytest.fixture
def section_set() -> SectionSet:
    """This package's own shipped declarations, read straight from package data.

    Not from an installed vault: `test_filing` wants the declaration without
    wanting a bundle, which is the split `resources.py` exists for."""
    return load_sections(Path(str(assets_root() / "sections")))


@pytest.fixture
def conformant_root(tmp_path: Path) -> Path:
    """A writable copy of the conformant vault with its declarations materialized.

    C2-I: the vault carries no `schema/` or `sections/` of its own. Installing
    them here removes any drift between the fixture and the package assets, and
    exercises the installer against a non-empty bundle as a side effect."""
    root = tmp_path / "conformant"
    shutil.copytree(CONFORMANT_ROOT, root)
    result = install_bundle(root, today=CONFORMANT_TODAY, dry_run=False)
    assert result.ok, result.diff()
    return root
