"""The four-branch discovery matrix, plus the two things discovery does not do."""

from __future__ import annotations

from pathlib import Path

import pytest
from graph_works_core.workspace.discovery import find_repo_root, resolve, resolve_root
from graph_works_core.workspace.errors import WorkspaceNotFound
from graph_works_core.workspace.layout import DEFAULT_WORKSPACE_NAME


def _workspace(root: Path, body: str = "version: 1\n") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "workspace.yaml").write_text(body, encoding="utf-8")
    return root


def _repo(root: Path) -> Path:
    (root / ".git").mkdir(parents=True, exist_ok=True)
    return root


# --- branch 1: the explicit argument ----------------------------------------


def test_an_explicit_workspace_wins(tmp_path):
    root = _workspace(tmp_path / "explicit")
    layout = resolve(workspace=root, environ={"GRAPH_WORKS_DIR": str(tmp_path / "ignored")})
    assert layout.root == root.resolve()


# --- branch 2: the environment override -------------------------------------


def test_the_env_override_is_honored_when_no_argument_is_given(tmp_path):
    root = _workspace(tmp_path / "pinned")
    layout = resolve(cwd=tmp_path, environ={"GRAPH_WORKS_DIR": str(root)})
    assert layout.root == root.resolve()


def test_a_blank_env_override_falls_through_to_discovery(tmp_path):
    repo = _repo(tmp_path / "repo")
    root = _workspace(repo / DEFAULT_WORKSPACE_NAME)
    layout = resolve(cwd=repo, environ={"GRAPH_WORKS_DIR": "   "})
    assert layout.root == root.resolve()


# --- branch 3: the .git walk-up ---------------------------------------------


def test_discovery_walks_up_to_the_repo_and_defaults_to_dot_works(tmp_path):
    repo = _repo(tmp_path / "repo")
    root = _workspace(repo / DEFAULT_WORKSPACE_NAME)
    nested = repo / "packages" / "deep"
    nested.mkdir(parents=True)
    layout = resolve(cwd=nested, environ={})
    assert layout.root == root.resolve()
    assert layout.repo_root == repo.resolve()


def test_outside_any_repo_discovery_falls_back_to_cwd(tmp_path):
    root = _workspace(tmp_path / DEFAULT_WORKSPACE_NAME)
    layout = resolve(cwd=tmp_path, environ={})
    assert layout.root == root.resolve()
    assert layout.repo_root is None


