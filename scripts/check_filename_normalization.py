#!/usr/bin/env python3
"""Fail loudly when a tracked filename's on-disk bytes drift from git's.

Wired as the first `just check` recipe (cheapest, most diagnostic): a
filename that reaches the repo in one Unicode normalization form and gets
checked out in another is exactly what
`2026-08-07-bug-moves-fixture-unicode-normalization` traced ten confusing
`okf-ext` test failures back to. `okf_io.bundle` tolerates the mismatch at
read time (matching is NFC-insensitive, ids stay raw disk bytes); this gate
is the other half -- catching the drift *by name*, repo-wide, the moment it
happens, rather than downstream in ten assertion diffs.

**Must not use `os.path.exists()`.** APFS lookup is normalization-insensitive,
so an `exists()`-based scan reports a drifted tree clean -- the exact false
negative the linked bug's own investigation hit. Comparing git's tracked
name against the raw bytes `os.listdir()` hands back for its parent directory
is what actually sees the mismatch.

Run with:
    uv run python scripts/check_filename_normalization.py [root]
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import unicodedata
from pathlib import Path, PurePosixPath


def _tracked_files(root: Path) -> list[str]:
    """Every path git tracks under *root*, as posix text, exactly as the
    index holds it -- `-z` so a name containing a newline still splits
    correctly.
    """
    result = subprocess.run(["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True)
    return [entry for entry in result.stdout.decode("utf-8").split("\0") if entry]


def find_drift(root: Path) -> list[tuple[str, str]]:
    """`(tracked, on_disk)` for every tracked file whose leaf name git-index
    bytes are not what `readdir()` reports for its parent directory, even
    though a canonically-equivalent entry is present there.

    A tracked file missing from disk entirely is not reported: that is a
    presence mismatch, and `git status` already covers it. This is a
    *normalization* mismatch only -- the file is there, under a different
    name.
    """
    dir_cache: dict[Path, list[str]] = {}
    drift: list[tuple[str, str]] = []
    for tracked in _tracked_files(root):
        relative = PurePosixPath(tracked)
        parent_relative = relative.parent
        parent = root / parent_relative
        if parent not in dir_cache:
            try:
                dir_cache[parent] = os.listdir(parent)
            except OSError:
                dir_cache[parent] = []
        entries = dir_cache[parent]
        leaf = relative.name
        if leaf in entries:
            continue
        canonical_leaf = unicodedata.normalize("NFC", leaf)
        match = next(
            (entry for entry in entries if entry != leaf and unicodedata.normalize("NFC", entry) == canonical_leaf),
            None,
        )
        if match is None:
            continue
        on_disk = f"{parent_relative}/{match}" if str(parent_relative) != "." else match
        drift.append((tracked, on_disk))
    return drift


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "", allow_abbrev=False)
    parser.add_argument("root", type=Path, nargs="?", default=Path("."), help="repo root to scan (default: cwd)")
    args = parser.parse_args(argv)
    root = args.root.resolve()

    drift = find_drift(root)
    if not drift:
        return 0

    print("Unicode-normalization drift: a tracked filename's on-disk bytes disagree with git's.", file=sys.stderr)
    for tracked, on_disk in drift:
        print(f"  {tracked}  ->  on disk as {on_disk}", file=sys.stderr)
    print(
        "\nSame name, different composition -- most often a checkout that decomposed a "
        "precomposed filename (or the reverse). Re-checkout the affected path(s), or rename on "
        "disk to match what git tracks.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
