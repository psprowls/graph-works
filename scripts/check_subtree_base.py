#!/usr/bin/env python3
"""Guard for the `plugins/graph-works` subtree merge base.

`git subtree pull --squash` has no database. It records which upstream commit was
last vendored by writing `git-subtree-split: <sha>` into a *commit message*, and
recovers it later by grepping history back from HEAD for that string. The whole
memory of "what we last vendored" is one commit message that has to stay
reachable. Nothing in git protects it, and nothing reports its loss: the next
pull simply finds no base, treats the prefix as a fresh import, and conflicts
across the entire tree — one upstream release later, with nothing connecting the
two events.

`SYNC.md`'s hard rule 2 is the human-facing half of this. This is the machine
half: it asserts the property the rule exists to protect, so that softening the
rule's wording from "never squash-merge" to "the note must survive" is a
tightening rather than a loophole.

Three assertions:

1. **Reachable.** A commit carrying `git-subtree-dir: plugins/graph-works` and a
   `git-subtree-split:` trailer is reachable from HEAD. Catches squash-merge,
   rebase, amend, `filter-repo`, and a merge commit dropped in a replay.
2. **Agrees with the ledger.** Its split SHA equals the last row of `SYNC.md`'s
   merge ledger. Catches a pull nobody recorded, a ledger row with no pull behind
   it, and a bare non-`--squash` pull, which writes no new note at all.
3. **Prefix-rooted.** Its tree is the upstream subtree, not the whole repo.
   Assertions 1 and 2 both pass if someone copies the trailers onto a repo-rooted
   commit, and the next pull then computes its delta as "delete everything
   outside the prefix". This is the only assertion that can be skipped: it needs
   the upstream object, which a clone that has never fetched `upstream` does not
   have. It falls back to a structural check rather than failing on a fresh
   checkout.

**ENFORCING, and part of `just check`** — unlike `audit-delta` and
`plugin-contract`, which are advisory because they go legitimately red during
in-progress work. This cannot: the base changes only during a re-base, and
`SYNC.md`'s ritual appends the ledger row as step 1, before anything else. There
is no window where correct work leaves this red.

It runs where `just check` runs, which is the limit worth stating: it converts a
silent failure discovered a release later into a red gate on the next run. It is
not a merge gate. A merge gate needs CI on the target branch (deferred, ADR-0010).

Usage
-----
    just subtree-base
    python3 scripts/check_subtree_base.py --json
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
SYNC_PATH = "plugins/SYNC.md"

#: Top-level names that exist in the repo root and never inside the vendored
#: subtree. Their presence in the squash commit's tree proves it is repo-rooted.
#: Used only when the upstream object is unavailable for the exact tree compare.
REPO_ROOT_MARKERS = ("pyproject.toml", "justfile", "packages", "plugins", "uv.lock")

_SPLIT_RE = re.compile(r"^git-subtree-split:\s*([0-9a-f]{40})\s*$", re.MULTILINE)
_LEDGER_RE = re.compile(r"^\|.*?`([0-9a-f]{40})`.*\|\s*$", re.MULTILINE)


class BaseError(RuntimeError):
    """The subtree base cannot be read, which is itself the finding."""


@dataclass
class Report:
    squash_commit: str | None = None
    split_sha: str | None = None
    ledger_sha: str | None = None
    failures: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


def git(*args: str, cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise BaseError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def ledger_sha(sync: str) -> str:
    """The split SHA from the last merge-ledger row.

    Scoped to table rows for the reason `audit_delta.last_split_sha` documents:
    the Recovery section talks *about* split SHAs in prose, and a bare "last 40
    hex chars" would eventually read one of those instead.
    """
    shas = _LEDGER_RE.findall(sync)
    if not shas:
        raise BaseError(f"no subtree-split SHA in a {SYNC_PATH} merge-ledger row")
    return shas[-1]


def find_latest_squash(cwd: Path, ref: str = "HEAD") -> tuple[str, str]:
    """The newest reachable squash commit for the prefix, and the SHA it names.

    Mirrors `git-subtree`'s own `find_latest_squash`: grep history for the
    `git-subtree-dir` trailer, take the FIRST hit (most recent, since `git log`
    is reverse-chronological), read its split SHA. Reimplemented rather than
    shelled out to because `git subtree` offers no way to ask this question
    without also performing a merge.
    """
    out = git(
        "log", "--no-show-signature",
        f"--grep=^git-subtree-dir: {PREFIX}/*$",
        "--pretty=format:%H%n%B%x00", ref,
        cwd=cwd,
    )
    for record in out.split("\x00"):
        record = record.strip()
        if not record:
            continue
        commit, _, body = record.partition("\n")
        match = _SPLIT_RE.search(body)
        if match:
            return commit.strip(), match.group(1)
    raise BaseError(
        f"no commit reachable from {ref} carries `git-subtree-dir: {PREFIX}`.\n"
        f"  The subtree merge base is gone. The next `git subtree pull` will treat\n"
        f"  {PREFIX} as a fresh import and conflict across the whole tree.\n"
        f"  Cause is almost always a squash-merge or rebase over the subtree merge —\n"
        f"  see `{SYNC_PATH}` hard rule 2."
    )


def check_prefix_rooted(squash: str, split: str, cwd: Path, report: Report) -> None:
    """Assertion 3 — the squash commit holds the subtree, not the repo.

    Exact tree equality when the upstream object is present: that is what
    `git subtree`'s own `new_squash_commit` builds (`commit-tree <split>^{tree}`),
    so anything else is not a squash commit. When the object is missing — a clone
    that never fetched `upstream` — fall back to proving the tree is not
    repo-rooted, and say which check ran.
    """
    squash_tree = git("rev-parse", f"{squash}^{{tree}}", cwd=cwd).strip()
    try:
        split_tree = git("rev-parse", f"{split}^{{tree}}", cwd=cwd).strip()
    except BaseError:
        entries = {line.split()[-1] for line in git("ls-tree", squash, cwd=cwd).splitlines() if line}
        intruders = sorted(entries & set(REPO_ROOT_MARKERS))
        if intruders:
            report.failures.append(
                f"squash commit {squash[:12]} is repo-rooted, not prefix-rooted "
                f"(its tree contains {', '.join(intruders)}).\n"
                f"  A pull against this computes its delta as 'delete everything outside {PREFIX}'."
            )
        report.notes.append(
            f"prefix-rooted: structural check only — upstream object {split[:12]} not present "
            f"locally (run `git fetch upstream` for the exact tree compare)"
        )
        return

    if squash_tree != split_tree:
        report.failures.append(
            f"squash commit {squash[:12]} does not carry upstream {split[:12]}'s tree "
            f"({squash_tree[:12]} != {split_tree[:12]}).\n"
            f"  A real squash commit is `commit-tree <split>^{{tree}}`; this is not one, so the\n"
            f"  trailers name a commit whose content it does not hold."
        )
    else:
        report.notes.append("prefix-rooted: exact tree match against the upstream object")


def check(cwd: Path) -> Report:
    report = Report()
    sync = (cwd / SYNC_PATH).read_text(encoding="utf-8")
    report.ledger_sha = ledger_sha(sync)
    report.squash_commit, report.split_sha = find_latest_squash(cwd)

    if report.split_sha != report.ledger_sha:
        report.failures.append(
            f"the reachable squash commit names {report.split_sha[:12]}, but the last "
            f"{SYNC_PATH} ledger row names {report.ledger_sha[:12]}.\n"
            f"  Either a pull landed without its ledger row, a ledger row was written without a\n"
            f"  pull behind it, or a bare (non-`--squash`) pull wrote no new note at all."
        )
    return report


def render(report: Report) -> str:
    lines = [f"subtree-base -- {PREFIX}"]
    if report.squash_commit:
        lines.append(f"  squash commit: {report.squash_commit[:12]}  ->  upstream {report.split_sha[:12]}")
        lines.append(f"  {SYNC_PATH} ledger row: {report.ledger_sha[:12]}")
    lines += [f"  {note}" for note in report.notes]
    lines.append("")
    if report.ok:
        lines.append("ok -- the subtree merge base is reachable, recorded, and prefix-rooted")
    else:
        lines += [f"FAILED: {failure}" for failure in report.failures]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)

    cwd = Path(__file__).resolve().parent.parent
    try:
        report = check(cwd)
        if report.squash_commit and report.split_sha:
            check_prefix_rooted(report.squash_commit, report.split_sha, cwd, report)
    except (BaseError, OSError) as exc:
        print(f"subtree-base: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({"ok": report.ok, **vars(report)}, indent=2))
    else:
        print(render(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
