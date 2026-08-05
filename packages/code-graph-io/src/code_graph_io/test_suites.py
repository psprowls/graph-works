"""TestSuite emitter: TestSuite nodes + physically_contains re-parenting + tests edges.

Discovers test root directories from filesystem layout (conventional
tests/, __tests__/ and package-local equivalents) and framework config
(pyproject [tool.pytest.ini_options] testpaths, pytest.ini, jest/vitest
'roots'). Emits one TestSuite per root, re-parents every is_test=true
File node from Repository to its suite, and derives tests edges from
import scans of the test files.

Suites are flat: tests/integration/auth/ produces ONE
suite named tests/integration/auth/, not nested suites.

The emitter produces only TestSuite -> Package and TestSuite -> Repository
edges.
"""

from __future__ import annotations

import fnmatch
import json
import re
import sqlite3
import sys
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from code_parser.projections.graph import GraphEdge, GraphNode

from code_graph_io import _ignore, upsert
from code_graph_io.import_scan import scan_files_imports
from code_graph_io.records import as_graph_records
from code_graph_io.structural_nodes import (
    _owning_package,
)
from code_graph_io.uri import RepoContext, repo_uri, test_suite_uri

# --- Module-private constants ---

_REPOSITORY_EDGE_THRESHOLD = 5

_SPEC_FILENAME_GLOBS = ("*_spec.py", "*_spec.js", "*_spec.ts")
_UNIT_FILENAME_GLOBS = (
    "test_*.py",
    "*_test.py",
    "*.test.js",
    "*.test.ts",
    "*.test.jsx",
    "*.test.tsx",
    "*_test.js",
    "*_test.ts",
)

_PYTEST_INI_FILENAMES = frozenset({"pytest.ini"})
_JS_TEST_CONFIG_GLOBS = ("jest.config.*", "vitest.config.*")


@dataclass(frozen=True)
class _TestRoot:
    rel_path: str  # e.g. "tests/integration" or "packages/foo/tests"
    owner_kind: str  # "repository" or "package"
    owner_name: str | None  # pkg_name when owner_kind=='package'
    owner_pkg_rel: str | None  # pkg_rel when owner_kind=='package'
    language: str | None  # 'python'|'javascript'|'typescript'|None


# --- Helpers ---


def _build_pkg_index(
    pkg_rows: list[tuple[str, str | None, str | None]],
) -> list[tuple[str, str, str | None]]:
    """Build the (pkg_prefix, pkg_name, pkg_rel) index sorted deepest-first
    for _owning_package consumption."""
    return sorted(
        ((pkg_rel or "", pkg_name, pkg_rel) for pkg_name, pkg_rel, _ in pkg_rows),
        key=lambda t: len(t[0]),
        reverse=True,
    )


def _read_pytest_testpaths(pkg_dir: Path) -> Iterable[list[str]]:
    """Yield testpaths lists from pyproject.toml and pytest.ini if present."""
    pp = pkg_dir / "pyproject.toml"
    if pp.exists():
        try:
            data = tomllib.loads(pp.read_text(encoding="utf-8"))
            testpaths = data.get("tool", {}).get("pytest", {}).get("ini_options", {}).get("testpaths")
            if isinstance(testpaths, list):
                yield [str(x) for x in testpaths if isinstance(x, str)]
            elif isinstance(testpaths, str):
                yield testpaths.split()
        except tomllib.TOMLDecodeError as exc:
            print(f"[test_suites] warning: {pp} malformed: {exc}", file=sys.stderr)

    ini = pkg_dir / "pytest.ini"
    if ini.exists():
        try:
            text = ini.read_text(encoding="utf-8")
            m = re.search(r"^\s*testpaths\s*=\s*(.+)$", text, re.MULTILINE)
            if m:
                yield m.group(1).split()
        except OSError as exc:
            print(f"[test_suites] warning: {ini} unreadable: {exc}", file=sys.stderr)


def _classify_suite_kind(suite_rel: str, file_rels: list[str]) -> str:
    """kind classification: dir-name precedence then filename fallback."""
    parts = suite_rel.lower().split("/")
    for p in parts:
        if "integration" in p:
            return "integration"
        if p in {"e2e", "system"}:
            return "e2e"
        if "contract" in p:
            return "contract"

    has_spec = any(fnmatch.fnmatch(Path(f).name, g) for f in file_rels for g in _SPEC_FILENAME_GLOBS)
    if has_spec:
        return "contract"

    has_unit = any(fnmatch.fnmatch(Path(f).name, g) for f in file_rels for g in _UNIT_FILENAME_GLOBS)
    if has_unit:
        return "unit"
    return "unknown"


