"""Tripwire for the MSYS argv quote-mangling defect class.

See work/epic-native-windows-support/children/bug-subprocess-argv-quote-mangling-msys: a
quote-bearing argument with no whitespace, built by native-Windows `subprocess.list2cmdline`,
is corrupted crossing into a Git Bash / MSYS-linked executable. `sed`, `awk`, `grep`, `bash`
and friends are all reached this way somewhere in a repo this size sooner or later; this test
fails the moment a new call site does.

**Heuristic, not proof of absence.** This is an AST scan over `packages/*/src` and `scripts/`
(this tree included) for a `subprocess.*` call whose first argument is a *list literal* with a
known-MSYS-tool name as its first element and a `"`-bearing string literal elsewhere in the
list. A call reaching an MSYS binary through a variable, an f-string, or a `PATH` lookup slips
through -- the boundary rule published in `README.md` is what covers that gap; this test is a
tripwire for the shape that actually occurred, not a gate that can prove none remain.

No dedicated `just` recipe: `scripts/tests` is already in the root `testpaths`
(`pyproject.toml:107`), so `just test` collects this file for free -- one call site does not
earn a new gate (design D-064).
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# MSYS-only tools -- binaries that live under Git for Windows' `usr/bin` and are built
# against the MSYS2 runtime, as opposed to native mingw binaries like `git.exe` or
# `node.exe`, which round-trip a quoted argument intact (measured; see the design spec).
MSYS_TOOLS = frozenset(
    {
        "sed",
        "awk",
        "grep",
        "bash",
        "sh",
        "head",
        "tail",
        "cut",
        "tr",
        "sort",
        "uniq",
        "wc",
        "sha256sum",
        "find",
        "xargs",
        "printf",
    }
)

SUBPROCESS_FUNCS = frozenset({"run", "Popen", "check_output", "check_call", "call"})


class _QuoteBearingArgvVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.hits: list[tuple[str, int]] = []

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        is_subprocess_call = (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "subprocess"
            and func.attr in SUBPROCESS_FUNCS
        )
        if is_subprocess_call and node.args:
            argv = node.args[0]
            if isinstance(argv, ast.List) and argv.elts:
                head = argv.elts[0]
                if isinstance(head, ast.Constant) and isinstance(head.value, str) and head.value in MSYS_TOOLS:
                    for element in argv.elts[1:]:
                        if (
                            isinstance(element, ast.Constant)
                            and isinstance(element.value, str)
                            and '"' in element.value
                        ):
                            self.hits.append((head.value, element.lineno))
        self.generic_visit(node)


def _scan_file(path: Path) -> list[tuple[str, int]]:
    visitor = _QuoteBearingArgvVisitor()
    visitor.visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
    return visitor.hits


def _iter_scan_files(root: Path) -> Iterator[Path]:
    packages_dir = root / "packages"
    if packages_dir.is_dir():
        for pkg_dir in sorted(p for p in packages_dir.iterdir() if p.is_dir()):
            src_dir = pkg_dir / "src"
            if src_dir.is_dir():
                yield from sorted(src_dir.rglob("*.py"))
    scripts_dir = root / "scripts"
    if scripts_dir.is_dir():
        yield from sorted(scripts_dir.rglob("*.py"))


def find_violations(root: Path) -> list[tuple[Path, str, int]]:
    violations: list[tuple[Path, str, int]] = []
    for py_file in _iter_scan_files(root):
        for tool, line in _scan_file(py_file):
            violations.append((py_file, tool, line))
    return violations


def test_no_quote_bearing_msys_argv_in_the_current_tree() -> None:
    violations = find_violations(REPO_ROOT)
    assert violations == [], "quote-bearing argument reaching an MSYS binary via subprocess: " + ", ".join(
        f"{path.relative_to(REPO_ROOT).as_posix()}:{line} ({tool})" for path, tool, line in violations
    )


def test_the_scanner_flags_a_quote_bearing_msys_argv(tmp_path: Path) -> None:
    offender = tmp_path / "scripts" / "bad.py"
    offender.parent.mkdir(parents=True)
    offender.write_text(
        "import subprocess\n"
        'subprocess.run(["sed", "-n", \'s/.*"x".*/\\1/p\', "file"])\n',
        encoding="utf-8",
        newline="\n",
    )
    violations = find_violations(tmp_path)
    assert [(tool, line) for _path, tool, line in violations] == [("sed", 2)]


def test_the_scanner_ignores_a_quote_bearing_argv_for_a_non_msys_tool(tmp_path: Path) -> None:
    clean = tmp_path / "scripts" / "clean.py"
    clean.parent.mkdir(parents=True)
    clean.write_text(
        "import subprocess\n"
        'subprocess.run(["git", "log", \'--grep=say "hi"\'])\n',
        encoding="utf-8",
        newline="\n",
    )
    assert find_violations(tmp_path) == []
