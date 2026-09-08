#!/usr/bin/env python3
"""Fail loudly when a tracked path would check out CRLF under Git for Windows.

Wired into `just check` right after `normalization` (same rationale: cheap,
early, diagnostic). Git for Windows sets `core.autocrlf=true` in its system
config at install time, so any tracked path with `eol: unspecified` checks
out CRLF there -- which makes a bash script unrunnable
(`#!/usr/bin/env bash\\r`) and makes a `PreToolUse` hook fail *silently*. See
`work/epic-native-windows-support/children/bug-enforce-lf-line-endings`.

The now-deleted vendored subtree's own `.gitattributes` covered its tree by
enumerating globs; enumeration is exactly what failed when four extensionless
scripts were added after that file was written. This gate is the mechanical
check a hand-authored `.gitattributes` cannot provide.

Its oracle is `git ls-files --eol`, which reports each tracked path's index
EOL alongside its resolved attributes. Two assertions, in both directions:

1. **No un-guarded CRLF.** Every tracked path whose attributes do not carry
   `-text` must be `i/lf`, `i/none`, or `i/-text`. `i/crlf` or `i/mixed`
   outside a `-text` tree is a failure -- this is what catches a script
   committed from a Windows box before the attributes take effect.
2. **No un-declared `-text`.** Every path resolving `-text` must sit under a
   prefix in `ALLOWLISTED_TEXT_PREFIXES` below. This is what stops the
   escape hatch from becoming the workaround: a new `-text` line has to be
   argued for in the allowlist, next to the comment saying why that corpus
   is byte-exact. Without it, the cheapest way to silence assertion 1 is to
   add a `-text` line, and the bug re-enters through the fix.

Run with:
    uv run python scripts/check_line_endings.py [root]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ALLOWLISTED_TEXT_PREFIXES = (
    "packages/okf-io/tests/fixtures/",
    "packages/okf-ext/tests/fixtures/",
    "packages/doc-wiki-okf/tests/fixtures/",
    # The gw plugin's rendered branding assets. `logo.png` is a rasterised
    # binary -- EOL normalization would corrupt the pixel data outright, not
    # just perturb it -- and `.gitattributes` names it explicitly rather than
    # relying on `text=auto`'s NUL-byte detection to keep noticing.
    "plugins/gw/assets/",
)


@dataclass(frozen=True)
class Violation:
    path: str
    reason: str


def _ls_files_eol(root: Path) -> list[tuple[str, str, str]]:
    """`(path, index_eol, attrs)` for every tracked path, straight from
    `git ls-files --eol -z` -- `-z` so a name containing a newline still
    splits correctly.
    """
    result = subprocess.run(
        ["git", "ls-files", "--eol", "-z"], cwd=root, check=True, capture_output=True
    )
    entries: list[tuple[str, str, str]] = []
    for line in result.stdout.decode("utf-8").split("\0"):
        if not line:
            continue
        fields, _, path = line.partition("\t")
        i_field = fields.split()[0]
        index_eol = i_field.partition("/")[2]
        entries.append((path, index_eol, fields))
    return entries


def _unset_diff_paths(root: Path, paths: list[str]) -> set[str]:
    """Paths among *paths* whose `diff` attribute also resolves unset.

    `binary` is shorthand for `-text -diff -merge`; a bare `-text` line (the
    fixture corpora's own declaration) leaves `diff` unspecified. That is the
    signal that separates "this is a real binary asset" (out of scope for
    assertion 2) from "this is declared byte-exact text" (in scope).
    """
    if not paths:
        return set()
    stdin = "\0".join(paths) + "\0"
    result = subprocess.run(
        ["git", "check-attr", "--stdin", "-z", "diff"],
        cwd=root,
        input=stdin.encode("utf-8"),
        check=True,
        capture_output=True,
    )
    fields = result.stdout.decode("utf-8").split("\0")[:-1]
    unset: set[str] = set()
    for path, _attr, value in zip(fields[0::3], fields[1::3], fields[2::3]):
        if value == "unset":
            unset.add(path)
    return unset


def find_violations(root: Path) -> list[Violation]:
    entries = _ls_files_eol(root)
    no_text_paths = [path for path, _index_eol, fields in entries if "attr/-text" in fields]
    binary_paths = _unset_diff_paths(root, no_text_paths)

    violations: list[Violation] = []
    for path, index_eol, fields in entries:
        has_no_text = "attr/-text" in fields
        if has_no_text:
            if path in binary_paths:
                continue
            if not path.startswith(ALLOWLISTED_TEXT_PREFIXES):
                violations.append(
                    Violation(
                        path,
                        "resolves -text but is not under an allowlisted byte-exact fixture prefix",
                    )
                )
            continue
        if index_eol in ("crlf", "mixed"):
            violations.append(
                Violation(path, f"checks out with unguarded {index_eol.upper()} line endings")
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

    print("Line-ending violations found:", file=sys.stderr)
    for violation in violations:
        print(f"  {violation.path}: {violation.reason}", file=sys.stderr)
    print(
        "\nA tracked path outside a declared byte-exact fixture tree must be LF-only, so it "
        "checks out correctly under Git for Windows' core.autocrlf=true default. Add an "
        "eol=lf rule that covers it, or -- if it genuinely is byte-exact -- add it to "
        "ALLOWLISTED_TEXT_PREFIXES in scripts/check_line_endings.py alongside a comment "
        "saying why.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
