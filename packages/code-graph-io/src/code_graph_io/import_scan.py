"""Shared import-graph scanner.

Used by test_suites.emit (TestSuite -> Package edges) and
derived_edges.compute (references / depends_on derivation).
The two callers differ only in whether test files are included; the
regex passes and the package-prefix index are identical.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from code_graph_io.structural_nodes import _owning_package, _resolve_import_root  # noqa: F401

PkgRow = tuple[str, str | None, str | None]
"""(pkg_name, pkg_rel, pkg_attrs_json) — the tuple shape callers pass in."""

_PYTHON_IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+([\w\.]+)", re.MULTILINE)
_JS_IMPORT_RE = re.compile(r"""(?:from|import|require)\s*\(?\s*['"]([^'"]+)['"]""")

_JS_EXTENSIONS = (".ts", ".js", ".tsx", ".jsx", ".mjs", ".cjs")
_JS_INDEX_SUFFIXES = ("index.ts", "index.js", "index.tsx", "index.jsx")
_SCAN_EXTENSIONS_JS = frozenset(_JS_EXTENSIONS)


def _build_pkg_index(
    pkg_rows: list[PkgRow],
) -> list[tuple[str, str, str | None]]:
    """Return [(pkg_prefix, pkg_name, pkg_rel)] sorted deepest-prefix-first.

    pkg_prefix is pkg_rel if not None else "" (root-rooted packages).
    """
    rows: list[tuple[str, str, str | None]] = []
    for pkg_name, pkg_rel, _attrs in pkg_rows:
        prefix = pkg_rel if pkg_rel is not None else ""
        rows.append((prefix, pkg_name, pkg_rel))
    rows.sort(key=lambda row: len(row[0]), reverse=True)
    return rows


def _build_importable_maps(
    pkg_rows: list[PkgRow],
) -> tuple[
    dict[str, tuple[str, str | None]],
    dict[str, tuple[str, str | None]],
]:
    """Return (py_importable_to_pkg, js_name_to_pkg).

    Python: pkg.name.replace('-', '_') -> (name, rel)
    JS/TS:  pkg.name -> (name, rel)  (package.json name including @scope)
    """
    py_map: dict[str, tuple[str, str | None]] = {}
    js_map: dict[str, tuple[str, str | None]] = {}
    for pkg_name, pkg_rel, pkg_attrs_json in pkg_rows:
        attrs = json.loads(pkg_attrs_json) if pkg_attrs_json else {}
        lang = attrs.get("language")
        if lang == "python":
            importable = pkg_name.replace("-", "_")
            py_map[importable] = (pkg_name, pkg_rel)
        elif lang in {"javascript", "typescript"}:
            js_map[pkg_name] = (pkg_name, pkg_rel)
    return py_map, js_map


def _match_python_import(
    module_str: str,
    py_map: dict[str, tuple[str, str | None]],
) -> tuple[str, str | None] | None:
    """Return (pkg_name, pkg_rel) for the first dotted segment, or None."""
    top = module_str.split(".", 1)[0]
    return py_map.get(top)


def _js_relative_candidates(spec: str, importing_file: Path) -> list[Path] | None:
    """Build the ordered candidate Paths for a relative/absolute JS specifier.

    Returns the candidate list (exact path, then each _JS_EXTENSIONS suffix,
    then each _JS_INDEX_SUFFIXES) or None if the path cannot be resolved.
    Caller probes cand.exists() in order. Shared by _match_js_import and
    resolve_js_import_file so the candidate logic is defined once.
    """
    try:
        resolved = (importing_file.parent / spec).resolve()
    except OSError:
        return None
    candidates: list[Path] = [resolved]
    for ext in _JS_EXTENSIONS:
        candidates.append(resolved.with_suffix(ext))
    for idx in _JS_INDEX_SUFFIXES:
        candidates.append(resolved / idx)
    return candidates


def _first_existing_rel(candidates: list[Path], repo_root: Path) -> str | None:
    """Return the repo-relative posix path of the first existing candidate FILE
    inside repo_root, or None.

    Directory candidates are skipped so a bare `./sub` specifier resolves to
    `./sub/index.js` (a file) rather than the directory itself.
    """
    for cand in candidates:
        if cand.is_file():
            try:
                return cand.relative_to(repo_root).as_posix()
            except ValueError:
                continue
    return None


def _match_js_import(
    spec: str,
    importing_file: Path,
    repo_root: Path,
    js_map: dict[str, tuple[str, str | None]],
    pkg_index: list[tuple[str, str, str | None]],
) -> tuple[str, str | None] | None:
    """Resolve a JS/TS import specifier to (pkg_name, pkg_rel) or None."""
    if spec.startswith(".") or spec.startswith("/"):
        candidates = _js_relative_candidates(spec, importing_file)
        if candidates is None:
            return None
        rel = _first_existing_rel(candidates, repo_root)
        if rel is None:
            return None
        return _owning_package(rel, pkg_index)
    # Bare specifier — split @scope/name (two segments) or name (one)
    if spec.startswith("@"):
        key = "/".join(spec.split("/", 2)[:2])
    else:
        key = spec.split("/", 1)[0]
    return js_map.get(key)


def resolve_js_import_file(spec: str, importing_file: Path, repo_root: Path) -> str | None:
    """Resolve a relative/absolute JS/TS specifier to a repo-relative FILE path.

    Returns the repo-relative posix path of the first existing candidate
    (reusing the same extension/index probing as _match_js_import), or None
    for bare/third-party specifiers and specifiers with no in-repo match.
    """
    if not (spec.startswith(".") or spec.startswith("/")):
        return None
    candidates = _js_relative_candidates(spec, importing_file)
    if candidates is None:
        return None
    return _first_existing_rel(candidates, repo_root)


def resolve_python_import_file(
    module_str: str,
    repo_root: Path,
    pkg_rows: list[PkgRow],
) -> str | None:
    """Resolve a first-party dotted module to its repo-relative FILE path.

    Maps the dotted module's top segment to a first-party Package via py_map,
    then probes the package's import root (src-layout then flat-layout via
    _resolve_import_root) for the dotted submodule as either a `.py` file or a
    package `__init__.py`. Returns the repo-relative posix path or None for
    stdlib/third-party modules and modules with no in-repo file match.
    """
    py_map, _js_map = _build_importable_maps(pkg_rows)
    hit = _match_python_import(module_str, py_map)
    if hit is None:
        return None
    pkg_name, pkg_rel = hit
    pkg_dir = (repo_root / pkg_rel) if pkg_rel else repo_root
    importable = pkg_name.replace("-", "_")
    import_root = _resolve_import_root(pkg_dir, importable)
    if import_root is None:
        return None
    segments = module_str.split(".")
    # segments[0] is the top-level importable (== import_root's dir name);
    # the remainder addresses a submodule under import_root.
    sub = segments[1:]
    base = import_root
    for seg in sub[:-1]:
        base = base / seg
    if sub:
        last = sub[-1]
        module_file = base / f"{last}.py"
        pkg_init = base / last / "__init__.py"
        target = module_file if module_file.exists() else pkg_init
    else:
        target = import_root / "__init__.py"
    if not target.exists():
        return None
    try:
        return target.relative_to(repo_root).as_posix()
    except ValueError:
        return None


def scan_files_imports(
    repo_root: Path,
    file_rel_paths: list[str],
    pkg_rows: list[PkgRow],
) -> set[tuple[str, str | None]]:
    """Scan a list of files for first-party Package imports.

    Returns the deduplicated set of (pkg_name, pkg_rel) tuples for every
    first-party Package the files import. Unreadable files (OSError) are
    silently skipped. Stdlib / third-party imports that don't appear in
    the py/js maps are ignored.
    """
    py_map, js_map = _build_importable_maps(pkg_rows)
    pkg_index = _build_pkg_index(pkg_rows)
    matched: set[tuple[str, str | None]] = set()
    for rel in file_rel_paths:
        fpath = repo_root / rel
        try:
            content = fpath.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        ext = Path(rel).suffix
        if ext == ".py":
            for m in _PYTHON_IMPORT_RE.finditer(content):
                hit = _match_python_import(m.group(1), py_map)
                if hit is not None:
                    matched.add(hit)
        elif ext in _SCAN_EXTENSIONS_JS:
            for m in _JS_IMPORT_RE.finditer(content):
                hit = _match_js_import(
                    m.group(1),
                    fpath,
                    repo_root,
                    js_map,
                    pkg_index,
                )
                if hit is not None:
                    matched.add(hit)
    return matched


def scan_package_imports(
    conn: sqlite3.Connection,
    repo_root: Path,
    pkg_name: str,
    pkg_rel: str | None,
    *,
    include_test_files: bool = False,
) -> set[tuple[str, str | None]]:
    """Return distinct first-party Packages imported by files in pkg.

    pkg_rel is the Package's repo-relative path prefix (None for root).
    include_test_files=False excludes files with attrs_json.is_test=true.
    include_test_files=True returns all imports —
    test_suites.emit when scanning test-file imports.
    """
    # Collect all Package/App rows once for the maps/index.
    # apps participate in import resolution the same way.
    pkg_rows: list[PkgRow] = [
        (row[0], row[1], row[2])
        for row in conn.execute("SELECT name, path, attrs_json FROM nodes WHERE kind IN ('package', 'app')").fetchall()
    ]

    # Query File rows scoped to the Package
    if pkg_rel:
        like = pkg_rel + "/%"
        rows = conn.execute(
            "SELECT path, attrs_json FROM nodes WHERE kind='file' AND path LIKE ?",
            (like,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT path, attrs_json FROM nodes WHERE kind='file'").fetchall()

    file_paths: list[str] = []
    for path, attrs_json in rows:
        if not include_test_files:
            attrs = json.loads(attrs_json) if attrs_json else {}
            if attrs.get("is_test"):
                continue
        file_paths.append(path)

    return scan_files_imports(repo_root, file_paths, pkg_rows)
