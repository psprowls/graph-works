from datetime import date
from pathlib import Path

from code_wiki_okf.entities.delete import prune_lane
from code_wiki_okf.init import install_bundle
from okf_ext.shape import load_sections
from okf_io import load_bundle

_TODAY = date(2026, 1, 1)

_PURPOSE_PLACEHOLDER = "> TODO: what this package does, who uses it, and why it exists, in one paragraph."
_PUBLIC_API_PLACEHOLDER = (
    "> TODO: the main exports and when to use them. Link code with backticked `path:line` references."
)


def _write_package_page(
    root: Path,
    name: str,
    resource: str,
    *,
    purpose_text: str = _PURPOSE_PLACEHOLDER,
    public_api_text: str = _PUBLIC_API_PLACEHOLDER,
    files_text: str = "_(none)_",
) -> None:
    path = root / "packages" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'---\ntype: Package\ntitle: "{name}"\nresource: "{resource}"\n---\n\n'
        f"## Purpose\n\n{purpose_text}\n\n"
        f"## Public API\n\n{public_api_text}\n\n"
        f"## Files\n\n{files_text}\n",
        encoding="utf-8",
    )


def test_untouched_prose_page_is_deleted(tmp_path: Path) -> None:
    install_bundle(tmp_path, today=_TODAY, dry_run=False)
    _write_package_page(tmp_path, "gone", "pkg:acme/repo/gone")
    section_set = load_sections(tmp_path / "sections")
    bundle = load_bundle(tmp_path)

    result = prune_lane(bundle, section_set, directory="packages/", should_exist={"pkg:acme/repo/still-here"})

    assert result.deleted == ("packages/gone",)
    assert result.declined == ()
    assert not (tmp_path / "packages" / "gone.md").exists()


def test_hand_edited_required_prose_section_is_declined(tmp_path: Path) -> None:
    install_bundle(tmp_path, today=_TODAY, dry_run=False)
    _write_package_page(
        tmp_path,
        "gone",
        "pkg:acme/repo/gone",
        purpose_text="A human wrote this by hand and it must never be deleted automatically.",
    )
    section_set = load_sections(tmp_path / "sections")
    bundle = load_bundle(tmp_path)

    result = prune_lane(bundle, section_set, directory="packages/", should_exist=set())

    assert result.deleted == ()
    assert result.declined == (("packages/gone", "prose-edited"),)
    assert (tmp_path / "packages" / "gone.md").exists()
    # File must be byte-identical to what was written -- declining must not
    # touch the page at all.
    assert "A human wrote this by hand" in (tmp_path / "packages" / "gone.md").read_text(encoding="utf-8")


def test_hand_edited_optional_prose_section_is_also_declined(tmp_path: Path) -> None:
    """`## Public API` is declared but not `required` in Package.yaml. The
    prose guard must protect it too -- deletion is not limited to required
    sections, unlike `sections.unfilled`."""
    install_bundle(tmp_path, today=_TODAY, dry_run=False)
    _write_package_page(
        tmp_path,
        "gone",
        "pkg:acme/repo/gone",
        public_api_text="`okf_io.load_bundle(root)` is the one entry point most callers need.",
    )
    section_set = load_sections(tmp_path / "sections")
    bundle = load_bundle(tmp_path)

    result = prune_lane(bundle, section_set, directory="packages/", should_exist=set())

    assert result.deleted == ()
    assert result.declined == (("packages/gone", "prose-edited"),)
    assert (tmp_path / "packages" / "gone.md").exists()


def test_generated_section_edits_never_block_deletion(tmp_path: Path) -> None:
    """`## Files` is declared `ownership: generated` in Package.yaml. Content
    that differs from its seeded placeholder there must never be consulted by
    the prose-untouched check -- only `prose`-ownership sections count."""
    install_bundle(tmp_path, today=_TODAY, dry_run=False)
    _write_package_page(
        tmp_path,
        "gone",
        "pkg:acme/repo/gone",
        files_text="- [some/real/file.py](/repositories/acme-repo/some/real/file.py.md)\n",
    )
    section_set = load_sections(tmp_path / "sections")
    bundle = load_bundle(tmp_path)

    result = prune_lane(bundle, section_set, directory="packages/", should_exist=set())

    assert result.deleted == ("packages/gone",)
    assert result.declined == ()
    assert not (tmp_path / "packages" / "gone.md").exists()


def test_page_still_in_should_exist_is_never_a_candidate(tmp_path: Path) -> None:
    install_bundle(tmp_path, today=_TODAY, dry_run=False)
    _write_package_page(tmp_path, "still-here", "pkg:acme/repo/still-here")
    section_set = load_sections(tmp_path / "sections")
    bundle = load_bundle(tmp_path)

    result = prune_lane(bundle, section_set, directory="packages/", should_exist={"pkg:acme/repo/still-here"})

    assert result.deleted == ()
    assert result.declined == ()
    assert (tmp_path / "packages" / "still-here.md").exists()


