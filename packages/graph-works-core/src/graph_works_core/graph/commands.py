"""The four graph commands — the seam between a workspace and the code graph.

`code-graph-io` takes a resolved `graph_dir` and raises typed exceptions; a
workspace has a layout and a bundle-declared graph directory. This module
turns one into the other, and turns those exceptions into the exit-code
contract `code_graph_io.exit_codes` documents as stable from v1 forward. That
is the whole job.

**No command raises for a graph-state reason.** A missing graph, a stale
schema, a build already in flight — each comes back as a `GraphResult`
carrying its code. Only `graph_target` raises, and only for configuration:
a malformed `_repositories.yaml` is a `ConfigError`, following the line
`code_wiki_okf.config` draws and `okf_ext`'s `VocabularyError` sets.

There is no Typer surface here and no tracing. Both live in E7, which builds
the command line over these functions: timing an invocation and recording it
is the concern of whoever invokes, and keeping it out means this module needs
no clock and writes nothing of its own — only the graph directory `build`
fills and the `out` path `export` is handed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from code_graph_io import (
    GraphNotInitializedError,
    GraphReader,
    SchemaMismatchError,
    exit_codes,
    open_reader,
    paths,
    render,
    update,
)
from code_wiki_okf.config import CONFIG_FILENAME, load_config

from graph_works_core.graph.graph_tools import ROW_CAP, _describe
from graph_works_core.workspace.layout import WorkspaceLayout


@dataclass(frozen=True, slots=True)
class GraphTarget:
    """Where the graph lives and which repos feed it, fully resolved.

    `member_names` is index-aligned with `members` — the `_repositories.yaml`
    key for each path, which is what `build(only=…)` scopes by. Both are
    empty when there is nothing to build, and a caller that only reads (a
    test, C5's tool factory) can supply neither.
    """

    graph_dir: Path
    members: tuple[Path, ...] = ()
    member_names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GraphResult:
    """One command's outcome: a stable exit code and two rendered strings.

    **`error` is non-empty only when `exit_code` is not `SUCCESS`**, so
    `if result.error:` and `if not result.ok:` are the same test. Anything a
    successful command has to say — including a row cap that fired — belongs
    in `output`.
    """

    exit_code: int
    output: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == exit_codes.SUCCESS


def graph_target(layout: WorkspaceLayout) -> GraphTarget:
    """Resolve *layout* into the one target every command takes.

    Reads `<bundle>/_repositories.yaml` when it is there. When it is not,
    falls back to `code_graph_io.paths.graph_dir(layout.root)` with
    `layout.repo_root` as the single member — the bootstrap path
    `code_graph_io.update.run` documents in its own docstring, where the graph
    DB is created before any manifest may exist. A workspace outside any
    repository resolves to no members at all, and a build against that target
    returns `NOT_IN_GIT_REPO`.

    Resolving the member here rather than inside `build` is what keeps the
    target complete: every command reads the target and nothing else, so a
    caller constructing one by hand needs no layout.

    Raises `code_wiki_okf.ConfigError` for a malformed `_repositories.yaml`.
    """
    if not (layout.bundle_dir / CONFIG_FILENAME).exists():
        if layout.repo_root is None:
            return GraphTarget(graph_dir=paths.graph_dir(layout.root))
        return GraphTarget(
            graph_dir=paths.graph_dir(layout.root),
            members=(layout.repo_root,),
            member_names=(layout.repo_root.name,),
        )
    config = load_config(layout.bundle_dir)
    return GraphTarget(
        graph_dir=config.graph_dir,
        members=tuple(repo.path for repo in config.repos),
        member_names=tuple(repo.name for repo in config.repos),
    )


def _open(target: GraphTarget) -> tuple[GraphReader | None, GraphResult]:
    """Open a read-only reader on *target*, or the result that says why not.

    The two store errors are the only ones `open_reader` documents, and they
    are the only two exit codes a read command can produce before it has read
    anything. Does not close on success: callers use `try`/`finally`.
    """
    try:
        return open_reader(graph_dir=target.graph_dir), GraphResult(exit_codes.SUCCESS)
    except GraphNotInitializedError as exc:
        return None, GraphResult(exit_codes.NOT_INITIALIZED, "", f"error: {exc}")
    except SchemaMismatchError as exc:
        return None, GraphResult(exit_codes.SCHEMA_MISMATCH, "", f"error: {exc}")


def build(target: GraphTarget, *, full: bool = False, only: str | None = None) -> GraphResult:
    """Build or refresh the graph at `target.graph_dir` from its members.

    `only` names one member by its `_repositories.yaml` key — the file is
    name-keyed, so matching a resolved path against the member list (what the
    module this ports from did) was both fragile and unnameable from a command
    line. An unknown key returns `GENERIC` naming what is declared.

    `update.run_workspace` returns nothing on success, so `output` is always
    empty. It is not *silent*, though: a schema rebuild under `full` and a
    deriver-version bump each print straight to the process's stderr, below
    this seam and outside the `GraphResult`. A caller that needs those lines
    captured has to redirect the stream.
    """
    members = list(target.members)
    if only is not None:
        declared = dict(zip(target.member_names, target.members, strict=False))
        if only not in declared:
            names = ", ".join(target.member_names) or "(none declared)"
            return GraphResult(exit_codes.GENERIC, "", f"error: unknown member: {only} (declared: {names})")
        members = [declared[only]]
    if not members:
        return GraphResult(
            exit_codes.NOT_IN_GIT_REPO,
            "",
            "error: no repositories to build; declare one under `repositories` in _repositories.yaml",
        )
    try:
        update.run_workspace(members, graph_dir=target.graph_dir, full=full)
    except update.NotInGitRepoError as exc:
        return GraphResult(exit_codes.NOT_IN_GIT_REPO, "", f"error: {exc}")
    except update.UpdateInProgressError as exc:
        return GraphResult(exit_codes.UPDATE_IN_PROGRESS, "", f"error: {exc}")
    except SchemaMismatchError as exc:
        return GraphResult(exit_codes.SCHEMA_MISMATCH, "", f"error: {exc}")
    except Exception as exc:
        # The catch-all the exit-code contract requires: a build that fails for
        # a reason nobody enumerated still has to come back as GENERIC(1)
        # rather than as a traceback through a script consumer.
        return GraphResult(exit_codes.GENERIC, "", f"error: {exc}")
    return GraphResult(exit_codes.SUCCESS)


def describe(
    target: GraphTarget,
    *,
    kind: str | None = None,
    identifier: str | None = None,
    depth: int | None = None,
    in_package: str | None = None,
    fmt: str = "human",
) -> GraphResult:
    """Describe one graph entity across thirteen describable kinds.

    `repository` takes no identifier; every other kind requires one.
    Omitting `kind` infers it from `identifier` via `resolve_selector` — a
    single match dispatches as if that kind had been passed explicitly; more
    than one match returns a disambiguation menu (`SUCCESS`, not an error);
    zero matches falls back to `kind="path"`.

    The dispatch itself is `graph_tools._describe`, shared with the
    reader-level callable so the two cannot drift apart the way the pair this
    ports from did.
    """
    reader, failure = _open(target)
    if reader is None:
        return failure
    try:
        exit_code, output, error = _describe(reader, kind, identifier, depth, in_package=in_package, fmt=fmt)
    finally:
        reader.close()
    return GraphResult(exit_code, output, error)


def find(
    target: GraphTarget,
    *,
    name: str | None = None,
    kind: str | None = None,
    in_package: str | None = None,
    fmt: str = "human",
) -> GraphResult:
    """Find nodes by name, kind and/or containing package.

    **Zero rows is `SUCCESS` for every filter.** The module this ports from
    returned `GENERIC` when `in_package` alone matched nothing, and documents
    that as a quirk traced to one source line of its pre-typed CLI rather than
    as a contract anyone chose.

    Past `ROW_CAP` rows `render` truncates and appends its own notice as the
    last line of `output`. It is deliberately not repeated in `error`: a
    consumer routing the two fields to stdout and stderr would print it twice,
    and it would be the one thing this surface puts in `error` on a call that
    succeeded.
    """
    reader, failure = _open(target)
    if reader is None:
        return failure
    try:
        records = reader.find(name=name, kind=kind, in_package=in_package)
    except ValueError as exc:
        return GraphResult(exit_codes.GENERIC, "", f"error: {exc}")
    finally:
        reader.close()
    return GraphResult(exit_codes.SUCCESS, render.render(records, fmt, cap=ROW_CAP))


def export(target: GraphTarget, *, out: Path | None = None) -> GraphResult:
    """Export the whole graph as GraphML.

    `out=None` returns the document in `output`; a path writes it there,
    creating missing parents, and returns a one-line summary. The reference
    spelled the first case `out_path == Path("-")` — a CLI convention a
    library has no stdout to name, so E7 maps `--out -` onto `None`.
    """
    reader, failure = _open(target)
    if reader is None:
        return failure
    try:
        document = reader.to_graphml()
        node_count = reader.node_count()
        edge_count = sum(reader.edge_counts_by_kind().values())
    finally:
        reader.close()

    if out is None:
        return GraphResult(exit_codes.SUCCESS, document)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(document, encoding="utf-8")
    return GraphResult(exit_codes.SUCCESS, f"wrote {node_count} nodes, {edge_count} edges → {out}")


__all__ = ["GraphResult", "GraphTarget", "build", "describe", "export", "find", "graph_target"]
