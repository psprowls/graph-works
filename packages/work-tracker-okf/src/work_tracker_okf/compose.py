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
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from okf_ext.logs import append_entry, atomic_replace, locked_log
from okf_ext.placement import placement_rule
from okf_ext.render import render_rule
from okf_ext.schemas import load_schemas, schema_rule
from okf_ext.sections import section_rule
from okf_ext.shape import SectionSet, load_sections
from okf_ext.tables import TextSplice, splice_text
from okf_io import (
    Bundle,
    Document,
    LogAppend,
    Rule,
    append_log_entry,
    load,
    parse,
)

from work_tracker_okf.advance import AdvancePlan, advance
from work_tracker_okf.advance import apply as apply_advance
from work_tracker_okf.decisions import DecisionApplication, DecisionPlan, apply_plan, ledger_ref
from work_tracker_okf.filing import FilingPlan, FilingRefusal, FilingSeed, _materialize_frontmatter, plan_filing
from work_tracker_okf.filing import apply as apply_filing
from work_tracker_okf.indexes import LaneIndexPlan, plan_indexes
from work_tracker_okf.items import WorkItem, load_items, placement_directories
from work_tracker_okf.paths import MANAGED_ARTIFACTS, ArtifactRef, artifact_ref, item_page, parse_item_path
from work_tracker_okf.rules import PLAN_TABLE_SPEC, lane_rules
from work_tracker_okf.sources import upsert
from work_tracker_okf.vocabulary import PARENT_TYPES, PLAN_SOURCE_ID, SPEC_SOURCE_ID

#: The heading `_rules/plan.py` reads and all six `sections/` declarations
#: require. Named here rather than imported: `_rules` is private, and one
#: string is cheaper than widening a private module's surface.
PLAN_HEADING = "Plan"

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
    vault_root: Path | None = None,
    declarations_dir: Path | None = None,
) -> tuple[Rule, ...]:
    """The one rule set `lint` and `advance` both validate against (C6-M).

    Identical to the tuple `test_conformant_vault.py` asserts the zero-errors
    gate against. Two commands composing different rule sets would mean two
    definitions of "clean".

    **Both house rules are raised to `severity="error"`.** `section_rule`
    defaults to `warn`, and the fixture test records why it is overridden: a
    gate a malformed section can pass is not a gate.

    **`render_rule()` stays at its `warn` default.** Before this, no rule in
    this set ever walked a work item for `[[wikilink]]` syntax at all --
    `render_rule` was wired into the wiki lane only. `render.wikilink-target`
    is a WARN by design (broken links are warn, never error; see
    ADR-0004) and must not be raised to error here: this vault currently
    trips it hundreds of times over, and `gw work advance`'s exit code must
    stay unaffected.

    **`placement_rule` is raised to `error` too, and takes no `depth` map.**
    All seven types declare `work/`, so a plain prefix comparison passes root,
    nested child, and local archive item paths; `depth="exact"` would flag
    every nested item, whose remainder still carries a `/`. The severity is
    the same argument the two house rules already make, in this lane's terms: a
    work page outside `work/` is invisible to `load_items`, so it gets no
    routing, no rollup, no archive eligibility, and every lane rule silently
    skips it. It is not merely unexpected, it is unreachable. The map is
    `items.placement_directories`, narrowed to this lane's own types -- see
    there for why an allow-list.

    **`repo_root` and `vault_root` stay optional and skip rather than
    report.** `repo_root` gates `targets.affects-missing`; either one present
    is enough to run `plan.action-target-missing`, which checks a token
    against whichever root(s) it is given -- not knowing where a root is says
    nothing about whether the paths under it are good. The two are distinct
    because a plan action can name either a code path (`repo_root`) or, via
    the standard "Execute implementation plan: ..." row, its own artifact's
    vault-relative path (`vault_root`); in a split topology (workspace and
    code repo are different git repos) the two roots are different
    directories.

    Raises `OSError` for a missing declarations directory and `ValueError` for
    a malformed one, straight out of `load_schemas` / `load_sections`. That is
    caller configuration, not bundle content -- `code_wiki_okf.cli.validate`
    guards the identical load the identical way, and `cli.py` turns it into
    exit 1 with the message.
    """
    declarations = root if declarations_dir is None else declarations_dir
    schema_set = load_schemas(declarations / "schema")
    return (
        schema_rule(schema_set, severity="error"),
        section_rule(load_sections(declarations / "sections"), severity="error"),
        render_rule(),
        placement_rule(placement_directories(schema_set), severity="error"),
        *lane_rules(repo_root=repo_root, vault_root=vault_root),
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

    Resolves `Transition.stamp_source` through the managed-artifact registry,
    keeping artifact names and locations centralized.

    **The title is the artifact's own H1 when the file has one** (C6-F), and
    `"<label> — <item title>"` otherwise. The conformant fixture authors
    `Design spec — the filing writer` for an item titled `The filing writer`,
    and that string is verbatim the artifact's H1; deriving would give
    `Design spec — The filing writer` and the two would disagree cosmetically
    forever.

    Raises `KeyError` for an id outside the two `Transition.stamp_source` can
    carry. Caller error: nothing else is stamped by this path.
    """
    if source_id not in {SPEC_SOURCE_ID, PLAN_SOURCE_ID}:
        raise KeyError(source_id)
    ref = artifact_ref(item.path, MANAGED_ARTIFACTS[source_id])
    return ref, _title_for(ref.path(root), f"{_STAMP_LABELS[source_id]} — {item.title}")


def apply_decision_and_register(
    root: Path,
    owner_path: str,
    plan: DecisionPlan,
    *,
    lock: Path,
) -> DecisionApplication:
    """Apply a parent-owned decision plan and register its durable ledger."""
    ref = ledger_ref(owner_path)
    if plan.ledger != ref.path(root):
        raise ValueError(f"decision plan ledger {plan.ledger} does not belong to {owner_path!r}")
    document = load(item_page(owner_path).path(root))
    if document.fm_data().get("type") not in PARENT_TYPES:
        raise ValueError(f"{owner_path!r} is not a parent-capable decision owner")
    application = apply_plan(plan, lock=lock)
    if application.written and upsert(document, ref, title="Decisions"):
        document.save()
    return application


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
    path: str,
    *,
    today: date,
    effort: str | None = None,
    owner: str | None = None,
    resolved_in: str | None = None,
    released_at: date | None = None,
    worktree: str | None = None,
    branch: str | None = None,
    return_: bool = False,
    dry_run: bool = True,
) -> AdvanceOutcome:
    """Advance *path*, stamp its artifact, ensure its plan row -- in **one save**.

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
        path,
        today=today,
        effort=effort,
        owner=owner,
        resolved_in=resolved_in,
        released_at=released_at,
        worktree=worktree,
        branch=branch,
        return_=return_,
        unreadable=bundle.unreadable,
    )
    if plan.refusal is not None:
        return AdvanceOutcome(plan=plan, stamped=None, stamp_title=None, plan_row=False, written=False)

    item = next(candidate for candidate in items if candidate.path == path)
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
class FilingApplication:
    page: Path | None = None
    indexes: tuple[LaneIndexPlan, ...] = ()
    log: LogAppend | None = None
    written: bool = False


