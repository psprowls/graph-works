#!/usr/bin/env python3
"""Seed the graph-works vault's `.gw/tags.yaml` from its own inventory.

`plan_scaffold` writes `tags.yaml` empty and nothing has ever filled it. This
fills it — and it builds **no mechanism** to do so. Every call below is a
shipped `okf_ext.tags` function; the one step with no machinery is the one in
the middle, where a human reads the evidence and authors the file.

Four subcommands, one per moment of the design's mechanism table:

    evidence   build the reviewable pack the admission decision is made from
    gate       prove a hand-authored `.gw/tags.yaml` before it is believed
    rename     plan, and optionally apply, the file's own `replaced_by`s
    verify     record the acceptance numbers the next seed argues against

Design notes
------------
* **The `ignore=` set is read from `workspace.yaml`, never retyped.** It cannot
  come through `code_wiki_okf.config.load_config`: `Config` carries no
  bundle-level `ignore` field -- the manifest's global `ignore:` is merged into
  each `RepoConfig` as a *git* pathspec. So this parses the key directly. That
  is still "read from the workspace"; it is just not the config object's job.

* **`okf_ext.tags.DEFAULT_IGNORE` is deliberately NOT spliced in.** It exists
  for callers who keep the vocabulary inside their bundle. This layout keeps it
  at `.gw/tags.yaml`, outside `bundle_dir`, so splicing it would silently ignore
  a real bundle member if one were ever named `tags.yaml`.

* **The quarantine is derived from the package, not listed here.** ADR-0029
  splits the two routes: a package contributes `TagDefinition` entries, the
  vocabulary *file* is the vault's. `work_tracker_okf` contributes `perf` and
  `security`; retyping them here would let the two lists drift, which is the
  one failure the ADR exists to prevent.

* **No clock.** `evidence` requires `--as-of` and `verify` requires `--today`,
  matching okf-io's house rule -- and making the evidence pack byte-reproducible
  from the same bundle, which is what lets its determinism be a test.

* **Nothing writes without an explicit flag.** `evidence` writes only where
  `--out` says; `rename` reports unless `--write`; `gate` and `verify` are pure
  reads. ADR-0022: a mutation surface plans by default, the caller applies.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from okf_ext import tags
from okf_io.bundle import Bundle
from ruamel.yaml import YAML
from work_tracker_okf.vocabulary import CONTRIBUTED_TAGS

MANIFEST_FILENAME = "workspace.yaml"
BUNDLE_DIRNAME = "wiki"
CONFIG_DIRNAME = ".gw"

#: The tags that arrive by ADR-0029's *package* route. Derived, never retyped.
#: They are admitted by contribution, not by frequency: their carrier count is
#: irrelevant to the floor, and a count of 0 is correct rather than dead weight.
QUARANTINE: frozenset[str] = frozenset(definition.name for definition in CONTRIBUTED_TAGS)


class SeedError(RuntimeError):
    """The workspace, the corpus, or the vocabulary is not in a readable shape."""


def resolve_ignore(workspace: Path, override: Sequence[str] | None = None) -> tuple[str, ...]:
    """The bundle `ignore=` set for *workspace*.

    *override* wins outright and skips the manifest read -- for the case where
    C4's final minimal set differs from what the manifest still says.
    """
    if override is not None:
        return tuple(override)
    manifest = workspace / MANIFEST_FILENAME
    if not manifest.is_file():
        raise SeedError(f"{manifest}: no workspace manifest; pass --ignore explicitly")
    data = YAML(typ="safe").load(manifest.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SeedError(f"{manifest}: must be a YAML mapping")
    raw = data.get("ignore")
    if raw is None:
        return ()
    if isinstance(raw, str) or not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise SeedError(f"{manifest}: `ignore` must be a list of strings")
    return tuple(raw)


def load(workspace: Path, ignore: Sequence[str]) -> tuple[Bundle, tags.TagInventory]:
    """Load the vault and inventory it, or refuse.

    A page whose `tags` key the inventory could not read is a page it did not
    count, and a policy decided over a partial corpus is a policy with a hole
    in it. Post-C4 this should never fire; if it does it is a C4 defect and is
    reported as one rather than worked around.
    """
    from okf_io import load_bundle

    bundle = load_bundle(workspace / BUNDLE_DIRNAME, ignore=tuple(ignore))
    inv = tags.inventory(bundle)
    if inv.skipped:
        detail = "\n".join(f"  {item.path}: {item.reason} — {item.detail}" for item in inv.skipped)
        raise SeedError(
            "the inventory could not read every page, so the corpus is partial and no "
            "admission policy may be decided over it (report this as a C4 defect):\n" + detail
        )
    return bundle, inv


#: Floors the coverage curve is computed at. The knee is what the human reads;
#: a floor stated without its coverage cost is a number nobody can argue with.
FLOORS: tuple[int, ...] = (1, 2, 3, 4, 5, 6)

#: The two cutoffs the similarity pass runs at. 0.8 is `clusters()`' own
#: default and the decision surface; 0.75 is a wider net, read once and
#: discarded -- explicitly NOT a second decision surface.
CUTOFFS: tuple[float, ...] = (0.8, 0.75)


@dataclass(frozen=True, slots=True)
class Row:
    """One tag in the frequency table."""

    tag: str
    count: int
    lanes: tuple[str, ...]
    live: bool


@dataclass(frozen=True, slots=True)
class Floor:
    """One row of the coverage curve: what floor `n` admits, and what it costs."""

    n: int
    admitted: int
    covered: int
    share: float


def is_live(concept_ids: Sequence[str]) -> bool:
    """False when every carrier sits in an archive lane.

    The predicate is a path *segment* test at any depth, matching
    `work_tracker_okf.mutation:424` -- `work/_archive/<slug>` and
    `work/<slug>/children/_archive/<child>` are both archived, and a future
    nesting is too. Narrowing it to a literal prefix would silently call an
    archived page live.

    A tag whose entire corpus is finished work is not a vocabulary entry; it is
    a label on a closed item. That is a strong prior on *dropped*, not a rule --
    the decision session's Q3 is where an archive-only tag is admitted anyway.
    """
    return any("/_archive/" not in f"/{concept_id}/" for concept_id in concept_ids)


def _lane(concept_id: str) -> str:
    """The top-level directory a concept sits in, or `.` at the root."""
    return concept_id.split("/", 1)[0] if "/" in concept_id else "."


def rows(inv: tags.TagInventory, *, quarantine: frozenset[str] = QUARANTINE) -> tuple[Row, ...]:
    """The frequency table: every non-quarantined tag, commonest first."""
    return tuple(
        Row(
            tag=tag,
            count=inv.counts[tag],
            lanes=tuple(sorted({_lane(concept_id) for concept_id in inv.concepts[tag]})),
            live=is_live(inv.concepts[tag]),
        )
        for tag in sorted(inv.counts, key=lambda name: (-inv.counts[name], name))
        if tag not in quarantine
    )


def coverage(table: Sequence[Row]) -> tuple[Floor, ...]:
    """The curve the floor is chosen from."""
    total = sum(row.count for row in table)
    return tuple(
        Floor(
            n=n,
            admitted=sum(1 for row in table if row.count >= n),
            covered=(covered := sum(row.count for row in table if row.count >= n)),
            share=round(covered / total, 4) if total else 0.0,
        )
        for n in FLOORS
    )


def _similarity_table(inv: tags.TagInventory, cutoff: float, quarantine: frozenset[str]) -> list[str]:
    lines = [f"### cutoff {cutoff}", "", "| score | left (n) | right (n) |", "| ---: | --- | --- |"]
    found = False
    for cluster in tags.clusters(inv, cutoff=cutoff):
        if cluster.kind != "similarity":
            continue
        left, right = cluster.members
        if left in quarantine or right in quarantine:
            continue
        found = True
        lines.append(f"| {cluster.score} | `{left}` ({inv.counts[left]}) | `{right}` ({inv.counts[right]}) |")
    if not found:
        lines.append("| — | none | — |")
    lines.append("")
    return lines


def render_evidence(
    inv: tags.TagInventory,
    *,
    as_of: str,
    quarantine: frozenset[str] = QUARANTINE,
) -> str:
    """The six-section pack, in the order the decision session reads it.

    Deterministic: `inventory` and `clusters` sort throughout, and nothing here
    undoes that. `as_of` is passed rather than read from a clock, which is both
    the house rule and what makes the determinism testable.
    """
    table = rows(inv, quarantine=quarantine)
    curve = coverage(table)
    applications = sum(row.count for row in table)
    singletons = [row for row in table if row.count == 1]
    tagged = {concept_id for tag in inv.counts for concept_id in inv.concepts[tag]}

    out: list[str] = [
        "# Evidence pack — tag vocabulary admission",
        "",
        f"Built from the migrated bundle as of {as_of}. Sections 2-4 exclude the",
        "quarantine (section 6): those entries are admitted by contribution, not by",
        "frequency, and their carrier counts are irrelevant to the floor.",
        "",
        "## Totals",
        "",
        "| | value |",
        "| --- | ---: |",
        f"| tagged pages | {len(tagged)} |",
        f"| untagged pages | {len(inv.untagged)} |",
        f"| distinct tags (excl. quarantine) | {len(table)} |",
        f"| tag applications | {applications} |",
        f"| used exactly once | {len(singletons)} |",
        f"| singleton share | {round(len(singletons) / len(table), 4) if table else 0.0} |",
        f"| archive-only tags | {sum(1 for row in table if not row.live)} |",
        f"| skipped pages | {len(inv.skipped)} |",
        "",
        "## Frequency",
        "",
        "| tag | count | lanes | live |",
        "| --- | ---: | --- | --- |",
    ]
    out.extend(f"| {row.tag} | {row.count} | {', '.join(row.lanes)} | {'yes' if row.live else 'no'} |" for row in table)
    out += [
        "",
        "## Coverage",
        "",
        "| floor | tags admitted | applications covered | share |",
        "| --- | ---: | ---: | ---: |",
    ]
    out.extend(f"| n >= {floor.n} | {floor.admitted} | {floor.covered} | {floor.share} |" for floor in curve)
    out += ["", "## Similarity", ""]
    for cutoff in CUTOFFS:
        out.extend(_similarity_table(inv, cutoff, quarantine))
    out += [
        "## Normalization",
        "",
        "Expected empty. A non-empty list means a non-canonical tag reached the",
        "migrated corpus, which is a C4 defect and is reported before any policy",
        "decision is taken.",
        "",
    ]
    normalization = [cluster for cluster in tags.clusters(inv) if cluster.kind == "normalization"]
    if not normalization:
        out.append("none")
    else:
        out.extend(f"- `{cluster.canonical}` <- {', '.join(f'`{m}`' for m in cluster.members)}" for cluster in normalization)
    out += [
        "",
        "## Quarantine",
        "",
        "Contributed by the package route (ADR-0029). **Not eligible for any",
        "disposition here** — never authored, never dropped, and never edited in",
        "`deprecated` or `replaced_by`. A count of 0 is correct, not dead weight.",
        "",
        "| count | tag |",
        "| ---: | --- |",
    ]
    out.extend(f"| {inv.counts.get(tag, 0)} | `{tag}` |" for tag in sorted(quarantine))
    out.append("")
    return "\n".join(out)


BOM = "﻿"


def gate_failures(vocab_path: Path) -> list[str]:
    """Every reason this seed should not be believed. Empty means it passes.

    Six checks. Four are the design's own Step 3; two more come from
    `2026-08-21-bug-tags-merge-anchor-shapes`, which is NOT a blocker for this
    item precisely because both of its cases are cheap to assert away:

    * a second column-0 `tags:` key -- the locator anchors the first, PyYAML
      reads the last, and the merge is non-idempotent forever;
    * a BOM -- the scaffold never writes one, but a hand-editor can.

    Every failure is collected rather than raised, so one run tells a human
    everything wrong with the file rather than the first thing.
    """
    failures: list[str] = []
    raw = vocab_path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return [f"{vocab_path}: not valid UTF-8 at byte {exc.start}: {exc.reason}"]
    if text.startswith(BOM):
        failures.append(f"{vocab_path}: starts with a UTF-8 BOM; the merge locator cannot anchor past it")
    anchors = sum(1 for line in text.splitlines() if line.startswith("tags:"))
    if anchors != 1:
        failures.append(f"{vocab_path}: found {anchors} column-0 `tags:` keys; exactly one is required")

    try:
        vocab = tags.load_vocabulary(vocab_path)
    except tags.VocabularyError as exc:
        failures.append(f"load_vocabulary: {exc}")
        return failures

    for definition in CONTRIBUTED_TAGS:
        if definition.name not in vocab.known:
            failures.append(f"quarantine: `{definition.name}` is missing; it is the package route's, not yours to remove")
            continue
        is_deprecated = definition.name in vocab.deprecated
        if is_deprecated != definition.deprecated:
            failures.append(
                f"quarantine: `{definition.name}` has `deprecated: {is_deprecated}`, the package contributes "
                f"`{definition.deprecated}`; a differing instruction refuses the entry on every future install"
            )
            continue
        replacement = vocab.deprecated.get(definition.name)
        if is_deprecated and replacement != definition.replaced_by:
            failures.append(
                f"quarantine: `{definition.name}` has `replaced_by: {replacement!r}`, the package contributes "
                f"`{definition.replaced_by!r}`"
            )

    plan = tags.plan_vocabulary_merge(vocab_path, CONTRIBUTED_TAGS)
    if not plan.ok:
        failures.extend(f"plan_vocabulary_merge refused: {refusal.kind}: {refusal.error}" for refusal in plan.refusals)
    if not plan.is_empty:
        failures.append(
            f"plan_vocabulary_merge would still add {list(plan.added)}; the two routes have not converged"
        )
    return failures


def run_rename(workspace: Path, ignore: Sequence[str], *, write: bool) -> int:
    """Plan the vocabulary's own `replaced_by`s, and apply them under *write*.

    `plan_from_vocabulary` rather than `plan_merge`: the plan is derived from
    the file just authored, so the file and the corpus cannot disagree about
    what a merge was (D-030). A merge worth doing is a merge worth recording as
    a deprecation; `plan_merge` stays for a collapse the vault does not want in
    its history, and this item has none.

    A plan is a value you inspect and filter, not a `dry_run` flag (ADR-0022) --
    which is why the dry run prints the same plan the write applies, rather than
    a different code path.
    """
    bundle, _ = load(workspace, ignore)
    vocab = tags.load_vocabulary(workspace / CONFIG_DIRNAME / tags.VOCABULARY_FILENAME)
    plan = tags.plan_from_vocabulary(bundle, vocab)

    print(f"{len(plan.edits)} edit(s) across {len(plan.concept_ids)} page(s)")
    for edit in plan.edits:
        print(f"  {edit.path}[{edit.index}]: {edit.old} -> {edit.new}")
    for skip in plan.skipped:
        print(f"  SKIP {skip.path}: {skip.reason} — {skip.detail}")
    if not write:
        print("dry run — nothing written. Re-run with --write to apply.")
        return 0
    if plan.is_empty:
        print("nothing to apply.")
        return 0

    result = tags.apply(bundle, plan)
    print(f"wrote {len(result.written)} page(s)")
    for failure in result.failed:
        print(f"FAIL {failure.path}: {failure.kind}: {failure.error}", file=sys.stderr)
    if result.failed:
        print(
            "a `stale` failure is worth re-planning; `unwritable` / `stage-error` / "
            "`commit-error` are worth retrying as-is.",
            file=sys.stderr,
        )
        return 1
    return 0


#: The lane `vocabulary_rule` is actually enforced over. `graph_works_core`
#: appends it to the *wiki* lane only (`lint_drift/lanes.py:139-141`), and the
#: work lane composes `work_tracker_okf.compose.rule_set`, which does not
#: include it (`compose.py:131-138`). So a `tags.unknown` finding under `work/`
#: is real but will never be raised by `gw lint`. That gap is filed against
#: `work-tracker-okf`; it is not fixed here.
UNENFORCED_PREFIX = "work/"


def run_verify(workspace: Path, ignore: Sequence[str], *, today: date) -> int:
    """The acceptance numbers, and the two assertions that guard them."""
    from okf_io import validate

    bundle, inv = load(workspace, ignore)
    vocab = tags.load_vocabulary(workspace / CONFIG_DIRNAME / tags.VOCABULARY_FILENAME)

    failed = False

    normalization = tags.plan_normalize(bundle)
    if not normalization.is_empty:
        failed = True
        print("FAIL the corpus carries non-canonical tags — a corpus defect, not an edit to make:", file=sys.stderr)
        for edit in normalization.edits:
            print(f"  {edit.path}[{edit.index}]: {edit.old} -> {edit.new}", file=sys.stderr)

    survivors = {tag: inv.counts[tag] for tag in vocab.deprecated if tag in inv.counts}
    if survivors:
        failed = True
        print("FAIL deprecated spellings still carried after the rename:", file=sys.stderr)
        for tag, count in sorted(survivors.items()):
            print(f"  {tag}: {count}", file=sys.stderr)

    report = validate(bundle, today=today, extra_rules=[tags.vocabulary_rule(vocab)])
    unknown = report.by_code("tags.unknown")
    enforced = [finding for finding in unknown if not (finding.path or "").startswith(UNENFORCED_PREFIX)]
    print(f"distinct tags: {len(inv.counts)}")
    print(f"tags.unknown, enforced (wiki lane): {len(enforced)}")
    print(f"tags.unknown, unenforced (work lane): {len(unknown) - len(enforced)}")
    print(f"tags.deprecated: {len(report.by_code('tags.deprecated'))}")
    print(f"tags.non-canonical: {len(report.by_code('tags.non-canonical'))}")
    return 1 if failed else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("workspace", type=Path, help="The workspace root — the directory holding `wiki/` and `.gw/`.")
    parser.add_argument(
        "--ignore",
        action="append",
        default=None,
        metavar="GLOB",
        help="Override the manifest's `ignore:` set. Repeatable.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    pack = sub.add_parser("evidence", help="Build the reviewable evidence pack.")
    pack.add_argument("--as-of", required=True, metavar="YYYY-MM-DD", help="Stamped into the pack. No clock is read.")
    pack.add_argument("--out", type=Path, default=None, help="Write here instead of stdout.")

    sub.add_parser("gate", help="Prove the hand-authored `.gw/tags.yaml`.")

    renamer = sub.add_parser("rename", help="Apply the vocabulary's own `replaced_by` instructions.")
    renamer.add_argument("--write", action="store_true", help="Apply. Without it, the plan is only reported.")

    checker = sub.add_parser("verify", help="Record the acceptance numbers.")
    checker.add_argument(
        "--today",
        required=True,
        type=date.fromisoformat,
        metavar="YYYY-MM-DD",
        help="Passed to `validate`. No clock is read.",
    )

    args = parser.parse_args(argv)
    try:
        ignore = resolve_ignore(args.workspace, args.ignore)
        if args.command == "evidence":
            _, inv = load(args.workspace, ignore)
            text = render_evidence(inv, as_of=args.as_of)
            if args.out is None:
                sys.stdout.write(text)
            else:
                args.out.parent.mkdir(parents=True, exist_ok=True)
                args.out.write_text(text, encoding="utf-8", newline="")
                print(f"wrote {args.out}")
        elif args.command == "gate":
            vocab_path = args.workspace / CONFIG_DIRNAME / tags.VOCABULARY_FILENAME
            failures = gate_failures(vocab_path)
            for failure in failures:
                print(f"FAIL {failure}", file=sys.stderr)
            if failures:
                return 1
            print(f"gate passed: {vocab_path}")
        elif args.command == "rename":
            return run_rename(args.workspace, ignore, write=args.write)
        elif args.command == "verify":
            return run_verify(args.workspace, ignore, today=args.today)
    except SeedError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
