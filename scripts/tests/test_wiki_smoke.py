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
    """
    result = wiki_smoke.run(_REAL_CHECKOUT, tmp_path / "bundle", graph_dir=tmp_path / "graph")

    assert result.sync_exit_code == 0, result.sync_output
    assert not result.mismatch

    mirror_dir = tmp_path / "bundle" / "repositories" / result.repo_name
    mirror_pages = [p for p in mirror_dir.rglob("*.md") if p.name != "index.md"]
    assert len(mirror_pages) == _tracked_file_count(_REAL_CHECKOUT)


def test_smoke_run_syncs_entities_when_repo_name_matches_the_graph(tmp_path: Path) -> None:
    result = wiki_smoke.run(_REAL_CHECKOUT, tmp_path / "bundle", graph_dir=tmp_path / "graph")

    assert result.sync_exit_code == 0, result.sync_output
    assert not result.mismatch
    assert (tmp_path / "bundle" / "repositories" / f"{result.repo_name}.md").exists()


def test_smoke_run_flags_a_repo_name_mismatch_instead_of_silently_syncing_zero_entities(tmp_path: Path) -> None:
    """The undocumented failure mode this item exists to close: a typo'd
    `_repositories.yaml` key currently makes `sync` exit 0 having synced no
    Repository/Package page for that repo at all -- with nothing in its
    output distinguishing that from "up to date, nothing changed". Only
    ecosystem-wide entities (dependencies) are unaffected, since
    `entities/sync.py`'s own docstring notes those carry no repo attribution
    to begin with. `wiki_smoke.run` must surface the repo-scoped silence as
    `mismatch=True`.
    """
    result = wiki_smoke.run(
        _REAL_CHECKOUT,
        tmp_path / "bundle",
        graph_dir=tmp_path / "graph",
        repo_name="definitely-not-the-graphs-name",
    )

    assert result.sync_exit_code == 0  # the CLI itself still doesn't raise -- that's the gap
    assert result.mismatch
    assert not (tmp_path / "bundle" / "repositories" / "definitely-not-the-graphs-name.md").exists()


def test_main_exits_nonzero_on_a_repo_name_mismatch(tmp_path: Path) -> None:
    exit_code = wiki_smoke.main(
        [
            str(_REAL_CHECKOUT),
            str(tmp_path / "bundle"),
            "--graph-dir",
            str(tmp_path / "graph"),
            "--repo-name",
            "definitely-not-the-graphs-name",
        ]
    )
    assert exit_code != 0


def test_main_exits_zero_on_a_clean_run(tmp_path: Path) -> None:
    exit_code = wiki_smoke.main([str(_REAL_CHECKOUT), str(tmp_path / "bundle"), "--graph-dir", str(tmp_path / "graph")])
    assert exit_code == 0
