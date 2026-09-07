#!/usr/bin/env python3
"""Fail loudly when a package ships a POSIX-only construct its README does not declare.

Wired into `just check` as `platform-declared`, beside `normalization` and
`line-endings`. This is rule 3a of
[ADR-0021](/adrs/0021-windows-is-supported-via-wsl-native-windows-is-deferred.md)
turned from an unenforced convention into a check -- see the design spec for
work/epic-native-windows-support/children/tech-debt-publish-platform-matrix.
`workflow-local`'s README accurately said "It is POSIX only" and nothing
enforced it, and a silent data-loss path (`os.kill(pid, 0)` mapping to
`TerminateProcess` on Windows) sat undetected behind that accurate-but-inert
declaration for as long as it existed. An unenforced convention did not
prevent the exact failure it described -- which is why this gate fails
rather than warns.

**Detection is AST, not string match.** Every `packages/*/src/**/*.py` file
is walked with `ast` and flagged for:

- `Import` / `ImportFrom` of any module in `POSIX_ONLY_MODULES`, at any scope
  (module-level or nested inside a function -- a guarded, function-local
  import still means the package carries a platform seam worth documenting);
- `Attribute` access naming a POSIX-only process primitive in
  `POSIX_ONLY_ATTRIBUTES` (`os.kill`, `signal.SIGKILL`, `os.setsid`,
  `os.fork`, `os.getuid`).

A package with any hit whose `README.md` carries no `## Platform` section
fails, unless the package is named in `ALTERNATE_BOUNDARY_HEADINGS` and its
README carries that heading's section with an explicit Windows statement in
it -- `workflow-local` is the one such case today, declaring its boundary
under `## What this package does not do` rather than `## Platform`. If this
carve-out ever proves too loose in practice, the honest fix is another named
allowlist entry, not a looser detector.

**The gate is deliberately asymmetric.** It fails under-declaration only. A
`## Platform` section in a package with no POSIX-only construct is not a
failure -- `workflow-orca`'s "runs unmodified on Windows" is exactly that
case, and a symmetric gate would delete genuinely useful information.
Under-declaring is the failure mode this epic keeps finding; over-declaring
is not.

`graph_works_core/util/platform.py` names `fcntl`, `os.kill` and `SIGKILL`,
but only as string/f-string prose and as a `POSIX_ONLY_MODULES`-shaped
frozenset of module-name *data* -- never as a real `Import`/`Attribute` AST
node. The detector does not see it, and no exemption is needed: it is the
reporter, not a consumer, and `graph-works-core` declares a Platform section
anyway via its real, guarded sites in `workspace/anchors.py`.

Run with:
    uv run python scripts/check_platform_declared.py [root]
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path

POSIX_ONLY_MODULES = frozenset({"fcntl", "grp", "pwd", "termios"})

POSIX_ONLY_ATTRIBUTES = frozenset(
    {
        ("os", "kill"),
        ("signal", "SIGKILL"),
        ("os", "setsid"),
        ("os", "fork"),
        ("os", "getuid"),
    }
)

PLATFORM_HEADING = "## Platform"

# A package whose Windows boundary is declared under a different heading than
# `## Platform`. Named here rather than loosened into the detector -- see
# `workflow-local`'s README, "## What this package does not do".
ALTERNATE_BOUNDARY_HEADINGS = {
    "workflow-local": "## What this package does not do",
}


@dataclass(frozen=True)
class Violation:
    package: str
    construct: str
    file: str
    line: int


class _PosixConstructVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.hits: list[tuple[str, int]] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name in POSIX_ONLY_MODULES:
                self.hits.append((alias.name, node.lineno))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module in POSIX_ONLY_MODULES:
            self.hits.append((node.module, node.lineno))
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.value, ast.Name) and (node.value.id, node.attr) in POSIX_ONLY_ATTRIBUTES:
            self.hits.append((f"{node.value.id}.{node.attr}", node.lineno))
        self.generic_visit(node)


def _scan_file(path: Path) -> list[tuple[str, int]]:
    visitor = _PosixConstructVisitor()
    visitor.visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
    return visitor.hits


def _section_body(text: str, heading: str) -> str | None:
    """Text between *heading* and the next `## `-level heading, or `None` if
    *heading* is not present."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip() == heading:
            body = []
            for later in lines[index + 1 :]:
                if later.startswith("## "):
                    break
                body.append(later)
            return "\n".join(body)
    return None


def _declares_platform(package: str, readme_text: str) -> bool:
    if _section_body(readme_text, PLATFORM_HEADING) is not None:
        return True
    alternate = ALTERNATE_BOUNDARY_HEADINGS.get(package)
    if alternate is None:
        return False
    body = _section_body(readme_text, alternate)
    return body is not None and "windows" in body.lower()


def find_violations(root: Path) -> list[Violation]:
    packages_dir = root / "packages"
    if not packages_dir.is_dir():
        return []

    violations: list[Violation] = []
    for pkg_dir in sorted(p for p in packages_dir.iterdir() if p.is_dir()):
        src_dir = pkg_dir / "src"
        if not src_dir.is_dir():
            continue

        hits: list[tuple[str, Path, int]] = []
        for py_file in sorted(src_dir.rglob("*.py")):
            for construct, line in _scan_file(py_file):
                hits.append((construct, py_file, line))
        if not hits:
            continue

        readme = pkg_dir / "README.md"
        readme_text = readme.read_text(encoding="utf-8") if readme.is_file() else ""
        if _declares_platform(pkg_dir.name, readme_text):
            continue

        for construct, py_file, line in hits:
            violations.append(
                Violation(
                    package=pkg_dir.name,
                    construct=construct,
                    file=py_file.relative_to(root).as_posix(),
                    line=line,
                )
            )
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "", allow_abbrev=False)
    parser.add_argument("root", type=Path, nargs="?", default=Path("."), help="repo root to scan (default: cwd)")
    args = parser.parse_args(argv)
    root = args.root.resolve()

    violations = find_violations(root)
    if not violations:
        return 0

    print("Platform-declaration violations found:", file=sys.stderr)
    for violation in violations:
        print(
            f"  {violation.package}: {violation.construct} at {violation.file}:{violation.line} "
            f"-- README.md has no '{PLATFORM_HEADING}' section",
            file=sys.stderr,
        )
    print(
        "\nA package that imports a POSIX-only module, or reaches a POSIX-only process "
        "primitive, must declare that in its README's '## Platform' section -- see "
        "work/epic-native-windows-support/children/tech-debt-publish-platform-matrix. "
        "Point readers at `gw util platform` for the live, per-capability answer.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
