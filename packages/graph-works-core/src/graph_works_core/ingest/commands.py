"""Ingest one document: the LLM layer over doc-wiki-okf's reading and writing.

Five capabilities a reader might expect are deliberately absent, each for a
reason that still holds:

**The skill branch** is a later vertical's. Two packages reached that
conclusion independently before this one was designed: C2 declines to
package `skill_planner` / `skill_synthesizer`, and
`doc_wiki_okf.ingest` declines to build the brief, saying the skill flow "is
the guidance flow, a layer above this one". Skill ingest is unavailable until
that vertical lands; that is the cost the epic already accepted by deferring
it. **Note for that later vertical:** the branch, its two prompts, and the two
roles C2 declined to package.

**`run_ingest_work_item`** is absent: it makes no LLM call, and
`work_tracker_okf` ships the filing surface. Discharged against C0 -- with C0's
own caveat that discharging a row means checking symbol by symbol, not finding
a command with the same name.

**No `_set_*_in_body` / `_rewrite_*_in_body` helpers.** `compose_frontmatter`
and `compose_body` do the job. Such helpers exist to edit YAML that has already
been serialized; nothing is serialized here until the plan is built, so there
is nothing to edit.

**No archive move, no `doc_pointers.sweep`.** Both died with `raw/`
(`ab906786`). Material is copied into `sources/references/` by `plan_ingest`,
never moved.

**No `_resolve_wikilinks`.** Deleting unresolvable `[[wikilinks]]` -- which
`okf_io.LinkGraph` cannot see anyway -- would silence a broken link instead of
surfacing it. With root-absolute markdown links, `okf_io.validate()` reports one
as a finding, and silently deleting a model's citation is worse than surfacing
one a human can act on.

**One composition, one write.** The response is parsed into frontmatter and a
body in memory, the suggest phase runs against that body, the run status is
folded in, and one `PagePlan` lands the page and its reference copy together
or not at all.

**No drift stamp.** `last_sync_commit` used to be written on source pages whose
validated kind was `doc` and whose state gate reported `allowed`. It was
undeclared in `Source.schema.json`, read by no lint rule, and its premise --
that the vault held no copy of an in-repo doc, so a SHA was the only record of
what had been summarised -- died when `plan_ingest` began copying every
material into `sources/references/`. Drift is a diff against that copy now, and
that works for material outside any git repo, which a SHA never did. Re-addable
the day a lint rule wants it, and it should come back keyed off the path rather
than the kind: "is `origin` a tracked path in this repo" is what the predicate
was always asking.

**Note for a CLI layer:** whether an uninitialized graph is fatal is the CLI's
policy, not this library's, and the CLI is what wires a graph-tool builder
into the suggest phase.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from code_graph_io import GraphNotInitializedError, SchemaMismatchError, open_reader
from code_wiki_okf.config import Config, load_config
from code_wiki_okf.git_state import compute_state_gate
from doc_wiki_okf.ingest import plan_document_brief
from doc_wiki_okf.ingest.layout import GRAPH_WIKI_LAYOUT, IngestLayout
from doc_wiki_okf.ingest.seams import NO_ENTITY, EntityMatcher, StateGate
from doc_wiki_okf.proposals.lanes import lane_set
from doc_wiki_okf.sources import copy_target, page_target, plan_ingest, source_kinds
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import BaseTool
from okf_ext.proposals import apply as apply_plan
from okf_ext.schemas import SchemaSet, load_schemas
from okf_ext.shape import load_sections
from okf_io import append_log_entry, load_bundle, update_index
from okf_io import parse as parse_document

from graph_works_core.agent_substrate.agent_tools import strip_code_fence
from graph_works_core.agent_substrate.roles import make_llm
from graph_works_core.ingest.entity_match import entity_matcher
from graph_works_core.ingest.prompts.ingestor import build_ingestor_system
from graph_works_core.ingest.suggest_pages import apply_suggestions, merge_apply_status, plan_suggestions
from graph_works_core.prompts import render_project_context
from graph_works_core.workspace.layout import WorkspaceLayout

logger = logging.getLogger(__name__)

#: The heading the entity forward-link lands under -- one of the seven the
#: `Source` section declaration names, not a heading this module invents.
TOUCHES_HEADING = "## Touches"

#: Optional `Source` frontmatter this module carries through from the model.
#: `title`, `description` and `source_kind` are not here, and not because the
#: model's values are discarded: each is read explicitly and passed to a named
#: `plan_ingest` argument -- `title` decides the page path before `plan_ingest`
#: is even called. Carrying them here as well would write a second copy that
#: fights the first.
CARRIED_KEYS: tuple[str, ...] = ("authors", "source_date", "tags", "tokens")


@dataclass(frozen=True, slots=True)
class IngestResult:
    """What one ingest did.

    `written` is `ApplyResult.written` -- the page and its reference copy, in
    that order. `refusals` is non-empty only when the plan was refused, in
    which case nothing landed and `ok` is `False`: the page and copy are one
    plan precisely so there is no third outcome.
    """

    ok: bool
    page: str
    copy: str
    title: str
    source_kind: str
    entity_uri: str | None = None
    entity_page: str | None = None
    frontmatter_parsed: bool = True
    proposals: tuple[Mapping[str, Any], ...] = ()
    proposal_status: Mapping[str, Any] = field(default_factory=dict)
    written: tuple[str, ...] = ()
    refusals: tuple[str, ...] = ()
    indexes_updated: tuple[str, ...] = ()


def parse_ingestor_response(text: str) -> tuple[dict[str, Any], str, bool]:
    """Split the model's response into `(frontmatter, body, parsed)`.

    `okf_io.parse` is the parser, not a YAML library: it is the same one the
    page will be re-read by, it never raises for content, and it keeps this
    package's dependency list at the three spec-declared names. `parsed` is
    `False` for a response carrying no frontmatter or frontmatter okf-io could
    not coerce, and the whole text comes back as the body in that case -- the
    page still lands, carrying what the model actually wrote.
    """
    document = parse_document(strip_code_fence(text))
    if document.parse_error is not None or not document.fm_raw:
        return {}, text, False
    # A malformed block can still parse as valid YAML -- e.g. `: : :` yields a
    # mapping keyed on `None` rather than raising. Real frontmatter is always
    # keyed on strings, so a non-string top-level key is the signal that this
    # is content okf-io could not coerce into anything usable, not a document
    # whose keys happen to be unfamiliar.
    if not all(isinstance(key, str) for key in document.fm_raw):
        return {}, text, False
    return document.fm_data(), document.body, True


def validated_source_kind(frontmatter: Mapping[str, Any], *, hint: str, kinds: Sequence[str]) -> str:
    """The model's classification if *kinds* declares it, else *hint*.

    Two-phase typing survives the vocabulary consolidation intact (K-H):
    `plan_document_brief` needs a `source_kind` *before* the model has read
    anything, so it takes the caller's hint; the model classifies from content;
    this validates the answer. What changed is where the list comes from -- the
    bundle's own `Source` schema, passed in, rather than a module tuple that
    could disagree with it.
    """
    proposed = str(frontmatter.get("source_kind") or "").strip().lower()
    return proposed if proposed in kinds else hint


def state_gate_adapter(config: Config) -> StateGate:
    """Adapt `code_wiki_okf.git_state.compute_state_gate` to the seam protocol.

    The protocol wants `(repo, /, *, workspace) -> Mapping[str, Any]` and
    `compute_state_gate` returns a frozen dataclass, so this is the fifteen
    lines that make one the other. `workspace` is accepted and unused: it is
    the protocol's parameter, and `Config` already carries everything the gate
    reads.

    The mapping is carried opaquely from here. `doc_wiki_okf` passes it through
    and never reads it; this module reads exactly `allowed` and `head_commit`,
    and only in `compose_frontmatter`.
    """

    def read_gate(repo: Path, /, *, workspace: Path) -> Mapping[str, Any]:
        gate = compute_state_gate(repo, enabled=config.state_gate.enabled, branches=config.state_gate.branches)
        return {"allowed": gate.allowed, "reason": gate.reason, "head_commit": gate.head_commit}

    return read_gate


#: `plan_ingest`'s own blank rule (`sources/plan.py`), restated so
#: `compose_frontmatter` can apply it one level down. `0` is deliberately not
#: blank: a run that filed nothing said so.
_BLANK: tuple[object, ...] = (None, "", (), [])


def compose_frontmatter(
    frontmatter: Mapping[str, Any],
    *,
    entity_uri: str | None,
    proposal_status: Mapping[str, Any],
) -> dict[str, Any]:
    """The `extra` frontmatter `plan_ingest` writes beside its own six keys.

    `plan_ingest` drops values in `(None, "", (), [])`, so an absent entity or
    an empty tag list contributes nothing rather than a null.

    **That rule is top-level only**, so a nested blank survives it: every page
    this tool wrote before this filter carried `error: null` inside its
    `proposal_status` block, on a clean run. The same rule is applied one level
    down here. `proposals: 0` is not blank under it and stays -- a run that
    filed nothing said so, and that is worth a key. The mapping can never
    filter down to `{}`: `reasoner` and `extractor` always carry a status
    string, so no empty `proposal_status` block can reach the page. Consumers
    read an absent key as empty.

    It takes no `state_gate=` and no kind: the drift stamp was this function's
    only reader of either, and K-F deleted it. The gate seam itself is
    untouched -- `plan_document_brief` still takes it, `read_state_gate` still
    runs, and `DocumentBrief.state_gate` still carries the result for whatever
    reads it next.
    """
    extra: dict[str, Any] = {key: frontmatter[key] for key in CARRIED_KEYS if frontmatter.get(key) not in _BLANK}
    extra["entity_uri"] = entity_uri
    extra["proposal_status"] = {key: value for key, value in proposal_status.items() if value not in _BLANK}
    return extra


#: An opening or closing code fence: three or more backticks or tildes, at up
#: to three columns of indentation. Enough to tell a fenced example of the
#: `## Touches` section from the section itself.
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def _touches_span(body: str) -> tuple[int, int] | None:
    """`(start, end)` of the real `## Touches` heading line, or `None`.

    "Real" means the heading is a line of its own -- not a substring, not
    indented into a code block -- and is not inside a fenced region. An
    ingestor page that documents its own output format carries a fenced
    example of the section, and writing the entity link into that example
    produces a page whose only forward link is inside a code block.

    An unclosed fence leaves everything after it fenced. That is the
    conservative reading and it costs nothing: `compose_body` appends a fresh
    section rather than guessing which side of the fence the heading is on.
    """
    offset = 0
    fence: str | None = None
    for line in body.splitlines(keepends=True):
        marker = _FENCE_RE.match(line)
        if marker is not None:
            token = marker.group(1)[0]
            fence = token if fence is None else (None if token == fence else fence)
        elif fence is None and line.rstrip() == TOUCHES_HEADING:
            return offset, offset + len(line)
        offset += len(line)
    return None


def compose_body(body: str, *, entity_page: str | None) -> str:
    """*body* carrying a root-absolute markdown link to *entity_page*.

    The durable forward-anchor a backlink derivation reads. Idempotent: the
    link is inserted under an existing `## Touches` heading, or the section is
    appended when the model omitted it -- or when the only heading it wrote is
    a fenced example of one. Never a wikilink -- a catalog written in
    wikilinks is invisible to backlinks, broken-link validation and traversal.
    """
    if entity_page is None:
        return body
    destination = f"/{entity_page}.md"
    link = f"[{destination}]({destination})"
    if link in body:
        return body
    span = _touches_span(body)
    if span is None:
        separator = "" if body.endswith("\n") else "\n"
        return f"{body}{separator}\n{TOUCHES_HEADING}\n\n- {link}\n"
    cut = span[1]
    # The heading is the last line and carries no newline, so there is nothing
    # to insert *before*; the section body is appended after it instead.
    if cut >= len(body):
        return f"{body}\n\n- {link}\n"
    separator = "" if body[cut : cut + 1] == "\n" else "\n"
    return f"{body[:cut]}{separator}- {link}\n{body[cut:]}"


#: What `generated.by` says when the caller does not.
_DEFAULT_BY = "agent:graph-works-core"

#: `doc_wiki_okf.cli.IGNORE`, restated. Declarations are not concepts, and a
#: reference copy is material rather than a page. It is duplicated rather than
#: imported because that constant lives in a module that imports `typer`, and a
#: library taking a CLI framework as a transitive import to read one tuple is
#: worse than six lines that a test pins to the original.
BUNDLE_IGNORE: tuple[str, ...] = (
    "_schema/*",
    "*/_schema/*",
    "_sections/*",
    "*/_sections/*",
    "sources/references/*",
    "*/sources/references/*",
)


@contextmanager
def _matcher_for(
    supplied: EntityMatcher | None, config: Config, schema_set: SchemaSet
) -> Iterator[EntityMatcher | None]:
    """Yield the entity matcher this run should use, closing what it opened.

    Precedence: an explicitly supplied matcher wins; otherwise this package's
    own implementation is built over the configured graph; otherwise `None`.

    **An uninitialized graph is a supported state, not an error.** Legacy
    raised at command entry on the theory that slug-aligning with the graph is
    the whole point. Once the matcher is an injectable seam, *absent* is
    specified -- `NO_ENTITY` -- and hard-failing on a state the design declares
    legal is a contradiction. Whether a graph is required becomes the CLI's
    policy, decided later.
    """
    if supplied is not None:
        yield supplied
        return
    try:
        reader = open_reader(graph_dir=config.graph_dir)
    except (GraphNotInitializedError, SchemaMismatchError) as exc:
        logger.info("no usable code graph at %s (%s); ingesting without an entity link", config.graph_dir, exc)
        yield None
        return
    try:
        yield entity_matcher(reader, schema_set)
    finally:
        reader.close()


#: `run_suggest_phase`'s five non-proposal outcome keys, and what `log.md`
#: calls each. Named separately because they are five different things: the
#: three before-write drops shared one "N suggestion(s) dropped" count that
#: told a reader which had happened only by accident.
_OUTCOME_KEYS: tuple[tuple[str, str], ...] = (
    ("unclassified", "unclassified"),
    ("refused", "refused"),
    # `duplicates` is the only label that is a plural noun -- the others are
    # adjectives or a verb phrase, which do not inflect -- so it is the only
    # one that needs the `(s)`, the same way `proposal(s)` does in the
    # rendered line.
    ("duplicates", "duplicate(s)"),
    ("failed", "failed to write"),
    ("errored", "errored"),
)


def _log_line(result_page: str, title: str, status: Mapping[str, Any]) -> str:
    """The one `log.md` bullet this ingest appends."""
    proposals = int(status.get("proposals", 0) or 0)
    line = f"**Ingest** [{title}](/{result_page}) — {proposals} proposal(s) filed"
    for key, label in _OUTCOME_KEYS:
        entries = status.get(key) or ()
        if entries:
            line += f"; {len(entries)} {label}"
    if status.get("error"):
        line += f"; suggest phase degraded: {status['error']}"
    return line


async def run_ingest_source(
    material: Path,
    *,
    layout: WorkspaceLayout,
    repo: Path,
    today: date,
    at: datetime,
    source_kind: str = "doc",
    origin: str = "",
    by: str = _DEFAULT_BY,
    match_entity: EntityMatcher | None = None,
    state_gate: StateGate | None = None,
    graph_tools: Sequence[BaseTool] = (),
    model_override: str | None = None,
    ingest_layout: IngestLayout = GRAPH_WIKI_LAYOUT,
) -> IngestResult:
    """Record *material* as a Source page, and propose what it justifies.

    One ingestor call, one best-effort suggest phase, one atomic write.

    *source_kind* is a **hint**: `plan_document_brief` needs a value before the
    model has read anything, the model classifies from content, and
    `validated_source_kind` decides against the vocabulary this bundle's
    `Source` schema declares. *origin* defaults to the material's own path --
    an opaque string saying where it came from.

    *layout* is the `WorkspaceLayout` -- where the workspace keeps its parts.
    *ingest_layout* is the `IngestLayout` -- the source-page template one
    vault uses. Two different questions, two different names in this
    signature: `plan_document_brief`, `page_target` and `plan_ingest` all take
    it, so none of the four can pick a different template by defaulting
    differently.

    **The brief's path is a prediction, not the answer.** `brief.title` is the
    material's first `# ` heading or its filename, guessed before anything had
    been read; the model's `title` decides the page, the reference copy, the
    slug, and which entity the page is matched against. The brief's
    `suggested_summary_path` and `IngestResult.page` agree only when the model
    agrees with the heading -- which is the common case, and is why the entity
    match re-runs only when it does not.

    The three seams are all defaulted:

    | Seam | Default |
    |---|---|
    | `match_entity` | this package's own, over the configured graph; `None` if there is no graph |
    | `state_gate` | `None` -- pass `state_gate_adapter(config)` to opt in |
    | `graph_tools` | `()` -- a later vertical builds them; the CLI wires them in |

    `today` and `at` are required and injected. Nothing in this package reads
    the clock.

    **Proposals commit after the page, not before.** `plan_suggestions` runs
    and `compose_frontmatter` composes against its plan-time status before
    `plan_ingest` is even built -- `proposal_status` has to exist for that call
    -- but `apply_suggestions` is deferred until after the page's own
    `apply_plan` succeeds. A refused or failed main write means
    `apply_suggestions` is never called, so no proposal cites a `resource` for
    a page that never landed.

    **A re-ingest is refused by `origin`, not just by title-derived path.**
    `plan_ingest` also refuses when an existing `Source` page already carries
    the same `origin`, so a re-ingest whose model picks a different title is
    still refused rather than landing a second page.
    """
    config = load_config(layout.bundle_dir)
    schema_set = load_schemas(config.declarations_dir / "_schema")
    kinds = source_kinds(schema_set)
    section_set = load_sections(config.declarations_dir / "_sections")
    lanes = lane_set(schema_set)
    bundle = load_bundle(layout.bundle_dir, ignore=BUNDLE_IGNORE)

    with _matcher_for(match_entity, config, schema_set) as matcher:
        brief = plan_document_brief(
            material,
            wiki=layout.bundle_dir,
            repo=repo,
            workspace_root=layout.root,
            today=today,
            source_kind=source_kind,
            layout=ingest_layout,
            state_gate=state_gate,
            match_entity=matcher,
        )

    try:
        text = brief.source_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        # `plan_document_brief` reads the same file through `reading.extract`,
        # which decodes with `errors="replace"` and therefore always succeeds.
        # This read cannot: the text becomes the reference copy, and a copy
        # with replacement characters in it is not the material. Refusing is
        # `doc_wiki_okf`'s own posture for material it cannot record, and it
        # costs nothing here -- no model has been called yet. Which is also why
        # this path keeps `brief.title`: there is no model title to prefer.
        refused = page_target(brief.title, today=today)
        logger.warning("cannot ingest %s: not UTF-8 text (%s)", brief.source_path, exc)
        return IngestResult(
            ok=False,
            page=refused,
            copy=copy_target(refused, brief.source_path),
            title=brief.title,
            source_kind=brief.source_kind,
            refusals=(f"{brief.source_path}: not-utf-8: {exc.reason} at byte {exc.start}",),
        )

    system = build_ingestor_system(
        layout=layout, kinds=kinds, schema_set=schema_set, project_context=render_project_context(layout)
    )
    human = (
        f"Source material: {brief.source_path}\n"
        f"Source kind (caller's hint): {brief.source_kind}\n"
        f"Word count: {brief.word_count}\n"
        f"Provisional page path (your own `title` decides the final one): "
        f"{brief.suggested_summary_path}\n\n"
        f"--- Source content ---\n{brief.preview}\n--- End source ---\n"
    )
    response = await make_llm("ingestor", layout=layout, model_override=model_override).ainvoke(
        [SystemMessage(system), HumanMessage(human)]
    )
    if not isinstance(response.content, str):
        raise RuntimeError("ingestor returned non-text content")

    frontmatter, body, parsed = parse_ingestor_response(response.content)
    validated = validated_source_kind(frontmatter, hint=brief.source_kind, kinds=kinds)
    # The model's title wins. `brief.title` was the material's first `# `
    # heading or its filename -- a guess made before anything had been read.
    title = str(frontmatter.get("title") or "").strip() or brief.title
    # A second, short-lived matcher block, and only when the title actually
    # changed: `brief.entity_match` was matched on the guess, and the entity
    # this page names should be the one its own title names. The comparison is
    # exact-string rather than slug-aware on purpose -- `lookup_by_name` keys
    # on the name verbatim, so two titles sharing a slug are still two
    # different entity lookups, and folding them would skip a match that would
    # have resolved differently. The cost of that is a second graph read when
    # the model rewords a title into the same slug; the reward is that no
    # graph reader is held open across the model call above.
    match = brief.entity_match
    if title != brief.title:
        with _matcher_for(match_entity, config, schema_set) as matcher:
            match = NO_ENTITY if matcher is None else matcher(repo, brief.source_path, title)
    entity_uri = match.uri
    entity_page = match.entity_filename
    page = page_target(title, today=today, layout=ingest_layout)
    # Hoisted out of the `plan_ingest` call below so the line the reasoner
    # reads and the value the page's frontmatter carries cannot diverge.
    resolved_origin = origin or str(material)

    composed = compose_body(body, entity_page=entity_page)
    planned, status = await plan_suggestions(
        bundle=bundle,
        schema_set=schema_set,
        lane_set=lanes,
        material=brief.source_path,
        source_text=brief.text,
        source_page=page,
        source_title=title,
        source_kind=validated,
        origin=resolved_origin,
        page_text=composed,
        entity_uri=entity_uri,
        entity_page=entity_page,
        by=by,
        at=at,
        graph_tools=graph_tools,
        layout=layout,
        model_override=model_override,
    )

    extra: dict[str, Any] = compose_frontmatter(
        frontmatter,
        entity_uri=entity_uri,
        proposal_status=status,
    )
    plan = plan_ingest(
        bundle,
        schema_set,
        section_set,
        brief.source_path,
        text=text,
        title=title,
        description=str(frontmatter.get("description") or "").strip() or title,
        source_kind=validated,
        origin=resolved_origin,
        by=by,
        at=at,
        today=today,
        layout=ingest_layout,
        body=composed,
        **extra,
    )
    base = IngestResult(
        ok=False,
        page=page,
        copy=copy_target(page, brief.source_path),
        title=title,
        source_kind=validated,
        entity_uri=entity_uri,
        entity_page=entity_page,
        frontmatter_parsed=parsed,
        proposal_status=status,
    )
    if not plan.ok:
        # `apply_suggestions` is never called: the page never landed, so zero
        # proposals do -- no orphans citing a `resource` that was never written.
        # `status["proposals"]` is still the plan-time optimistic count, so it's
        # overridden to 0 rather than reused from `base`.
        return replace(
            base,
            proposal_status={**status, "proposals": 0},
            refusals=tuple(f"{refusal.path}: {refusal.kind}: {refusal.detail}" for refusal in plan.refusals),
        )

    applied = apply_plan(bundle, plan)
    if not applied.ok:
        # Same reasoning as the refusal above: the page write never landed,
        # so `apply_suggestions` never ran and zero proposals did either.
        return replace(
            base,
            proposal_status={**status, "proposals": 0},
            written=tuple(applied.written),
            refusals=tuple(f"{failure.path}: {failure.kind}" for failure in applied.failed),
        )

    proposal_reports, apply_status = apply_suggestions(bundle, planned)
    status = merge_apply_status(status, apply_status)
    base = replace(base, proposals=tuple(proposal_reports), proposal_status=status)

    reconciled = load_bundle(layout.bundle_dir, ignore=BUNDLE_IGNORE)
    #: The directories reconciled after every ingest: the one the source page
    #: landed in, and the bundle root whose own index lists it as a
    #: subdirectory. Derived from the page rather than named, so a replaced
    #: `IngestLayout.source_page_template` reconciles the lane it actually
    #: wrote to.
    directories = [str(PurePosixPath(page).parent), ""]
    updates = update_index(reconciled, directories=directories, create_missing=True, dry_run=False)
    log_document = reconciled.logs.get("")
    if log_document is not None:
        append_log_entry(log_document, _log_line(page, title, status), on=today, dry_run=False)

    return replace(
        base,
        ok=True,
        written=tuple(applied.written),
        indexes_updated=tuple(
            sorted(
                "" if update.path == "index.md" else update.path.removesuffix("/index.md")
                for update in updates
                if update.changed
            )
        ),
    )


__all__ = [
    "BUNDLE_IGNORE",
    "CARRIED_KEYS",
    "TOUCHES_HEADING",
    "IngestResult",
    "compose_body",
    "compose_frontmatter",
    "parse_ingestor_response",
    "run_ingest_source",
    "state_gate_adapter",
    "validated_source_kind",
]
