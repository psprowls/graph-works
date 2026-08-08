import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
from code_graph_io import update
from code_graph_io.handle import GraphReader, open_reader


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@dataclass(frozen=True)
class GraphRepoFixture:
    repo_root: Path
    graph_dir: Path
    reader: GraphReader


@pytest.fixture
def graph_repo(tmp_path: Path) -> GraphRepoFixture:
    """A real Python repo (`pkg/base.py`, imported by `pkg/user.py`; a third
    file `pkg/helper.py` with a top-level, `__all__`-exported function, for
    non-empty Symbols/Exports coverage), plus one file the graph skips
    (`node_modules/dep/index.js`), graphed via `code_graph_io.update.run_workspace`,
    opened read-only. `pyproject.toml` itself is a real tracked file the graph
    describes but assigns no language and no containing package -- a rich page
    with neither owned key.
    """
    repo_root = tmp_path / "repo"
    pkg = repo_root / "src" / "pkg"
    pkg.mkdir(parents=True)
    (repo_root / "pyproject.toml").write_text('[project]\nname = "pkg"\nversion = "0.1.0"\ndependencies = []\n')
    (pkg / "__init__.py").write_text("")
    (pkg / "base.py").write_text("VALUE = 1\n")
    (pkg / "user.py").write_text("from pkg.base import VALUE\n\nresult = VALUE + 1\n")
    (pkg / "helper.py").write_text("def greet():\n    return 1\n\n\n__all__ = ['greet']\n")
    # A file the graph deliberately never emits a node for (default skip dir),
    # even though git tracks it -- the real-world shape of a "minimal" page:
    # on disk and version-controlled, but outside the graph's reach.
    vendored = repo_root / "node_modules" / "dep"
    vendored.mkdir(parents=True)
    (vendored / "index.js").write_text("module.exports = {};\n")
    _git(["init", "-q", "-b", "main"], repo_root)
    _git(["config", "user.email", "t@t"], repo_root)
    _git(["config", "user.name", "t"], repo_root)
    _git(["add", "-A"], repo_root)
    _git(["commit", "-q", "-m", "init"], repo_root)

    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    update.run_workspace([repo_root], graph_dir=graph_dir, full=True)

    reader = open_reader(graph_dir=graph_dir)
    yield GraphRepoFixture(repo_root=repo_root, graph_dir=graph_dir, reader=reader)
    reader.close()
