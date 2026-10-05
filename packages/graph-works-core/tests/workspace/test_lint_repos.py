"""Effective lint roots preserve declarations and require Git proof to project."""

from __future__ import annotations

import errno
import shutil
from dataclasses import replace
from pathlib import Path

import pytest
from graph_works_core.workspace import lint_repos
from graph_works_core.workspace.discovery import resolve
from graph_works_core.workspace.errors import WorkspaceError
from graph_works_core.workspace.layout import layout_for
from graph_works_core.workspace.provenance import GitOutcome, probe_git

_CLONE_DECL = (
    "repositories:\n  gw:\n    path: okf/repositories/gw/references/git\n    checkout: .gw/worktrees/gw/main\n"
)


def _write(path, text):
    path.write_text(text, encoding="utf-8", newline="\n")


def _workspace(root, body=""):
    root.mkdir(parents=True, exist_ok=True)
    _write(root / "workspace.yaml", f"version: 1\n{body}")
    layout = resolve(workspace=root, environ={})
    (layout.bundle_dir / "work").mkdir(parents=True, exist_ok=True)
    return layout


def _git(cwd, *args):
    out = probe_git(cwd, *args)
    assert out.returncode == 0, out.stderr or out.cause
    return out.stdout


def _no_probe(_layout):
    raise AssertionError("no projection expected")


