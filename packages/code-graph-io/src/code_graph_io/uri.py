"""URI composition surface."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class RepoContext:
    org: str
    repo: str


def repo_uri(ctx: RepoContext) -> str:
    return f"repo:{ctx.org}/{ctx.repo}"


def pkg_uri(ctx: RepoContext, name: str) -> str:
    return f"pkg:{ctx.org}/{ctx.repo}/{name}"


def app_uri(ctx: RepoContext, name: str) -> str:
    """app URI for scanner-classified application packages."""
    return f"app:{ctx.org}/{ctx.repo}/{name}"


def subpkg_uri(ctx: RepoContext, pkg_name: str, dotted_path: str) -> str:
    return f"subpkg:{ctx.org}/{ctx.repo}/{pkg_name}/{dotted_path}"


def file_uri(ctx: RepoContext, rel_path: str) -> str:
    return f"file:{ctx.org}/{ctx.repo}/{rel_path}"


def entry_point_uri(ctx: RepoContext, pkg_name: str, ep_name: str) -> str:
    return f"entry_point:{ctx.org}/{ctx.repo}/{pkg_name}/{ep_name}"


def test_suite_uri(ctx: RepoContext, suite_name: str) -> str:
    return f"test_suite:{ctx.org}/{ctx.repo}/{suite_name}"


# agent_plugin entities are repo-scoped (a development artifact lives in a
# specific repo), unlike the retired concept-level `plugin:{name}`.
def agent_plugin_uri(ctx: RepoContext, name: str) -> str:
    return f"agent_plugin:{ctx.org}/{ctx.repo}/{name}"


def solution_uri(ctx: RepoContext, name: str) -> str:
    return f"solution:{ctx.org}/{ctx.repo}/{name}"


def dependency_uri(ctx: RepoContext, ecosystem: str, name: str) -> str:
    """Repository-scoped Dependency URI: one node per (repository, dependency).

    ``org``, ``repo`` and ``ecosystem`` never contain ``/``, so the URI parses
    left to right with ``split("/", 3)`` and a scoped npm name keeps its slash.
    """
    return f"dependency:{ctx.org}/{ctx.repo}/{ecosystem}/{name}"


def dependency_path(ctx: RepoContext, ecosystem: str, name: str) -> str:
    """Synthetic store path for a Dependency node; unique per repository."""
    return f"dependency:{ctx.org}/{ctx.repo}:{ecosystem}:{name}"


def dependency_identifier_from_path(path: str) -> str | None:
    """``<org>/<repo>/<ecosystem>/<name>`` from a synthetic path, else ``None``.

    Callers that hold only a ``find``-style NodeRecord (no ``uri`` column) use
    this to build the ``gw graph describe --kind dependency`` identifier.
    """
    payload = path.removeprefix("dependency:")
    if payload == path:
        return None
    parts = payload.split(":", 2)
    if len(parts) != 3 or not all(parts):
        return None
    scope, ecosystem, name = parts
    org, separator, repo = scope.partition("/")
    if not separator or not org or not repo or "/" in repo:
        return None
    return f"{org}/{repo}/{ecosystem}/{name}"


def builtin_uri(language: str, module_name: str) -> str:
    return f"builtin:{language}/{module_name}"


_SSH_REMOTE_RE = re.compile(r"^git@[^:]+:([^/]+)/([^/]+?)(?:\.git)?$")
_HTTPS_REMOTE_RE = re.compile(r"^https?://[^/]+/([^/]+)/([^/]+?)(?:\.git)?/?$")


def parse_remote_url(url: str) -> tuple[str, str] | None:
    m = _SSH_REMOTE_RE.match(url)
    if m is not None:
        return m.group(1), m.group(2)
    m = _HTTPS_REMOTE_RE.match(url)
    if m is not None:
        return m.group(1), m.group(2)
    return None
