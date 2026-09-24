from datetime import date
from pathlib import Path

from code_wiki_okf.entities.delete import prune_entities
from code_wiki_okf.init import install_bundle
from okf_io import load_bundle

_TODAY = date(2026, 1, 1)
_PURPOSE_PLACEHOLDER = "> TODO: what this package does, who uses it, and why it exists, in one paragraph."
_PUBLIC_API_PLACEHOLDER = (
    "> TODO: the main exports and when to use them. Link code with backticked `path:line` references."
)


def _write_package_page(
    root: Path,
    member: str,
    resource: str,
    *,
    purpose_text: str = _PURPOSE_PLACEHOLDER,
    public_api_text: str = _PUBLIC_API_PLACEHOLDER,
    generated: bool = True,
) -> Path:
    path = root / member
    path.parent.mkdir(parents=True, exist_ok=True)
    provenance = "generated:\n  by: code-wiki-okf/0.4.0\n  at: '2026-01-01T00:00:00+00:00'\n" if generated else ""
    path.write_text(
        f'---\ntype: Package\ntitle: gone\nresource: "{resource}"\n{provenance}---\n\n'
        f"## Purpose\n\n{purpose_text}\n\n"
        f"## Public API\n\n{public_api_text}\n\n"
        "## Files\n\n_(none)_\n",
        encoding="utf-8",
    )
    return path


_WHY_PLACEHOLDER = "> TODO: why the workspace depends on this, and what it is used for."


def _write_dependency_page(
    root: Path,
    member: str,
    resource: str,
    *,
    implemented_by: str = "[]",
    why_text: str = _WHY_PLACEHOLDER,
    generated: bool = True,
) -> Path:
    path = root / member
    path.parent.mkdir(parents=True, exist_ok=True)
    provenance = "generated:\n  by: code-wiki-okf/0.4.0\n  at: '2026-01-01T00:00:00+00:00'\n" if generated else ""
    path.write_text(
        f'---\ntype: Dependency\ntitle: gone\nresource: "{resource}"\n'
        f"implemented_by: {implemented_by}\n{provenance}---\n\n"
        f"## Why we depend on this\n\n{why_text}\n\n"
        "## Gotchas / workarounds\n\n> TODO: known issues, version pins, or workarounds this dependency needs.\n",
        encoding="utf-8",
    )
    return path


def test_dependency_with_implemented_by_is_guarded_like_any_other_page(tmp_path: Path) -> None:
    """A workspace-implemented Dependency gets no *page* (ADR-0048 suppresses
    the write in `entities/sync.py`), but a stale page left over from before
    that rule is still ordinary prune input: authored prose declines it.

    This is the regression for the removed lane-residue bypass. ADR-0034's
    prose guard has no per-type exceptions, and the one-time sweep that
    cleared the live bundle's residue is history, not a standing rule."""
    install_bundle(tmp_path, today=_TODAY, dry_run=False)
    page = _write_dependency_page(
        tmp_path,
        "code-graph/repo/entities/dependencies/pypi/gone.md",
        "dependency:acme/repo/pypi/gone",
        implemented_by="[pkg:acme/repo/gone]",
        why_text="A human wrote a detailed justification here.",
    )

    result = prune_entities(load_bundle(tmp_path), frozenset())

    assert result.deleted == ()
    assert result.declined == (("code-graph/repo/entities/dependencies/pypi/gone", "prose-edited"),)
    assert page.exists()


def test_unedited_dependency_with_implemented_by_is_deleted_like_any_other_page(tmp_path: Path) -> None:
    """The other half: `implemented_by` is not a shield either. A stale,
    generated, placeholder-prose Dependency page prunes on the ordinary path,
    with no special-casing in either direction."""
    install_bundle(tmp_path, today=_TODAY, dry_run=False)
    page = _write_dependency_page(
        tmp_path,
        "code-graph/repo/entities/dependencies/pypi/gone.md",
        "dependency:acme/repo/pypi/gone",
        implemented_by="[pkg:acme/repo/gone]",
    )

    result = prune_entities(load_bundle(tmp_path), frozenset())

    assert result.deleted == ("code-graph/repo/entities/dependencies/pypi/gone",)
    assert result.declined == ()
    assert not page.exists()


def test_dependency_without_code_wiki_provenance_is_declined(tmp_path: Path) -> None:
    """A Dependency page with no code-wiki `generated.by` stamp is a human's
    page, not ours, and stays whatever its prose says."""
    install_bundle(tmp_path, today=_TODAY, dry_run=False)
    page = _write_dependency_page(
        tmp_path,
        "code-graph/repo/entities/dependencies/pypi/gone.md",
        "dependency:acme/repo/pypi/gone",
        implemented_by="[pkg:acme/repo/gone]",
        generated=False,
    )

    result = prune_entities(load_bundle(tmp_path), frozenset())

    assert result.deleted == ()
    assert result.declined == (("code-graph/repo/entities/dependencies/pypi/gone", "not-generated"),)
    assert page.exists()


def test_dependency_without_implemented_by_still_needs_the_prose_guard(tmp_path: Path) -> None:
    """A third-party Dependency page — no `implemented_by` — declines on
    edited prose, identically to the implemented case above."""
    install_bundle(tmp_path, today=_TODAY, dry_run=False)
    page = _write_dependency_page(
        tmp_path,
        "code-graph/repo/entities/dependencies/pypi/external.md",
        "dependency:acme/repo/pypi/external",
        implemented_by="[]",
        why_text="A human wrote a detailed justification here.",
    )

    result = prune_entities(load_bundle(tmp_path), frozenset())

    assert result.deleted == ()
    assert result.declined == (("code-graph/repo/entities/dependencies/pypi/external", "prose-edited"),)
    assert page.exists()