def test_pages_outside_directory_are_ignored(tmp_path: Path) -> None:
    """A resource-having page in a different lane's directory must never be
    treated as a deletion candidate for this lane, even if its resource is
    absent from `should_exist`."""
    install_bundle(tmp_path, today=_TODAY, dry_run=False)
    # A Package page whose resource is gone, but pruning is scoped to
    # "dependencies/" here -- it must be left alone.
    _write_package_page(tmp_path, "gone", "pkg:acme/repo/gone")
    section_set = load_sections(tmp_path / "sections")
    bundle = load_bundle(tmp_path)

    result = prune_lane(bundle, section_set, directory="dependencies/", should_exist=set())

    assert result.deleted == ()
    assert result.declined == ()
    assert (tmp_path / "packages" / "gone.md").exists()


def test_undeclared_type_is_declined_not_deleted(tmp_path: Path) -> None:
    """A candidate page whose `type:` has no matching declaration in the
    passed-in `section_set` -- blank, misspelled, or simply not one of the
    types loaded from `sections/` -- must decline rather than delete: with
    no declaration there is nothing to check prose against, and that is a
    reason for caution, not a reason to skip the check entirely."""
    install_bundle(tmp_path, today=_TODAY, dry_run=False)
    path = tmp_path / "packages" / "gone.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '---\ntype: NotARealType\ntitle: "gone"\nresource: "pkg:acme/repo/gone"\n---\n\n## Purpose\n\nirrelevant\n',
        encoding="utf-8",
    )
    section_set = load_sections(tmp_path / "sections")
    bundle = load_bundle(tmp_path)

    result = prune_lane(bundle, section_set, directory="packages/", should_exist=set())

    assert result.deleted == ()
    assert result.declined == (("packages/gone", "no-declaration-for-type"),)
    assert (tmp_path / "packages" / "gone.md").exists()


def test_no_resource_candidates_yields_empty_result(tmp_path: Path) -> None:
    install_bundle(tmp_path, today=_TODAY, dry_run=False)
    section_set = load_sections(tmp_path / "sections")
    bundle = load_bundle(tmp_path)

    result = prune_lane(bundle, section_set, directory="packages/", should_exist=set())

    assert result.deleted == ()
    assert result.declined == ()


def _write_repository_page(root: Path, name: str, resource: str) -> None:
    path = root / "repositories" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'---\ntype: Repository\ntitle: "{name}"\nresource: "{resource}"\n---\n\n'
        "## Overview\n\n> TODO: what this repository is and what it contains, in one paragraph.\n\n"
        "## Layout\n\n> TODO: the top-level directory layout, and what lives where.\n",
        encoding="utf-8",
    )


def _write_mirror_file_page(root: Path, repo: str, rel_path: str, resource: str) -> None:
    path = root / "repositories" / repo / "fs" / f"{rel_path}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'---\ntype: File\ntitle: "{rel_path}"\nresource: "{resource}"\n---\n\n'
        "## Notes\n\n"
        "> TODO: anything a reader should know about this file that the generated sections below don't capture.\n"
        "\n## Symbols\n\n_(populated by `code-wiki-okf sync` — not yet generated)_\n"
        "\n## Imports\n\n_(populated by `code-wiki-okf sync` — not yet generated)_\n"
        "\n## Exports\n\n_(populated by `code-wiki-okf sync` — not yet generated)_\n"
        "\n## Imported By\n\n_(populated by `code-wiki-okf sync` — not yet generated)_\n",
        encoding="utf-8",
    )


def test_exact_depth_leaves_mirror_file_pages_alone(tmp_path: Path) -> None:
    """A plain `"repositories/"` prefix also matches a repo's own mirror
    subtree (`repositories/<name>/<rel_path>`, child 3's business). Without
    `exact_depth=True`, this candidate would be swept up alongside the
    Repository entity page it shares a prefix with -- the bug the combined
    `sync` command surfaced (issue: entity-lane pruning deleted freshly
    mirrored File pages on a second `sync` run)."""
    install_bundle(tmp_path, today=_TODAY, dry_run=False)
    _write_repository_page(tmp_path, "acme", "repo:acme/acme")
    _write_mirror_file_page(tmp_path, "acme", "src/mod.py", "file:acme/src/mod.py")
    section_set = load_sections(tmp_path / "sections")
    bundle = load_bundle(tmp_path)

    result = prune_lane(bundle, section_set, directory="repositories/", should_exist=set(), exact_depth=True)

    assert result.deleted == ("repositories/acme",)
    assert result.declined == ()
    assert (tmp_path / "repositories" / "acme" / "fs" / "src" / "mod.py.md").exists()


def test_without_exact_depth_the_mirror_subtree_is_incorrectly_swept(tmp_path: Path) -> None:
    """Documents the bug `exact_depth` fixes: the default (`exact_depth=False`)
    still matches the old, unsafe behavior other lanes rely on (no nested
    subtree of their own), so this is deliberately still exercisable -- but
    callers touching the `repositories/` lane must pass `exact_depth=True`."""
    install_bundle(tmp_path, today=_TODAY, dry_run=False)
    _write_repository_page(tmp_path, "acme", "repo:acme/acme")
    _write_mirror_file_page(tmp_path, "acme", "src/mod.py", "file:acme/src/mod.py")
    section_set = load_sections(tmp_path / "sections")
    bundle = load_bundle(tmp_path)

    result = prune_lane(bundle, section_set, directory="repositories/", should_exist=set())

    assert set(result.deleted) == {"repositories/acme", "repositories/acme/fs/src/mod.py"}
