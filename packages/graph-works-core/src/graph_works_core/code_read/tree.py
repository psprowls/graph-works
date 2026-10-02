"""The code-graph tree and search reads over the scanned `code-graph/` pages.

Refusals are results: an unknown repository comes back as a complete value
with `refusal` set. Nothing here reads the repository's working tree; it reads
the bundle the scan wrote.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from graph_works_core.workspace.bundle import load_workspace_bundle
from graph_works_core.workspace.layout import WorkspaceLayout
from graph_works_core.workspace.repo_files import declared_repos

TreeRefusal = Literal["unknown-repository"]

_ROOT = "code-graph"


@dataclass(frozen=True, slots=True)
class CodeTreeNode:
    """One page or directory index under a repository's code-graph folder."""

    id: str
    title: str
    type: str | None
    resource: str | None
    parent: str | None
    is_index: bool


@dataclass(frozen=True, slots=True)
class CodeGraphTree:
    """Every node of `code-graph/<repo>`, flat and parent-linked, sorted by id."""

    repo: str
    nodes: tuple[CodeTreeNode, ...]
    refusal: TreeRefusal | None


@dataclass(frozen=True, slots=True)
class SearchHit:
    """One code-graph page that matched a search term."""

    id: str
    title: str
    type: str | None
    resource: str | None
    description: str | None
    repo: str


@dataclass(frozen=True, slots=True)
class CodeGraphSearch:
    """The ranked hits for `q`, cut to the limit; `truncated` when more matched."""

    q: str
    repo: str | None
    hits: tuple[SearchHit, ...]
    truncated: bool
    refusal: TreeRefusal | None


def _repo_known(layout: WorkspaceLayout, repo: str) -> bool:
    return any(entry.name == repo for entry in declared_repos(layout))


def _str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def run_code_graph_tree(layout: WorkspaceLayout, repo: str) -> CodeGraphTree:
    """The repository page, every concept and every directory index under `code-graph/<repo>/`."""
    if not _repo_known(layout, repo):
        return CodeGraphTree(repo, (), "unknown-repository")
    bundle = load_workspace_bundle(layout)
    root_id = f"{_ROOT}/{repo}"
    prefix = f"{root_id}/"
    index_dirs = {directory for directory in bundle.indexes if directory.startswith(prefix)}

    def parent_of(node_id: str, directory: str) -> str | None:
        current = directory
        while current.startswith(prefix):
            if current in index_dirs and f"{current}/index" != node_id:
                return f"{current}/index"
            current = current.rpartition("/")[0]
        return root_id if node_id != root_id and root_id in bundle.concepts else None

    nodes: list[CodeTreeNode] = []
    for concept_id, document in bundle.concepts.items():
        if concept_id != root_id and not concept_id.startswith(prefix):
            continue
        data = document.fm_data(dates="iso")
        nodes.append(
            CodeTreeNode(
                concept_id,
                document.fm.title or concept_id.rpartition("/")[2],
                document.fm.type or None,
                _str(data.get("resource")),
                parent_of(concept_id, concept_id.rpartition("/")[0]),
                False,
            )
        )
    for directory in index_dirs:
        node_id = f"{directory}/index"
        document = bundle.indexes[directory]
        nodes.append(
            CodeTreeNode(
                node_id,
                document.fm.title or directory.rpartition("/")[2],
                document.fm.type or None,
                None,
                parent_of(node_id, directory.rpartition("/")[0]),
                True,
            )
        )
    return CodeGraphTree(repo, tuple(sorted(nodes, key=lambda node: node.id)), None)


def _rank(q: str, hit: SearchHit) -> int | None:
    title = hit.title.casefold()
    if title == q:
        return 0
    if title.startswith(q):
        return 1
    haystack = (hit.title, hit.id, hit.resource or "", hit.description or "")
    return 2 if any(q in field.casefold() for field in haystack) else None


def run_code_graph_search(
    layout: WorkspaceLayout, q: str, *, repo: str | None = None, limit: int = 50
) -> CodeGraphSearch:
    """Code-graph pages matching `q`: exact title, then title prefix, then any substring."""
    if repo is not None and not _repo_known(layout, repo):
        return CodeGraphSearch(q, repo, (), False, "unknown-repository")
    bundle = load_workspace_bundle(layout)
    scope = f"{_ROOT}/{repo}" if repo is not None else _ROOT
    needle = q.casefold()
    ranked: list[tuple[int, SearchHit]] = []
    for concept_id, document in bundle.concepts.items():
        if concept_id != scope and not concept_id.startswith(f"{scope}/"):
            continue
        data = document.fm_data(dates="iso")
        hit = SearchHit(
            concept_id,
            document.fm.title or concept_id.rpartition("/")[2],
            document.fm.type or None,
            _str(data.get("resource")),
            _str(data.get("description")),
            concept_id.split("/")[1] if concept_id.count("/") else "",
        )
        rank = _rank(needle, hit)
        if rank is not None:
            ranked.append((rank, hit))
    ranked.sort(key=lambda pair: (pair[0], pair[1].id))
    return CodeGraphSearch(q, repo, tuple(hit for _, hit in ranked[:limit]), len(ranked) > limit, None)
