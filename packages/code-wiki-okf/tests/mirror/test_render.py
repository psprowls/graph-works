import subprocess
from datetime import UTC, datetime
from pathlib import Path

from code_graph_io import open_reader
from code_graph_io.update import run_workspace
from code_wiki_okf import __version__
from code_wiki_okf.mirror.render import render_file
from code_wiki_okf.placement import PlacementContext, context_from_resource
from okf_ext.generators import Render

_AT = datetime(2026, 1, 1, tzinfo=UTC)
_SHA = "a" * 40


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _multi_repo(root: Path, *, name: str, package: str, symbol: str) -> Path:
    repo = root / name
    source = repo / "src" / "shared"
    source.mkdir(parents=True)
    (repo / "package.json").write_text(
        f'{{"name": "{package}", "version": "0.1.0"}}\n',
        encoding="utf-8",
    )
    (source / f"{name}_dep.js").write_text("export const VALUE = 1;\n", encoding="utf-8")
    (source / "module.js").write_text(
        f'import {{ VALUE }} from "./{name}_dep.js";\n\nexport function {symbol}() {{ return VALUE; }}\n',
        encoding="utf-8",
    )
    (source / f"{name}_user.js").write_text(
        f'import {{ {symbol} }} from "./module.js";\n\nexport const RESULT = {symbol}();\n',
        encoding="utf-8",
    )
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "remote", "add", "origin", f"https://github.com/acme/{name}.git")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _context(rel_path: str) -> PlacementContext:
    return context_from_resource("File", f"file:local/pkg-repo/{rel_path}")


def test_rich_file_gets_owned_keys(graph_repo) -> None:
    frontmatter, render = render_file(graph_repo.reader, _context("src/pkg/user.py"), at=_AT, sha=_SHA)
    assert frontmatter["type"] == "File"
    assert frontmatter["title"] == "user.py"
    assert frontmatter["resource"] == "file:local/pkg-repo/src/pkg/user.py"
    assert frontmatter["language"] == "python"
    assert frontmatter["package"] == "[pkg](/repositories/pkg-repo/packages/pkg.md)"
    assert isinstance(render, Render)


def test_rich_file_render_frontmatter_carries_owned_and_provenance_keys(graph_repo) -> None:
    frontmatter, render = render_file(graph_repo.reader, _context("src/pkg/user.py"), at=_AT, sha=_SHA)
    assert render.frontmatter["language"] == frontmatter["language"]
    assert render.frontmatter["package"] == frontmatter["package"]
    assert render.frontmatter["generated"] == frontmatter["generated"]
    assert render.frontmatter["last_updated_commit"] == frontmatter["last_updated_commit"]


def test_rich_file_render_frontmatter_omits_identity_keys(graph_repo) -> None:
    _frontmatter, render = render_file(graph_repo.reader, _context("src/pkg/user.py"), at=_AT, sha=_SHA)
    assert "type" not in render.frontmatter
    assert "title" not in render.frontmatter
    assert "resource" not in render.frontmatter


def test_rich_file_without_language_or_package_render_frontmatter_omits_both_keys(graph_repo) -> None:
    # Mirrors test_rich_file_without_language_or_package_omits_both_keys but
    # for Render.frontmatter -- a key absent from the returned frontmatter
    # dict must not leak into Render.frontmatter either.
    _frontmatter, render = render_file(graph_repo.reader, _context("pyproject.toml"), at=_AT, sha=_SHA)
    assert "language" not in render.frontmatter
    assert "package" not in render.frontmatter
    assert "generated" in render.frontmatter
    assert "last_updated_commit" in render.frontmatter


def test_rich_file_imports_section_names_the_import(graph_repo) -> None:
    # The graph resolves `from pkg.base import VALUE` to a file-granularity
    # `imports` edge -- the specific imported symbol name is not retained past
    # resolution, only the resolved file path (`code_graph_io.resolve`) --
    # so the section can only name the imported file, not `VALUE` itself.
    _frontmatter, render = render_file(graph_repo.reader, _context("src/pkg/user.py"), at=_AT, sha=_SHA)
    assert "base.py" in render.sections["Imports"]


def test_rich_file_imported_by_section_names_the_importer(graph_repo) -> None:
    _frontmatter, render = render_file(graph_repo.reader, _context("src/pkg/base.py"), at=_AT, sha=_SHA)
    assert "user.py" in render.sections["Imported By"]


def test_rich_file_with_no_children_renders_none_marker(graph_repo) -> None:
    _frontmatter, render = render_file(graph_repo.reader, _context("src/pkg/base.py"), at=_AT, sha=_SHA)
    assert render.sections["Exports"] == "_(none)_"


def test_minimal_file_has_no_owned_keys(graph_repo) -> None:
    frontmatter, render = render_file(graph_repo.reader, _context("node_modules/dep/index.js"), at=_AT, sha=_SHA)
    assert "language" not in frontmatter
    assert "package" not in frontmatter
    assert "role_flags" not in frontmatter
    assert render == Render()


