"""The mirror lane's one answer about where a page lives."""

from __future__ import annotations

from pathlib import Path

from code_wiki_okf.mirror.paths import (
    MIRROR_SUBDIR,
    mirror_concept_id,
    mirror_page_path,
    mirror_prefix,
)


def test_prefix_roots_at_fs_below_the_repo() -> None:
    assert mirror_prefix("acme") == "repositories/acme/fs"


def test_concept_id_is_the_prefix_plus_the_relative_path() -> None:
    assert mirror_concept_id("acme", "src/a.py") == "repositories/acme/fs/src/a.py"


def test_page_path_appends_md_under_the_bundle_root() -> None:
    assert mirror_page_path(Path("/b"), "acme", "src/a.py") == Path("/b/repositories/acme/fs/src/a.py.md")


def test_the_subdir_is_the_single_source_the_others_read() -> None:
    """The three helpers must not each hard-code `fs` independently."""
    assert MIRROR_SUBDIR in mirror_prefix("acme").split("/")
    assert mirror_prefix("acme") == f"repositories/acme/{MIRROR_SUBDIR}"
