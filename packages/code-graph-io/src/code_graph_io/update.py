"""Update orchestrator: git diff → parse + project + upsert → resolve → metadata."""

from __future__ import annotations

import datetime as _dt
import os
import sqlite3
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

from code_parser.parse import parse_bytes
from code_parser.projections.graph import to_graph_records

from code_graph_io import _ignore, builtins, packages, resolve, schema, store, tokens, upsert
from code_graph_io.paths import graph_dir
from code_graph_io.uri import repo_uri

# The `.agent-workspace/` dir holds only local machine state (graph DB + cache,
# subagent traces, search index); ignore it wholesale.
_GITIGNORE_BODY = "*\n"


class NotInGitRepoError(Exception):
    pass


class UpdateInProgressError(Exception):
    """Raised when another writer holds the SQLite write lock past the timeout."""


class StrictTreeInvariantError(Exception):
    """Raised when the physically_contains containment tree has a child node
    with more than one parent edge — violates the strict tree invariant.

    Most commonly caused by an emitter inserting a duplicate parent edge or
    by test_suites.emit's re-parenting failing to DELETE the prior
    physically_contains edge before INSERTing the new one.
    """

    def __init__(self, *, offending_child_ids: list[int]) -> None:
        self.offending_child_ids = offending_child_ids
        count = len(offending_child_ids)
        sample = offending_child_ids[:20]
        super().__init__(
            f"physically_contains tree invariant violated for {count} node(s). "
            f"Likely cause: an emitter inserted a duplicate parent edge, or "
            f"test re-parenting failed to delete the prior edge. "
            f"Offending child node ids (first {min(count, 20)}): {sample}"
        )