def test_minimal_file_still_gets_provenance(graph_repo) -> None:
    frontmatter, _render = render_file(graph_repo.reader, _context("node_modules/dep/index.js"), at=_AT, sha=_SHA)
    assert frontmatter["generated"] == {"by": frontmatter["generated"]["by"], "at": _AT.isoformat()}
    assert frontmatter["last_updated_commit"] == _SHA


def test_rich_file_generated_by_carries_the_package_version(graph_repo) -> None:
    # trust.actor-convention (spec §7) requires generated.by to match
    # `human:`, `process:`, or `<producer>/<version>` -- a bare "code-wiki-okf"
    # with no version suffix fails that pattern.
    frontmatter, _render = render_file(graph_repo.reader, _context("src/pkg/user.py"), at=_AT, sha=_SHA)
    assert frontmatter["generated"]["by"] == f"code-wiki-okf/{__version__}"


def test_minimal_file_generated_by_carries_the_package_version(graph_repo) -> None:
    frontmatter, _render = render_file(graph_repo.reader, _context("node_modules/dep/index.js"), at=_AT, sha=_SHA)
    assert frontmatter["generated"]["by"] == f"code-wiki-okf/{__version__}"


def test_role_flags_lists_only_true_flags(graph_repo) -> None:
    frontmatter, _render = render_file(graph_repo.reader, _context("src/pkg/base.py"), at=_AT, sha=_SHA)
    assert "is_importable" in frontmatter["role_flags"]
    assert "has_main" not in frontmatter["role_flags"]


def test_rich_file_symbols_section_lists_the_symbol(graph_repo) -> None:
    _frontmatter, render = render_file(graph_repo.reader, _context("src/pkg/helper.py"), at=_AT, sha=_SHA)
    assert "`greet`" in render.sections["Symbols"]
    assert "(function)" in render.sections["Symbols"]


def test_rich_file_exports_section_lists_the_export(graph_repo) -> None:
    # `helper.py` declares `__all__ = ['greet']` -- the only way the Python
    # parser records an `exports` edge -- so this is the one fixture file with
    # a non-empty Exports section.
    _frontmatter, render = render_file(graph_repo.reader, _context("src/pkg/helper.py"), at=_AT, sha=_SHA)
    assert "`greet`" in render.sections["Exports"]
    assert "(function)" in render.sections["Exports"]


def test_rich_file_without_language_or_package_omits_both_keys(graph_repo) -> None:
    # `pyproject.toml` is a real tracked file `describe_path()` knows (hence
    # still "rich" -- sections present, not a bare `Render()`), but its
    # extension has no entry in `extension_languages()` and it sits at the
    # repo root, outside any package's `contains` edge.
    frontmatter, render = render_file(graph_repo.reader, _context("pyproject.toml"), at=_AT, sha=_SHA)
    assert "language" not in frontmatter
    assert "package" not in frontmatter
    assert render != Render()


def test_neither_rich_nor_minimal_file_stamps_tokens(graph_repo) -> None:
    # `run_tokens_update` (graph-works-core) is `tokens`'s sole writer now --
    # see 2026-08-19-tech-debt-tokens-metric-proxy-string. This lane must not
    # stamp it, rich or minimal, in either the returned frontmatter or Render.
    rich_frontmatter, rich_render = render_file(graph_repo.reader, _context("src/pkg/user.py"), at=_AT, sha=_SHA)
    assert "tokens" not in rich_frontmatter
    assert "tokens" not in rich_render.frontmatter

    minimal_frontmatter, minimal_render = render_file(
        graph_repo.reader, _context("node_modules/dep/index.js"), at=_AT, sha=_SHA
    )
    assert "tokens" not in minimal_frontmatter
    assert "tokens" not in minimal_render.frontmatter


def test_render_file_scopes_same_relative_path_to_its_repository(tmp_path: Path) -> None:
    alpha = _multi_repo(tmp_path, name="alpha", package="alpha-pkg", symbol="alpha_symbol")
    beta = _multi_repo(tmp_path, name="beta", package="beta-pkg", symbol="beta_symbol")
    graph_dir = tmp_path / "graph"
    run_workspace([alpha, beta], graph_dir=graph_dir, full=True)

    with open_reader(graph_dir=graph_dir) as reader:
        context = context_from_resource("File", "file:acme/beta/src/shared/module.js")
        frontmatter, render = render_file(reader, context, at=_AT, sha=_SHA)

    assert frontmatter["package"] == "[beta-pkg](/repositories/beta/packages/beta-pkg.md)"
    assert "`beta_symbol`" in render.sections["Symbols"]
    assert "alpha_symbol" not in render.sections["Symbols"]
    assert "beta_dep.js" in render.sections["Imports"]
    assert "alpha_dep.js" not in render.sections["Imports"]
    assert "beta_user.js" in render.sections["Imported By"]
    assert "alpha_user.js" not in render.sections["Imported By"]
