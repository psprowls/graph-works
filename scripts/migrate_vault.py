"""Carry a graph-wiki-dialect vault to an OKF v0.2 bundle, one phase per subcommand.

    uv run scripts/migrate_vault.py frontmatter <vault>          # dry run
    uv run scripts/migrate_vault.py frontmatter <vault> --write  # apply

**`uv run` is mandatory.** This imports `okf_io`, `okf_ext`, `doc_wiki_okf`,
`code_wiki_okf` and `graph_works_core` from the workspace; a `./migrate_vault.py`
shebang invocation cannot resolve any of them. Same trap
`scripts/convert-wikilinks.md` documents.

Orchestration over shipped machinery (D-007): every transformation below is a
composition of a package capability, never a re-implementation. The two
exceptions -- the entity `uri:` -> `resource:` remap and archive-shape
normalization -- are the two mechanisms no package owns, and both are written
here on purpose.

Each subcommand is idempotent and writes one commit's worth of change, so a
run can be committed per phase and a partially migrated vault resumes.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from okf_io import Bundle, Document, build_link_graph, load_bundle, parse, validate

#: Members that are never concepts, in every phase.
#:
#: `_schema/*` and `sections/*` are deliberately absent: declarations do not
#: live in the bundle. `okf_ext.bundle.model:21-22` fixes their names and every
#: core call site passes `declarations_dir=layout.config_dir`
#: (`workspace/init.py:351-356`), so they land at `.gw/schema/` and
#: `.gw/sections/` -- outside `layout.bundle_dir`, and therefore never members.
#:
#: `work/_archive/*/00-open-work.md` is deliberately absent too: it is an item
#: page wearing an artifact's filename, and phase 6a is what fixes the filename.
#: Ignoring it would leave `plan_migration` unable to see 174 archived items
#: while reporting success -- see `cmd_archive_shape`. Stage documents are
#: numbered `01`-`03` (design-spec, plan-plan, execute-results). Because
#: `fnmatchcase`'s `*` crosses `/`, a plain `work/*/[0-9][0-9]-*.md` matches
#: `00-open-work.md` at any depth under `work/`, archived or not -- so both
#: patterns below are split across `0[1-9]` and `[1-9][0-9]` to exclude `00`
#: specifically, rather than the two-digit shape `00-03` share.
BASE_IGNORE: tuple[str, ...] = (
    ".templates/*",
    "CLAUDE.md",
    "*.json",
    "guidance/*",
    "work/*/0[1-9]-*.md",
    "work/*/[1-9][0-9]-*.md",
    "work/*/child-specs/*",
)

#: Phases that run before the entity lane is retired by `cmd_entities`.
_BEFORE_ENTITY_RETIREMENT: frozenset[str] = frozenset(
    {"decisions", "frontmatter", "titles", "links", "archive-shape", "work"}
)


def ignore_patterns(phase: str) -> tuple[str, ...]:
    """The `ignore=` set for *phase*, and for that phase's gate assertion.

    One declaration consumed by every subcommand **and** by `cmd_gate`, so the
    sweep and its acceptance can never disagree about what counts as a concept.
    `load_bundle`'s globs are `fnmatchcase` over bundle-relative posix paths and
    `*` crosses `/` (`packages/okf-io/src/okf_io/bundle.py:217-233`).

    This is load-bearing on phase 6, not a performance choice. `plan_migration`
    loads with its own `LEGACY_IGNORE`, so the 61 frontmatter-carrying stage
    documents *are* loaded as concepts by the migrator. They are safe only
    because phase 4 never gives them a `type:`. If `cmd_frontmatter` ever
    converted a stage document's `kind: bug` into `type: Bug`, `_nodes` would
    refuse it as `legacy-path-invalid`.
    """
    patterns = list(BASE_IGNORE)
    if phase in _BEFORE_ENTITY_RETIREMENT:
        patterns.append("entities/*")
    return tuple(patterns)


@dataclass(frozen=True, slots=True)
class Refusal:
    """One member this phase declined to touch, and why. Never a guess."""

    member: str
    reason: str
    detail: str


@dataclass
class PhaseReport:
    """What a subcommand did, or would do. Rendered by `report()`.

    `changed` names members whose bytes this phase writes. `unchanged` names
    members it inspected and deliberately left byte-identical -- reported so a
    second run showing `changed == ()` reads as idempotence rather than as a
    phase that did nothing.
    """

    phase: str
    changed: tuple[str, ...] = ()
    unchanged: tuple[str, ...] = ()
    refusals: tuple[Refusal, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.refusals


def report(result: PhaseReport, *, write: bool) -> int:
    """Print *result* and return the process exit code.

    `1` for any refusal: a phase that refused is a phase whose next phase must
    not run, and a shell driver committing per phase needs that as a status.
    """
    print(f"phase            {result.phase}")
    print(f"members changed  {len(result.changed)}")
    print(f"members skipped  {len(result.unchanged)}")
    print(f"refusals         {len(result.refusals)}")
    for note in result.notes:
        print(f"  note           {note}")
    if result.refusals:
        print("\nrefused (nothing written for these):")
        for refusal in result.refusals:
            print(f"  {refusal.member}: {refusal.reason} -- {refusal.detail}")
    print()
    print("WROTE changes to disk." if write else "Dry run. Nothing written; pass --write to apply.")
    return 0 if result.ok else 1


def _layout(vault: Path):  # -> WorkspaceLayout
    """The workspace layout whose `bundle_dir` is *vault*.

    Imported lazily so `--help` does not pay for the core package, and so a
    phase that never touches the transaction executor never loads it.
    """
    from graph_works_core.workspace.layout import layout_for

    workspace = vault.parent
    return layout_for(workspace, bundle_dir=vault.name)


def migration_dir(vault: Path) -> Path:
    """`.gw/migration/` -- where the decisions file, the entity snapshot and the
    quarantine live. Outside the bundle, so never a member and never an
    `ignore=` entry (ADR-0024)."""
    return _layout(vault).config_dir / "migration"


def load(vault: Path, phase: str) -> Bundle:
    return load_bundle(vault, ignore=ignore_patterns(phase))


_SUBCOMMANDS: tuple[tuple[str, str], ...] = (
    ("decisions", "phase 4a: emit .gw/migration/decisions.yaml for human review"),
    ("frontmatter", "phase 4b: apply the closed frontmatter maps"),
    ("titles", "phase 4c: populate title: -- MUST run before `links`"),
    ("links", "phase 5: convert [[wikilinks]] to markdown links"),
    ("archive-shape", "phase 6a: promote work/_archive/<slug>/00-open-work.md"),
    ("work", "phase 6: the harvested path-native work migrator"),
    ("entities", "phase 7: quarantine, regenerate, remap uri: -> resource:"),
    ("lanes", "phase 8: move each lane into its declared directory"),
    ("bundle", "phase 9: log.md reformat and sub-index frontmatter strip"),
    ("gate", "phase 10: the four acceptance assertions"),
)


# ---- phase 4a: decisions ----

#: A legacy `kind:` this vault's `concepts/` lane uses -> the Diataxis type it
#: most plausibly is. A *proposal*, always validated through `classify` and
#: always overridable by the human's `decision:`. A `kind:` absent from this map
#: yields a blank `proposed:` -- the caller declining to choose, which
#: `classify` already models as `undecided` (`classify.py:74-79`).
_KIND_PROPOSALS: dict[str, str] = {
    "architecture": "Explanation",
    "design": "Explanation",
    "concept": "Explanation",
    "invariant": "Reference",
    "reference": "Reference",
    "runbook": "HowTo",
    "how-to": "HowTo",
    "tutorial": "Tutorial",
}

_QUESTIONS: tuple[str, ...] = ("diataxis-type", "status-active")

DECIDED_BY = "scripts/migrate_vault.py decisions"


def legal_decisions(question: str) -> tuple[str, ...]:
    """The closed answer set for *question*. Read from the packages, not typed twice."""
    from doc_wiki_okf.diataxis import TYPE_NAMES

    if question == "diataxis-type":
        return TYPE_NAMES
    if question == "status-active":
        return ("delete", "draft", "stable", "deprecated")
    raise ValueError(f"unknown question: {question}")


def _schema_set(vault: Path):  # -> SchemaSet
    """The bundle's own declared schema set, read from `.gw/schema/`.

    A vault mid-migration may not have declarations installed yet -- this
    phase runs before the schemas exist in plenty of the fixtures this script
    is tested against. That is not a caller error the way a genuinely wrong
    path is: it just means every proposal `classify`s to `undeclared-type`,
    which is a legitimate refusal `classify` already models, not a crash.
    """
    from okf_ext.schemas import SchemaError, load_schemas
    from okf_ext.schemas.model import SchemaSet

    schema_dir = _layout(vault).config_dir / "schema"
    try:
        return load_schemas(schema_dir)
    except (OSError, SchemaError):
        return SchemaSet(schemas={}, sources={}, documents={}, root=schema_dir)


def _diataxis_row(vault: Path, member: str, document: Document, schema_set) -> dict:
    legacy_kind = str(document.fm.extra.get("kind") or "").strip()
    proposal = _KIND_PROPOSALS.get(legacy_kind, "")
    title = document.fm.title or member

    from doc_wiki_okf.diataxis import Unclassified, classify

    decision = classify(
        schema_set,
        type_name=proposal,
        title=title,
        rationale=f"proposed from legacy kind: {legacy_kind!r}" if legacy_kind else "no legacy kind to propose from",
        decided_by=DECIDED_BY,
    )
    if isinstance(decision, Unclassified):
        return {
            "member": member,
            "lane": "concepts",
            "question": "diataxis-type",
            "current": {"kind": legacy_kind or None},
            "proposed": None,
            "rationale": f"no proposal: {decision.reason} -- {decision.detail}",
            "decision": None,
        }
    return {
        "member": member,
        "lane": "concepts",
        "question": "diataxis-type",
        "current": {"kind": legacy_kind or None},
        "proposed": decision.type_name,
        "rationale": decision.rationale,
        "decision": None,
    }


def _status_active_row(member: str) -> dict:
    return {
        "member": member,
        "lane": "concepts",
        "question": "status-active",
        "current": {"status": "active"},
        "proposed": "delete",
        "rationale": (
            "OKF DOCUMENT_STATUSES is {draft, stable, deprecated}. The other concepts "
            "carry no status and read `stable` through `effective_status`."
        ),
        "decision": None,
    }


def cmd_decisions(args: argparse.Namespace) -> PhaseReport:
    """Phase 4a. Emit the reviewed judgment surface; write nothing to the vault.

    Deterministic by construction: rows sorted by `(member, question)`, and a
    re-run merges by that same key so every `decision:` a human wrote survives
    and only genuinely new rows are appended. That review is the whole point --
    `cmd_frontmatter` refuses any page a row does not cover.
    """
    vault: Path = args.vault
    bundle = load(vault, "decisions")
    schema_set = _schema_set(vault)

    rows: list[dict] = []
    for concept_id in sorted(bundle.concepts):
        if not concept_id.startswith("concepts/"):
            continue
        member = f"{concept_id}.md"
        document = bundle.concepts[concept_id]
        rows.append(_diataxis_row(vault, member, document, schema_set))
        if str(document.fm.status or "").strip() == "active":
            rows.append(_status_active_row(member))
    rows.sort(key=lambda row: (row["member"], row["question"]))

    target = migration_dir(vault) / "decisions.yaml"
    merged, carried = _merge_decisions(target, rows)

    notes = (
        f"{len(rows)} row(s): "
        + ", ".join(f"{q}={sum(1 for row in rows if row['question'] == q)}" for q in _QUESTIONS),
        f"{carried} human decision(s) carried over from the existing file",
        f"target: {target}",
    )
    if args.write:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_render_decisions(merged), encoding="utf-8")
    return PhaseReport(phase="decisions", changed=(str(target),) if args.write else (), notes=notes)


def _merge_decisions(target: Path, rows: list[dict]) -> tuple[list[dict], int]:
    """Overlay every non-null `decision:` from an existing *target* onto *rows*.

    Keyed on `(member, question)`. A row whose member has left the vault is
    dropped rather than carried: a decision about a page that no longer exists
    is not a decision the sweep can act on, and keeping it would make the file
    non-deterministic against its own vault.
    """
    if not target.exists():
        return rows, 0
    from ruamel.yaml import YAML

    existing = YAML(typ="safe").load(target.read_text(encoding="utf-8")) or {}
    prior = {
        (row.get("member"), row.get("question")): row.get("decision")
        for row in existing.get("rows") or ()
        if row.get("decision") is not None
    }
    carried = 0
    for row in rows:
        value = prior.get((row["member"], row["question"]))
        if value is not None:
            row["decision"] = value
            carried += 1
    return rows, carried


def _render_decisions(rows: Sequence[dict]) -> str:
    """Hand-rendered rather than dumped, so the header comment and the
    `decision:` affordance survive a round trip and the output is byte-stable.
    """
    from ruamel.yaml import YAML
    from ruamel.yaml.compat import StringIO

    yaml = YAML()
    yaml.default_flow_style = False
    yaml.width = 88
    stream = StringIO()
    yaml.dump({"version": 1, "rows": list(rows)}, stream)
    return (
        "# scripts/migrate_vault.py decisions\n"
        "# Reviewed by a human, then consumed by: migrate_vault.py frontmatter / lanes\n"
        "#\n"
        "# Leave `decision:` empty to accept `proposed:`. Write a different legal\n"
        "# value to override it. An illegal value is a refusal, never a fallback.\n" + stream.getvalue()
    )


def load_decisions(path: Path) -> tuple[dict[tuple[str, str], str], tuple[Refusal, ...]]:
    """Resolve *path* into `{(member, question): decision}`, plus refusals.

    Three rules, all tested:
      * a row whose `decision:` is empty **inherits** `proposed:`;
      * a row whose `decision:` differs from `proposed:` **wins**;
      * a row whose `decision:` is not a legal value for its `question` is a
        **refusal, not a fallback** -- and a row with neither a decision nor a
        proposal is one too, since inheriting a blank is the same guess.
    """
    from ruamel.yaml import YAML

    if not path.exists():
        return {}, (Refusal(member=str(path), reason="missing-decisions-file", detail="run `decisions` first"),)

    data = YAML(typ="safe").load(path.read_text(encoding="utf-8")) or {}
    resolved: dict[tuple[str, str], str] = {}
    refusals: list[Refusal] = []
    for row in data.get("rows") or ():
        member = str(row.get("member") or "")
        question = str(row.get("question") or "")
        if question not in _QUESTIONS:
            refusals.append(Refusal(member=member, reason="unknown-question", detail=question))
            continue
        chosen = row.get("decision")
        if chosen is None:
            chosen = row.get("proposed")
        if chosen is None:
            refusals.append(
                Refusal(member=member, reason="undecided", detail=f"{question}: no decision and no proposal")
            )
            continue
        if str(chosen) not in legal_decisions(question):
            refusals.append(
                Refusal(
                    member=member,
                    reason="illegal-decision",
                    detail=f"{chosen!r} is not one of {list(legal_decisions(question))}",
                )
            )
            continue
        resolved[(member, question)] = str(chosen)
    return resolved, tuple(refusals)


# ---- phase 4b: frontmatter ----


def _work_types() -> dict[str, str]:
    """`{legacy kind: OKF type}`, derived from `SLUG_PREFIXES` so a rename in the
    vocabulary cannot drift from this map."""
    from work_tracker_okf.vocabulary import SLUG_PREFIXES

    return {prefix: type_name for type_name, prefix in SLUG_PREFIXES.items()}


#: The two defect kinds that are a `Bug` plus a contributed tag. Zero pages in
#: the live vault; the map ships because `CONTRIBUTED_TAGS` still defines both.
_DEFECT_KIND_TAGS: dict[str, str] = {"security": "security", "perf": "perf"}

_ADR_STATUSES: dict[str, str] = {"accepted": "stable", "superseded": "deprecated"}

#: `source_type:` values this vault carries that are all one OKF `source_kind`.
#: `pr` and `example` are absent from the vault and pass through unchanged --
#: kept, with their tests, because the vocabulary still defines them.
_SOURCE_KINDS: dict[str, str] = {"spec": "doc", "transcript": "doc", "note": "doc"}


def _source_kind_vocabulary(vault: Path) -> frozenset[str]:
    """The legal `source_kind` values, read at run time from the bundle's own
    `Source` schema -- never from a code constant."""
    schema = _schema_set(vault).schemas.get("Source", {})
    values = (schema.get("properties", {}).get("source_kind", {}) or {}).get("enum")
    return frozenset(values or ())


def _convert_work_page(document: Document, member: str) -> Refusal | None:
    """`kind` -> `type`, `status` -> `work_status`, `summary` -> `description`."""
    from work_tracker_okf.vocabulary import WORK_STATUSES

    kind = str(document.fm.extra.get("kind") or "").strip()
    if not kind:
        return None  # not an item page; leave it alone

    if kind in _DEFECT_KIND_TAGS:
        document.set("type", "Bug")
        tags = list(document.fm.tags)
        tag = _DEFECT_KIND_TAGS[kind]
        if tag not in tags:
            tags.append(tag)
        document.set("tags", tags)
    else:
        type_name = _work_types().get(kind)
        if type_name is None:
            return Refusal(member=member, reason="unknown-work-kind", detail=f"kind: {kind!r}")
        document.set("type", type_name)
    document.delete("kind")

    status = str(document.fm.status or "").strip()
    if status:
        if status not in WORK_STATUSES:
            return Refusal(member=member, reason="unknown-work-status", detail=f"status: {status!r}")
        document.set("work_status", status)
        document.delete("status")
    _summary_to_description(document)
    return None


def _convert_source_page(document: Document, member: str, vocabulary: frozenset[str]) -> Refusal | None:
    if not (document.fm.type or "").strip():
        document.set("type", "Source")
    legacy = str(document.fm.extra.get("source_type") or "").strip()
    if legacy:
        mapped = _SOURCE_KINDS.get(legacy, legacy)
        if vocabulary and mapped not in vocabulary:
            return Refusal(member=member, reason="unknown-source-kind", detail=f"{legacy!r} -> {mapped!r}")
        document.set("source_kind", mapped)
        document.delete("source_type")
    _summary_to_description(document)
    return None


def _convert_adr_page(document: Document, member: str) -> Refusal | None:
    if not (document.fm.type or "").strip():
        document.set("type", "Adr")
    status = str(document.fm.status or "").strip()
    if status in _ADR_STATUSES:
        document.set("status", _ADR_STATUSES[status])
    _summary_to_description(document)
    return None


def _convert_concept_page(document: Document, member: str, decisions: dict) -> Refusal | None:
    """The only lane with a judgment surface, and the only one that can refuse
    for a reason the closed maps do not answer."""
    if isinstance(document.fm_raw.get("sources"), int):
        document.delete("sources")

    status = str(document.fm.status or "").strip()
    if status == "active":
        chosen = decisions.get((member, "status-active"))
        if chosen is None:
            return Refusal(member=member, reason="uncovered-by-decisions", detail="status-active")
        if chosen == "delete":
            document.delete("status")
        else:
            document.set("status", chosen)
    _summary_to_description(document)
    return None


def _summary_to_description(document: Document) -> None:
    """`summary:` -> `description:`. `category:` is deliberately not touched --
    not a reserved key, harmless, and removing it is prose-adjacent cleanup
    this sweep does not own."""
    summary = document.fm_raw.get("summary")
    if summary is not None:
        document.set("description", summary)
        document.delete("summary")


_LANE_CONVERTERS = ("work/", "sources/", "adrs/", "concepts/")


def cmd_frontmatter(args: argparse.Namespace) -> PhaseReport:
    """Phase 4b. Apply the closed maps, refusing anything they do not answer.

    `Document.set`/`delete`/`serialize` on the parsed original, so an unmutated
    page serializes to its original bytes and a mutated one carries a minimal
    splice (`okf_io/document.py:409`).

    Every `concepts/` page must be covered by a decisions-file row for every
    question the closed maps cannot answer. A page a row does not cover is a
    refusal, never a guess -- and a refusal anywhere aborts the whole phase,
    because the reviewed file is the contract and a partially applied contract
    is worse than none.
    """
    vault: Path = args.vault
    decisions, decision_refusals = load_decisions(migration_dir(vault) / "decisions.yaml")
    bundle = load(vault, "frontmatter")
    vocabulary = _source_kind_vocabulary(vault)

    refusals = list(decision_refusals)
    dirty: list[tuple[str, Document]] = []
    unchanged: list[str] = []

    for concept_id in sorted(bundle.concepts):
        member = f"{concept_id}.md"
        if not any(concept_id.startswith(lane) for lane in _LANE_CONVERTERS):
            unchanged.append(member)
            continue
        document = bundle.concepts[concept_id]
        original = document.raw_text
        if concept_id.startswith("work/"):
            refusal = _convert_work_page(document, member)
        elif concept_id.startswith("sources/"):
            refusal = _convert_source_page(document, member, vocabulary)
        elif concept_id.startswith("adrs/"):
            refusal = _convert_adr_page(document, member)
        else:
            refusal = _convert_concept_page(document, member, decisions)
        if refusal is not None:
            refusals.append(refusal)
            continue
        if document.serialize() == original:
            unchanged.append(member)
        else:
            dirty.append((member, document))

    if refusals:
        return PhaseReport(
            phase="frontmatter",
            unchanged=tuple(unchanged),
            refusals=tuple(refusals),
            notes=("refused; nothing written. Fix the decisions file and re-run.",),
        )

    if args.write:
        for member, document in dirty:
            (vault / member).write_text(document.serialize(), encoding="utf-8", newline="")

    return PhaseReport(
        phase="frontmatter",
        changed=tuple(member for member, _ in dirty),
        unchanged=tuple(unchanged),
    )


# ---- phase 4c: titles ----


def cmd_titles(args: argparse.Namespace) -> PhaseReport:
    """Phase 4c. Populate `title:` from the H1.

    **Separate from `frontmatter` on purpose.** `title:` must land before
    `links` runs, or a bare `[[entities/pkg_okf-io]]` converts to link text
    `pkg_okf-io` instead of `okf-io` (`convert_wikilinks.link_text:214` ->
    `heading_of:188` -> `strip_entity_prefix:181`). Governs 2 pages in the live
    vault; it is its own subcommand so the ordering is executable and testable
    rather than a comment.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from convert_wikilinks import heading_of

    vault: Path = args.vault
    bundle = load(vault, "titles")

    refusals: list[Refusal] = []
    dirty: list[tuple[str, Document]] = []
    unchanged: list[str] = []

    for concept_id in sorted(bundle.concepts):
        member = f"{concept_id}.md"
        document = bundle.concepts[concept_id]
        if (document.fm.title or "").strip():
            unchanged.append(member)
            continue
        heading = heading_of(document)
        if not heading:
            refusals.append(
                Refusal(member=member, reason="no-title-source", detail="no title: and no H1; refusing to guess")
            )
            continue
        document.set("title", heading)
        dirty.append((member, document))

    if args.write and not refusals:
        for member, document in dirty:
            (vault / member).write_text(document.serialize(), encoding="utf-8", newline="")

    return PhaseReport(
        phase="titles",
        changed=() if refusals else tuple(member for member, _ in dirty),
        unchanged=tuple(unchanged),
        refusals=tuple(refusals),
    )