def _primary_with_linked(tmp_path, *, detach=False, decl=_CLONE_DECL, nested="", separate_git=False):
    primary = tmp_path / "primary"
    root = primary / nested
    _workspace(root, decl)
    _write(primary / ".gitignore", "**/.gw/worktrees/\n**/references/git/\n**/workspace.local.yaml\n")
    checkout = root / ".gw/worktrees/gw/main"
    (checkout / "packages/graph-works-core").mkdir(parents=True)
    git_dir = ("--separate-git-dir", str(tmp_path / "separate.git")) if separate_git else ()
    _git(primary, "init", "-q", "-b", "main", *git_dir)
    _git(primary, "add", "-A")
    _git(primary, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    linked = tmp_path / "linked"
    args = ("--detach",) if detach else ("-b", "epic/x")
    _git(primary, "worktree", "add", "-q", *args, str(linked))
    return resolve(workspace=root, environ={}), resolve(workspace=linked / nested, environ={}), checkout


def test_no_manifest_repositories_is_empty(tmp_path):
    assert lint_repos.lint_repositories(_workspace(tmp_path / "ws"), probe=_no_probe).roots == {}


def test_absent_manifest_is_empty(tmp_path):
    assert lint_repos.lint_repositories(layout_for(tmp_path / "absent"), probe=_no_probe).union == ()


@pytest.mark.parametrize("source", ["shared", "local"])
@pytest.mark.parametrize("failure", [PermissionError, OSError, FileNotFoundError])
def test_unreadable_configuration_refuses_instead_of_returning_no_roots(tmp_path, monkeypatch, source, failure):
    layout = _workspace(tmp_path / "ws", _CLONE_DECL)
    _write(layout.local_manifest_path, "repositories:\n  gw:\n    checkout: local\n")
    unreadable = layout.manifest_path if source == "shared" else layout.local_manifest_path
    code = {PermissionError: errno.EACCES, OSError: errno.EIO, FileNotFoundError: errno.ENOENT}[failure]
    cause = failure(code, "injected configuration read failure", str(unreadable))
    real = Path.read_bytes

    def failing_read(path):
        if path == unreadable:
            raise cause
        return real(path)

    monkeypatch.setattr(Path, "read_bytes", failing_read)
    with pytest.raises(WorkspaceError, match="cannot read workspace repository configuration") as excinfo:
        lint_repos.lint_repositories(layout, probe=_no_probe)
    assert str(layout.manifest_path) in str(excinfo.value)
    assert str(unreadable) in str(excinfo.value)
    assert "injected configuration read failure" in str(excinfo.value)
    assert excinfo.value.__cause__ is cause


def test_external_relative_path_keeps_manifest_anchoring(tmp_path):
    code = tmp_path / "code"
    code.mkdir()
    layout = _workspace(tmp_path / "ws", "repositories:\n  code:\n    path: ../code\n")
    assert lint_repos.lint_repositories(layout, probe=_no_probe).roots == {"code": code.resolve()}


def test_in_bundle_clone_uses_existing_declared_checkout(tmp_path):
    layout = _workspace(tmp_path / "ws", _CLONE_DECL)
    checkout = layout.root / ".gw/worktrees/gw/main"
    checkout.mkdir(parents=True)
    assert lint_repos.lint_repositories(layout, probe=_no_probe).roots == {"gw": checkout.resolve()}


def test_absolute_checkout_is_used_as_declared(tmp_path):
    checkout = tmp_path / "elsewhere"
    checkout.mkdir()
    layout = _workspace(tmp_path / "ws", _CLONE_DECL.replace(".gw/worktrees/gw/main", str(checkout)))
    assert lint_repos.lint_repositories(layout, probe=_no_probe).roots == {"gw": checkout}


def test_missing_absolute_checkout_refuses_without_projection(tmp_path):
    gone = tmp_path / "gone"
    layout = _workspace(tmp_path / "ws", _CLONE_DECL.replace(".gw/worktrees/gw/main", str(gone)))
    with pytest.raises(WorkspaceError, match=r"repositories\.gw.*absolute checkout") as excinfo:
        lint_repos.lint_repositories(layout, probe=_no_probe)
    assert str(gone) in str(excinfo.value)


@pytest.mark.parametrize("is_file", [False, True])
def test_missing_external_directory_refuses_naming_repo_and_path(tmp_path, is_file):
    absent = tmp_path / "absent"
    if is_file:
        _write(absent, "not a checkout")
    layout = _workspace(tmp_path / "ws", "repositories:\n  code:\n    path: ../absent\n")
    with pytest.raises(WorkspaceError, match=r"repositories\.code") as excinfo:
        lint_repos.lint_repositories(layout, probe=_no_probe)
    assert str(absent.resolve()) in str(excinfo.value)


def test_missing_checkout_with_no_primary_refuses(tmp_path):
    layout = _workspace(tmp_path / "ws", _CLONE_DECL)
    with pytest.raises(WorkspaceError, match=r"repositories\.gw.*does not exist"):
        lint_repos.lint_repositories(layout, probe=lambda _l: None)


def test_no_checkout_declaration_refuses(tmp_path):
    layout = _workspace(tmp_path / "ws", _CLONE_DECL.replace("    checkout: .gw/worktrees/gw/main\n", ""))
    with pytest.raises(WorkspaceError, match=r"repositories\.gw.*declares no checkout"):
        lint_repos.lint_repositories(layout, probe=_no_probe)


def test_local_override_checkout_wins_without_projection(tmp_path):
    layout = _workspace(tmp_path / "ws", _CLONE_DECL)
    local = layout.root / "local-checkout"
    local.mkdir()
    _write(layout.local_manifest_path, "repositories:\n  gw:\n    checkout: local-checkout\n")
    assert lint_repos.lint_repositories(layout, probe=_no_probe).roots == {"gw": local}


def test_missing_relative_local_override_refuses_without_projection(tmp_path):
    layout = _workspace(tmp_path / "ws", _CLONE_DECL)
    _write(layout.local_manifest_path, "repositories:\n  gw:\n    checkout: gone\n")
    with pytest.raises(WorkspaceError, match=r"workspace\.local\.yaml"):
        lint_repos.lint_repositories(layout, probe=_no_probe)


def test_roots_preserve_order_and_cannot_be_mutated(tmp_path):
    for name in ("two", "one"):
        (tmp_path / name).mkdir()
    layout = _workspace(tmp_path / "ws", "repositories:\n  two:\n    path: ../two\n  one:\n    path: ../one\n")
    roots = lint_repos.lint_repositories(layout, probe=_no_probe)
    assert tuple(roots.roots) == ("two", "one")
    assert roots.union == (tmp_path / "two", tmp_path / "one")
    with pytest.raises(TypeError):
        roots.roots["two"] = tmp_path / "one"


@pytest.mark.parametrize("detach", [False, True])
def test_linked_workspace_projects_primary_checkout(tmp_path, detach):
    primary, linked, checkout = _primary_with_linked(tmp_path, detach=detach)
    assert lint_repos.primary_workspace(linked).root == primary.root
    assert lint_repos.lint_repositories(linked).roots == {"gw": checkout.resolve()}


def test_primary_workspace_itself_is_not_projected(tmp_path):
    primary, _linked, _checkout = _primary_with_linked(tmp_path)
    assert lint_repos.primary_workspace(primary) is None


def test_separate_git_dir_primary_is_not_projected(tmp_path):
    layout = _workspace(tmp_path / "sep")
    _git(layout.root, "init", "-q", "--separate-git-dir", str(tmp_path / "sep.git"))
    assert lint_repos.primary_workspace(layout) is None


def test_separate_git_dir_linked_refuses_when_git_cannot_prove_primary_root(tmp_path):
    _primary, linked, _checkout = _primary_with_linked(tmp_path, separate_git=True)
    # Git lists the separate Git directory as the main worktree, not the
    # working directory containing workspace.yaml. There is no path to borrow.
    with pytest.raises(WorkspaceError, match=r"separate\.git.*no workspace\.yaml"):
        lint_repos.lint_repositories(linked)


def test_non_git_workspace_has_no_primary(tmp_path):
    assert lint_repos.primary_workspace(_workspace(tmp_path / "plain")) is None


def test_divergent_declaration_is_not_laundered(tmp_path):
    _primary, linked, _checkout = _primary_with_linked(tmp_path)
    _write(linked.manifest_path, "version: 1\n" + _CLONE_DECL.replace("gw/main", "gw/other"))
    with pytest.raises(WorkspaceError, match="differs from the primary workspace"):
        lint_repos.lint_repositories(linked)


def test_projection_reports_missing_primary_checkout(tmp_path):
    _primary, linked, checkout = _primary_with_linked(tmp_path)
    shutil.rmtree(checkout)
    with pytest.raises(WorkspaceError, match=r"repositories\.gw.*primary workspace") as excinfo:
        lint_repos.lint_repositories(linked)
    assert str(checkout.resolve()) in str(excinfo.value)


def test_primary_local_override_is_honored(tmp_path):
    primary, linked, _checkout = _primary_with_linked(tmp_path)
    local = tmp_path / "local"
    local.mkdir()
    _write(primary.local_manifest_path, f"repositories:\n  gw:\n    checkout: {local}\n")
    assert lint_repos.lint_repositories(linked).roots == {"gw": local}


def test_probe_is_called_once_for_multiple_projected_roots(tmp_path):
    decl = _CLONE_DECL + (
        "  other:\n    path: okf/repositories/other/references/git\n    checkout: .gw/worktrees/other/main\n"
    )
    primary, linked, checkout = _primary_with_linked(tmp_path, decl=decl)
    other = primary.root / ".gw/worktrees/other/main"
    other.mkdir(parents=True)
    calls = []

    def probe(layout):
        calls.append(layout.root)
        return lint_repos.primary_workspace(layout)

    assert lint_repos.lint_repositories(linked, probe=probe).union == (checkout, other)
    assert calls == [linked.root]


def test_nested_workspace_preserves_primary_layout_overrides(tmp_path, monkeypatch):
    decl = _CLONE_DECL.replace("okf/", "knowledge/")
    decl += "layout:\n  bundle_dir: knowledge\n  config_dir: control\n  cache_dir: scratch\n  worktrees_dir: trees\n"
    primary, linked, checkout = _primary_with_linked(tmp_path, nested="nested/ws", decl=decl)
    monkeypatch.setenv("GRAPH_WORKS_DIR", str(tmp_path / "unrelated"))
    projected_layout = lint_repos.primary_workspace(linked)
    assert projected_layout == primary
    assert projected_layout.bundle_dir == primary.root / "knowledge"
    assert projected_layout.config_dir == primary.root / "control"
    assert projected_layout.cache_dir == primary.root / "scratch"
    assert projected_layout.worktrees_dir == primary.root / "trees"
    assert lint_repos.lint_repositories(linked).roots == {"gw": checkout}


def test_missing_mirrored_workspace_refuses(tmp_path):
    primary, linked, _checkout = _primary_with_linked(tmp_path, nested="nested/ws")
    primary.manifest_path.unlink()
    with pytest.raises(WorkspaceError, match=r"no workspace\.yaml") as excinfo:
        lint_repos.primary_workspace(linked)
    assert str(primary.root) in str(excinfo.value)


def test_unregistered_moved_worktree_refuses(tmp_path):
    primary, linked, _checkout = _primary_with_linked(tmp_path)
    _git(primary.root, "worktree", "lock", str(linked.root))
    moved = tmp_path / "moved"
    linked.root.rename(moved)
    with pytest.raises(WorkspaceError, match="not a registered worktree"):
        lint_repos.primary_workspace(layout_for(moved))


def test_broken_linked_git_file_refuses(tmp_path):
    _primary, linked, _checkout = _primary_with_linked(tmp_path)
    _write(linked.root / ".git", f"gitdir: {tmp_path / 'missing.git'}\n")
    with pytest.raises(WorkspaceError, match="repository cannot be read"):
        lint_repos.primary_workspace(linked)


def test_bare_primary_refuses(tmp_path):
    primary, _linked, _checkout = _primary_with_linked(tmp_path)
    bare = tmp_path / "bare.git"
    _git(tmp_path, "clone", "-q", "--bare", str(primary.root), str(bare))
    linked = tmp_path / "bare-linked"
    _git(bare, "worktree", "add", "-q", "--detach", str(linked))
    with pytest.raises(WorkspaceError, match=r"bare.*cannot project"):
        lint_repos.primary_workspace(resolve(workspace=linked, environ={}))


def test_foreign_common_directory_refuses(tmp_path, monkeypatch):
    primary, linked, _checkout = _primary_with_linked(tmp_path)
    real = lint_repos.probe_git

    def lying(cwd, *args, **kwargs):
        outcome = real(cwd, *args, **kwargs)
        if cwd == primary.root and args == ("rev-parse", "--path-format=absolute", "--git-common-dir"):
            return replace(outcome, stdout=str(tmp_path / "foreign.git") + "\n")
        return outcome

    monkeypatch.setattr(lint_repos, "probe_git", lying)
    with pytest.raises(WorkspaceError, match="common directory"):
        lint_repos.primary_workspace(linked)


@pytest.mark.parametrize(
    ("command", "outcome", "message"),
    [
        ("rev-parse", GitOutcome(0, "/only-one-path\n", "ok"), "unexpected git rev-parse"),
        ("worktree", GitOutcome(1, "", "ok", "unreadable"), "git worktree list failed"),
        ("worktree", GitOutcome(0, "", "ok"), "no worktrees"),
        ("worktree", GitOutcome(0, "HEAD deadbeef\n\n", "ok"), "malformed git worktree"),
    ],
)
def test_unusable_git_evidence_refuses(tmp_path, monkeypatch, command, outcome, message):
    _primary, linked, _checkout = _primary_with_linked(tmp_path)
    real = lint_repos.probe_git

    def failing(cwd, *args, **kwargs):
        return outcome if args[0] == command else real(cwd, *args, **kwargs)

    monkeypatch.setattr(lint_repos, "probe_git", failing)
    with pytest.raises(WorkspaceError, match=message):
        lint_repos.primary_workspace(linked)


def test_prunable_registration_does_not_prove_membership(tmp_path, monkeypatch):
    _primary, linked, _checkout = _primary_with_linked(tmp_path)
    real = lint_repos.probe_git

    def prunable(cwd, *args, **kwargs):
        outcome = real(cwd, *args, **kwargs)
        if args == ("worktree", "list", "--porcelain"):
            records = outcome.stdout.rstrip("\n").split("\n\n")
            records[-1] += "\nprunable stale administrative files"
            return replace(outcome, stdout="\n\n".join(records) + "\n\n")
        return outcome

    monkeypatch.setattr(lint_repos, "probe_git", prunable)
    with pytest.raises(WorkspaceError, match="not a registered worktree"):
        lint_repos.primary_workspace(linked)


@pytest.mark.parametrize(
    "outcome",
    [GitOutcome(1, "", "ok", "unreadable"), GitOutcome(0, "", "ok"), GitOutcome(0, "/one\n/two\n", "ok")],
)
def test_failed_or_malformed_primary_common_dir_refuses(tmp_path, monkeypatch, outcome):
    primary, linked, _checkout = _primary_with_linked(tmp_path)
    real = lint_repos.probe_git

    def failing(cwd, *args, **kwargs):
        if cwd == primary.root and args == ("rev-parse", "--path-format=absolute", "--git-common-dir"):
            return outcome
        return real(cwd, *args, **kwargs)

    monkeypatch.setattr(lint_repos, "probe_git", failing)
    with pytest.raises(WorkspaceError, match="common directory"):
        lint_repos.primary_workspace(linked)
