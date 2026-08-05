"""Post-upsert sweep: resolve placeholder-dst edges by joining (kind, name)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from code_graph_io._ignore import should_skip

# JS/TS extensions used to pick the JS vs python resolver in
# resolve_file_imports. Mirrors import_scan._JS_EXTENSIONS; kept as a local
# tuple so this module avoids a top-level import_scan import (import_scan ->
# structural_nodes -> update -> resolve would be a cycle). import_scan itself
# is imported lazily inside resolve_file_imports.
_JS_EXTENSIONS = (".ts", ".js", ".tsx", ".jsx", ".mjs", ".cjs")

# Placeholder kinds eligible for the conservative single-candidate cross-kind
# fallback. Restricted to the code-symbol placeholders emitted by the
# graph projection for unresolved call/export refs — NOT file-import stubs
# (those are handled by resolve_file_imports).
_CROSS_KIND_RESOLVABLE = frozenset({"function", "method", "class", "type"})
_UNRESOLVED_SYMBOL_KIND = "unresolved_symbol"


def _set_resolution(attrs_json: str | None, resolution: str) -> str:
    attrs: dict[str, object] = json.loads(attrs_json) if attrs_json else {}
    attrs["resolution"] = resolution
    return json.dumps(attrs, sort_keys=True)


def resolve_file_imports(conn: sqlite3.Connection, repo_root: Path) -> None:
    """Resolve `imports` edges from raw-specifier stub nodes; drop unresolvable ones.

    The graph projection emits each import ref as dst=('file', target_name,
    raw_specifier), which materialises a `file` node with path=raw_specifier and
    uri IS NULL — a "specifier stub" (both `from X import y` and plain `import X`).
    `sweep` only reconciles path-IS-NULL placeholders, so these stubs (non-null
    path) survive untouched. This pass handles them:

      - exactly one real file node at the resolved path  -> repoint edge,
        resolution="exact"
      - multiple real file nodes at the resolved path     -> fan out,
        resolution="ambiguous"
      - no in-repo file (external / third-party / stdlib) -> DELETE the edge:
        the stub is then orphaned and removed by the cleanup below. stdlib usage
        is still recorded by builtins.refresh as kind='builtin' + used_by edges,
        so no information is lost.

    Real files are identified by (repo_root / specifier).is_file() and skipped —
    NOT by name != path or uri IS NULL, both of which a real file can match at
    this stage of update.run (uri is attached later by structural_nodes.emit).

    After processing, specifier stubs left unreferenced (no inbound edge) are
    deleted. Runs inside update's open transaction — does not open a new
    connection.
    """
    from code_graph_io import import_scan  # noqa: PLC0415 — lazy to break import cycle

    repo_root = Path(repo_root)
    # Specifier-stub imports edges: dst is a file node materialised from the
    # edge dst-key ('file', symbol, raw_specifier) — dst.name is the imported
    # symbol and dst.path is the raw specifier. This includes BOTH from-imports
    # (name != path, e.g. ('file','contextmanager','contextlib')) AND plain
    # imports (name == path, e.g. ('file','os','os')).
    #
    # We cannot key on uri IS NULL alone, nor on name != path: at this point in
    # update.run, structural_nodes.emit has not yet attached file: URIs, so every
    # REAL file node also transiently has uri IS NULL, and plain-import stubs have
    # name == path just like real files. The reliable discriminator is the
    # working tree — a stub's path is a bare module specifier ("os", "contextlib")
    # with no corresponding file, whereas a real file's path is an on-disk file.
    # The loop below skips any dst whose path is a real file (see is_file() check).
    #
    # dst.name (the imported symbol) is preserved in edge attrs so per-symbol
    # information is not lost once the edge points at the (multi-symbol) file.
    rows = conn.execute(
        "SELECT e.src, e.dst, e.attrs_json, src.path AS importing_path, "
        "dst.path AS specifier, dst.name AS symbol "
        "FROM edges e "
        "JOIN nodes src ON e.src = src.id "
        "JOIN nodes dst ON e.dst = dst.id "
        "WHERE e.kind = 'imports' "
        "AND dst.kind = 'file' AND dst.uri IS NULL AND dst.path IS NOT NULL "
        "AND src.path IS NOT NULL"
    ).fetchall()

    if not rows:
        return

    pkg_rows: list[import_scan.PkgRow] = [
        (r[0], r[1], r[2])
        for r in conn.execute("SELECT name, path, attrs_json FROM nodes WHERE kind IN ('package', 'app')").fetchall()
    ]

    stub_ids: set[int] = set()
    for src_id, stub_id, attrs_json, importing_path, specifier, symbol in rows:
        # Spare real files. A real source file transiently has uri IS NULL here
        # (structural_nodes.emit attaches the uri later), and plain-import stubs
        # share the name == path shape of real files — so probe the working tree.
        # If `specifier` is an actual file under repo_root it is NOT a stub: leave
        # its node and edges untouched (and out of stub_ids, so cleanup spares it).
        if not Path(specifier).is_absolute() and (repo_root / specifier).is_file():
            # An ABSOLUTE specifier would make `repo_root / specifier` discard
            # repo_root (pathlib) and probe the host path — never treat that as a
            # repo file; let it fall through to resolution (and get dropped).
            continue
        stub_ids.add(stub_id)
        importing_file = repo_root / importing_path
        if Path(importing_path).suffix in _JS_EXTENSIONS:
            rel = import_scan.resolve_js_import_file(specifier, importing_file, repo_root)
        else:
            rel = import_scan.resolve_python_import_file(specifier, repo_root, pkg_rows)

        if rel is None:
            # External / third-party / stdlib specifier with no in-repo file.
            # Drop the edge entirely instead of parking it on the stub as
            # resolution="unresolved". The orphaned stub is removed by the
            # cleanup at the end of this function, so external imports never leave
            # a kind='file' node behind. (stdlib usage is still recorded by
            # builtins.refresh as kind='builtin' + used_by edges.)
            conn.execute(
                "DELETE FROM edges WHERE src=? AND dst=? AND kind='imports'",
                (src_id, stub_id),
            )
            continue

        real = conn.execute(
            "SELECT id FROM nodes WHERE kind='file' AND path=? AND path IS NOT NULL",
            (rel,),
        ).fetchall()
        if not real:
            # Specifier resolved to a repo-relative path but no file node exists
            # there (untracked / ignored target). Same as external — drop the
            # edge so no stub survives.
            conn.execute(
                "DELETE FROM edges WHERE src=? AND dst=? AND kind='imports'",
                (src_id, stub_id),
            )
            continue

        conn.execute(
            "DELETE FROM edges WHERE src=? AND dst=? AND kind='imports'",
            (src_id, stub_id),
        )
        resolution = "exact" if len(real) == 1 else "ambiguous"
        # Preserve the imported symbol (the old stub's name) on the edge — the
        # real file node carries no per-symbol info.
        repoint_attrs: dict[str, object] = json.loads(attrs_json) if attrs_json else {}
        repoint_attrs["resolution"] = resolution
        if symbol is not None:
            repoint_attrs.setdefault("symbol", symbol)
        repoint_json = json.dumps(repoint_attrs, sort_keys=True)
        for (real_dst,) in real:
            conn.execute(
                "INSERT INTO edges(src, dst, kind, attrs_json) "
                "VALUES (?, ?, 'imports', ?) "
                "ON CONFLICT(src, dst, kind) DO UPDATE SET attrs_json=excluded.attrs_json",
                (src_id, real_dst, repoint_json),
            )

    # Delete specifier stubs now orphaned (no inbound non-containment edge).
    # Scope strictly to the stub ids collected above — never broaden to all
    # NULL-uri files (T-nsr-03), which would delete legitimate placeholders.
    #
    # package.refresh runs before this pass in update.run(), so older/ordering
    # paths can attach package `contains` edges to temporary import-specifier
    # stubs. Those containment edges are not semantic once imports have been
    # resolved/dropped; remove them so the stub can be deleted.
    if stub_ids:
        placeholders = ",".join("?" for _ in stub_ids)
        conn.execute(
            f"DELETE FROM edges WHERE kind='contains' AND dst IN ({placeholders})",
            list(stub_ids),
        )
        conn.execute(
            f"DELETE FROM nodes WHERE id IN ({placeholders}) AND uri IS NULL AND id NOT IN (SELECT dst FROM edges)",
            list(stub_ids),
        )


def sweep(conn: sqlite3.Connection) -> None:
    """Resolve every edge whose dst points at a placeholder node (path IS NULL)."""
    placeholder_edges = conn.execute(
        "SELECT e.src, e.dst, e.kind, e.attrs_json, n.kind, n.name "
        "FROM edges e JOIN nodes n ON e.dst=n.id "
        "WHERE n.path IS NULL"
    ).fetchall()

    for src, old_dst, edge_kind, attrs_json, node_kind, node_name in placeholder_edges:
        edge_attrs: dict[str, object] = json.loads(attrs_json) if attrs_json else {}
        symbol_kind = edge_attrs.get("symbol_kind") if node_kind == _UNRESOLVED_SYMBOL_KIND else node_kind
        if not isinstance(symbol_kind, str):
            symbol_kind = node_kind

        matches = conn.execute(
            "SELECT id FROM nodes WHERE kind=? AND name=? AND path IS NOT NULL",
            (symbol_kind, node_name),
        ).fetchall()

        if not matches:
            # conservative cross-kind fallback: a same-kind match found
            # nothing. For code-kind placeholders (including the explicit
            # unresolved_symbol nodes emitted for call/export refs):
            #
            # Tier 2 — cross-kind EXACT name. Resolve ONLY when exactly 1
            # candidate. Preserves the historical single-candidate cross-kind
            # resolution AND the 2+-collision guard (bare colliding names like
            # get/render stay unresolved). A qualified self/this target
            # (Foo.helper) emitted pre-qualified by the projection matches its
            # method node here exactly — precision win even when Bar.helper
            # exists.
            #
            # Tier 3 — suffix fan-out. When tier 2 finds ZERO exact cross-kind
            # candidates, a bare leaf call (save) reaches every qualified method
            # node (*.save) via LIKE '%.<leaf>'. Runs ONLY when no exact name
            # match exists, so it never overrides the collision guard. When
            # multiple qualified methods share the leaf, all are resolved as
            # "ambiguous" (same-file collision promoted from unresolved →
            # ambiguous, which is the correct post-qualification behavior).
            # Note: LIKE '%.x' cannot use the (kind, name) index (leading
            # wildcard); acceptable at single-repo graph scale.
            if symbol_kind in _CROSS_KIND_RESOLVABLE:
                # Leaf = trailing bare segment (Foo.save → save; save → save).
                leaf = node_name.rsplit(".", 1)[-1]

                cross = conn.execute(
                    "SELECT id FROM nodes WHERE kind IN ('function', 'method', 'class', 'type') "
                    "AND name=? AND path IS NOT NULL",
                    (node_name,),
                ).fetchall()
                if len(cross) == 1:
                    # Tier 2: unique exact name match (including qualified targets).
                    matches = cross
                elif not cross:
                    # Tier 3: suffix fan-out — only when no exact match exists.
                    matches = conn.execute(
                        "SELECT id FROM nodes WHERE kind IN ('function', 'method', 'class', 'type') "
                        "AND name LIKE '%.' || ? AND path IS NOT NULL",
                        (leaf,),
                    ).fetchall()
                # cross has 2+ exact candidates: tier 2 collision guard fires →
                # matches stays empty → falls through to unresolved below.
            if not matches:
                new_attrs = _set_resolution(attrs_json, "unresolved")
                conn.execute(
                    "UPDATE edges SET attrs_json=? WHERE src=? AND dst=? AND kind=?",
                    (new_attrs, src, old_dst, edge_kind),
                )
                continue

        conn.execute(
            "DELETE FROM edges WHERE src=? AND dst=? AND kind=?",
            (src, old_dst, edge_kind),
        )
        resolution = "exact" if len(matches) == 1 else "ambiguous"
        for (real_dst,) in matches:
            new_attrs = _set_resolution(attrs_json, resolution)
            conn.execute(
                "INSERT INTO edges(src, dst, kind, attrs_json) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(src, dst, kind) DO UPDATE SET attrs_json=excluded.attrs_json",
                (src, real_dst, edge_kind, new_attrs),
            )

    # Delete placeholder nodes that were successfully resolved — their edges now point
    # at real nodes, so these stubs are unreferenced and would otherwise appear as
    # spurious path=None hits in `gw graph find` and similar queries.
    # * spare URI-bearing structural nodes (Repository,
    # Domain, TestSuite, EntryPoint) — they have no path but are not orphans.
    conn.execute(
        "DELETE FROM nodes WHERE path IS NULL AND uri IS NULL AND kind != 'package' "
        "AND id NOT IN (SELECT dst FROM edges)"
    )


def sweep_skip_dir_files(conn: sqlite3.Connection, skip_dirs: frozenset[str]) -> None:
    """Delete file nodes that are skip-dir build artifacts (uri IS NULL, path in skip-dir).

    Targets nodes with kind='file', uri IS NULL, path IS NOT NULL whose path has a
    component in skip_dirs (e.g. dist/, build/, node_modules/).  These are import-edge
    endpoints materialised by _ensure_node/_upsert_edge that bypassed the walk's skip-dir
    filter.  After deleting the file nodes, any edges left orphaned (src or dst no longer
    present) are removed.

    Scope is intentionally narrow: only kind='file' AND uri IS NULL AND skip-dir component.
    URI-bearing nodes, non-file nodes, and NULL-uri files outside skip-dirs are untouched.
    """
    candidates = conn.execute(
        "SELECT id, path FROM nodes WHERE kind = 'file' AND uri IS NULL AND path IS NOT NULL"
    ).fetchall()

    to_delete = [node_id for node_id, path in candidates if should_skip(path, skip_dirs)]

    if not to_delete:
        return

    placeholders = ",".join("?" for _ in to_delete)
    conn.execute(f"DELETE FROM nodes WHERE id IN ({placeholders})", to_delete)

    # Remove edges orphaned by the node deletions (ON DELETE CASCADE is not
    # guaranteed to fire for all SQLite builds, so we do this explicitly).
    conn.execute("DELETE FROM edges WHERE src NOT IN (SELECT id FROM nodes) OR dst NOT IN (SELECT id FROM nodes)")
