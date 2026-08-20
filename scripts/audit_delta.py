#!/usr/bin/env python3
"""Drift checker for the `plugins/graph-works` fork ledger.

`plugins/PATCHES.md` is prose, and prose has no way to know when it is wrong. A
patch lands without an entry, or an entry outlives the patch it describes, and
the ledger quietly stops describing the tree -- which is the same failure the
ledger exists to prevent. That the base was recoverable in 2026-08 depended on a
blob match that happened to be clean; that luck is not a plan.

This reads the last `git-subtree-split` SHA out of `SYNC.md`'s merge ledger,
diffs `HEAD:plugins/graph-works` against that upstream commit, and cross-checks
the result against what `PATCHES.md` claims -- in both directions:

* **undocumented divergence** -- a file differs from upstream with no `patched`
  entry. Someone patched and forgot.
* **retired patch** -- a `patched` entry whose file no longer diverges. Upstream
  adopted it, or a merge dropped it.
* **coverage gap** -- a grafted file the ledger classifies zero times, or twice.

`state: planned` entries are this audit's decisions for a later child to apply.
They make no claim about the tree, so they are reported as an outstanding
worklist rather than checked.

`state: removed` names a grafted file this fork deliberately deleted. It makes no
claim about the tree either, but it still has to be *named*: the coverage check
reads the grafted base, not the current tree, so a deleted file is grafted
forever and goes unclassified the moment its entry stops naming it. Dropping the
`file:` line trades a false `patched` claim for a false coverage gap; `removed`
is how the ledger says "gone on purpose".

**Advisory by design.** `just check` does not call this. An enforcing gate fails
on legitimate in-progress work -- the child that applies these dispositions would
run its whole execution against a red gate until its last ledger entry landed.
The two moments that matter, the post-merge checklist in `SYNC.md` and the merge
drill, invoke it explicitly.

**Compares `HEAD`, not the working tree.** A cross-prefix diff needs a tree-ish
on both sides, and the working tree does not have one. Commit, then check.

Usage
-----
    just audit-delta
    python3 scripts/audit_delta.py --json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

PREFIX = "plugins/graph-works"
LEDGER_PATH = "plugins/PATCHES.md"
SYNC_PATH = "plugins/SYNC.md"

BLOCK_RE = re.compile(r"<!--\s*audit-delta\s*\n(.*?)\n\s*-->", re.DOTALL)
BASE_RE = re.compile(r"<!--\s*audit-delta-base\s*\n(.*?)\n\s*-->", re.DOTALL)
FENCE_RE = re.compile(r"^([ \t]*)(`{3,}|~{3,}).*?^\1\2[^\n]*$", re.DOTALL | re.MULTILINE)
STATES = ("patched", "verbatim", "planned", "removed")


class LedgerError(RuntimeError):
    """The ledger or the sync document is not in a shape this can read."""


@dataclass(frozen=True)
class Entry:
    state: str
    files: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Base:
    base: str
    theirs: str
    grafted_roots: tuple[str, ...]


@dataclass
class Report:
    undocumented: list[str] = field(default_factory=list)
    retired: list[str] = field(default_factory=list)
    uncovered: list[str] = field(default_factory=list)
    duplicated: list[str] = field(default_factory=list)
    planned: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.undocumented or self.retired or self.uncovered or self.duplicated)


def strip_fenced(text: str) -> str:
    """Blank out fenced code blocks.

    Entry #0 documents the block format inside a fence. A regex over the raw
    file would read that example as a real claim about a file named
    `docs/example.md`.
    """
    return FENCE_RE.sub(lambda m: "\n" * m.group(0).count("\n"), text)


def parse_entries(ledger: str) -> list[Entry]:
    entries: list[Entry] = []
    for match in BLOCK_RE.finditer(strip_fenced(ledger)):
        body = match.group(1)
        state_match = re.search(r"^state:\s*(\S+)\s*$", body, re.MULTILINE)
        if state_match is None:
            raise LedgerError(f"audit-delta block with no `state:` line:\n{body}")
        state = state_match.group(1)
        if state not in STATES:
            raise LedgerError(f"unknown state {state!r}; expected one of {STATES}")
        files = re.findall(r"^file:\s*(\S+)\s*$", body, re.MULTILINE)
        entries.append(Entry(state=state, files=files))
    return entries


def parse_base(ledger: str) -> Base:
    match = BASE_RE.search(strip_fenced(ledger))
    if match is None:
        raise LedgerError(f"no `audit-delta-base` block in {LEDGER_PATH} -- see entry #0")
    body = match.group(1)

    def field_of(name: str) -> str:
        found = re.search(rf"^{name}:\s*(.+?)\s*$", body, re.MULTILINE)
        if found is None:
            raise LedgerError(f"`audit-delta-base` block is missing `{name}:`")
        return found.group(1)

    return Base(
        base=field_of("base"),
        theirs=field_of("theirs"),
        grafted_roots=tuple(field_of("grafted-roots").split()),
    )


def last_split_sha(sync: str) -> str:
    """The `git-subtree-split` SHA from the last row of SYNC.md's merge ledger.

    Scoped to table rows deliberately. The Recovery section talks *about* split
    SHAs, and a bare "last 40 hex chars in the file" would eventually read one
    of those instead of the ledger.
    """
    shas = re.findall(r"^\|.*?`([0-9a-f]{40})`.*\|\s*$", sync, re.MULTILINE)
    if not shas:
        raise LedgerError(f"no subtree-split SHA in a {SYNC_PATH} merge-ledger row")
    return shas[-1]


def git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise LedgerError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def divergent_files(sha: str, cwd: Path) -> set[str]:
    """Prefix-relative paths where HEAD's subtree differs from the upstream commit.

    `--no-renames` matters: without it, a renamed file's `--name-status` line is
    `R100\told\tnew` -- three tab-separated fields, not two -- and a naive
    `split("\t", 1)[1]` would read the whole `"old\tnew"` remainder as one bogus
    path instead of the real file, silently corrupting the report for exactly
    the upstream event (a rename between releases) this checker exists to catch.
    """
    out = git("diff", "--no-renames", "--name-status", sha, f"HEAD:{PREFIX}", cwd=cwd)
    return {line.split("\t", 1)[1] for line in out.splitlines() if "\t" in line}


def grafted_files(base: Base, cwd: Path) -> set[str]:
    out = git("ls-tree", "-r", "--name-only", base.base, cwd=cwd)
    return {p for p in out.splitlines() if p.startswith(base.grafted_roots)}


def compare(*, divergent: set[str], entries: list[Entry], grafted: set[str]) -> Report:
    claimed = {f for e in entries if e.state == "patched" for f in e.files}
    named: dict[str, int] = {}
    for entry in entries:
        for path in entry.files:
            named[path] = named.get(path, 0) + 1
    return Report(
        undocumented=sorted(divergent - claimed),
        retired=sorted(claimed - divergent),
        uncovered=sorted(grafted - set(named)),
        duplicated=sorted(p for p, n in named.items() if n > 1),
        planned=sorted({f for e in entries if e.state == "planned" for f in e.files}),
    )


def render(report: Report, sha: str) -> str:
    lines = [f"audit-delta -- {PREFIX} against upstream {sha[:12]}", ""]
    for title, paths, why in (
        ("Undocumented divergence", report.undocumented, "differs from upstream, no ledger entry"),
        ("Retired patches", report.retired, "ledger claims a patch that is no longer there"),
        ("Unclassified grafted files", report.uncovered, "named by no ledger entry"),
        ("Doubly-classified files", report.duplicated, "named by more than one ledger entry"),
    ):
        if paths:
            lines.append(f"{title} ({len(paths)}) -- {why}:")
            lines += [f"  {p}" for p in paths]
            lines.append("")
    if report.planned:
        lines.append(f"Outstanding planned dispositions: {len(report.planned)} (informational)")
        lines.append("")
    lines.append("clean" if report.ok else "FINDINGS -- see above")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)

    cwd = Path(__file__).resolve().parent.parent
    try:
        ledger = (cwd / LEDGER_PATH).read_text(encoding="utf-8")
        sync = (cwd / SYNC_PATH).read_text(encoding="utf-8")
        base = parse_base(ledger)
        entries = parse_entries(ledger)
        sha = last_split_sha(sync)
        report = compare(
            divergent=divergent_files(sha, cwd),
            entries=entries,
            grafted=grafted_files(base, cwd),
        )
    except (LedgerError, OSError) as exc:
        print(f"audit-delta: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({"sha": sha, "ok": report.ok, **vars(report)}, indent=2))
    else:
        print(render(report, sha))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
