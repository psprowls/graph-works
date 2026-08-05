"""Builtin classification + emission: stdlib imports → kind:builtin nodes + used_by edges.

Scans every package's source files for imports of Python / Node.js standard-library
modules and emits one Builtin node per (language, module_name) pair plus one `used_by`
edge per (package, builtin) carrying the sorted union of named imports in `attrs_json`.

Decision references
-------------------
- Python stdlib source is `sys.stdlib_module_names` at the *scanner runtime*.
  Drift across Python versions is accepted — no ``requires-python`` parsing.
- Node builtins are cached per graph directory at
  ``<graph_dir>/cache/node-builtins-<major>.json``.
- When ``node`` is missing AND no cache exists, JS Builtin emission is silently
  skipped with zero exceptions and zero stderr output.
- Top-level module only — ``from os.path import join`` → ``builtin:python/os``.
- ``node:fs/promises`` → ``builtin:javascript/fs``.
- Module-level edges only — no Function/Symbol nodes for stdlib calls.
- Edge ``attrs_json.imported_symbols`` is the sorted union of all named imports
  seen across the package.
- One ``used_by`` edge per (package, builtin) — file-level granularity not kept.

Assumption: Multi-line ``from x import (\\n    a,\\n    b,\\n)`` may produce
an incomplete ``imported_symbols`` list because the single-line regex cannot capture the
continuation. The ``used_by`` edge itself is always correct — only the symbol list may
be partial. AST migration is flagged as a deferred improvement.
"""

from __future__ import annotations

import contextlib
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

from code_parser.projections.graph import GraphEdge, GraphNode

from code_graph_io import _ignore, upsert
from code_graph_io.records import as_graph_records
from code_graph_io.uri import RepoContext, builtin_uri, repo_uri

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Python stdlib as reported by the scanner runtime.
_PYTHON_STDLIB: frozenset[str] = sys.stdlib_module_names

_NODE_SUBPROCESS_TIMEOUT_S = 5

# Regex for Python `from X import a, b` — captures the post-import clause.
# Only matches single-line `from X import ...`; multi-line paren imports are
# captured partially (Assumption).
_PYTHON_FROM_IMPORT_RE = re.compile(r"^\s*from\s+[\w\.]+\s+import\s+([^#\n\\(]+)", re.MULTILINE)
# Module spec capture — same as import_scan._PYTHON_IMPORT_RE
_PYTHON_IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+([\w\.]+)", re.MULTILINE)

# JS import capture — same as import_scan._JS_IMPORT_RE
_JS_IMPORT_RE = re.compile(r"""(?:from|import|require)\s*\(?\s*['"]([^'"]+)['"]""")
# JS named import capture: `import { a, b } from "x"` — captures the brace clause.
_JS_NAMED_IMPORT_RE = re.compile(r"""import\s*\{([^}]+)\}\s+from\s*['"][^'"]+['"]""")

# JS file extensions — mirrors import_scan._SCAN_EXTENSIONS_JS.
# Defined inline to avoid the circular import chain:
#   update → builtins → import_scan → structural_nodes → update
_SCAN_EXTENSIONS_JS: frozenset[str] = frozenset((".ts", ".js", ".tsx", ".jsx", ".mjs", ".cjs"))


# ---------------------------------------------------------------------------
# Python stdlib helpers
# ---------------------------------------------------------------------------


def _is_python_stdlib(module_str: str) -> bool:
    """Return True if the top-level segment is a Python stdlib module"""
    top = module_str.split(".", 1)[0]
    return top in _PYTHON_STDLIB


# ---------------------------------------------------------------------------
# Node spec normalisation
# ---------------------------------------------------------------------------


def _normalize_node_spec(spec: str) -> str:
    """Collapse ``node:`` prefix and subpath to the bare module name.

    Examples::

        _normalize_node_spec("node:fs/promises") == "fs"
        _normalize_node_spec("fs")               == "fs"
        _normalize_node_spec("node:test")        == "test"
    """
    s = spec[5:] if spec.startswith("node:") else spec
    return s.split("/", 1)[0]


# ---------------------------------------------------------------------------
# Node major detection
# ---------------------------------------------------------------------------