def _discover_test_roots(
    repo_root: Path,
    skip_dirs: frozenset[str],
    pkg_rows: list[tuple[str, str | None, str | None]],
) -> list[_TestRoot]:
    """Discover conventional + config-declared test root directories.

    Conventional (filesystem-driven):
      - repo-root tests/: if it has subdirs, each subdir is a root; otherwise
        if it holds files directly, tests/ itself is one root .
      - Each Package's <pkg>/tests/ and <pkg>/__tests__/ if present.

    Config-driven:
      - pyproject [tool.pytest.ini_options] testpaths -> additional roots
        relative to the Package directory.
      - pytest.ini [pytest] testpaths via single-line regex.
    """
    roots: list[_TestRoot] = []
    seen: set[str] = set()

    def _add(
        rel: str,
        owner_kind: str,
        *,
        owner_name: str | None = None,
        owner_pkg_rel: str | None = None,
        language: str | None = None,
    ) -> None:
        if rel in seen:
            return
        if _ignore.should_skip(rel, skip_dirs):
            return
        seen.add(rel)
        roots.append(
            _TestRoot(
                rel_path=rel,
                owner_kind=owner_kind,
                owner_name=owner_name,
                owner_pkg_rel=owner_pkg_rel,
                language=language,
            )
        )

    # Repo-root tests
    repo_tests = repo_root / "tests"
    if repo_tests.is_dir():
        subdirs = [d for d in repo_tests.iterdir() if d.is_dir() and not _ignore.should_skip(d.name, skip_dirs)]
        if subdirs:
            for sub in sorted(subdirs):
                rel = sub.relative_to(repo_root).as_posix()
                _add(rel, "repository")
        else:
            # Flat: tests/ itself is the suite if it holds files.
            files = [f for f in repo_tests.iterdir() if f.is_file()]
            if files:
                _add("tests", "repository")

    # Package-local tests/ and __tests__/
    for pkg_name, pkg_rel, pkg_attrs_json in pkg_rows:
        pkg_attrs = json.loads(pkg_attrs_json) if pkg_attrs_json else {}
        lang = pkg_attrs.get("language")
        if not pkg_rel:
            # Root Package — already covered by repo-root tests scan above.
            continue
        pkg_dir = repo_root / pkg_rel
        for candidate in ("tests", "__tests__"):
            cdir = pkg_dir / candidate
            if cdir.is_dir():
                rel = cdir.relative_to(repo_root).as_posix()
                _add(
                    rel,
                    "package",
                    owner_name=pkg_name,
                    owner_pkg_rel=pkg_rel,
                    language=lang,
                )

    # Config-driven roots — pyproject testpaths.
    pkg_index = _build_pkg_index(pkg_rows)
    for _pkg_name, pkg_rel, pkg_attrs_json in pkg_rows:
        pkg_attrs = json.loads(pkg_attrs_json) if pkg_attrs_json else {}
        lang = pkg_attrs.get("language")
        pkg_dir = (repo_root / pkg_rel) if pkg_rel else repo_root
        if lang == "python":
            for cfg_paths in _read_pytest_testpaths(pkg_dir):
                for tp_rel in cfg_paths:
                    full = (pkg_dir / tp_rel).resolve()
                    try:
                        rel = full.relative_to(repo_root).as_posix()
                    except ValueError:
                        continue
                    if not full.is_dir():
                        continue
                    owner = _owning_package(rel, pkg_index)
                    if owner is None:
                        _add(rel, "repository", language="python")
                    else:
                        own_name, own_rel = owner
                        _add(
                            rel,
                            "package",
                            owner_name=own_name,
                            owner_pkg_rel=own_rel,
                            language="python",
                        )

    return roots


# --- Public emit() ---


