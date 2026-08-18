"""What a composing CLI does that no single writer owns.

Composition is **library, not CLI** (C6-D). Tier 4 drives this lane through the
Python API rather than by shelling out, so anything buried in a Typer callback
is something tier 4 has to reimplement -- stamping a `sources[]` entry, ensuring
the plan row, composing the rule set, reconciling index and log after filing.
`cli.py` keeps argument parsing, echoing and exit codes, and nothing else.

Two promises left open by earlier children land here. `advance.py:47-50` records
`stamp_source` and `sync_plan_table` as *unresolved requests* -- "child 6
composes them" -- and `README.md`'s filing section defers `work/index.md`
reconciliation and the `log.md` line to "a composing CLI's". Both are kept
below.

Nothing here reads the clock: `today` and `on` are arguments, as everywhere
else below `cli.py`.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from okf_ext.schemas import load_schemas, schema_rule
from okf_ext.sections import section_rule
from okf_ext.shape import SectionSet, load_sections
from okf_ext.tables import TextSplice, splice_text
from okf_io import (
    Bundle,
    Document,
    IndexUpdate,
    Rule,
    append_log_entry,
    load,
    load_bundle,
    update_index,
)

from work_tracker_okf.advance import AdvancePlan, advance
from work_tracker_okf.advance import apply as apply_advance
from work_tracker_okf.filing import FilingPlan, file_item
from work_tracker_okf.filing import apply as apply_filing
from work_tracker_okf.items import IGNORE, WORK_DIR, WorkItem, load_items
from work_tracker_okf.paths import ArtifactRef, artifact_path
from work_tracker_okf.rules import PLAN_TABLE_SPEC, lane_rules
from work_tracker_okf.sources import upsert
from work_tracker_okf.vocabulary import PLAN_SOURCE_ID, SPEC_SOURCE_ID

#: The heading `_rules/plan.py` reads and all six `_sections/` declarations
#: require. Named here rather than imported: `_rules` is private, and one
#: string is cheaper than widening a private module's surface.
PLAN_HEADING = "Plan"

#: `Transition.stamp_source` -> the `(phase, kind)` pair `artifact_path`
#: composes from. The inverse of `paths._LITERAL_IDS`, typed out rather than
#: derived from it: two entries beat reaching into a private map.
_STAMP_SLOTS: dict[str, tuple[str, str]] = {
    SPEC_SOURCE_ID: ("design", "spec"),
    PLAN_SOURCE_ID: ("plan", "plan"),
}

#: The label each stamp falls back to when its artifact carries no H1.
_STAMP_LABELS: dict[str, str] = {SPEC_SOURCE_ID: "Design spec", PLAN_SOURCE_ID: "Plan"}

#: The two fixed cells of the implementation row, carried from the reference
#: `gw work advance` rather than re-invented.
_ROW_DONE_WHEN = "Implementation lands and the item is resolved"
_ROW_RATIONALE = "Workflow plan stage complete"

_H1_RE = re.compile(r"^#[ \t]+(\S.*?)[ \t]*$")
_FENCES = ("```", "~~~")


def rule_set(
    root: Path,
    *,
    repo_root: Path | None = None,
    declarations_dir: Path | None = None,
) -> tuple[Rule, ...]:
    """The one rule set `lint` and `advance` both validate against (C6-M).

    Identical to the tuple `test_conformant_vault.py` asserts the zero-errors
    gate against. Two commands composing different rule sets would mean two
    definitions of "clean".

    **Both house rules are raised to `severity="error"`.** `section_rule`
    defaults to `warn`, and the fixture test records why it is overridden: a
    gate a malformed section can pass is not a gate.

    **`repo_root` stays optional and skips rather than reports.** Omitting it
    drops `targets.affects-missing` and `plan.action-target-missing`, because
    not knowing where the repo is says nothing about whether the paths are
    good.

    Raises `OSError` for a missing declarations directory and `ValueError` for
    a malformed one, straight out of `load_schemas` / `load_sections`. That is
    caller configuration, not bundle content -- `code_wiki_okf.cli.validate`
    guards the identical load the identical way, and `cli.py` turns it into
    exit 1 with the message.
    """
    declarations = root if declarations_dir is None else declarations_dir
    return (
        schema_rule(load_schemas(declarations / "_schema"), severity="error"),
        section_rule(load_sections(declarations / "_sections"), severity="error"),
        *lane_rules(repo_root=repo_root),
    )


def _first_h1(body: str) -> str | None:
    """The first ATX level-one heading in *body*, or `None`.

    Fenced regions are skipped: a fence is the one place a `# ` line is not a
    heading, and a spec opening with a shell transcript is not exotic.
    """
    fenced = False
    for line in body.splitlines():
        if line.lstrip().startswith(_FENCES):
            fenced = not fenced
            continue
        if fenced:
            continue
        match = _H1_RE.match(line)
        if match is not None:
            return match.group(1)
    return None


def _title_for(target: Path, fallback: str) -> str:
    """*target*'s own H1, or *fallback*.

    Read through `okf_io.load` rather than off the raw text, so a spec with
    frontmatter does not offer a YAML comment as its heading -- the splitter
    is the only thing that knows where the body starts.
    """
    if not target.is_file():
        return fallback
    return _first_h1(load(target).body) or fallback


def stamp_for(root: Path, item: WorkItem, source_id: str) -> tuple[ArtifactRef, str]:
    """The `ArtifactRef` *source_id* names for *item*, and the title to stamp.

    Inverts `Transition.stamp_source` -- a bare id, `design-spec` or `plan` --
    back to the `(phase, kind)` pair `paths.artifact_path` composes from. That
    is the seam child 3 left open when it deleted `artifact_slot` on the
    promise that the destination is derivable from `stamp_source` plus the
    in-flight phase.

    **The title is the artifact's own H1 when the file has one** (C6-F), and
    `"<label> — <item title>"` otherwise. The conformant fixture authors
    `Design spec — the filing writer` for an item titled `The filing writer`,
    and that string is verbatim the artifact's H1; deriving would give
    `Design spec — The filing writer` and the two would disagree cosmetically
    forever.

    Raises `KeyError` for an id outside the two `Transition.stamp_source` can
    carry. Caller error: nothing else is stamped by this path.
    """
    phase, kind = _STAMP_SLOTS[source_id]
    ref = artifact_path(item.slug, phase, kind, archived=item.archived)
    return ref, _title_for(ref.path(root), f"{_STAMP_LABELS[source_id]} — {item.title}")


def plan_row_splice(document: Document, ref: ArtifactRef) -> TextSplice:
    """The splice `ensure_plan_row` would apply. Pure -- writes nothing.

    Split out so `advance --dry-run` can report whether the row *would* change
    rather than reporting the transition's intent. `splice_text` is already a
    pure text->text function, so the split costs one call and buys an honest
    preview.

    **The action cell names `ref.resource`, the root-absolute spelling.** Its
    leading slash is what keeps `plan.action-target-missing` off it: that rule
    fullmatches `[\\w][\\w.\\-]*/[\\w.\\-/]+` against one whitespace token, and a
    token starting with `/` cannot match. The bundle-relative form would make
    every advanced item report an error under `--repo-root`.

    Idempotence is keyed on the action cell with `on_conflict="skip"`: a second
    advance over the same item leaves the row alone, and a human who reworded
    the other two cells keeps their words.
    """
    return splice_text(
        document.body,
        PLAN_HEADING,
        PLAN_TABLE_SPEC,
        {
            "action": f"Execute implementation plan: {ref.resource}",
            "done when": _ROW_DONE_WHEN,
            "rationale": _ROW_RATIONALE,
        },
        key="action",
        on_conflict="skip",
    )


def ensure_plan_row(document: Document, ref: ArtifactRef) -> bool:
    """Apply `plan_row_splice` to *document*. Returns whether the body changed.

    **`splice_text` + `set_body`, not `tables.plan_row` / `tables.apply`**
    (C6-E). The bundle-level pair plans and writes against a `Bundle`
    independently, so pairing it with `advance.apply` would write the same page
    twice -- two writes, two chances to half-apply, and okf-io's byte-fidelity
    splice running over a file that already moved underneath it. The pure
    function underneath both hands its result to `Document.set_body`, and the
    whole transition stays one `Document.save()`.

    The caller saves, matching `sources.upsert`.
    """
    splice = plan_row_splice(document, ref)
    if not splice.changed:
        return False
    document.set_body(splice.after)
    return True


@dataclass(frozen=True, slots=True)
class AdvanceOutcome:
    """What one advance did, in one save.

    Speaks the writer vocabulary `AdvancePlan`, `FilingPlan`, `IndexUpdate` and
    `BundleInstall` already use: a `changed` that renders nothing, and a plan
    that renders itself on demand.

    `plan_row` is the splice's real answer in **both** modes -- `plan_row_splice`
    is pure, so a dry run can compute it without writing.
    """

    plan: AdvancePlan
    stamped: ArtifactRef | None
    stamp_title: str | None
    plan_row: bool
    written: bool

    @property
    def changed(self) -> bool:
        return self.written


def advance_and_stamp(
    bundle: Bundle,
    slug: str,
    *,
    today: date,
    effort: str | None = None,
    owner: str | None = None,
    resolved_in: str | None = None,
    worktree: str | None = None,
    branch: str | None = None,
    dry_run: bool = True,
) -> AdvanceOutcome:
    """Advance *slug*, stamp its artifact, ensure its plan row -- in **one save**.

    `advance.apply` sets the frontmatter, `sources.upsert` merges the stamp,
    `ensure_plan_row` hands the spliced body to `Document.set_body`, then a
    single `Document.save()`. Three effects, one write (C6-E) --
    `sources.upsert` was built for exactly this: it mutates the in-memory
    `Document` and lets the caller save.

    **The stamp is unconditional** (C6-G): no existence check before writing
    the pointer, matching the reference `gw work advance`. A pointer at a
    missing artifact is `targets.artifact-missing`, a `warn`, and the caller's
    post-write lint is what surfaces it immediately rather than at the next
    `lint` run.

    `sync_plan_table` is nested under the stamp because `workflow.py` sets it on
    exactly one transition -- plan-complete -> execute -- where `stamp_source`
    is `PLAN_SOURCE_ID`. The row names the same artifact the stamp does, and
    resolving them in one pass is what keeps them from disagreeing.

    `dry_run=True` is okf-io's writer default: the call plans and writes
    nothing. It is the real run minus its last line, computed the same way.

    Nothing here raises for content. An unparseable page never reaches the
    write: `load_items` projects it with `type=""`, `route` reports it as a
    blocker, and `advance` returns a `blocked` refusal -- the same invariant
    `test_advance.py` already pins for `apply`.
    """
    items = load_items(bundle)
    plan = advance(
        items,
        slug,
        today=today,
        effort=effort,
        owner=owner,
        resolved_in=resolved_in,
        worktree=worktree,
        branch=branch,
    )
    if plan.refusal is not None:
        return AdvanceOutcome(plan=plan, stamped=None, stamp_title=None, plan_row=False, written=False)

    item = next(candidate for candidate in items if candidate.slug == slug)
    # Direct indexing: the item came out of `bundle.concepts`, so the key is
    # there by construction. A `KeyError` here would be a bug, not content.
    document = bundle.concepts[item.path.removesuffix(".md")]

    stamped: ArtifactRef | None = None
    title: str | None = None
    row = False
    if plan.stamp_source is not None:
        stamped, title = stamp_for(bundle.root, item, plan.stamp_source)
        if plan.sync_plan_table:
            row = plan_row_splice(document, stamped).changed

    if dry_run:
        return AdvanceOutcome(plan=plan, stamped=stamped, stamp_title=title, plan_row=row, written=False)

    apply_advance(document, plan)
    if stamped is not None and title is not None:
        upsert(document, stamped, title=title)
        if plan.sync_plan_table:
            row = ensure_plan_row(document, stamped)
    document.save()
    return AdvanceOutcome(plan=plan, stamped=stamped, stamp_title=title, plan_row=row, written=True)


@dataclass(frozen=True, slots=True)
class FilingOutcome:
    """What one filing did across its three files.

    `plan` is the `FilingPlan` -- its `refusal` and `warnings` are the caller's
    to report. `path` is `None` for a dry run and for a refusal, which is the
    same statement: nothing landed.
    """

    plan: FilingPlan
    path: Path | None
    indexes: tuple[IndexUpdate, ...]
    logged: str | None

    @property
    def changed(self) -> bool:
        return self.path is not None


def append_lane_log(root: Path, entry: str, *, on: date) -> str | None:
    """Append one `log.md` line to the bundle at *root*. Returns what landed.

    **The log rule** (C6-I): a command that changes *what the vault contains*
    appends one line -- `init` (already does, inside `install_bundle`), `file`,
    `archive`. A command that changes an existing page's fields does not --
    `advance`, `sync-children`. One rule, so neither half needs remembering.

    `None` when the bundle carries no root `log.md`, when it will not parse, or
    when the append is refused. Refusals are reported by returning nothing
    rather than by raising: nothing on this package's content path raises, and
    an out-of-order log is a human's problem, not a reason to lose the write
    that already happened. `init.install_bundle` makes the same call for the
    same reason.
    """
    log_document = load_bundle(root).logs.get("")
    if log_document is None or log_document.parse_error is not None:
        return None
    try:
        append_log_entry(log_document, entry, on=on, dry_run=False)
    except (ValueError, OSError):
        return None
    return entry


def file_and_reconcile(
    root: Path,
    *,
    type: str,  # the lane's own field name, as `filing.file_item`'s already is
    title: str,
    description: str,
    on: date,
    words: str | None = None,
    epic_child: bool = False,
    parent: str | None = None,
    depends_on: Sequence[str] = (),
    affects: Sequence[str] = (),
    tags: Sequence[str] = (),
    section_set: SectionSet,
    dry_run: bool = True,
) -> FilingOutcome:
    """File one item, reconcile `work/index.md`, append one `log.md` line.

    This is the composition `README.md`'s filing section defers to "a composing
    CLI's": filing writes **one page** (C2-F), because index reconciliation is
    bundle-wide and folding it in would make a per-item writer span three files
    in two directories.

    The bundle is loaded **after** the page lands, through `IGNORE`: the
    reconcile has to see the new page, and `ARCHIVE_IGNORE` would make
    `update_index` want a `# Subdirectories` entry for every working directory.
    Same reload `archive.apply_archive` does, for the same reasons.

    `create_missing=True` for `work/` alone, on C4-D's argument transferred:
    `init` scaffolds neither lane index, so a vault whose first act is `file`
    would otherwise never get one.

    `descriptions="preserve"` is okf-io's default and stays: okf-io owns
    *which* entries appear, the human owns *what they say* (ADR-0009).

    `dry_run=True` matches every other writer here. A refusal writes nothing at
    all -- not the page, not the index, not the log -- because `apply` is never
    reached.
    """
    plan = file_item(
        root,
        type=type,
        title=title,
        description=description,
        on=on,
        words=words,
        epic_child=epic_child,
        parent=parent,
        depends_on=depends_on,
        affects=affects,
        tags=tags,
        section_set=section_set,
    )
    if plan.refusal is not None or dry_run:
        return FilingOutcome(plan=plan, path=None, indexes=(), logged=None)

    landed = apply_filing(plan)
    indexes = update_index(
        load_bundle(root, ignore=IGNORE),
        directories=[WORK_DIR],
        create_missing=True,
        dry_run=False,
    )
    return FilingOutcome(
        plan=plan,
        path=landed,
        indexes=indexes,
        logged=append_lane_log(root, f"filed {plan.slug} ({type})", on=on),
    )


__all__ = [
    "PLAN_HEADING",
    "AdvanceOutcome",
    "FilingOutcome",
    "advance_and_stamp",
    "append_lane_log",
    "ensure_plan_row",
    "file_and_reconcile",
    "plan_row_splice",
    "rule_set",
    "stamp_for",
]
