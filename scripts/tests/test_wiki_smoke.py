"""Acceptance tests for `scripts/wiki_smoke.py` -- the one-command
graph-build -> init -> sync smoke run.

Exercised against a real checkout (this repo's own `packages/config-io`),
not the synthetic four-file `tmp_path` fixture every other test in
`code-wiki-okf` uses -- that fixture is deliberately not touched here (see
`packages/code-wiki-okf/tests/mirror/conftest.py`'s own docstring for why it
exists in the first place).

Needs `code_graph_io` and `code_wiki_okf` on the path, so run with:
    uv run --package code-wiki-okf pytest scripts/tests/test_wiki_smoke.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from code_wiki_okf.placement import is_entity_lane_page

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import wiki_smoke

_REPO_ROOT = Path(__file__).resolve().parents[2]
_REAL_CHECKOUT = _REPO_ROOT / "packages" / "config-io"


def _tracked_file_count(repo: Path) -> int:
    out = subprocess.run(["git", "ls-files"], cwd=repo, check=True, capture_output=True, text=True)
    return len([line for line in out.stdout.splitlines() if line.strip()])


def test_smoke_run_creates_one_mirror_page_per_tracked_file(tmp_path: Path) -> None:
    """The mirror lane's whole contract, checked against real scale: a real
    package with tests, a README and a pyproject.toml, not four files.

    Every directory the walk touches also gets its own reconciled
    `index.md` (a directory listing, not a file mirror) -- excluded here so
    the count checked is exactly the one-page-per-tracked-file contract the
    coverage gap names, not an incidental directory-index count that would
    change if the fixture's directory depth changed.

    Entity pages (Package, App, TestSuite, AgentPlugin, and the Repository
    page itself) are also excluded since they're metadata pages, not file
    mirrors -- checked structurally via `is_entity_lane_page` (the same
    predicate `sync/snapshot.py` and `graph_works_core.scan.commands` key
    off) rather than a parent-directory-name heuristic, so a mirrored
    source file that happens to live under a directory literally named
    `packages/` etc. is never mistaken for an entity page.
    """
    bundle_dir = tmp_path / "bundle"
    result = wiki_smoke.run(_REAL_CHECKOUT, bundle_dir)

    assert result.sync_exit_code == 0, result.sync_output
    assert not result.mismatch

    mirror_dir = bundle_dir / "code-graph" / result.repo_name / "file-system"
    # Exclude index.md files (directory listings) and entity lane pages.
    mirror_pages = [
        p
        for p in mirror_dir.rglob("*.md")
        if p.name != "index.md" and not is_entity_lane_page(p.relative_to(bundle_dir).with_suffix("").as_posix())
    ]
    assert len(mirror_pages) == _tracked_file_count(_REAL_CHECKOUT)


def test_smoke_run_syncs_entities_when_repo_name_matches_the_graph(tmp_path: Path) -> None:
    result = wiki_smoke.run(_REAL_CHECKOUT, tmp_path / "bundle")

    assert result.sync_exit_code == 0, result.sync_output
    assert not result.mismatch
    assert (tmp_path / "bundle" / "code-graph" / f"{result.repo_name}.md").exists()


def test_smoke_run_flags_a_repo_name_mismatch_instead_of_silently_syncing_zero_entities(tmp_path: Path) -> None:
    """A typo'd repository key is an explicit failed sync, never a silent
    zero-entity success. `wiki_smoke.run` retains its own mismatch signal so
    callers can distinguish identity mismatch from another sync refusal.
    """
    result = wiki_smoke.run(
        _REAL_CHECKOUT,
        tmp_path / "bundle",
        repo_name="definitely-not-the-graphs-name",
    )

    assert result.sync_exit_code != 0
    assert result.mismatch
    assert not (tmp_path / "bundle" / "code-graph" / "definitely-not-the-graphs-name.md").exists()


def test_main_exits_nonzero_on_a_repo_name_mismatch(tmp_path: Path) -> None:
    exit_code = wiki_smoke.main(
        [
            str(_REAL_CHECKOUT),
            str(tmp_path / "bundle"),
            "--repo-name",
            "definitely-not-the-graphs-name",
        ]
    )
    assert exit_code != 0


def test_main_exits_zero_on_a_clean_run(tmp_path: Path) -> None:
    exit_code = wiki_smoke.main([str(_REAL_CHECKOUT), str(tmp_path / "bundle")])
    assert exit_code == 0


def test_smoke_run_suppresses_the_packages_own_self_implemented_dependency(tmp_path: Path) -> None:
    """`config-io` is distributable, so `reconcile_dependencies` always mints a
    `dependency:pypi/config-io` node with `config-io`'s own manifest as its
    (self-)`implemented_by` -- ADR-0034's "every distributable manifest Package
    creates exactly one ecosystem-qualified Dependency". ADR-0048 then narrows
    the *page* consequence of that: a Dependency node whose `implemented_by` is
    non-empty gets no page at all, ecosystem-qualified filename or otherwise --
    `dependencies/` is the third-party lane; `config-io`'s own page is its
    Package page.
    """
    bundle_dir = tmp_path / "bundle"
    result = wiki_smoke.run(_REAL_CHECKOUT, bundle_dir)

    assert result.sync_exit_code == 0, result.sync_output
    assert not result.mismatch
    deps = bundle_dir / "code-graph" / result.repo_name / "entities" / "dependencies" / "pypi"
    assert not (deps / "config-io.md").exists()
    assert not (deps / "config_io.md").exists()


def test_smoke_run_files_a_third_party_dependency_under_its_ecosystem(tmp_path: Path) -> None:
    """The PEP 503 distribution-name regression this smoke test originally
    guarded (`_normalize_name` filing a Dependency under an *import* name
    instead of the PyPI *distribution* name -- see `packages/code-graph-io`'s
    own history for the `code_graph_io` vs `code-graph-io` incident) is still
    live for a genuine third-party dependency: `config-io`'s sole runtime
    dependency, `ruamel.yaml` (swapped from `pyyaml` by the read-seam
    consolidation -- see `packages/config-io/AGENTS.md`), is never
    self-implemented by this checkout, so it still gets a page, and still at
    its distribution name, normalized (`ruamel.yaml` -> `ruamel-yaml`).

    This assertion is the gate that was missing. `scripts/gw-smoke/run.sh`
    checked the dependency lane but nothing runs it; this file runs inside
    `just check` but never looked at `dependencies/`, so a Dependency filed
    under an import name shipped unnoticed.
    """
    bundle_dir = tmp_path / "bundle"
    result = wiki_smoke.run(_REAL_CHECKOUT, bundle_dir)

    assert result.sync_exit_code == 0, result.sync_output
    assert not result.mismatch
    page = bundle_dir / "code-graph" / result.repo_name / "entities" / "dependencies" / "pypi" / "ruamel-yaml.md"
    assert page.is_file()


def test_smoke_run_builds_the_global_discovery_catalogs(tmp_path: Path) -> None:
    """`okf/index.md` is the one cross-repo discovery view (ADR-0039); there is
    deliberately no standalone global Package/App/AgentPlugin/TestSuite lane
    index, and no global File catalog either (spec decision 7).
    """
    bundle_dir = tmp_path / "bundle"
    result = wiki_smoke.run(_REAL_CHECKOUT, bundle_dir)

    assert result.sync_exit_code == 0, result.sync_output
    for member in (
        "index.md",
        "code-graph/index.md",
        f"code-graph/{result.repo_name}/entities/dependencies/index.md",
        f"code-graph/{result.repo_name}/entities/dependencies/pypi/index.md",
    ):
        assert (bundle_dir / member).is_file(), member

    assert not (bundle_dir / "files").exists()
    assert not (bundle_dir / "repositories").exists()
    assert not (bundle_dir / "dependencies").exists(), "dependencies are per repository; no global lane"
    for lane in ("packages", "apps", "agent-plugins", "test-suites"):
        assert not (bundle_dir / lane).exists(), f"{lane} is a redundant global lane and must not exist"
