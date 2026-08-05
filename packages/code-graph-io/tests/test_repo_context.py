"""Tests for `repo_context` — git-remote identity with a local fallback.

The fallback is the interesting half: this is the public entry point callers
outside code-graph-io use to identify a repo, and it must never raise for a
directory that simply is not a git repo or has no usable origin.
"""

from __future__ import annotations

from pathlib import Path

from code_graph_io import repo_context as repo_context_module
from code_graph_io.repo_context import repo_context
from code_graph_io.update import NotInGitRepoError


def test_parses_org_and_repo_from_origin(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(repo_context_module, "_git", lambda *a, **kw: "git@github.com:acme/widgets.git\n")
    ctx = repo_context(tmp_path)
    assert (ctx.org, ctx.repo) == ("acme", "widgets")


def test_falls_back_to_local_when_not_a_git_repo(monkeypatch, tmp_path: Path) -> None:
    def _raise(*a, **kw):
        raise NotInGitRepoError("not a git repo")

    monkeypatch.setattr(repo_context_module, "_git", _raise)
    ctx = repo_context(tmp_path / "myproject")
    assert (ctx.org, ctx.repo) == ("local", "myproject")


def test_falls_back_to_local_when_remote_url_is_unparseable(monkeypatch, tmp_path: Path) -> None:
    # A remote that exists but is not a recognizable host/org/repo URL.
    monkeypatch.setattr(repo_context_module, "_git", lambda *a, **kw: "not-a-url\n")
    ctx = repo_context(tmp_path / "myproject")
    assert (ctx.org, ctx.repo) == ("local", "myproject")