FilingCompositionRefusal = FilingRefusal | Literal["index-refused", "log-refused"]


@dataclass(frozen=True, slots=True)
class FilingCompositionPlan:
    filing: FilingPlan
    indexes: tuple[LaneIndexPlan, ...]
    log: LogAppend | None
    refusal: FilingCompositionRefusal | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FilingOutcome:
    plan: FilingCompositionPlan
    application: FilingApplication = FilingApplication()


class FilingApplyError(OSError):
    def __init__(self, message: str, application: FilingApplication) -> None:
        super().__init__(message)
        self.application = application


class FilingPlanStaleError(FilingApplyError):
    """A preflighted log snapshot no longer matches the file being applied."""

    def __init__(
        self,
        message: str,
        application: FilingApplication,
        *,
        expected: bytes,
        actual: bytes | None,
    ) -> None:
        super().__init__(message, application)
        self.expected = expected
        self.actual = actual


def _planned_document(filing: FilingPlan) -> Document:
    document = parse("", path=filing.target)
    for key, value in _materialize_frontmatter(filing.frontmatter).items():
        document.set(key, value)
    document.set_body(filing.body)
    return document


def _filing_lanes(filing: FilingPlan) -> tuple[str, ...]:
    location = parse_item_path(filing.path)
    if location is None:
        return ()
    owned = (Path(path).parent.as_posix() for path in filing.required_indexes)
    return tuple(sorted({location.lane, *owned}))


def _unchanged_indexes(root: Path, items: Sequence[WorkItem], lanes: Sequence[str] = ()) -> tuple[LaneIndexPlan, ...]:
    return tuple(replace(plan, after=plan.before or "") for plan in plan_indexes(root, items, lanes=lanes))


