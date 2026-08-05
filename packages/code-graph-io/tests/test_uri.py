"""Unit coverage for code_graph_io.uri."""

from __future__ import annotations

import dataclasses

import pytest
from code_graph_io.queries import _VALID_KINDS
from code_graph_io.uri import (
    RepoContext,
    agent_plugin_uri,
    app_uri,
    dependency_uri,
    entry_point_uri,
    file_uri,
    parse_remote_url,
    pkg_uri,
    repo_uri,
    subpkg_uri,
)
from code_graph_io.uri import test_suite_uri as _test_suite_uri  # alias: avoid pytest collection


def test_repo_context_is_frozen() -> None:
    ctx = RepoContext("a", "b")
    assert hash(ctx) is not None
    assert dataclasses.is_dataclass(ctx)
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.org = "x"  # type: ignore[misc]


def test_repo_uri() -> None:
    assert repo_uri(RepoContext("org", "repo")) == "repo:org/repo"


def test_pkg_uri() -> None:
    assert pkg_uri(RepoContext("org", "repo"), "auth-service") == "pkg:org/repo/auth-service"


def test_app_uri_shape() -> None:
    """app_uri returns app:<org>/<repo>/<name> for any RepoContext."""
    assert app_uri(RepoContext("org", "repo"), "agent-workspace-agent") == "app:org/repo/agent-workspace-agent"
    assert app_uri(RepoContext("acme", "tools"), "cli") == "app:acme/tools/cli"


def test_subpkg_uri_preserves_dotted_path() -> None:
    # Lock: dotted Python import path, NOT slash-separated FS path
    result = subpkg_uri(RepoContext("local", "agent-research"), "agent-workspace-cli", "graph_wiki_cli.graph_cli")
    assert result == "subpkg:local/agent-research/agent-workspace-cli/graph_wiki_cli.graph_cli"
    assert "graph_wiki_cli.graph_cli" in result
    assert "graph_wiki_cli/graph_cli" not in result


def test_file_uri_preserves_forward_slashes() -> None:
    assert file_uri(RepoContext("org", "repo"), "src/foo/bar.py") == "file:org/repo/src/foo/bar.py"


def test_entry_point_uri() -> None:
    assert entry_point_uri(RepoContext("org", "repo"), "pkg", "cli") == "entry_point:org/repo/pkg/cli"


def test_test_suite_uri() -> None:
    assert _test_suite_uri(RepoContext("org", "repo"), "unit") == "test_suite:org/repo/unit"


def test_valid_kinds_excludes_package_family() -> None:
    # package_family is removed from the kind admission set.
    # Asserted here so the negative regression check lives next to the URI
    # builder tests for future code-archaeology.
    assert "package_family" not in _VALID_KINDS


def test_agent_plugin_uri() -> None:
    ctx = RepoContext(org="test", repo="repo")
    assert agent_plugin_uri(ctx, "agent-workspace") == "agent_plugin:test/repo/agent-workspace"


def test_dependency_uri() -> None:
    assert dependency_uri("pypi", "boto3") == "dependency:pypi/boto3"


def test_dependency_uri_npm() -> None:
    # Multi-ecosystem coverage; ecosystem is required to avoid cross-registry
    # collision (e.g. `react` exists in npm and on PyPI as `react-py`).
    assert dependency_uri("npm", "react") == "dependency:npm/react"


@pytest.mark.parametrize(
    "url,expected",
    [
        ("git@github.com:pat/agent-research.git", ("pat", "agent-research")),
        ("git@github.com:pat/agent-research", ("pat", "agent-research")),
        ("https://github.com/pat/agent-research.git", ("pat", "agent-research")),
        ("https://github.com/pat/agent-research", ("pat", "agent-research")),
        ("https://github.com/pat/agent-research/", ("pat", "agent-research")),
        ("https://gitlab.com/group/subgroup/repo", None),
        ("git@gitlab.com:group/subgroup/repo.git", None),
        ("git@gitlab.com:group/subgroup/repo", None),
        ("git://foo/bar", None),
        ("file:///tmp/x", None),
        ("not a url", None),
    ],
)
def test_parse_remote_url(url: str, expected: tuple[str, str] | None) -> None:
    assert parse_remote_url(url) == expected