def emit(
    conn: sqlite3.Connection,
    *,
    repo_root: Path,
    ctx: RepoContext,
    skip_dirs: frozenset[str],
) -> None:
    """Emit TestSuite nodes + physically_contains re-parenting + tests edges."""
    repo_root = Path(repo_root).resolve()

    # include both package and app nodes — apps are tested
    # the same way packages are.
    # Multi-repo: scope to this member (see structural_nodes.emit).
    member_repo = repo_uri(ctx)
    pkg_rows_raw = conn.execute(
        "SELECT name, path, attrs_json, kind FROM nodes WHERE kind IN ('package', 'app') "
        "AND (repo = ? OR repo IS NULL)",
        (member_repo,),
    ).fetchall()
    pkg_rows: list[tuple[str, str | None, str | None]] = [(r[0], r[1], r[2]) for r in pkg_rows_raw]
    # Side-table mapping pkg name -> kind so the tests-edge dst tuple uses
    # the right kind.
    pkg_kind_map: dict[str, str] = {r[0]: r[3] for r in pkg_rows_raw}

    roots = _discover_test_roots(repo_root, skip_dirs, pkg_rows)

    # Map each TestRoot's rel_path -> list of test File rel-paths it owns.
    root_files: dict[str, list[str]] = {r.rel_path: [] for r in roots}

    test_file_rows = conn.execute(
        "SELECT id, path FROM nodes WHERE kind='file' AND json_extract(attrs_json, '$.is_test') = 1 "
        "AND (repo = ? OR repo IS NULL)",
        (member_repo,),
    ).fetchall()

    def _assign_root(file_rel: str) -> _TestRoot | None:
        # cascade: deepest-prefix match wins.
        best: _TestRoot | None = None
        best_len = -1
        for r in roots:
            if (file_rel == r.rel_path or file_rel.startswith(r.rel_path + "/")) and len(r.rel_path) > best_len:
                best_len = len(r.rel_path)
                best = r
        return best

    for _file_id, file_rel in test_file_rows:
        r = _assign_root(file_rel)
        if r is None:
            print(
                f"[test_suites] warning: test file {file_rel} not under any "
                f"discovered test root — leaving Repository -> File edge in place ",
                file=sys.stderr,
            )
            # A prior run may have re-parented this file onto a TestSuite that
            # no longer discovers it (root renamed/removed, file moved out).
            # Clear that stale edge so the fresh Repository -> File edge
            # written by structural_nodes.emit earlier this transaction is
            # left as the file's sole parent. Scoped to TestSuite-sourced
            # edges only — must not touch the correct baseline edge.
            conn.execute(
                "DELETE FROM edges WHERE kind='physically_contains' AND dst=? "
                "AND src IN (SELECT id FROM nodes WHERE kind='test_suite')",
                (_file_id,),
            )
            continue
        root_files[r.rel_path].append(file_rel)

    # Find Repository node id (parent for repo-owned suites). Multi-repo: scope
    # to THIS member's repository so suites re-parent under the right repo.
    repo_row = conn.execute(
        "SELECT id, name, path FROM nodes WHERE kind='repository' AND (repo = ? OR repo IS NULL)",
        (member_repo,),
    ).fetchone()
    if repo_row is None:
        # Defensive: structural_nodes.emit hasn't run yet — abort.
        return
    repo_name = repo_row[1]
    repo_path = repo_row[2]
    if repo_path is None:
        raise ValueError("graph node path is required for this projection")
    repo_key = ("repository", repo_name, repo_path)

    # Build TestSuite nodes + physically_contains parent edges.
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []

    for r in roots:
        if r.owner_kind != "repository" and r.owner_name is None:
            continue
        owner_name = r.owner_name

        # Compute kind_attr first — suite_name depends on it for package-owned suites.
        kind_attr = _classify_suite_kind(r.rel_path, root_files[r.rel_path])

        # naming: repository-owned suites use the full rel_path as the
        # display name (already unique). Package-owned suites use a qualified name
        # <owner_name>-<kind>-tests so every node has a unique name even when multiple
        # packages have a tests/ directory (previously all resolved to basename 'tests').
        suite_name = r.rel_path if r.owner_kind == "repository" else f"{owner_name}-{kind_attr}-tests"

        attrs: dict = {
            "uri": test_suite_uri(ctx, r.rel_path),
            "suite_kind": kind_attr,
            "path": r.rel_path,
            "owner_kind": r.owner_kind,
        }
        if r.language is not None:
            attrs["language"] = r.language

        nodes.append(
            GraphNode(
                kind="test_suite",
                name=suite_name,
                path=r.rel_path,
                line=None,
                attrs=attrs,
            )
        )

        if r.owner_kind == "repository":
            parent_src = repo_key
        else:
            # owner may be a Package OR App; resolve kind from
            # the side-table built earlier.
            if owner_name is None:
                continue
            owner_kind_str = pkg_kind_map.get(owner_name, "package")
            parent_src = (owner_kind_str, owner_name, r.owner_pkg_rel)
        edges.append(
            GraphEdge(
                src=parent_src,
                dst=("test_suite", suite_name, r.rel_path),
                kind="physically_contains",
                attrs={},
            )
        )

    upsert.upsert_records(conn, as_graph_records(nodes=nodes, edges=edges))

    # Re-parent test files: DELETE-then-INSERT atomic. The outer
    # update.run already wraps emit() in a transaction,
    # so the per-file DELETE+INSERT is atomic with the rest of gw graph update.
    for r in roots:
        if r.owner_kind == "repository":
            suite_key_name = r.rel_path
        else:
            # Must use the same qualified name as the node creation above so the
            # DB lookup finds the newly-upserted node, not the old 'tests'-named one.
            _rp_kind_attr = _classify_suite_kind(r.rel_path, root_files[r.rel_path])
            suite_key_name = f"{r.owner_name}-{_rp_kind_attr}-tests"
        suite_row = conn.execute(
            "SELECT id FROM nodes WHERE kind='test_suite' AND name=? AND path=?",
            (suite_key_name, r.rel_path),
        ).fetchone()
        if suite_row is None:
            continue
        suite_id = suite_row[0]

        for file_rel in root_files[r.rel_path]:
            file_id_row = conn.execute(
                "SELECT id FROM nodes WHERE kind='file' AND path=?",
                (file_rel,),
            ).fetchone()
            if file_id_row is None:
                continue
            file_id = file_id_row[0]
            conn.execute(
                "DELETE FROM edges WHERE kind='physically_contains' AND dst=?",
                (file_id,),
            )
            conn.execute(
                "INSERT INTO edges (src, dst, kind, attrs_json) VALUES (?, ?, 'physically_contains', NULL)",
                (suite_id, file_id),
            )

    # tests-edge derivation (TestSuite -> Package, TestSuite -> Repository).
    _emit_tests_edges(
        conn,
        repo_root,
        ctx,
        pkg_rows,
        roots,
        root_files,
        repo_key,
        pkg_kind_map=pkg_kind_map,
    )


