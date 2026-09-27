"""Composite retirement keeps authored history while removing generated residue."""

from pathlib import Path

import pytest
from code_graph_io import open_reader
from code_graph_io.update import run_workspace
from code_wiki_okf.config import Config
from code_wiki_okf.placement import canonical_concept_id, context_from_resource, file_system_directory
from code_wiki_okf.sync import SyncResult, sync_bundle
from markdown_it import MarkdownIt
from okf_io import build_link_graph, load_bundle
from okf_io.links import is_external, resolve_path

from .test_run import _AT, _TODAY, _bundle_bytes, _git, _workspace


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")


def _sync(root: Path, graph: Path, config: Config, *, dry_run: bool = False) -> SyncResult:
    with open_reader(graph_dir=graph) as reader:
        return sync_bundle(root, config=config, reader=reader, at=_AT, today=_TODAY, dry_run=dry_run)


def _member(kind: str, resource: str) -> str:
    return canonical_concept_id(context_from_resource(kind, resource)) + ".md"


def _packages(tmp_path: Path) -> tuple[Path, Path, Config]:
    root, graph, config = _workspace(tmp_path)
    repo = config.repos[0].path
    for name, dependencies in (("retired", '["httpx", "attrs"]'), ("survivor", '["attrs"]')):
        _write(
            repo / "packages" / name / "pyproject.toml",
            f'[project]\nname = "{name}"\nversion = "0.1.0"\ndependencies = {dependencies}\n',
        )
        _write(repo / "packages" / name / "src" / f"{name}.py", f'{name.upper()} = "{name}"\n')
        _write(repo / "packages" / name / "tests" / f"test_{name}.py", f"def test_{name}():\n    assert True\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "add two packages with suites and dependencies")
    run_workspace([repo], graph_dir=graph, full=True)
    assert _sync(root, graph, config).ok
    return root, graph, config


def _author(path: Path, heading: str, prose: str) -> None:
    text = path.read_text(encoding="utf-8")
    marker = f"## {heading}\n\n"
    assert marker in text
    _write(path, text.replace(marker, marker + prose + "\n\n", 1))


def _catalog_targets(root: Path, *, excluded: tuple[str, ...] = ()) -> set[str]:
    targets: set[str] = set()
    for directory, document in load_bundle(root).indexes.items():
        if f"{directory}/index.md" in excluded:
            continue
        source = f"{directory}/index" if directory else "index"
        for token in MarkdownIt().parse(document.body):
            for child in token.children or ():
                if child.type != "link_open":
                    continue
                href = child.attrGet("href")
                assert href is not None
                if not is_external(href):
                    target = resolve_path(href, source_id=source)
                    assert target is not None
                    targets.add(target)
    return targets


def _assert_catalog_links_exist(root: Path, *, excluded: tuple[str, ...] = ()) -> None:
    assert {target for target in _catalog_targets(root, excluded=excluded) if not (root / target).exists()} == set()


@pytest.mark.parametrize("authored_kind", [None, "Package", "TestSuite", "Dependency"])
def test_retire_package_tree_preserves_only_authored_orphans(tmp_path: Path, authored_kind: str | None) -> None:
    root, graph, config = _packages(tmp_path)
    repo = config.repos[0].path
    bundle = load_bundle(root)
    retired = {
        "Package": _member("Package", "pkg:acme/demo/retired"),
        "Dependency": _member("Dependency", "dependency:acme/demo/pypi/httpx"),
    }
    suites = [
        f"{concept_id}.md"
        for concept_id, document in bundle.concepts.items()
        if document.fm.type == "TestSuite" and "retired" in str(document.fm_raw.get("resource", ""))
    ]
    assert len(suites) == 1
    retired["TestSuite"] = suites[0]
    assert all((root / member).is_file() for member in retired.values())
    surviving = {
        _member("Package", "pkg:acme/demo/survivor"),
        _member("Dependency", "dependency:acme/demo/pypi/attrs"),
    }
    assert all((root / member).is_file() for member in surviving)
    authored_before = None
    if authored_kind:
        page = root / retired[authored_kind]
        heading = "Why we depend on this" if authored_kind == "Dependency" else "Purpose"
        _author(page, heading, "Authored retirement history must remain available to future readers.")
        authored_before = page.read_bytes()
    mirror_root = file_system_directory("demo")
    obsolete_indexes = {
        path.relative_to(root).as_posix() for path in (root / mirror_root / "packages/retired").rglob("index.md")
    }
    assert len(obsolete_indexes) >= 3
    _git(repo, "rm", "-r", "packages/retired")
    _git(repo, "commit", "-q", "-m", "retire package and its last-consumer dependency")
    run_workspace([repo], graph_dir=graph, full=True)

    before = _bundle_bytes(root)
    preview = _sync(root, graph, config, dry_run=True)
    assert _bundle_bytes(root) == before
    result = _sync(root, graph, config)

    assert preview.ok and result.ok, (result.mirror.failed_repos, result.entities)
    assert preview.entities.deleted == result.entities.deleted
    assert preview.entities.declined == result.entities.declined
    assert preview.indexes == result.indexes
    for kind, member in retired.items():
        concept_id = member.removesuffix(".md")
        if kind == authored_kind:
            assert (root / member).read_bytes() == authored_before
            assert (concept_id, "prose-edited") in result.entities.declined
            assert concept_id not in result.entities.deleted
            assert member in _catalog_targets(root)
        else:
            assert concept_id in result.entities.deleted
            assert not (root / member).exists()
    assert set(result.indexes.deleted) == obsolete_indexes
    assert result.indexes.declined == ()
    assert all(not (root / member).exists() for member in obsolete_indexes)
    assert all((root / member).is_file() for member in surviving)
    assert (root / mirror_root / "index.md").exists()
    assert (root / mirror_root / "packages/survivor/src/index.md").exists()
    _assert_catalog_links_exist(root)

    settled = _bundle_bytes(root)
    for dry_run in (True, False):
        repeated = _sync(root, graph, config, dry_run=dry_run)
        assert repeated.ok
        assert repeated.entities.deleted == ()
        assert repeated.indexes.deleted == ()
        assert repeated.entities.declined == result.entities.declined
        assert _bundle_bytes(root) == settled


def test_cross_package_rename_preserves_authored_file_history_and_rebases_links(tmp_path: Path) -> None:
    root, graph, config = _packages(tmp_path)
    repo = config.repos[0].path
    old_source = "packages/retired/src/retired.py"
    new_source = "packages/survivor/lib/renamed.py"
    old_member = _member("File", f"file:acme/demo/{old_source}")
    new_member = _member("File", f"file:acme/demo/{new_source}")
    notes = "2026-01-01: introduced the compatibility rule; preserve this authored history."
    _author(root / old_member, "Notes", notes + "\n\n[Manifest](../pyproject.toml.md)")
    referring = _member("File", "file:acme/demo/src/widgets.py")
    _author(root / referring, "Notes", f"[Compatibility rule](/{old_member})")
    mirror_root = file_system_directory("demo")
    authored_index = root / mirror_root / "packages/retired/tests/index.md"
    _write(authored_index, authored_index.read_text(encoding="utf-8") + "\nKeep this authored test migration record.\n")
    authored_index_before = authored_index.read_bytes()
    (repo / "packages/survivor/lib").mkdir()
    _git(repo, "mv", old_source, new_source)
    (repo / old_source).parent.rmdir()
    _git(repo, "rm", "-r", "packages/retired/tests")
    _git(repo, "commit", "-q", "-m", "move unchanged source across package boundary")
    run_workspace([repo], graph_dir=graph, full=True)
    before = _bundle_bytes(root)
    preview = _sync(root, graph, config, dry_run=True)
    assert _bundle_bytes(root) == before
    result = _sync(root, graph, config)

    assert preview.ok and result.ok, (result.mirror.failed_repos, result.entities)
    assert result.mirror.results[0].moved == ((old_member, new_member),)
    assert not (root / old_member).exists()
    moved = (root / new_member).read_text(encoding="utf-8")
    assert notes in moved
    links = build_link_graph(load_bundle(root))
    assert any(
        link.source == new_member.removesuffix(".md")
        and link.target == _member("File", "file:acme/demo/packages/retired/pyproject.toml")
        for link in links.links
    )
    assert any(link.source == referring.removesuffix(".md") and link.target == new_member for link in links.links)
    assert preview.indexes == result.indexes
    assert result.indexes.deleted == (f"{mirror_root}/packages/retired/src/index.md",)
    assert result.indexes.declined == ((authored_index.relative_to(root).as_posix(), "unrecognized-content"),)
    assert authored_index.read_bytes() == authored_index_before
    assert (root / mirror_root / "packages/retired/index.md").exists()
    assert (root / mirror_root / "packages/survivor/lib/index.md").exists()
    # The explicitly declined authored index is byte-preserved, including its
    # stale link; reporting retention does not claim to heal authored content.
    _assert_catalog_links_exist(root, excluded=(authored_index.relative_to(root).as_posix(),))