def plan_file_and_reconcile(
    bundle: Bundle,
    items: Sequence[WorkItem],
    seed: FilingSeed,
    section_set: SectionSet,
) -> FilingOutcome:
    """Plan the page, lane index, and root log without writing any of them.

    *lane_dir* is `plan_filing`'s, passed through, and it also names the index
    reconciled here: the lane index belongs beside the pages it lists, so a
    page filed somewhere else would otherwise be added to an index it does not
    live under. `None` keeps `WORK_DIR` on both, which is every caller holding
    no `SchemaSet`.
    """
    filing = plan_filing(bundle.root, items, seed, section_set)
    if filing.refusal is not None:
        return FilingOutcome(
            plan=FilingCompositionPlan(
                filing=filing,
                indexes=(),
                log=None,
                refusal=filing.refusal,
                warnings=filing.warnings,
            )
        )

    document = _planned_document(filing)
    planned_id = filing.path
    lanes = _filing_lanes(filing)
    synthetic = replace(
        bundle,
        concepts=MappingProxyType({**bundle.concepts, planned_id: document}),
    )
    try:
        indexes = plan_indexes(bundle.root, load_items(synthetic), lanes=lanes)
    except ValueError as exc:
        return FilingOutcome(
            plan=FilingCompositionPlan(
                filing=filing,
                indexes=_unchanged_indexes(bundle.root, items, lanes),
                log=None,
                refusal="index-refused",
                warnings=(*filing.warnings, str(exc)),
            )
        )

    log_document = bundle.logs.get("")
    if log_document is None or log_document.parse_error is not None:
        detail = "root log.md is missing" if log_document is None else str(log_document.parse_error)
        return FilingOutcome(
            plan=FilingCompositionPlan(
                filing=filing,
                indexes=indexes,
                log=None,
                refusal="log-refused",
                warnings=(*filing.warnings, detail),
            )
        )
    try:
        log = append_log_entry(
            log_document,
            f"filed {filing.path} ({seed.type})",
            on=seed.on,
            dry_run=True,
        )
    except ValueError as exc:
        return FilingOutcome(
            plan=FilingCompositionPlan(
                filing=filing,
                indexes=indexes,
                log=None,
                refusal="log-refused",
                warnings=(*filing.warnings, str(exc)),
            )
        )
    return FilingOutcome(
        plan=FilingCompositionPlan(
            filing=filing,
            indexes=indexes,
            log=log,
            warnings=filing.warnings,
        )
    )


def apply_file_and_reconcile(plan: FilingCompositionPlan) -> FilingApplication:
    """Apply a fully preflighted filing composition.

    The lane comes off `plan.filing.target` rather than from a second argument
    -- the canonical item page under `<root>/<lane>`, so the indexes written
    here are exactly those `plan_file_and_reconcile` planned at every depth.
    """
    if plan.refusal is not None:
        return FilingApplication()

    application = FilingApplication()
    root = plan.filing.target
    for _ in Path(plan.filing.path).parts:
        root = root.parent
    try:
        if plan.filing.target.exists():
            raise FileExistsError(f"{plan.filing.target}: a page already exists here")
        if plan.filing.owned_directory.exists():
            raise FileExistsError(f"{plan.filing.owned_directory}: an owned directory already exists here")

        page = apply_filing(plan.filing)
        application = FilingApplication(page=page)

        for index in plan.indexes:
            index.path.parent.mkdir(parents=True, exist_ok=True)
            index.path.write_text(index.after, encoding="utf-8", newline="")
        indexes = plan.indexes
        application = FilingApplication(page=page, indexes=indexes)

        if plan.log is not None:
            log_path = Path(plan.log.path) if plan.log.path is not None else root / "log.md"
            expected = plan.log.before.encode("utf-8")
            with locked_log(log_path):
                try:
                    actual = log_path.read_bytes()
                except FileNotFoundError as exc:
                    raise FilingPlanStaleError(
                        f"{log_path}: log disappeared after filing was planned",
                        application,
                        expected=expected,
                        actual=None,
                    ) from exc
                if actual != expected:
                    raise FilingPlanStaleError(
                        f"{log_path}: log changed after filing was planned",
                        application,
                        expected=expected,
                        actual=actual,
                    )
                atomic_replace(log_path, plan.log.after.encode("utf-8"))
            application = FilingApplication(page=page, indexes=indexes, log=plan.log)
    except FilingPlanStaleError:
        raise
    except OSError as exc:
        raise FilingApplyError(str(exc), application) from exc

    return FilingApplication(
        page=application.page,
        indexes=application.indexes,
        log=application.log,
        written=True,
    )


def append_lane_log(root: Path, entry: str, *, on: date) -> str | None:
    """Append one `log.md` line to the bundle at *root*. Returns what landed.

    **The log rule** (C6-I): a command that changes *what the vault contains*
    appends one line -- `init` (already does, inside `install_bundle`), `file`,
    `archive`. A command that changes an existing page's fields does not --
    `advance`. One rule, so neither half needs remembering.

    `None` when the bundle carries no root `log.md`, when it will not parse, or
    when the append is refused. Refusals are reported by returning nothing
    rather than by raising: nothing on this package's content path raises, and
    an out-of-order log is a human's problem, not a reason to lose the write
    that already happened. `init.install_bundle` makes the same call for the
    same reason.
    """
    return append_entry(root, entry, on=on)


__all__ = [
    "PLAN_HEADING",
    "AdvanceOutcome",
    "FilingApplication",
    "FilingApplyError",
    "FilingCompositionPlan",
    "FilingOutcome",
    "FilingPlanStaleError",
    "advance_and_stamp",
    "append_lane_log",
    "apply_decision_and_register",
    "apply_file_and_reconcile",
    "ensure_plan_row",
    "plan_file_and_reconcile",
    "plan_row_splice",
    "rule_set",
    "stamp_for",
]