def test_generated_stale_page_is_deleted_by_type_even_when_misplaced(tmp_path: Path) -> None:
    install_bundle(tmp_path, today=_TODAY, dry_run=False)
    misplaced = _write_package_page(tmp_path, "misc/misplaced.md", "pkg:acme/repo/gone")

    result = prune_entities(load_bundle(tmp_path), frozenset())

    assert result.deleted == ("misc/misplaced",)
    assert result.declined == ()
    assert not misplaced.exists()


def test_live_resource_is_never_a_prune_candidate(tmp_path: Path) -> None:
    install_bundle(tmp_path, today=_TODAY, dry_run=False)
    page = _write_package_page(
        tmp_path,
        "code-graph/repo/entities/packages/still-here.md",
        "pkg:acme/repo/still-here",
    )

    result = prune_entities(load_bundle(tmp_path), frozenset({"pkg:acme/repo/still-here"}))

    assert result.deleted == ()
    assert result.declined == ()
    assert page.exists()


def test_hand_edited_required_prose_is_retained_and_reported(tmp_path: Path) -> None:
    install_bundle(tmp_path, today=_TODAY, dry_run=False)
    page = _write_package_page(
        tmp_path,
        "code-graph/repo/entities/packages/gone.md",
        "pkg:acme/repo/gone",
        purpose_text="A human wrote this and it must remain linkable.",
    )
    before = page.read_bytes()

    result = prune_entities(load_bundle(tmp_path), frozenset())

    assert result.deleted == ()
    assert result.declined == (("code-graph/repo/entities/packages/gone", "prose-edited"),)
    assert page.read_bytes() == before


def test_hand_edited_optional_prose_is_also_retained(tmp_path: Path) -> None:
    install_bundle(tmp_path, today=_TODAY, dry_run=False)
    page = _write_package_page(
        tmp_path,
        "code-graph/repo/entities/packages/gone.md",
        "pkg:acme/repo/gone",
        public_api_text="Use `gone.public_api()` for the supported surface.",
    )

    result = prune_entities(load_bundle(tmp_path), frozenset())

    assert result.deleted == ()
    assert result.declined == (("code-graph/repo/entities/packages/gone", "prose-edited"),)
    assert page.exists()


def test_unstamped_code_wiki_page_is_retained_as_human_authored(tmp_path: Path) -> None:
    install_bundle(tmp_path, today=_TODAY, dry_run=False)
    page = _write_package_page(
        tmp_path,
        "code-graph/repo/entities/packages/gone.md",
        "pkg:acme/repo/gone",
        generated=False,
    )

    result = prune_entities(load_bundle(tmp_path), frozenset())

    assert result.deleted == ()
    assert result.declined == (("code-graph/repo/entities/packages/gone", "not-generated"),)
    assert page.exists()


def test_unrelated_human_page_under_generated_directory_is_outside_policy_ownership(tmp_path: Path) -> None:
    install_bundle(tmp_path, today=_TODAY, dry_run=False)
    page = tmp_path / "code-graph/repo/entities/packages/notes.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    before = "---\ntype: Note\ntitle: notes\nresource: note:repo/packages\n---\n\nHuman notes.\n"
    page.write_text(before, encoding="utf-8")

    result = prune_entities(load_bundle(tmp_path), frozenset())

    assert result.deleted == ()
    assert result.declined == ()
    assert page.read_text(encoding="utf-8") == before


def test_all_seven_policy_types_are_owned_independently_of_depth(tmp_path: Path) -> None:
    install_bundle(tmp_path, today=_TODAY, dry_run=False)
    resources = {
        "Repository": "repo:acme/repo",
        "Package": "pkg:acme/repo/lib",
        "App": "app:acme/repo/web",
        "AgentPlugin": "agent_plugin:acme/repo/reviewer",
        "TestSuite": "test_suite:acme/repo/unit",
        "File": "file:acme/repo/src/main.py",
        "Dependency": "dependency:acme/repo/pypi/httpx",
    }
    for index, (type_name, resource) in enumerate(resources.items()):
        path = tmp_path / f"arbitrary/depth/{index}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"---\ntype: {type_name}\ntitle: stale\nresource: {resource}\n"
            "generated:\n  by: code-wiki-okf/0.4.0\n  at: '2026-01-01T00:00:00+00:00'\n---\n",
            encoding="utf-8",
        )

    result = prune_entities(load_bundle(tmp_path), frozenset())

    assert result.deleted == tuple(f"arbitrary/depth/{index}" for index in range(7))
    assert result.declined == ()


def test_unknown_type_with_a_resource_is_not_owned_or_reported(tmp_path: Path) -> None:
    install_bundle(tmp_path, today=_TODAY, dry_run=False)
    page = tmp_path / "code-graph/repo/entities/dependencies/pypi/handbook.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        "---\ntype: Handbook\ntitle: handbook\nresource: handbook:dependencies\n"
        "generated:\n  by: code-wiki-okf/0.4.0\n  at: '2026-01-01T00:00:00+00:00'\n---\n",
        encoding="utf-8",
    )

    result = prune_entities(load_bundle(tmp_path), frozenset())

    assert result.deleted == ()
    assert result.declined == ()
    assert page.exists()