def _node_major() -> str | None:
    """Return the Node.js major version string (e.g. ``"20"``), or None on failure.

    Silently returns None if ``node`` is absent, times out, or exits non-zero.
    """
    try:
        out = subprocess.run(
            ["node", "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=_NODE_SUBPROCESS_TIMEOUT_S,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    v = out.stdout.strip().lstrip("v")
    if not v:
        return None
    return v.split(".", 1)[0]


# ---------------------------------------------------------------------------
# Node builtins harvest
# ---------------------------------------------------------------------------


def _harvest_node_builtins() -> list[str] | None:
    """Shell out to Node to get the full ``require('module').builtinModules`` list.

    Returns the parsed JSON list on success, or None on any failure.
    """
    try:
        out = subprocess.run(
            [
                "node",
                "-e",
                'console.log(JSON.stringify(require("module").builtinModules))',
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=_NODE_SUBPROCESS_TIMEOUT_S,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    try:
        data = json.loads(out.stdout)
    except json.JSONDecodeError:
        return None
    # A non-list payload means Node printed something we don't understand;
    # that is the "any failure" case the docstring promises returns None.
    return [str(m) for m in data] if isinstance(data, list) else None


# ---------------------------------------------------------------------------
# Node builtins loader (with disk cache)
# ---------------------------------------------------------------------------


def _load_node_builtins(cache_dir: Path) -> frozenset[str]:
    """Load Node built-in module names, using a per-major disk cache.

    Steps:
    1. Detect Node major via ``node --version``.  If unavailable → return empty.
    2. Check ``cache_dir / "node-builtins-<major>.json"``.  Cache hit → return.
    3. Cache miss → harvest via subprocess.  Failure → return empty.
    4. Write cache (best-effort; OSError silently ignored).
    """
    major = _node_major()
    if major is None:
        return frozenset()

    cache_file = cache_dir / f"node-builtins-{major}.json"
    if cache_file.exists():
        try:
            return frozenset(json.loads(cache_file.read_text()))
        except (OSError, json.JSONDecodeError):
            pass  # fall through to re-harvest

    harvested = _harvest_node_builtins()
    if harvested is None:
        return frozenset()

    cache_dir.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):  # best-effort — never raise
        cache_file.write_text(json.dumps(harvested))
    return frozenset(harvested)


# ---------------------------------------------------------------------------
# Symbol extraction helpers
# ---------------------------------------------------------------------------


def _extract_python_symbols(line: str) -> list[str]:
    """Return named import tokens from a ``from X import a, b as c`` line.

    Strips ``as alias`` suffixes and parentheses.  Returns ``[]`` for plain
    ``import X`` statements (no symbol list applies).
    """
    m = _PYTHON_FROM_IMPORT_RE.search(line)
    if m is None:
        return []
    clause = m.group(1)
    tokens = []
    for raw in clause.split(","):
        token = raw.strip().rstrip(",").strip("()")
        # Drop `as alias` suffix
        token = re.sub(r"\s+as\s+\w+$", "", token).strip()
        if token and token.isidentifier():
            tokens.append(token)
    return tokens


def _extract_js_symbols(line: str) -> list[str]:
    """Return named import tokens from ``import { a, b } from "X"`` lines."""
    m = _JS_NAMED_IMPORT_RE.search(line)
    if m is None:
        return []
    tokens = []
    for raw in m.group(1).split(","):
        token = raw.strip()
        # Drop `as alias`
        token = re.sub(r"\s+as\s+\w+$", "", token).strip()
        if token and re.match(r"^\w+$", token):
            tokens.append(token)
    return tokens


# ---------------------------------------------------------------------------
# Per-package scanner
# ---------------------------------------------------------------------------


def _scan_package(
    repo_root: Path,
    pkg_name: str,
    file_rels: list[str],
    node_builtins: frozenset[str],
) -> dict[tuple[str, str], set[str]]:
    """Scan a list of file paths for stdlib imports within a single package.

    Returns an accumulator ``{(language, module_name): set[str]}`` containing
    the union of imported symbols seen across all files.
    """
    acc: dict[tuple[str, str], set[str]] = {}

    for rel in file_rels:
        fpath = repo_root / rel
        try:
            content = fpath.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        suffix = Path(rel).suffix

        if suffix == ".py":
            for m in _PYTHON_IMPORT_RE.finditer(content):
                module_str = m.group(1)
                top = module_str.split(".", 1)[0]
                if not _is_python_stdlib(top):
                    continue
                key = ("python", top)
                if key not in acc:
                    acc[key] = set()
                # Capture symbols from the matched line
                line_start = content.rfind("\n", 0, m.start()) + 1
                line_end = content.find("\n", m.end())
                if line_end == -1:
                    line_end = len(content)
                line = content[line_start:line_end]
                acc[key].update(_extract_python_symbols(line))

        elif suffix in _SCAN_EXTENSIONS_JS:
            for m in _JS_IMPORT_RE.finditer(content):
                spec = m.group(1)
                normalized = _normalize_node_spec(spec)
                # Only classify as builtin if it's in the Node builtins list.
                # Also match the normalized form (some lists use "node:X" form).
                if normalized not in node_builtins and spec not in node_builtins:
                    continue
                key = ("javascript", normalized)
                if key not in acc:
                    acc[key] = set()
                # Capture named imports from the matched line
                line_start = content.rfind("\n", 0, m.start()) + 1
                line_end = content.find("\n", m.end())
                if line_end == -1:
                    line_end = len(content)
                line = content[line_start:line_end]
                acc[key].update(_extract_js_symbols(line))

    return acc


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def refresh(
    conn: sqlite3.Connection,
    *,
    repo_root: Path,
    graph_dir: Path,
    ctx: RepoContext,
) -> None:
    """Scan package files for stdlib imports and emit Builtin nodes + ``used_by`` edges.

    Must be called inside an existing ``store.transaction(conn)`` block.
    The caller (``update.run``) owns the transaction — this function must NOT open
    a new one.

    Silent on all external-tool failures: absent ``node``, subprocess
    timeout, cache write failure, and unreadable files are all silently skipped.
    """
    repo_root = Path(repo_root).resolve()
    graph_dir = Path(graph_dir).resolve()
    skip_dirs = _ignore.load_skip_dirs(repo_root)

    # Load all Package/App rows from the graph (written by packages.refresh before us).
    # * apps also import builtins; include them.
    # Multi-repo: scope to this member (see structural_nodes.emit).
    member_repo = repo_uri(ctx)
    pkg_rows = conn.execute(
        "SELECT name, path, attrs_json, kind FROM nodes WHERE kind IN ('package', 'app') "
        "AND (repo = ? OR repo IS NULL)",
        (member_repo,),
    ).fetchall()
    if not pkg_rows:
        return

    # Load Node builtins once per refresh() call.
    node_builtins = _load_node_builtins(graph_dir / "cache")

    # Accumulator: (pkg_name, language, module_name) -> set[str] of imported symbols.
    edge_acc: dict[tuple[str, str, str], set[str]] = {}
    # Track unique (language, module_name) pairs for node emission.
    node_keys: set[tuple[str, str]] = set()
    # Map pkg_name -> (pkg_rel, pkg_kind) for edge src construction.
    pkg_rel_map: dict[str, str | None] = {}
    pkg_kind_map: dict[str, str] = {}

    for pkg_name, pkg_rel, _attrs_json, pkg_kind in pkg_rows:
        pkg_rel_map[pkg_name] = pkg_rel
        pkg_kind_map[pkg_name] = pkg_kind

        # Collect file paths under this package's directory prefix.
        if pkg_rel:
            like = pkg_rel + "/%"
            file_rows = conn.execute(
                "SELECT path, attrs_json FROM nodes WHERE kind='file' AND path LIKE ?",
                (like,),
            ).fetchall()
        else:
            file_rows = conn.execute("SELECT path, attrs_json FROM nodes WHERE kind='file'").fetchall()

        file_rels: list[str] = []
        for file_path, file_attrs_json in file_rows:
            # Skip test files (mirror import_scan.scan_package_imports include_test_files=False).
            if file_attrs_json:
                try:
                    file_attrs = json.loads(file_attrs_json)
                    if file_attrs.get("is_test"):
                        continue
                except (json.JSONDecodeError, TypeError):
                    pass
            # Skip ignored directories.
            if _ignore.should_skip(file_path, skip_dirs):
                continue
            file_rels.append(file_path)

        if not file_rels:
            continue

        pkg_acc = _scan_package(repo_root, pkg_name, file_rels, node_builtins)
        for (lang, module_name), symbols in pkg_acc.items():
            node_keys.add((lang, module_name))
            edge_key = (pkg_name, lang, module_name)
            if edge_key not in edge_acc:
                edge_acc[edge_key] = set()
            edge_acc[edge_key].update(symbols)

    if not node_keys and not edge_acc:
        return

    # Emit one Builtin node per (language, module_name).
    # path=lang is used as the upsert key discriminator so that
    # "python/os" and "javascript/os" can coexist in the same DB
    # (otherwise both would have key (kind='builtin', name='os', path=None)
    # and the second upsert would silently overwrite the first).
    builtin_nodes: list[GraphNode] = [
        GraphNode(
            kind="builtin",
            name=module_name,
            path=lang,
            line=None,
            attrs={
                "uri": builtin_uri(lang, module_name),
                "language": lang,
                "module_name": module_name,
            },
        )
        for (lang, module_name) in sorted(node_keys)
    ]

    # Emit one `used_by` edge per (package/app, builtin) with the symbol union.
    # * src uses the consumer's actual kind so App consumers
    # resolve correctly.
    builtin_edges: list[GraphEdge] = [
        GraphEdge(
            src=(
                pkg_kind_map.get(pkg_name, "package"),
                pkg_name,
                pkg_rel_map.get(pkg_name),
            ),
            dst=("builtin", module_name, lang),
            kind="used_by",
            attrs={"imported_symbols": sorted(symbols)},
        )
        for (pkg_name, lang, module_name), symbols in sorted(edge_acc.items())
    ]

    upsert.upsert_records(conn, as_graph_records(nodes=builtin_nodes, edges=builtin_edges))
