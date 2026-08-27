#!/usr/bin/env python3
"""Fail loudly when shipped source relies on a platform-dependent text-IO default.

Python text mode has two defaults this repo cannot afford:

    encoding=  ->  locale.getpreferredencoding(False)  ->  cp1252 on stock Windows
    newline=   ->  translate "\\n" to os.linesep        ->  every LF becomes CRLF

The second is the one that corrupts data. An okf document that is already CRLF
on disk -- `packages/okf-io/tests/fixtures/edge/encoding/crlf.md` is exactly
that, and marked `-text` in `.gitattributes` so it stays that way -- read
byte-exactly, left unmutated, and written back through a translating writer
comes out as CR CR LF, one stray CR per line, non-idempotently: every
round-trip adds another. That breaks `okf-io`'s central contract.

No lint rule expresses this. `ruff`'s PLW1514 is preview-only, covers
`encoding=` alone, and `scripts` and `plugins` are in ruff's `exclude`.

Scope is shipped source: `packages/<pkg>/src/**` and `scripts/**` except
`scripts/tests/**`. Test trees write to `tmp_path` and are covered by running
the suite on Windows, which is child 9's job, not this guard's.

To exempt a call deliberately, put `# text-io-ok: <reason>` on any line the
call spans. A bare pragma with no reason is itself an error.

Run with:
    uv run python scripts/check_text_io_explicit.py [root]
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

PRAGMA = "# text-io-ok:"

# `X.open(...)` where X is one of these takes its mode at positional index 1,
# like the builtin -- unlike `Path.open(...)`, which takes it at index 0.
_MODULE_STYLE_OPEN = frozenset({"io", "codecs"})


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    call: str
    missing: str  # "encoding" or "newline"

    def render(self) -> str:
        why = (
            "locale default (cp1252 on stock Windows)"
            if self.missing == "encoding"
            else "os.linesep translation (LF -> CRLF on Windows)"
        )
        return f"  {self.path}:{self.line}: {self.call} is missing {self.missing}= -- {why}"


def in_scope(relative: str) -> bool:
    """Shipped source only. See the module docstring for why tests are out."""
    if not relative.endswith(".py"):
        return False
    parts = PurePosixPath(relative).parts
    if parts[:1] == ("scripts",):
        return parts[1:2] != ("tests",)
    return len(parts) > 3 and parts[0] == "packages" and parts[2] == "src"


def _tracked_files(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True
    )
    return [entry for entry in result.stdout.decode("utf-8").split("\0") if entry]


def _keyword(call: ast.Call, name: str) -> ast.keyword | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword
    return None


def _opaque(call: ast.Call) -> bool:
    """A `**kwargs` splat could supply either keyword; do not guess."""
    return any(keyword.arg is None for keyword in call.keywords)


def _mode(call: ast.Call) -> str | None:
    """"r" or "w" for a text-mode IO call; None when the call is not one.

    Returns None for binary modes, for `os.open` (file-descriptor level, and
    so unaffected by either default), and for a computed mode -- which is not
    statically decidable and is not worth a false positive.
    """
    func = call.func
    attribute = isinstance(func, ast.Attribute)
    if attribute:
        name = func.attr
    elif isinstance(func, ast.Name):
        name = func.id
    else:
        return None

    qualifier = func.value.id if attribute and isinstance(func.value, ast.Name) else None
    if qualifier == "os" and name == "open":
        return None
    if name == "read_text":
        return "r"
    if name == "write_text":
        return "w"
    if name not in {"open", "fdopen"}:
        return None

    keyword = _keyword(call, "mode")
    raw: ast.expr | None = keyword.value if keyword is not None else None
    if raw is None:
        # `Path.open(mode)` takes index 0; `open()`, `io.open()`, `codecs.open()`
        # and `os.fdopen()` all take index 1.
        index = 0 if (attribute and name == "open" and qualifier not in _MODULE_STYLE_OPEN) else 1
        raw = call.args[index] if len(call.args) > index else None
    if raw is None:
        mode = "r"
    elif isinstance(raw, ast.Constant) and isinstance(raw.value, str):
        mode = raw.value
    else:
        return None
    if "b" in mode:
        return None
    return "w" if any(character in mode for character in "wax+") else "r"


def _label(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute):
        return f"{func.attr}(...)"
    return f"{func.id}(...)" if isinstance(func, ast.Name) else "call(...)"


def _scan(path: Path, relative: str) -> list[Violation]:
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    tree = ast.parse(source, filename=relative)

    found: list[Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        mode = _mode(node)
        if mode is None or _opaque(node):
            continue
        span = lines[node.lineno - 1 : (node.end_lineno or node.lineno)]
        if any(PRAGMA in line for line in span):
            continue
        required = ("encoding", "newline") if mode == "w" else ("encoding",)
        for name in required:
            if _keyword(node, name) is None:
                found.append(Violation(relative, node.lineno, _label(node), name))
    return found


def find_violations(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    for relative in _tracked_files(root):
        if not in_scope(relative):
            continue
        violations.extend(_scan(root / relative, relative))
    return sorted(violations, key=lambda v: (v.path, v.line, v.missing))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0] if __doc__ else "", allow_abbrev=False
    )
    parser.add_argument("root", type=Path, nargs="?", default=Path("."), help="repo root to scan (default: cwd)")
    args = parser.parse_args(argv)

    violations = find_violations(args.root.resolve())
    if not violations:
        return 0

    print("Implicit text-IO defaults in shipped source:", file=sys.stderr)
    for violation in violations:
        print(violation.render(), file=sys.stderr)
    print(
        "\nPass encoding= on every text read and write, and newline= on every text write.\n"
        '  newline="" for content whose bytes are a contract (okf members, seeds, ledgers) --\n'
        "            it writes the string's own line endings through untouched.\n"
        '  newline="\\n" for formats defined as LF (JSON, JSONL).\n'
        "Or write bytes: `path.write_bytes(text.encode(\"utf-8\"))` has neither default.\n"
        f"To exempt a call deliberately, put `{PRAGMA} <reason>` on a line it spans.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
