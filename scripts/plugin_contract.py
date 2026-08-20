"""`just plugin-contract` — the three assertions from the CLI contract page (D-034).

Repo tooling: ruff-excluded, part of no package, matching the rest of `scripts/`. The
recipe sits outside `just check`, because a gate that cannot pass yet must not block every
unrelated change.

Identity resolves by *capability*, not by parsing a version string. `gw util
describe-surface --json` exists only in graph-works-cli, so running it is the proof; the
`gw version` string is reported alongside but never decides the branch. Never print a bare
`pass` — the `gw` on PATH may still be the donor CLI, and a green result that did not name
the binary would silently mean "agrees with the CLI E7 is replacing".

A1 and A3 need the plugin tree, which lives in a different repository, so they report
`skipped (no plugin tree)` rather than passing vacuously.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

Runner = Callable[[list[str]], "tuple[int, str]"]

#: The identity `gw util describe-surface --json` proves. Anything else is the donor CLI
#: (advisory) or nothing at all (pending).
CLI_NAME = "graph-works-cli"

#: Verbs owned by another work item. Named, short, and emptied by that item — not a
#: permanent escape hatch.
DEFERRED: dict[str, str] = {"guidance suggest": "guidance-okf-port"}

#: Flags the page lists that the CLI does not implement *yet*, each mapped to the work item
#: that owns closing the gap. This is a ledger, not a mute switch: a page flag the CLI does
#: not declare and that has no entry here **fails**. That is what stops drift growing back
#: silently — a new gap has no owner, so it goes red the day it appears.
FLAG_ADVISORIES: dict[tuple[str, str], str] = {("next", "--file"): "guidance-okf-port"}

#: Files whose `gw` mentions are not invocations, and why. Same discipline as the two
#: ledgers above: named, short, and deleted when the reason stops being true.
TREE_MENTIONS: dict[str, str] = {
    "tests/hooks/test-skill-doc-routing.sh": (
        "asserts the ABSENCE of three candidate verbs from a hook message; the verb strings "
        "are the assertion, not a call"
    ),
}

_BLOCK = re.compile(r"<!--\s*cli-contract\s*\n(.*?)-->", re.DOTALL)
_HEADING = re.compile(r"^(#{1,6})\s", re.MULTILINE)


@dataclass(frozen=True)
class ContractVerb:
    verb: str
    flags: tuple[str, ...] = ()


@dataclass
class Report:
    lines: list[str] = field(default_factory=list)
    failed: bool = False

    def note(self, line: str) -> None:
        self.lines.append(line)

    def fail(self, line: str) -> None:
        self.lines.append(line)
        self.failed = True


def _nearest_heading_level(text: str, position: int) -> int:
    """The level of the last heading before *position*, or 0 when there is none."""
    level = 0
    for match in _HEADING.finditer(text, 0, position):
        level = len(match.group(1))
    return level


def parse_contract_page(text: str) -> list[ContractVerb]:
    """Every real verb block on the page.

    A block counts only when its nearest preceding heading is a `###`. The two blocks under
    `## How to read an entry` are prose examples (`work example-verb`), and counting them
    would make the checker fail forever against verbs that were never meant to exist.
    """
    verbs: list[ContractVerb] = []
    for match in _BLOCK.finditer(text):
        if _nearest_heading_level(text, match.start()) != 3:
            continue
        fields: dict[str, str] = {}
        for line in match.group(1).splitlines():
            if ":" in line and not line.startswith((" ", "\t")):
                key, _, value = line.partition(":")
                fields.setdefault(key.strip(), value.strip())
        verb = fields.get("verb", "").strip()
        if not verb:
            continue
        raw_flags = fields.get("flags", "")
        flags = tuple(flag.strip() for flag in raw_flags.split(",") if flag.strip().startswith("-"))
        verbs.append(ContractVerb(verb=verb, flags=flags))
    return verbs


def _subprocess_runner(argv: list[str]) -> tuple[int, str]:
    completed = subprocess.run(argv, capture_output=True, text=True, check=False)
    return completed.returncode, completed.stdout


def resolve_identity(runner: Runner) -> tuple[str | None, dict[str, object] | None, str]:
    """(identity, surface, version-string). `identity` is None when `gw` is not there."""
    try:
        code, out = runner(["gw", "util", "describe-surface", "--json"])
    except (FileNotFoundError, OSError):
        return None, None, ""
    version = ""
    try:
        version_code, version_out = runner(["gw", "version"])
        version = version_out.strip() if version_code == 0 else ""
    except (FileNotFoundError, OSError):
        version = ""
    if code == 0:
        try:
            return CLI_NAME, json.loads(out), version
        except json.JSONDecodeError:
            pass
    try:
        help_code, _ = runner(["gw", "--help"])
    except (FileNotFoundError, OSError):
        return None, None, version
    if help_code == 0:
        return "graph-wiki-cli", None, version
    return None, None, version


def _surface_index(surface: dict[str, object] | None) -> dict[str, set[str]]:
    """verb string -> the set of option spellings the CLI declares for it."""
    index: dict[str, set[str]] = {}
    commands = surface.get("commands", []) if surface else []
    assert isinstance(commands, list)
    for entry in commands:
        path = " ".join(str(part) for part in entry.get("path", []))
        opts: set[str] = set()
        for option in entry.get("options", []):
            opts.update(str(spelling) for spelling in option.get("opts", []))
        index[path] = opts
    return index


def assert_verbs(verbs: list[ContractVerb], surface: dict[str, object] | None, report: Report) -> None:
    """A2 — every verb and flag on the page exists in the CLI.

    A missing verb fails. A missing flag fails too, *unless* `FLAG_ADVISORIES` names the
    work item that owns closing it — an owned gap advises and stays green while that item
    runs. An unowned one is drift nobody has looked at, and drift that only advises is drift
    that grows.
    """
    index = _surface_index(surface)
    resolved = 0
    missing: list[str] = []
    deferred_lines: list[str] = []
    advisories: list[str] = []
    undeclared: list[str] = []
    for entry in verbs:
        if entry.verb in DEFERRED:
            deferred_lines.append(f"  1 deferred ({DEFERRED[entry.verb]}): {entry.verb}")
            continue
        if entry.verb not in index:
            missing.append(entry.verb)
            continue
        resolved += 1
        for flag in entry.flags:
            if flag not in index[entry.verb]:
                owner = FLAG_ADVISORIES.get((entry.verb, flag))
                if owner is None:
                    undeclared.append(f"  undeclared flag: {entry.verb} {flag} (no owning work item)")
                else:
                    advisories.append(f"  advisory: {entry.verb} {flag} (deferred to {owner})")

    # A label line, matching A1/A3, so a fully-passing run is legible without counting.
    if missing or undeclared:
        report.note("A2 every page verb exists in the CLI: FAILED")
        for verb in missing:
            report.fail(f"  missing verb: {verb}")
        for line in undeclared:
            report.fail(line)
    else:
        report.note("A2 every page verb exists in the CLI: ok")
    report.note(f"  {resolved}/{len(verbs)} verbs resolve")
    report.lines.extend(deferred_lines)
    report.lines.extend(advisories)


@dataclass(frozen=True)
class TreeFile:
    relative: str
    text: str | None  # None when the file couldn't be decoded as UTF-8 text.


def _read_tree(tree: Path) -> list[TreeFile]:
    """Every file under *tree*, read once — A1 and A3 both walk it, so share the read."""
    files: list[TreeFile] = []
    for path in sorted(tree.rglob("*")):
        if not path.is_file():
            continue
        try:
            text: str | None = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            text = None
        files.append(TreeFile(relative=path.relative_to(tree).as_posix(), text=text))
    return files


#: An invocation is *written as code*: a fenced block, or an inline span. Prose that merely
#: says the word `gw` ("a mutation made outside the gw commands") is not a call, and treating
#: it as one is what buried the real findings under noise.
_FENCE = re.compile(r"```.*?```", re.DOTALL)
_SPAN = re.compile(r"`[^`\n]+`")

#: `[ \t]` rather than `\s`: `\s` crosses newlines, so a paragraph ending in "…resolved via
#: gw" swallowed the next line's first word and reported it as a verb.
_INVOCATION = re.compile(r"\bgw[ \t]+([a-z][a-z0-9-]*(?:[ \t]+[a-z][a-z0-9-]*)?)")


def _code_spans(file: TreeFile) -> list[tuple[int, int]] | None:
    """The regions of *file* that are code, or None when the whole file is."""
    if not file.relative.endswith(".md"):
        return None
    assert file.text is not None
    return [match.span() for match in _FENCE.finditer(file.text)] + [
        match.span() for match in _SPAN.finditer(file.text)
    ]


def assert_tree_invocations(tree_files: list[TreeFile] | None, verbs: list[ContractVerb], report: Report) -> None:
    """A1 — every `gw` verb invoked in the plugin tree appears on the page.

    Reported honestly, but never fails the exit code, and the contract page's Enforcement
    section says so too. This is a regex heuristic over prose and skill files, not a parse of
    real invocations: it cannot tell a call from a sentence with certainty, so a hit is worth
    a human's attention rather than a build break. Exit is gated on A2/A3 only.

    An invocation that is a *prefix* of a page verb is not unknown — `gw work` in prose is the
    group `gw work advance` belongs to, not a missing entry.
    """
    if tree_files is None:
        report.note("A1 every plugin invocation is on the page: skipped (no plugin tree)")
        return
    known = {entry.verb for entry in verbs}
    unknown: set[str] = set()
    for file in tree_files:
        if file.text is None or file.relative in TREE_MENTIONS:
            continue
        spans = _code_spans(file)
        for match in _INVOCATION.finditer(file.text):
            if spans is not None and not any(start <= match.start() and match.end() <= end for start, end in spans):
                continue
            invocation = match.group(1)
            if any(
                invocation == verb or invocation.startswith(f"{verb} ") or verb.startswith(f"{invocation} ")
                for verb in known
            ):
                continue
            unknown.add(invocation.split()[0])

    # A label line in both branches — `assert_verbs`'s own comment states the intent, and A1
    # broke it: a run *with* findings printed no heading, so its advisories appeared orphaned
    # under A2's block.
    if unknown:
        report.note("A1 every plugin invocation is on the page: advisory")
        for invocation in sorted(unknown):
            report.note(f"  advisory: invocation not on the page: gw {invocation}")
    else:
        report.note("A1 every plugin invocation is on the page: ok")


def assert_tree_clean(tree_files: list[TreeFile] | None, report: Report) -> None:
    """A3 — zero `graph-wiki` occurrences and zero `skills/*/scripts/*.py` references."""
    if tree_files is None:
        report.note("A3 no stale identifiers or script references: skipped (no plugin tree)")
        return
    clean = True
    for file in tree_files:
        if re.fullmatch(r"skills/[^/]+/scripts/.+\.py", file.relative):
            report.fail(f"  script reference: {file.relative}")
            clean = False
            continue
        if file.text is not None and "graph-wiki" in file.text:
            report.fail(f"  stale identifier graph-wiki: {file.relative}")
            clean = False
    if clean:
        report.note("A3 no stale identifiers or script references: ok")


def main(argv: list[str] | None = None, *, runner: Runner = _subprocess_runner) -> int:
    parser = argparse.ArgumentParser(description="Check the plugin CLI contract.")
    parser.add_argument("--contract-page", default="")
    parser.add_argument("--plugin-tree", default="")
    args = parser.parse_args(argv)

    identity, surface, version = resolve_identity(runner)
    report = Report()
    version_suffix = f"  [{version}]" if version else ""
    if identity is None:
        report.note(f"pending (gw not found){version_suffix}")
    else:
        state = "pass" if identity == CLI_NAME else "advisory"
        report.note(f"{state} ({identity}){version_suffix}")

    page_path = Path(args.contract_page) if args.contract_page else None
    verbs: list[ContractVerb] = []
    if page_path is None:
        report.note("A2 every page verb exists in the CLI: pending (no contract page)")
    else:
        try:
            text = page_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            report.note(f"A2 every page verb exists in the CLI: pending (unreadable: {page_path})")
        else:
            verbs = parse_contract_page(text)
            if surface is None:
                # No graph-works-cli surface to check against: `identity` is either the
                # donor CLI (advisory) or absent (pending). Either way there is nothing
                # authoritative to fail A2 against, so it stays informational.
                report.note(f"A2 every page verb exists in the CLI: skipped (no {CLI_NAME} surface)")
            else:
                assert_verbs(verbs, surface, report)

    tree = Path(args.plugin_tree) if args.plugin_tree else None
    tree_files = _read_tree(tree) if tree is not None else None
    assert_tree_invocations(tree_files, verbs, report)
    assert_tree_clean(tree_files, report)

    for line in report.lines:
        print(line)
    return 1 if report.failed else 0


if __name__ == "__main__":  # pragma: no cover -- exercised through main() in the tests
    raise SystemExit(main())