def _git(args: list[str], *, cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise NotInGitRepoError(result.stderr.strip() or "git command failed")
    return result.stdout


def _head(cwd: Path) -> str:
    return _git(["rev-parse", "HEAD"], cwd=cwd).strip()


def _all_tracked(cwd: Path) -> list[tuple[str, str]]:
    out = _git(["ls-files"], cwd=cwd)
    return [("A", line) for line in out.splitlines() if line]


def _diff(cwd: Path, prev: str) -> list[tuple[str, str]]:
    out = _git(["diff", "--name-status", f"{prev}..HEAD"], cwd=cwd)
    rows: list[tuple[str, str]] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if not parts:
            continue
        status = parts[0][0]
        if status == "R":
            if len(parts) < 3:
                continue
            old_path, new_path = parts[1], parts[2]
            rows.append(("D", old_path))
            rows.append(("M", new_path))
        elif status in {"A", "M", "D"}:
            rows.append((status, parts[-1]))
    return rows


def _is_parseable(path: str) -> bool:
    from code_parser.parsers import EXTENSIONS

    return Path(path).suffix in EXTENSIONS


def _delete_file_nodes(conn: sqlite3.Connection, path: str, repo_uri_val: str) -> None:
    conn.execute(
        "DELETE FROM nodes WHERE path = ? AND (repo = ? OR repo IS NULL)",
        (path, repo_uri_val),
    )


def _set_metadata(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO metadata(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def _get_metadata(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _ensure_gitignore(workspace: Path) -> None:
    target = graph_dir(workspace) / ".gitignore"
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_GITIGNORE_BODY)


def _changed_files(repo_root: Path, full: bool, prev: str | None) -> list[tuple[str, str]]:
    if full or prev is None:
        return _all_tracked(repo_root)
    return _diff(repo_root, prev)


def _process_files(
    conn: sqlite3.Connection,
    repo_root: Path,
    changed: Iterable[tuple[str, str]],
    skip_dirs: frozenset[str],
    repo_uri_val: str,
) -> None:
    for status, rel in changed:
        if _ignore.should_skip(rel, skip_dirs):
            continue
        if not _is_parseable(rel):
            continue
        if status == "D":
            _delete_file_nodes(conn, rel, repo_uri_val)
            continue
        full = repo_root / rel
        if not full.exists():
            continue
        source = full.read_bytes()
        tree = parse_bytes(source, path=Path(rel), package=None)
        records = to_graph_records(tree)
        for node in records.nodes:
            if node.path is not None:
                node.attrs.setdefault("language", tree.language)
            if node.start_byte is not None and node.end_byte is not None:
                snippet = source[node.start_byte : node.end_byte].decode("utf-8", "replace")
                node.attrs["token_count"] = tokens.count_tokens(snippet)
        upsert.upsert_records(conn, records)


def _default_lock_timeout() -> int:
    raw = os.environ.get("GRAPH_WIKI_LOCK_TIMEOUT_MS")
    if raw is None:
        return 30_000
    try:
        return max(0, int(raw))
    except ValueError:
        return 30_000


def _read_schema_version_or_none(db_path: Path) -> str | None:
    """Read `metadata.schema_version` from `db_path` without touching the schema.

    Uses a transient read-only sqlite connection so a v1 DB can be probed
    without raising `SchemaMismatchError` (we want the version value,
    not the gate). Returns None on any sqlite error or if the metadata row
    is missing (defensive — caller treats None as "rebuild required").
    """
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    return row[0] if row else None


def _enforce_strict_tree_invariant(conn: sqlite3.Connection) -> None:
    """raise StrictTreeInvariantError if any child has >1
    physically_contains parents. Always on. Always runs at the end of
    update.run inside the transaction."""
    rows = conn.execute(
        "SELECT dst, COUNT(*) FROM edges WHERE kind = 'physically_contains' GROUP BY dst HAVING COUNT(*) > 1"
    ).fetchall()
    if rows:
        raise StrictTreeInvariantError(offending_child_ids=[row[0] for row in rows])


def _unlink_db_files(db_path: Path) -> None:
    """Unlink `db_path` plus its `code.db-wal` / `code.db-shm` siblings.

    The WAL/SHM siblings mirror `code.db`'s name; they live in `db_path.parent`.
    """
    db_path.unlink(missing_ok=True)
    (db_path.parent / "code.db-wal").unlink(missing_ok=True)
    (db_path.parent / "code.db-shm").unlink(missing_ok=True)


def _update_one_repo(
    conn: sqlite3.Connection,
    repo_root: Path,
    workspace: Path,
    *,
    full: bool,
    global_workspace: dict[str, tuple[str, str, str, str]],
    deferred: list[packages.CrossRepoLink],
) -> None:
    """Run the single-repo pipeline for one member, then stamp its nodes.

    Lifts the per-repo body of the former monolithic `run()` into a function
    invoked once per workspace member. Global steps (resolve.sweep, strict-tree
    invariant, workspace-level metadata, cross-repo link pass) stay in
    `run_workspace`. After the pipeline runs, every node this member produced
    that is still unstamped (`repo IS NULL`, excluding the global builtin /
    dependency nodes) is stamped with this member's `repo:` URI, and the
    per-repo `last_indexed_commit:<uri>` metadata key is written.
    """
    head = _head(repo_root)
    from code_graph_io.repo_context import (
        repo_context,
    )

    ctx = repo_context(repo_root)
    repo_uri_val = repo_uri(ctx)
    skip_dirs = _ignore.load_skip_dirs(repo_root)
    commit_key = f"last_indexed_commit:{repo_uri_val}"
    prev = _get_metadata(conn, commit_key)
    changed = _changed_files(repo_root, full=full, prev=prev)
    if not changed and prev == head and not full:
        return

    # Scope path-bearing node identity to this member for the whole pass so two
    # sibling repos sharing a relative path (e.g. `pyproject.toml`) don't merge
    # into one node. Cleared in `finally` so the global post-loop steps run
    # unscoped.
    upsert.set_current_repo(conn, repo_uri_val)
    try:
        _process_files(conn, repo_root, changed, skip_dirs, repo_uri_val)
        packages.refresh(
            conn,
            repo_root=repo_root,
            ctx=ctx,
            current_repo=repo_uri_val,
            global_workspace=global_workspace,
            deferred_cross_repo=deferred,
        )
        builtins.refresh(conn, repo_root=repo_root, workspace=workspace, ctx=ctx)
        # Resolve file-import edges to real file nodes BEFORE the full-mode cleanup
        # DELETE (below). The cleanup purges specifier-path stub file nodes (not in
        # tracked_paths), cascade-deleting every imports edge still pointing at them.
        # Running resolution first repoints edges onto the real (tracked) file nodes
        # that survive the DELETE; the orphaned stubs are then cleaned up safely.
        resolve.resolve_file_imports(conn, repo_root)
        if full:
            tracked_paths = [
                rel for _, rel in changed if _is_parseable(rel) and not _ignore.should_skip(rel, skip_dirs)
            ]
            if tracked_paths:
                placeholders = ",".join("?" for _ in tracked_paths)
                conn.execute(
                    "DELETE FROM nodes WHERE repo = ? "
                    "AND kind NOT IN ('package', 'app', 'builtin', 'dependency') "
                    f"AND path IS NOT NULL AND path NOT IN ({placeholders})",
                    (repo_uri_val, *tracked_paths),
                )
            else:
                conn.execute(
                    "DELETE FROM nodes WHERE repo = ? "
                    "AND kind NOT IN ('package', 'app', 'builtin', 'dependency') "
                    "AND path IS NOT NULL",
                    (repo_uri_val,),
                )
        # Deferred imports to avoid the structural_nodes / entry_points /
        # test_suites -> update -> ... cycle (each reuses update._git /
        # NotInGitRepoError or imports from structural_nodes which imports from
        # update).
        from code_graph_io import (
            agent_plugins,
            entry_points,
            structural_nodes,
            test_suites,
        )

        structural_nodes.emit(conn, repo_root=repo_root, ctx=ctx, skip_dirs=skip_dirs)
        agent_plugins.emit(conn, repo_root=repo_root, ctx=ctx, skip_dirs=skip_dirs)
        entry_points.emit(conn, repo_root=repo_root, ctx=ctx, skip_dirs=skip_dirs)
        test_suites.emit(conn, repo_root=repo_root, ctx=ctx, skip_dirs=skip_dirs)
        resolve.sweep_skip_dir_files(conn, skip_dirs)
        # Repo stamp: claim every node this member produced that isn't already
        # owned. builtin / dependency nodes stay global (repo NULL).
        conn.execute(
            "UPDATE nodes SET repo = ? WHERE repo IS NULL AND kind NOT IN ('builtin', 'dependency')",
            (repo_uri_val,),
        )
        _set_metadata(conn, commit_key, head)
    finally:
        upsert.set_current_repo(conn, None)


def run(repo_root: Path, *, workspace: Path, full: bool = False, lock_timeout_ms: int | None = None) -> None:
    """Single-repo update — delegates to `run_workspace` with one member.

    `workspace` is always supplied by the caller: discovering it (from an
    env var, a manifest, or a directory walk) is the caller's job, not this
    package's. `update` is the bootstrap path that creates the graph DB
    before any manifest may exist, so it must not require one.
    """
    repo_root = Path(repo_root).resolve()
    workspace = Path(workspace).resolve()
    run_workspace([repo_root], workspace=workspace, full=full, lock_timeout_ms=lock_timeout_ms)


def run_workspace(
    members: list[Path], *, workspace: Path, full: bool = False, lock_timeout_ms: int | None = None
) -> None:
    """Update the code graph for one or more member repos into one DB.

    Each member runs the per-repo pipeline (`_update_one_repo`) in sequence
    inside one SQLite transaction; nodes are stamped with the producing
    member's `repo:` URI. After the member loop, cross-repo internal-package
    edges collected in `deferred` are emitted, then the global resolve /
    strict-tree / workspace-metadata steps run once.
    """
    members = [Path(m).resolve() for m in members]
    workspace = Path(workspace).resolve()
    db_path = graph_dir(workspace) / "code.db"
    if db_path.exists():
        found = _read_schema_version_or_none(db_path)
        if found != str(schema.SCHEMA_VERSION):
            if full:
                print(
                    f"Schema v{found} detected — rebuilding code.db at schema v{schema.SCHEMA_VERSION}.",
                    file=sys.stderr,
                )
                _unlink_db_files(db_path)
            else:
                raise store.SchemaMismatchError(found=found, expected=schema.SCHEMA_VERSION)
    if lock_timeout_ms is None:
        lock_timeout_ms = _default_lock_timeout()
    conn = None
    try:
        try:
            conn = store.connect(db_path, create=True, busy_timeout_ms=lock_timeout_ms)
            _ensure_gitignore(workspace)
            stored_deriver = _get_metadata(conn, "deriver_version")
            db_nonempty = stored_deriver is not None
            if db_nonempty and stored_deriver != str(schema.DERIVER_VERSION):
                print(
                    f"Deriver logic changed (deriver_version {stored_deriver} → {schema.DERIVER_VERSION})"
                    " — forcing full rebuild.",
                    file=sys.stderr,
                )
                full = True
            global_workspace = packages.build_workspace_index(members)
            deferred: list[packages.CrossRepoLink] = []
            with store.transaction(conn):
                for repo_root in members:
                    _update_one_repo(
                        conn,
                        repo_root,
                        workspace,
                        full=full,
                        global_workspace=global_workspace,
                        deferred=deferred,
                    )
                packages.link_cross_repo_packages(conn, deferred)
                resolve.sweep(conn)
                _enforce_strict_tree_invariant(conn)
                # Single-repo back-compat: mirror the lone member's per-repo
                # commit into the legacy unscoped `last_indexed_commit` key that
                # downstream tooling (and the existing test suite) reads. Multi-
                # repo workspaces have no single HEAD, so the key is left unset.
                if len(members) == 1:
                    from code_graph_io.repo_context import repo_context

                    only_uri = repo_uri(repo_context(members[0]))
                    only_commit = _get_metadata(conn, f"last_indexed_commit:{only_uri}")
                    if only_commit is not None:
                        _set_metadata(conn, "last_indexed_commit", only_commit)
                _set_metadata(conn, "last_indexed_at", _dt.datetime.now(_dt.UTC).isoformat())
                _set_metadata(conn, "deriver_version", str(schema.DERIVER_VERSION))
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower():
                raise UpdateInProgressError(
                    "another `gw graph update` appears to be in progress "
                    f"(SQLite write lock held longer than {lock_timeout_ms}ms)"
                ) from exc
            raise
    finally:
        if conn is not None:
            # Defense-in-depth: the per-member finally already clears this, but
            # guard against id(conn) recycling before the connection is closed.
            upsert.set_current_repo(conn, None)
            conn.close()