def test_resolve_root_uses_the_same_precedence_before_a_manifest_exists(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    explicit = tmp_path / "explicit"
    pinned = tmp_path / "pinned"

    assert resolve_root(workspace=explicit, cwd=repo, environ={"GRAPH_WORKS_DIR": str(pinned)}) == explicit.resolve()
    assert resolve_root(cwd=repo, environ={"GRAPH_WORKS_DIR": str(pinned)}) == pinned.resolve()
    assert resolve_root(cwd=repo, environ={}) == (repo / ".works").resolve()


def test_resolve_root_falls_back_to_cwd_outside_git(tmp_path: Path) -> None:
    assert resolve_root(cwd=tmp_path, environ={}) == (tmp_path / ".works").resolve()


# --- branch 4: nothing there ------------------------------------------------


def test_a_directory_with_no_manifest_is_not_a_workspace(tmp_path):
    with pytest.raises(WorkspaceNotFound) as excinfo:
        resolve(workspace=tmp_path, environ={})
    message = str(excinfo.value)
    assert "workspace.yaml" in message
    assert "plan_init" in message


def test_a_workspace_that_does_not_exist_at_all_refuses_the_same_way(tmp_path):
    with pytest.raises(WorkspaceNotFound):
        resolve(workspace=tmp_path / "nowhere", environ={})


# --- what resolve returns ---------------------------------------------------


def test_the_manifest_overrides_reach_the_layout(tmp_path):
    root = _workspace(tmp_path / "ws", "version: 1\nlayout:\n  bundle_dir: wiki\n  cache_dir: var/cache\n")
    layout = resolve(workspace=root, environ={})
    assert layout.bundle_dir == root.resolve() / "wiki"
    assert layout.cache_dir == root.resolve() / "var" / "cache"
    assert layout.config_dir == root.resolve() / ".gw"


def test_the_repo_the_workspace_lives_in_is_reported(tmp_path):
    repo = _repo(tmp_path / "repo")
    root = _workspace(repo / DEFAULT_WORKSPACE_NAME)
    assert resolve(workspace=root, environ={}).repo_root == repo.resolve()


def test_a_workspace_outside_any_repo_reports_no_repo(tmp_path):
    root = _workspace(tmp_path / "loose")
    assert resolve(workspace=root, environ={}).repo_root is None


def test_an_explicit_repo_root_overrides_the_walk_up(tmp_path):
    """The scenario `repo_root` exists for: the workspace sits outside the repo
    it catalogs, so `find_repo_root` cannot find it on its own -- this is what
    lets a caller who already knows the intended repo reproduce it on every
    `resolve()`, not just at init."""
    elsewhere = _repo(tmp_path / "elsewhere")
    root = _workspace(tmp_path / "loose")
    layout = resolve(workspace=root, environ={}, repo_root=elsewhere)
    assert layout.repo_root == elsewhere.resolve()


def test_an_explicit_repo_root_wins_over_a_walk_up_that_would_find_a_different_repo(tmp_path):
    repo = _repo(tmp_path / "repo")
    root = _workspace(repo / DEFAULT_WORKSPACE_NAME)
    pinned = _repo(tmp_path / "pinned")
    assert resolve(workspace=root, environ={}, repo_root=pinned).repo_root == pinned.resolve()


def test_the_process_environment_is_the_default_source(tmp_path, monkeypatch):
    root = _workspace(tmp_path / "pinned")
    monkeypatch.setenv("GRAPH_WORKS_DIR", str(root))
    monkeypatch.chdir(tmp_path)
    assert resolve().root == root.resolve()


# --- find_repo_root ---------------------------------------------------------


def test_find_repo_root_accepts_the_repo_itself(tmp_path):
    repo = _repo(tmp_path / "repo")
    assert find_repo_root(repo) == repo.resolve()


def test_find_repo_root_is_none_outside_any_repo(tmp_path):
    assert find_repo_root(tmp_path) is None


# --- what discovery deliberately does not do --------------------------------


def test_the_old_workspace_pointer_is_not_consulted(tmp_path, monkeypatch):
    """`GRAPH_WIKI_WORKSPACE` names the old layout. Honoring it would resolve a
    path and then fail one call later."""
    _workspace(tmp_path / "old-shaped")
    monkeypatch.setenv("GRAPH_WIKI_WORKSPACE", str(tmp_path / "old-shaped"))
    with pytest.raises(WorkspaceNotFound):
        resolve(workspace=tmp_path / "empty", environ={})


def test_no_module_in_the_package_reads_the_old_variable():
    """No module *consults* the old pointer as config -- `environ[...]` /
    `environ.get(...)` / `os.getenv(...)` naming it. `commands/orchestrate.py`'s
    `_prompt()` used to emit `GRAPH_WIKI_WORKSPACE=<value>` into a dispatched
    worker's prompt on purpose, but no emission in this package names the old
    variable any more either -- `_prompt()` now builds both lines from
    `WORKSPACE_VAR` ("GRAPH_WORKS_DIR"), the same pointer this package's own
    resolution reads.
    """
    source_root = Path(__file__).resolve().parents[1] / "src" / "graph_works_core"
    read_patterns = (
        'environ["GRAPH_WIKI_WORKSPACE"]',
        "environ['GRAPH_WIKI_WORKSPACE']",
        'environ.get("GRAPH_WIKI_WORKSPACE"',
        "environ.get('GRAPH_WIKI_WORKSPACE'",
        'getenv("GRAPH_WIKI_WORKSPACE"',
        "getenv('GRAPH_WIKI_WORKSPACE'",
    )
    for module in source_root.rglob("*.py"):
        text = module.read_text(encoding="utf-8")
        for pattern in read_patterns:
            assert pattern not in text, (module, pattern)
