"""The asset list is complete in both directions."""

from pathlib import Path

from doc_wiki_okf.resources import SEED_RELATIVE_PATHS, assets_root, seed_files

_SCAFFOLD_MEMBERS = ("index.md", "log.md", "_tags.yaml")


def test_twelve_paths_schemas_before_sections() -> None:
    assert len(SEED_RELATIVE_PATHS) == 12
    schema_last = max(i for i, p in enumerate(SEED_RELATIVE_PATHS) if p.startswith("_schema/"))
    sections_first = min(i for i, p in enumerate(SEED_RELATIVE_PATHS) if p.startswith("_sections/"))
    assert schema_last < sections_first


def test_the_fragment_file_is_a_member() -> None:
    """An installed `_sections/` without it cannot resolve a `placeholder_ref`."""
    assert "_sections/_fragments.doc_wiki.yaml" in SEED_RELATIVE_PATHS


def test_no_scaffold_member_is_claimed() -> None:
    """Those three are `okf_ext.bundle`'s scaffold to write."""
    for member in _SCAFFOLD_MEMBERS:
        assert member not in SEED_RELATIVE_PATHS


def test_seed_files_reads_twelve_non_empty_files() -> None:
    files = seed_files()
    assert set(files) == set(SEED_RELATIVE_PATHS)
    assert all(text.strip() for text in files.values())


def test_every_listed_path_exists_on_disk() -> None:
    root = assets_root()
    for relative in SEED_RELATIVE_PATHS:
        assert (root / relative).is_file(), relative


def test_every_file_on_disk_is_listed() -> None:
    """The direction that catches an asset added without an installer entry."""
    root = Path(str(assets_root()))
    found = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
    assert found == sorted(SEED_RELATIVE_PATHS)


def test_the_source_declaration_is_a_member() -> None:
    """S-K: `Source` is declared outside `RUBRIC`, so nothing in `test_rubric.py`
    guarantees its two files are installed. This is what does."""
    assert "_schema/Source.schema.json" in SEED_RELATIVE_PATHS
    assert "_sections/Source.yaml" in SEED_RELATIVE_PATHS