def _emit_tests_edges(
    conn: sqlite3.Connection,
    repo_root: Path,
    ctx: RepoContext,
    pkg_rows: list[tuple[str, str | None, str | None]],
    roots: list[_TestRoot],
    root_files: dict[str, list[str]],
    repo_key: tuple[str, str, str | None],
    *,
    pkg_kind_map: dict[str, str] | None = None,
) -> None:
    """Scan every test file in each TestSuite for imports and emit
    TestSuite -> Package edges for matched first-party packages.

    When a single suite imports from >=_REPOSITORY_EDGE_THRESHOLD distinct
    first-party packages, also emits a TestSuite -> Repository edge.

    The regex scan + package-prefix resolution lives in
    code_graph_io.import_scan.scan_files_imports.
    """
    edges_out: list[GraphEdge] = []

    for r in roots:
        if r.owner_kind == "repository":
            suite_key_name = r.rel_path
        else:
            # Same qualified-name formula as the node-creation loop so tests-edges
            # reference the correct suite node.
            _te_kind_attr = _classify_suite_kind(r.rel_path, root_files[r.rel_path])
            suite_key_name = f"{r.owner_name}-{_te_kind_attr}-tests"
        suite_key = ("test_suite", suite_key_name, r.rel_path)

        file_rel_paths = list(root_files[r.rel_path])
        matched_pkgs = scan_files_imports(repo_root, file_rel_paths, pkg_rows)

        for pkg_name, pkg_rel in matched_pkgs:
            # use the actual kind so tests-edges from a suite
            # to an App node resolve correctly.
            pkg_kind_value = pkg_kind_map.get(pkg_name, "package") if pkg_kind_map else "package"
            edges_out.append(
                GraphEdge(
                    src=suite_key,
                    dst=(pkg_kind_value, pkg_name, pkg_rel),
                    kind="tests",
                    attrs={},
                )
            )

        # K=5 whole-system edge.
        if len(matched_pkgs) >= _REPOSITORY_EDGE_THRESHOLD:
            edges_out.append(
                GraphEdge(
                    src=suite_key,
                    dst=repo_key,
                    kind="tests",
                    attrs={},
                )
            )

    if edges_out:
        upsert.upsert_records(conn, as_graph_records(edges=edges_out))
