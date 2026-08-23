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
        "repositories/repo/packages/still-here.md",
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
        "repositories/repo/packages/gone.md",
        "pkg:acme/repo/gone",
        purpose_text="A human wrote this and it must remain linkable.",
    )
    before = page.read_bytes()

    result = prune_entities(load_bundle(tmp_path), frozenset())

    assert result.deleted == ()
    assert result.declined == (("repositories/repo/packages/gone", "prose-edited"),)
    assert page.read_bytes() == before


def test_hand_edited_optional_prose_is_also_retained(tmp_path: Path) -> None:
    install_bundle(tmp_path, today=_TODAY, dry_run=False)
    page = _write_package_page(
        tmp_path,
        "repositories/repo/packages/gone.md",
        "pkg:acme/repo/gone",
        public_api_text="Use `gone.public_api()` for the supported surface.",
    )

    result = prune_entities(load_bundle(tmp_path), frozenset())

    assert result.deleted == ()
    assert result.declined == (("repositories/repo/packages/gone", "prose-edited"),)
    assert page.exists()


def test_unstamped_code_wiki_page_is_retained_as_human_authored(tmp_path: Path) -> None:
    install_bundle(tmp_path, today=_TODAY, dry_run=False)
    page = _write_package_page(
        tmp_path,
        "repositories/repo/packages/gone.md",
        "pkg:acme/repo/gone",
        generated=False,
    )

    result = prune_entities(load_bundle(tmp_path), frozenset())

    assert result.deleted == ()
    assert result.declined == (("repositories/repo/packages/gone", "not-generated"),)
    assert page.exists()


def test_unrelated_human_page_under_generated_directory_is_outside_policy_ownership(tmp_path: Path) -> None:
    install_bundle(tmp_path, today=_TODAY, dry_run=False)
    page = tmp_path / "repositories/repo/packages/notes.md"
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
        "Dependency": "dependency:pypi/httpx",
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
    page = tmp_path / "dependencies/pypi/handbook.md"
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