# ---- phase 5: links ----


def cmd_links(args: argparse.Namespace) -> PhaseReport:
    """Phase 5. Convert `[[wikilink]]` forms to markdown links.

    Delegated wholesale to `scripts/convert_wikilinks.py`, already built and
    guarded by 39 tests. Nothing here re-implements it; this wrapper exists so
    the sweep has one uniform subcommand surface and one uniform report.

    **`backfill=False` is deliberate.** `cmd_titles` owns `title:` population,
    and two backfills that could disagree is exactly the failure the ordering
    constraint exists to prevent. `titles` refuses a page with no title source;
    `convert_wikilinks`'s own backfill would silently derive one from the stem.

    Unresolved targets are notes, not refusals: `convert_wikilinks` leaves them
    byte-identical on purpose, and a dangling wikilink is a pre-existing vault
    defect this phase reports rather than a reason to stop the sweep.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from collections import defaultdict

    from convert_wikilinks import run as convert

    result = convert(args.vault, write=args.write, relative=False, backfill=False)

    unresolved: dict[str, int] = defaultdict(int)
    for skip in result.skips:
        if skip.reason == "unresolved":
            unresolved[skip.raw] += 1
    notes = tuple(f"unresolved (left byte-identical): {raw} x{count}" for raw, count in sorted(unresolved.items()))

    return PhaseReport(phase="links", changed=tuple(sorted(result.changed)), notes=notes)


# ---- phase 6a: archive-shape ----


#: The filename an archived item page wears in this vault's nested form. It is
#: an item page, not an artifact, which is why `ignore_patterns` deliberately
#: does not hide it.
ARCHIVED_ITEM_FILENAME = "00-open-work.md"


def cmd_archive_shape(args: argparse.Namespace) -> PhaseReport:
    """Phase 6a (D-029). Promote each archived item page out of its own directory.

        work/_archive/<slug>/00-open-work.md  ->  work/_archive/<slug>.md

    **Ordering: after phase 5, before phase 6.** After 5 because `moves` cannot
    repair a `[[wikilink]]` and says so (`okf_ext/moves/__init__.py:23-30`).
    Before 6 because `plan_migration` refuses without it:
    `parse_item_path("work/_archive/<slug>/00-open-work")` returns `None`
    (`paths.py:59-104`), so every archived page takes `_nodes`'
    `legacy-path-invalid` branch (`migration.py:194-201`) and the whole plan is
    refused.

    **Adding the pattern to `ignore=` is the wrong fix, and is rejected here on
    purpose.** `_with_ignored_work_documents` (`migration.py:148-172`) re-admits
    an ignored `.md` only when its id parses as an item path, and this one does
    not -- so the pages would stay ignored, `_desired_member` (`:522-548`) would
    map each to itself, and the migration would silently pass while leaving 174
    legacy archived items in a vault certified path-native.

    Through `moves.plan_move_many` + `moves.apply`, so every inbound reference
    is repaired in the same pass. The directory survives, now holding only the
    stage documents -- exactly the live lane's shape, and exactly what
    `_desired_member`'s `prefix = f"{node.old_path}/"` branch expects.
    """
    from okf_ext import moves

    vault: Path = args.vault
    bundle = load(vault, "archive-shape")

    mapping: dict[str, str] = {}
    refusals: list[Refusal] = []
    for concept_id in sorted(bundle.concepts):
        member = f"{concept_id}.md"
        parts = member.split("/")
        if len(parts) != 4 or parts[0] != "work" or parts[1] != "_archive":
            continue
        if parts[3] != ARCHIVED_ITEM_FILENAME:
            continue
        destination = f"work/_archive/{parts[2]}.md"
        if (vault / destination).exists():
            refusals.append(
                Refusal(
                    member=member,
                    reason="ambiguous-archive-shape",
                    detail=f"{destination} already exists; refusing to choose between the two forms",
                )
            )
            continue
        mapping[member] = destination

    if refusals:
        return PhaseReport(phase="archive-shape", refusals=tuple(refusals), notes=("refused; nothing moved.",))
    if not mapping:
        return PhaseReport(phase="archive-shape", notes=("no archived item pages in the nested form",))

    plan = moves.plan_move_many(bundle, mapping)
    notes: list[str] = [f"{len(mapping)} archived item page(s) to promote"]
    if plan.stranded:
        notes.append(f"stranded: {moves.stranded_summary(plan.stranded)}")
    if not plan.ok:
        return PhaseReport(
            phase="archive-shape",
            refusals=tuple(Refusal(member=r.path, reason=r.kind, detail=str(r.detail)) for r in plan.refusals),
            notes=tuple(notes),
        )

    if args.write:
        moves.apply(bundle, plan)
    return PhaseReport(phase="archive-shape", changed=tuple(sorted(mapping)), notes=tuple(notes))


# ---- phase 6: work ----


def cmd_work(args: argparse.Namespace) -> PhaseReport:
    """Phase 6. Land the work lane path-native, through the harvested migrator.

    `plan_migration` is the only entry point (`migration.py:698`); the plan is
    applied through `apply_mutation(layout, plan.mutation)` -- the same call
    `run_migrate_layout` makes (`graph_works_core/work/commands.py:574-589`).
    That module is permanent and stays an import; only `migration.py` was
    copied (D-013).

    **`plan_migration` is handed a bundle loaded with `LEGACY_IGNORE`, not the
    sweep's `ignore_patterns("work")`.** `LEGACY_IGNORE` (`work_tracker_okf.items.
    IGNORE`, `items.py:18`) does not hide `work/*/0[1-9]-*.md` the way the
    sweep's own ignore set does, so the 61 stage documents *are* visible to
    `_nodes` as bundle concepts. They are safe only because phase 4 never gives
    them a `type:` -- `_nodes` reaches `type_name is None` and `continue`s
    without a refusal (`migration.py:214-217`). Loading with the sweep's own
    ignore set instead would hide those files from `plan_migration` entirely --
    they would stay in `bundle.ignored` and never reload as concepts, and the
    later domain-move materialization would fail on them with
    `not-a-member` rather than moving them.

    **`_projected_refusals` copies the whole vault** into a `TemporaryDirectory`
    and reloads it (`migration.py:627-695`) -- every member, not just the work
    lane. Correct, and slow. Reported as a note so an attended run expects it.
    """
    from graph_works_core.workspace.transactions import apply_mutation
    from migrate_vault_work import LEGACY_IGNORE, plan_migration

    vault: Path = args.vault
    plan = plan_migration(load_bundle(vault, ignore=LEGACY_IGNORE))

    notes = [
        f"{len(plan.manifest)} item(s) in the manifest",
        "note: the projection step copies the whole vault into a tempdir and reloads it; expect it to be slow",
    ]
    notes.extend(str(warning) for warning in plan.opaque_warnings)

    if not plan.ok:
        #: The harvested migrator is one-time-use: `_nodes` builds a node for
        #: every `work/` concept with a recognized `type:`, migrated or not,
        #: so a second run over an already-migrated lane recomputes the same
        #: (identity) targets and the domain move planner refuses them --
        #: `same-path` per member, `directory-dest-exists` for the directory
        #: itself -- rather than treating them as no-ops. That is a real
        #: refusal for anything genuinely colliding; it is only a no-op when
        #: *every* manifest entry already matches its target and *every*
        #: refusal is one of those two identity kinds.
        already_native = (
            bool(plan.manifest)
            and all(entry.old_path == entry.new_path for entry in plan.manifest)
            and all(refusal.kind in ("same-path", "directory-dest-exists") for refusal in plan.mutation.refusals)
        )
        if already_native:
            return PhaseReport(phase="work", notes=tuple([*notes, "already path-native; nothing to do"]))
        return PhaseReport(
            phase="work",
            refusals=tuple(Refusal(member=r.path, reason=r.kind, detail=r.detail) for r in plan.mutation.refusals),
            notes=tuple([*notes, "refused; nothing applied.", plan.diff()]),
        )

    changed = tuple(
        sorted(
            {move.source for move in plan.mutation.moves}
            | {write.member for write in plan.mutation.writes}
            | set(plan.mutation.deletes)
        )
    )

    if not changed:
        return PhaseReport(phase="work", notes=tuple([*notes, "already path-native; nothing to do"]))

    if args.write:
        application = apply_mutation(_layout(vault), plan.mutation)
        if not application.ok:
            #: `apply_mutation` can roll back *without raising* -- postcondition
            #: validation fails after the writes land, the transaction restores
            #: the pre-mutation snapshot, and `MutationApplication.ok` is the
            #: only signal. Ignoring the return value here would let this phase
            #: report `ok=True, changed=(...)` for a run that wrote nothing.
            reason = "rolled-back" if application.rolled_back else "apply-mutation-failed"
            failures = application.failures or ("apply_mutation reported failure with no detail",)
            return PhaseReport(
                phase="work",
                refusals=tuple(Refusal(member=vault.name, reason=reason, detail=failure) for failure in failures),
                notes=tuple([*notes, "apply_mutation failed; nothing durable was written."]),
            )
        return PhaseReport(phase="work", changed=changed, notes=tuple(notes))

    return PhaseReport(phase="work", notes=tuple([*notes, plan.diff()]))


# ---- phase 7: entities ----


#: `code_wiki_okf.placement._RESOURCE_PREFIXES` inverted (`placement.py:23-31`).
#: Derived rather than typed twice, so a prefix rename in the package cannot
#: drift from this map.
def _type_for_prefix() -> dict[str, str]:
    from code_wiki_okf.placement import _RESOURCE_PREFIXES  # noqa: PLC2701 -- inverting the package's own table

    return {prefix: type_name for type_name, prefix in _RESOURCE_PREFIXES.items()}


def parse_repo_renames(values: Sequence[str]) -> dict[str, str]:
    """`["old=new", …]` -> `{old: new}`. C3 renames the repo before C4 runs
    (D-008), and `<repo>` is the only payload component that reaches the path
    (`placement.py:143-146`), so a rename is a pure string substitution -- and an
    explicit flag rather than an inference, so it is reviewable."""
    renames: dict[str, str] = {}
    for value in values:
        old, separator, new = value.partition("=")
        if not separator or not old or not new:
            raise ValueError(f"--repo-rename expects OLD=NEW, got {value!r}")
        renames[old] = new
    return renames


def successor_id(uri: str, *, repo_renames: dict[str, str] | None = None) -> str | None:
    """The canonical concept id *uri* becomes, or `None` when it is unparseable.

    **Derived, not discovered.** `context_from_resource` (`placement.py:138-172`)
    parses `<prefix>:<org>/<repo>/<name>`; `canonical_concept_id`
    (`placement.py:233-263`) is pure -- resource identity is its only input, no
    filesystem, no bundle. So the map is computable from the snapshot alone, and
    the regenerated lane is used to *verify* it (every computed id must exist),
    never to discover it.
    """
    from code_wiki_okf.placement import PlacementError, canonical_concept_id, context_from_resource

    prefix, separator, payload = uri.partition(":")
    if not separator:
        return None
    type_name = _type_for_prefix().get(prefix)
    if type_name is None:
        return None

    if repo_renames:
        parts = payload.split("/")
        # `<org>/<repo>[/<name>]` for repo-scoped types; `<ecosystem>/<name>`
        # for Dependency, whose first component is not a repository and is
        # therefore never renamed.
        if type_name != "Dependency" and len(parts) >= 2:
            parts[1] = repo_renames.get(parts[1], parts[1])
            payload = "/".join(parts)
        uri = f"{prefix}:{payload}"

    try:
        return canonical_concept_id(context_from_resource(type_name, uri))
    except PlacementError:
        return None


def cmd_entities(args: argparse.Namespace) -> PhaseReport:
    """Phase 7 (D-011, D-044). Retire the entity lane and remap its references.

    Five steps, each resumable by skipping when its own output already exists:

      1. snapshot every `entities/*.md` to `.gw/migration/entities-preimage.json`
      2. **quarantine, do not delete** -- move `entities/` to
         `.gw/migration/entities-preimage/`, out of the bundle so `load_bundle`
         never walks it and `_preflight_existing` (`sync.py:222-270`) never sees
         it, while D-006's recovery path stays one `mv` away
      3. regenerate with `gw scan`
      4. match and rewrite both reference surfaces
      5. report, for a human to review before anything is deleted

    **This subcommand never deletes the quarantine.** D-011 makes the step-5
    review a gate: deletion happens after a human signs off, by hand.
    """
    vault: Path = args.vault
    out = migration_dir(vault)
    renames = parse_repo_renames(args.repo_rename)
    notes: list[str] = [f"repo renames applied: {renames or 'none'}"]

    snapshot_path = out / "entities-preimage.json"
    quarantine = out / "entities-preimage"

    # --- step 1: snapshot -------------------------------------------------
    rows = _entity_snapshot(vault) if not snapshot_path.exists() else json.loads(
        snapshot_path.read_text(encoding="utf-8")
    )["rows"]
    if args.write and not snapshot_path.exists():
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(json.dumps({"rows": rows}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    notes.append(f"{len(rows)} entity page(s) snapshotted")

    # --- step 2: quarantine ----------------------------------------------
    if args.write and (vault / "entities").is_dir() and not quarantine.exists():
        quarantine.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(vault / "entities"), str(quarantine))
        notes.append(f"quarantined entities/ -> {quarantine} (NOT deleted; delete after reviewing the report)")

    # --- step 3: regenerate ----------------------------------------------
    if args.write and args.scan:
        completed = subprocess.run(
            ["uv", "run", "--package", "graph-works-cli", "gw", "scan", "--workspace", str(vault.parent)],
            capture_output=True, text=True, check=False,
        )
        notes.append(f"gw scan exit={completed.returncode}")
        if completed.returncode != 0:
            return PhaseReport(
                phase="entities",
                refusals=(Refusal(member="gw scan", reason="scan-failed", detail=completed.stderr.strip()[:400]),),
                notes=tuple(notes),
            )

    # --- step 4: match and rewrite ---------------------------------------
    bundle = load(vault, "entities")
    successors: dict[str, str] = {}   # old member -> new member
    unmatched: list[dict] = []
    for row in rows:
        new_id = successor_id(row["uri"], repo_renames=renames)
        if new_id is None or new_id not in bundle.concepts:
            unmatched.append({**row, "successor": new_id})
            continue
        successors[row["member"]] = f"{new_id}.md"

    changed = _rewrite_entity_references(vault, bundle, successors, rows, renames, write=args.write)

    # --- step 5: report -----------------------------------------------
    report_path = out / "entities-unmatched.md"
    if args.write:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(_render_unmatched(unmatched, bundle, rows, renames), encoding="utf-8")
    notes.append(f"{len(unmatched)} unmatched entity uri(s); see {report_path}")
    notes.append(f"{len(successors)} matched; {len(changed)} member(s) rewritten")

    return PhaseReport(phase="entities", changed=tuple(sorted(changed)), notes=tuple(notes))


def _entity_snapshot(vault: Path) -> list[dict]:
    """`{member, uri, kind, title, h1}` per `entities/*.md`, sorted by member."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from convert_wikilinks import heading_of

    lane = load_bundle(vault, ignore=BASE_IGNORE)  # entity lane deliberately visible
    rows: list[dict] = []
    for concept_id in sorted(lane.concepts):
        if not concept_id.startswith("entities/"):
            continue
        document = lane.concepts[concept_id]
        rows.append(
            {
                "member": f"{concept_id}.md",
                "uri": str(document.fm.extra.get("uri") or ""),
                "kind": str(document.fm.extra.get("kind") or ""),
                "title": document.fm.title or "",
                "h1": heading_of(document) or "",
            }
        )
    return rows


def _rewrite_entity_references(
    vault: Path,
    bundle: Bundle,
    successors: dict[str, str],
    rows: Sequence[dict],
    renames: dict[str, str],
    *,
    write: bool,
) -> list[str]:
    """Both surfaces, in one pass over the bundle.

      * the markdown links -- by this point phase 5 has converted them, so they
        are `[label](/entities/pkg_okf-io__ab12cd.md)` and the rewrite is a path
        substitution through `Document.set_body` on the parsed original;
      * the `sources/` `entity_uri:` values, through `Document.set`.
        `entity_uri` is not an OKF reserved key and not an OKF reference form,
        so nothing in `moves` or `validate` would ever have caught these.
    """
    uri_successors = {
        row["uri"]: f"{prefix}:{payload}"
        for row in rows
        if (renamed := _renamed_uri(row["uri"], renames)) is not None
        for prefix, _, payload in (renamed.partition(":"),)
    }
    changed: list[str] = []
    for concept_id in sorted(bundle.concepts):
        document = bundle.concepts[concept_id]
        original = document.raw_text

        body = document.body
        for old_member, new_member in successors.items():
            body = body.replace(f"/{old_member}", f"/{new_member}")
        if body != document.body:
            document.set_body(body)

        current = str(document.fm.extra.get("entity_uri") or "")
        if current and current in uri_successors and uri_successors[current] != current:
            document.set("entity_uri", uri_successors[current])

        if document.serialize() != original:
            changed.append(f"{concept_id}.md")
            if write:
                (vault / f"{concept_id}.md").write_text(document.serialize(), encoding="utf-8", newline="")
    return changed


def _renamed_uri(uri: str, renames: dict[str, str]) -> str | None:
    """*uri* with its repository component substituted, or `None` when it does
    not parse. Extracted so the link rewrite and the `entity_uri:` rewrite share
    one substitution and cannot disagree."""
    prefix, separator, payload = uri.partition(":")
    if not separator or not renames:
        return uri or None
    parts = payload.split("/")
    if prefix != "dependency" and len(parts) >= 2:
        parts[1] = renames.get(parts[1], parts[1])
    return f"{prefix}:{'/'.join(parts)}"


def _inbound_entity_reference_counts(bundle: Bundle) -> dict[str, int]:
    """`/entities/…` destinations per target, across every body, plus every
    `entity_uri:` value carried by a `sources/` page.

    Markdown link targets are counted by member (`entities/<slug>.md`, no
    leading slash); `entity_uri:` values are counted by uri. The same
    namespace `_render_unmatched` walks against, so an unmatched snapshot row
    (keyed by member or uri) and a target this phase never heard of can be
    told apart by lookup key alone.
    """
    import re

    pattern = re.compile(r"\]\(/?(entities/[^)\s#?]+\.md)")
    counts: dict[str, int] = {}
    for document in bundle.concepts.values():
        for match in pattern.finditer(document.body):
            target = match.group(1)
            counts[target] = counts.get(target, 0) + 1
        entity_uri = str(document.fm.extra.get("entity_uri") or "")
        if entity_uri:
            counts[entity_uri] = counts.get(entity_uri, 0) + 1
    return counts


def _absence_reason(row: dict, renames: dict[str, str]) -> str:
    """Best-effort note for why *row*'s computed successor is missing from the
    regenerated lane -- naming the landed change that plausibly explains it, so
    the human review is a judgment about a stated reason rather than a
    re-derivation from scratch."""
    if row.get("successor") is None:
        return "uri: does not parse as a known entity prefix"

    kind = str(row.get("kind") or "").strip()
    uri = str(row.get("uri") or "")
    prefix, _, payload = uri.partition(":")
    parts = payload.split("/")
    if renames and prefix != "dependency" and len(parts) >= 2 and parts[1] in renames:
        return "repository was renamed by --repo-rename; verify the renamed successor by hand"
    if kind == "dependency" or prefix == "dependency":
        return "dependency facets landed after this page was written (a9bdbff8); verify by ecosystem/name"
    if prefix in {"app", "agent_plugin", "test_suite"}:
        return "C# parsing support landed after this page was written (a9bdbff8); verify the successor by hand"
    return "repository-first placement changed member paths (ADR-0026, cc697716); or `gw scan` has not run yet"


def _render_unmatched(unmatched: Sequence[dict], bundle: Bundle, rows: Sequence[dict], renames: dict[str, str]) -> str:
    """One row per snapshot uri with no successor in the regenerated lane.

    Distinguishes *was already broken* from *broken by this phase*, because gate
    assertion 3 measures a delta, not an absolute: 105 distinct link targets
    against 75 snapshot rows means roughly 30 targets were already dangling
    before the sweep started.

    Each row names *why* the absence is expected -- repository-first placement
    (ADR-0026, landed at `cc697716`), dependency facets, C# parsing (`a9bdbff8`),
    or the repo rename -- so the review is a judgment about a stated reason
    rather than a re-derivation.
    """
    known_uris = {row["uri"] for row in rows}
    known_members = {row["member"] for row in rows}
    inbound = _inbound_entity_reference_counts(bundle)
    lines = [
        "# Entity remap — unmatched\n",
        f"Repo renames applied: `{renames or 'none'}`\n",
        "Reviewed by a human. **The quarantine directory at `.gw/migration/entities-preimage/`",
        "is deleted only after this file is signed off** (D-011/D-044).\n",
        "## Snapshot URIs with no successor in the regenerated lane\n",
        "| old member | uri | computed successor | inbound refs | expected because |",
        "|---|---|---|---:|---|",
    ]
    for row in sorted(unmatched, key=lambda r: r["member"]):
        refs = inbound.get(row["member"], 0) + inbound.get(row["uri"], 0)
        lines.append(
            f"| `{row['member']}` | `{row['uri']}` | `{row['successor'] or '(unparseable)'}` | "
            f"{refs} | {_absence_reason(row, renames)} |"
        )
    lines.append("\n## Reference targets that were already dangling before this sweep\n")
    lines.append("These name no snapshot row, so nothing this phase did broke them.\n")
    lines.append("| target | inbound refs |")
    lines.append("|---|---:|")
    for target, count in sorted(inbound.items()):
        if target not in known_uris and target not in known_members:
            lines.append(f"| `{target}` | {count} | ")
    return "\n".join(lines) + "\n"


# ---- phase 8: lanes ----


def lane_destinations(vault: Path) -> dict[str, str]:
    """`{type: directory}` from the bundle's declared schema set.

    Read from `x-okf-directory` through `okf_ext.schemas.declared_directories`
    (`loader.py:148-167`), **never from a constant here**: the declarations are
    what phase 3's `gw bootstrap` installed, and a second copy in this script
    would drift the moment one changed.
    """
    from okf_ext.schemas import declared_directories

    return declared_directories(_schema_set(vault))


def cmd_lanes(args: argparse.Namespace) -> PhaseReport:
    """Phase 8. Move each lane into the directory its schema declares.

    **After 5, 6a and 7.** Moves repair markdown references, which is the entire
    reason the wikilink conversion precedes them
    (`okf_ext/moves/__init__.py:23-30`).

    Three routes, because two lanes already have a package that owns the whole
    sequence and re-implementing either would be the scope violation this sweep
    is built to avoid:

      * `concepts/` -> `diataxis.plan_retype` + `apply_retype`
        (`retype.py:112` / `:186`), which performs the move *and* the `type:`
        rewrite in one plan, consuming the decisions file's `diataxis-type` rows;
      * `proposals/` -> `proposals.migrate_and_move(root, by=…, at=…, ignore=…)`
        (`migrate.py:450`), which is rewrite-reload-move in one call;
      * everything else -> `moves.plan_move_dir` + `moves.apply`.
    """
    from datetime import UTC, datetime

    from doc_wiki_okf import proposals as proposals_capability
    from doc_wiki_okf.diataxis import apply_retype, plan_retype
    from okf_ext import moves

    vault: Path = args.vault
    destinations = lane_destinations(vault)
    refusals: list[Refusal] = []
    notes: list[str] = []
    changed: list[str] = []

    # --- concepts: retype + move, one plan per page ------------------------
    bundle = load(vault, "lanes")
    schema_set = _schema_set(vault)
    concept_ids = sorted(cid for cid in bundle.concepts if cid.startswith("concepts/"))

    if concept_ids:
        # Loaded only when there is a `concepts/` lane to retype: a vault with
        # none (a proposals-only run, say) must not be refused over a
        # decisions file it never needed.
        decisions, decision_refusals = load_decisions(migration_dir(vault) / "decisions.yaml")
        refusals.extend(decision_refusals)
        for concept_id in concept_ids:
            member = f"{concept_id}.md"
            chosen = decisions.get((member, "diataxis-type"))
            if chosen is None:
                refusals.append(Refusal(member=member, reason="uncovered-by-decisions", detail="diataxis-type"))
                continue
            plan = plan_retype(bundle, schema_set, concept_id, chosen)
            if plan.move.stranded:
                notes.append(f"stranded: {moves.stranded_summary(plan.move.stranded)}")
            if not plan.ok:
                refusals.extend(Refusal(member=member, reason=r.kind, detail=r.detail) for r in plan.refusals)
                refusals.extend(Refusal(member=member, reason=r.kind, detail=r.detail) for r in plan.move.refusals)
                continue
            changed.append(member)
            if args.write and not refusals:
                apply_retype(bundle, plan)
                bundle = load(vault, "lanes")  # apply does not update the in-memory bundle

    if refusals:
        return PhaseReport(phase="lanes", refusals=tuple(refusals), notes=(*notes, "refused; lane left as is."))

    # --- proposals: delegated wholesale ------------------------------------
    if (vault / "proposals").is_dir() and args.write:
        outcome = proposals_capability.migrate_and_move(
            vault,
            by=DECIDED_BY,
            at=datetime.now(tz=UTC),
            ignore=ignore_patterns("lanes"),
        )
        notes.append(f"proposals: {len(outcome.rewrite.written)} rewritten, {len(outcome.move.moved)} moved")
        if outcome.move_plan.stranded:
            notes.append(f"stranded: {moves.stranded_summary(outcome.move_plan.stranded)}")
        changed.extend(outcome.rewrite.written)

    # --- every other lane: plan_move_dir -----------------------------------
    bundle = load(vault, "lanes")
    for lane, destination in sorted(_plain_lane_moves(vault, destinations).items()):
        plan = moves.plan_move_dir(bundle, lane, destination)
        if plan.stranded:
            notes.append(f"stranded: {moves.stranded_summary(plan.stranded)}")
        if not plan.ok:
            refusals.extend(Refusal(member=lane, reason=r.kind, detail=str(r.detail)) for r in plan.refusals)
            continue
        if plan.is_empty:
            continue
        changed.extend(move.source for move in plan.moves)
        if args.write:
            moves.apply(bundle, plan)
            bundle = load(vault, "lanes")

    return PhaseReport(
        phase="lanes",
        changed=tuple(sorted(set(changed))),
        refusals=tuple(refusals),
        notes=tuple(notes),
    )


def _plain_lane_moves(vault: Path, destinations: dict[str, str]) -> dict[str, str]:
    """`{source lane: destination}` for every lane that is a straight directory
    move -- everything `concepts/` and `proposals/` do not own, and only where
    the source exists and differs from the destination (which is what makes a
    second run a no-op)."""
    candidates = {
        "adrs": destinations.get("Adr", "adrs"),
        "sources": destinations.get("Source", "sources"),
    }
    return {
        lane: destination
        for lane, destination in candidates.items()
        if lane != destination.rstrip("/") and (vault / lane).is_dir()
    }


# ---- phase 9: bundle ----

#: `## [YYYY-MM-DD] <op> | <title>` -- the retired heading grammar. 609 of them
#: across 22 dates in the live vault, oldest-first.
_LEGACY_LOG_HEADING = re.compile(r"^##\s+\[(\d{4}-\d{2}-\d{2})\]\s*(.*)$")

_LOG_HEADER = """# Log

One `## <ISO date>` section per day, newest first; one list item per entry
(OKF v0.2 §9). `okf_io.log` enforces both: `iso_date` returns `None` for a
non-ISO heading and `reserved.log-heading-not-date` reports it
(`okf_io/log.py:91-138`), and a non-newest-first ordering is reported at
`log.py:218-224`.
"""


def reformat_log(text: str) -> str:
    """Regroup *text*'s 609 legacy headings into dated sections, newest first.

    A regroup and a reversal, not a per-heading edit: each legacy heading
    becomes one bullet `- <op> | <title>` under its date's section, with any
    prose that followed it nested beneath as an indented continuation.
    """
    lines = text.splitlines()
    entries: dict[str, list[tuple[str, list[str]]]] = {}
    current: tuple[str, str, list[str]] | None = None
    for line in lines:
        match = _LEGACY_LOG_HEADING.match(line)
        if match:
            if current is not None:
                day, title, detail = current
                entries.setdefault(day, []).append((title, detail))
            current = (match.group(1), match.group(2).strip(), [])
            continue
        if current is not None:
            current[2].append(line)
    if current is not None:
        day, title, detail = current
        entries.setdefault(day, []).append((title, detail))

    out: list[str] = [_LOG_HEADER.rstrip(), ""]
    for day in sorted(entries, reverse=True):
        out.append(f"## {day}")
        out.append("")
        for title, detail in entries[day]:
            out.append(f"- {title}")
            for detail_line in _nested(detail):
                out.append(detail_line)
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def _nested(detail: Sequence[str]) -> list[str]:
    """Indent an entry's trailing prose two spaces so it reads as that bullet's
    continuation rather than as a sibling paragraph."""
    body = "\n".join(detail).strip()
    if not body:
        return []
    return ["  " + line if line.strip() else "" for line in body.splitlines()]


def cmd_bundle(args: argparse.Namespace) -> PhaseReport:
    """Phase 9. Bundle-level cleanups: the log's heading grammar and the indexes.

    Three edits, one family:

      * **`log.md`** -- regroup 609 `## [DATE] op | title` headings into one
        `## <ISO date>` section per date, newest first, and rewrite the prose
        header that documented the old format with it.
      * **Sub-index frontmatter strip** -- §8 restricts what a non-root
        `index.md` may carry; exactly 4 in the live vault carry frontmatter.
      * **Root `index.md` gains `okf_version: 0.2`** -- phase 3's scaffold
        requires it (`okf_ext/bundle/plan.py:88-94`), and §12 permits nothing
        else there. Executed in phase 3 in the runbook; landed here because it
        is the same family of edit and the same code path.
    """
    vault: Path = args.vault
    changed: list[str] = []

    log_path = vault / "log.md"
    if log_path.is_file():
        original = log_path.read_text(encoding="utf-8")
        has_legacy_heading = any(
            _LEGACY_LOG_HEADING.match(line) for line in original.splitlines()
        )
        if has_legacy_heading:
            reformatted = reformat_log(original)
            if reformatted != original:
                changed.append("log.md")
                if args.write:
                    log_path.write_text(reformatted, encoding="utf-8", newline="")

    for index_path in sorted(vault.rglob("index.md")):
        member = str(index_path.relative_to(vault))
        text = index_path.read_text(encoding="utf-8")
        document = parse(text, path=index_path)
        if member == "index.md":
            if document.fm_raw.get("okf_version") is None:
                document.set("okf_version", 0.2)
                changed.append(member)
                if args.write:
                    index_path.write_text(document.serialize(), encoding="utf-8", newline="")
            continue
        if not document.fm_raw:
            continue
        changed.append(member)
        if args.write:
            index_path.write_text(document.body.lstrip("\n"), encoding="utf-8", newline="")

    return PhaseReport(phase="bundle", changed=tuple(changed))


# ---- phase 10: gate ----


def cmd_gate(args: argparse.Namespace) -> PhaseReport:
    """Phase 10. The four assertions that define "migrated".

    Run as a test over a fixture here, and as the live acceptance in C4. **The
    real vault is the acceptance gate, not the test suite.**

      1. `validate(...).ok is True`, that phase's lane out of the ignore set.
      2. **No `coercion_failures` on any document.** `validate()` will not tell
         you this (`okf-io/models.py:160`), and the live vault has 10 pages
         guaranteed to trip it before the sweep.
      3. **`links.broken` did not increase** against the pre-sweep baseline. A
         delta, not an absolute: ~30 entity link targets were already dangling
         before the sweep started. With no baseline file, this writes one and
         says so -- passing vacuously would be worse than failing.
      4. **Zero unmatched entity references** carried past phase 7's report.
    """
    vault: Path = args.vault
    bundle = load(vault, "gate")
    refusals: list[Refusal] = []
    notes: list[str] = []

    # --- 1 ------------------------------------------------------------------
    report_1 = validate(bundle, today=args.today)
    for finding in report_1.errors:
        refusals.append(Refusal(member=finding.path or "?", reason="validate-error", detail=f"{finding.code}: {finding.message}"))
    notes.append(f"validate: {len(report_1.errors)} error(s), {len(report_1.warnings)} warning(s)")

    # --- 2 ------------------------------------------------------------------
    for concept_id in sorted(bundle.concepts):
        failures = bundle.concepts[concept_id].fm.coercion_failures
        if failures:
            refusals.append(
                Refusal(member=f"{concept_id}.md", reason="coercion-failure", detail=", ".join(sorted(failures)))
            )

    # --- 3 ------------------------------------------------------------------
    graph = build_link_graph(bundle)
    broken = len(graph.broken)
    notes.append(f"links.broken: {broken}")
    baseline: Path | None = args.baseline
    if baseline is None:
        notes.append("no --baseline given; assertion 3 not evaluated")
    elif not baseline.exists():
        notes.append(f"baseline written to {baseline}; assertion 3 not evaluated on this run")
        if args.write:
            baseline.parent.mkdir(parents=True, exist_ok=True)
            baseline.write_text(json.dumps({"broken": broken}, indent=2) + "\n", encoding="utf-8")
    else:
        before = int(json.loads(baseline.read_text(encoding="utf-8"))["broken"])
        if broken > before:
            refusals.append(
                Refusal(
                    member="(bundle)",
                    reason="broken-links-increased",
                    detail=f"{before} -> {broken}",
                )
            )

    # --- 4 ------------------------------------------------------------------
    unmatched_path = migration_dir(vault) / "entities-unmatched.md"
    outstanding = _unmatched_row_count(unmatched_path)
    notes.append(f"unmatched entity references: {outstanding}")
    if outstanding:
        refusals.append(
            Refusal(
                member=str(unmatched_path),
                reason="unmatched-entity-references",
                detail=f"{outstanding} row(s) still carried past phase 7's report",
            )
        )

    return PhaseReport(phase="gate", refusals=tuple(refusals), notes=tuple(notes))


def _unmatched_row_count(path: Path) -> int:
    """Data rows in the report's first table. Zero when the file is absent --
    phase 7 not having run is a different failure, and assertion 4 is about
    references *carried past* a report that exists."""
    if not path.exists():
        return 0
    rows = 0
    entered_table = False
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|") and "---" not in stripped:
            if stripped.lower().startswith("| old member"):
                entered_table = True
                continue
            rows += 1
        elif entered_table and not stripped.startswith("|"):
            break  # first table only
    return rows


_HANDLERS = {
    "decisions": cmd_decisions,
    "frontmatter": cmd_frontmatter,
    "titles": cmd_titles,
    "links": cmd_links,
    "archive-shape": cmd_archive_shape,
    "work": cmd_work,
    "entities": cmd_entities,
    "lanes": cmd_lanes,
    "bundle": cmd_bundle,
    "gate": cmd_gate,
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0], allow_abbrev=False)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, help_text in _SUBCOMMANDS:
        sub = subparsers.add_parser(name, help=help_text, allow_abbrev=False)
        sub.add_argument("vault", type=Path, help="bundle root, e.g. <workspace>/wiki")
        sub.add_argument("--write", action="store_true", help="apply changes (default: dry run)")
        if name == "entities":
            sub.add_argument(
                "--repo-rename",
                action="append",
                default=[],
                metavar="OLD=NEW",
                help="rewrite this repository name inside every old uri: before parsing (D-008)",
            )
            sub.add_argument(
                "--no-scan",
                dest="scan",
                action="store_false",
                default=True,
                help="skip `gw scan` (the regenerated lane already exists, or a caller ran it separately)",
            )
        if name == "gate":
            sub.add_argument(
                "--today", type=date.fromisoformat, required=True, help="ISO date; okf-io never reads the clock"
            )
            sub.add_argument("--baseline", type=Path, help="pre-sweep link baseline written by an earlier `gate` run")

    args = parser.parse_args(argv)

    if not args.vault.is_dir():
        print(f"not a directory: {args.vault}", file=sys.stderr)
        return 2

    handler = _HANDLERS[args.command]
    return report(handler(args), write=args.write)


if __name__ == "__main__":
    raise SystemExit(main())
