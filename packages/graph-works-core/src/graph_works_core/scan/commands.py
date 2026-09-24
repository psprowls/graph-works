"""The three-phase scan pipeline: worklist, fan-out, splice-and-stamp.

Phase 1 is mechanical and decides *what needs prose*. Phase 2 is the only phase
that talks to a model. Phase 3 is deterministic and decides *what lands*. The
split is what makes a scan resumable: a run that dies after phase 1 replays from
`worklist.json` without re-walking the graph.

**The structural half is not here.** `code_wiki_okf.sync.sync_bundle` owns the
complete entity/File plan, preflight and application. This module invokes that
one composite operation and adapts its result to graph-works' structural
summary.

**Drift propagation is not here either** (design spec §3.1). This vertical emits
and applies prose-refresh work and nothing else; the lint vertical builds its own
worklist over the same graph.

**The prose surface is declaration-driven.** `ownership == "prose"` in a type's
`sections/*.yaml` is the definition of what this fills. Adding a prose section
to a declaration puts it in the worklist with no change here.

This module imports `commands.graph`, and deliberately: the graph surface is
what every vertical reads the code through, and re-deriving a `GraphTarget`
here would be a second answer to a question C7 already owns. It is not the
only edge into that surface -- `commands.query` and `adapters.query_orchestrator`
resolve their targets the same way, which is the point.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from pathlib import Path, PurePosixPath

from code_graph_io import (
    GraphNotInitializedError,
    GraphReader,
    NodeRecord,
    SchemaMismatchError,
    open_reader,
)
from code_wiki_okf.config import Config, RepoConfig
from code_wiki_okf.entities.sync import SyncSummary
from code_wiki_okf.git_state import changed_files_since, head_commit
from code_wiki_okf.placement import (
    PlacementError,
    canonical_concept_id,
    context_from_resource,
    is_code_wiki_type,
)
from code_wiki_okf.sync import MirrorSummary, SyncResult, sync_bundle
from langchain_core.tools import BaseTool
from okf_ext.body import find_section, split_lines
from okf_ext.bundle import SECTIONS_DIRNAME
from okf_ext.shape import SectionSet, SectionSpec, load_sections
from okf_ext.splice import assemble, bare_lines, dominant_newline, has_trailing_newline
from okf_ext.splice import replace as splice_replace
from okf_ext.writing import PendingWrite, write_all
from okf_io import Bundle, Document, append_log_entry, load_bundle, update_index
from okf_io import parse as parse_document
from subagents_io.pool import SubagentPool

from graph_works_core.agent_substrate.agent_tools import truncate_text
from graph_works_core.agent_substrate.roles import role_binding
from graph_works_core.graph import commands as graph
from graph_works_core.graph import graph_tools
from graph_works_core.scan.prose_refresh import run_prose_refresh
from graph_works_core.scan.prose_refresher import build_prose_refresh_prompt, sanitize_prose_result
from graph_works_core.scan.scan_contract import (
    ApplyResult,
    ProseRefreshResult,
    ProseRefreshTask,
    ScanResults,
    ScanWorklist,
    SkippedPage,
    result_from_payload,
    worklist_from_payload,
    worklist_payload,
)
from graph_works_core.workspace.errors import ScanError
from graph_works_core.workspace.layout import WorkspaceLayout

#: The frontmatter key this vertical owns. A full SHA, stamped by phase 3 only.
#: `last_updated_commit` cannot serve: `entities.sync` advances it to HEAD during
#: the structural pass, unconditionally, so reusing it would make the next scan's
#: diff gate read empty even when prose failed (design spec §4).
PROSE_ANCHOR_KEY = "prose_refreshed_commit"

#: The per-page bound on retrying a decline. A declared provenance key in all
#: six declarations that carry prose sections, not a private key this module
#: writes and reads -- a key that exists nowhere in the declared vocabulary is
#: invisible to the next reader of `Package.yaml` (design spec D1).
PROSE_ATTEMPTS_KEY = "prose_refresh_attempts"

#: How many times a page may come back still holding a placeholder before phase
#: 1 stops dispatching it. The diff trigger ignores the cap entirely, so moved
#: code always earns a fresh attempt and the counter self-heals.
MAX_PROSE_ATTEMPTS = 3

#: The okf type of each entity lane whose *prose* this vertical fills, mapped
#: to the graph kind `graph_tools.describe` renders it under. Mirror `File` is
#: absent because it has no prose lane to fill -- not because the structural
#: pass does not touch it; it does (`sync_bundle`).
LANE_TYPES: Mapping[str, str] = {
    "Package": "package",
    "App": "app",
    "TestSuite": "test_suite",
    "AgentPlugin": "agent_plugin",
    "Repository": "repository",
    "Dependency": "dependency",
}

#: One page's budget inside a task.
MAX_TASK_PAGE_CHARS = 20_000

#: One entity's rendered graph description inside a task.
MAX_TASK_GRAPH_CHARS = 8_000

#: How many changed paths a task's rendered diff names.
MAX_TASK_DIFF_FILES = 200

#: The basenames that mean "a package lives here". Used only to scope the
#: *repository* page's diff gate: its two prose sections are `Overview` and
#: `Layout`, and a manifest appearing or disappearing is what a layout change
#: looks like on disk.
#:
#: A deliberate duplication of `code_graph_io`'s own discovery set, which
#: exposes no public constant to import. For `pyproject.toml` and
#: `package.json` the duplication is exact -- `code_graph_io` discovers both by
#: these same filenames (`packages.py:178,186`). For `plugin.json` it is not:
#: `code_graph_io` discovers a plugin by the two-segment pattern
#: `.claude-plugin/plugin.json` (`agent_plugins.py:217`), while matching here is
#: on basename alone, so a stray `plugin.json` anywhere in the repo -- not only
#: under `.claude-plugin/` -- also marks the repository page stale. That is
#: deliberately over-inclusive rather than precise: a false "stale" costs one
#: spurious model call, while a false "not stale" would let `Layout` rot in
#: silence, which is the failure this filter exists to close. `pytest.ini` and
#: `setup.cfg` are not from `code_graph_io`'s package discovery at all -- they
#: are role-flag markers in `structural_nodes.py`'s `_CONFIG_FILENAMES`, a
#: different mechanism carried into this set for the same over-inclusive
#: reason. This set still needs review whenever either upstream set changes.
MANIFEST_FILENAMES: frozenset[str] = frozenset(
    {"pyproject.toml", "package.json", "plugin.json", "pytest.ini", "setup.cfg"}
)

_ABBREVIATED = 12


@dataclass(frozen=True, slots=True)
class _EntityRef:
    """One graph entity, resolved to everything phase 1 needs about it."""

    uri: str
    type_name: str
    describe_kind: str
    describe_identifier: str
    name: str
    repo_path: Path | None
    relative_root: str
    head: str | None

    @property
    def absolute_root(self) -> str:
        if self.repo_path is None:
            return ""
        return str(self.repo_path / self.relative_root) if self.relative_root else str(self.repo_path)


def _entity_relative_root(node: NodeRecord) -> str:
    """The repo-relative directory an entity's files live under.

    Every lane node's `path` **is** that directory. `code_graph_io` derives a
    package's from its manifest's *parent* (`packages.py:327`) and never stores
    the manifest itself; a plugin node stores the plugin directory
    (`agent_plugins.py:247`), a test suite its root (`test_suites.py:177`), a
    repository the empty string. There is nothing to strip.

    A `path.suffix` heuristic used to stand here on the premise that a package
    node points at its manifest. It does not, and the branch fired only on the
    accident of a dot in a directory name -- `packages/foo.bar` -- where it
    widened the diff scope to `packages/` and made every sibling's commits read
    as staleness.
    """
    raw = str(node.path or "")
    if not raw:
        return ""
    normalized = PurePosixPath(raw).as_posix()
    return "" if normalized == "." else normalized


def _repo_entity_refs(
    reader: GraphReader, repo_cfg: RepoConfig, repo_uri: str, head: str | None
) -> dict[str, _EntityRef]:
    listings: Mapping[str, list[NodeRecord]] = {
        "Package": reader.list_packages(),
        "App": reader.list_apps(),
        "TestSuite": reader.list_test_suites(),
        "AgentPlugin": reader.list_agent_plugins(),
    }
    refs: dict[str, _EntityRef] = {}
    for type_name, nodes in listings.items():
        for node in nodes:
            uri = str(node.attrs.get("uri") or "")
            if not uri or node.attrs.get("repo") != repo_uri:
                continue
            refs[uri] = _EntityRef(
                uri=uri,
                type_name=type_name,
                describe_kind=LANE_TYPES[type_name],
                describe_identifier=node.name,
                name=node.name,
                repo_path=repo_cfg.path,
                relative_root=_entity_relative_root(node),
                head=head,
            )
    for node in reader.list_dependencies():
        uri = str(node.attrs.get("uri") or "")
        if not uri or node.attrs.get("repo") != repo_uri:
            continue
        refs[uri] = _EntityRef(
            uri=uri,
            type_name="Dependency",
            describe_kind="dependency",
            describe_identifier=uri.removeprefix("dependency:"),
            name=node.name,
            repo_path=repo_cfg.path,
            relative_root="",
            head=head,
        )
    refs[repo_uri] = _EntityRef(
        uri=repo_uri,
        type_name="Repository",
        describe_kind="repository",
        describe_identifier=repo_cfg.name,
        name=repo_cfg.name,
        repo_path=repo_cfg.path,
        relative_root="",
        head=head,
    )
    return refs


def entity_refs(reader: GraphReader, config: Config) -> dict[str, _EntityRef]:
    """Every entity the graph names, keyed by the `resource:` its page carries.

    Per-repo HEAD comes from `Config.repositories`, the same source
    `entities.sync` reads (design spec §3.5). A repository declared in
    `workspace.yaml` but absent from the graph contributes nothing, matching
    `sync`'s own skip.

    Dependencies are repository-owned (D-004): each carries its repository's
    path and HEAD, like every other repo-owned kind.
    """
    repo_uris = {node.name: str(node.attrs.get("uri") or "") for node in reader.list_repositories()}
    refs: dict[str, _EntityRef] = {}
    for repo_cfg in config.repos:
        repo_uri = repo_uris.get(repo_cfg.name)
        if not repo_uri:
            continue
        refs.update(_repo_entity_refs(reader, repo_cfg, repo_uri, head_commit(repo_cfg.path)))
    return refs


def prose_specs(section_set: SectionSet, type_name: str) -> tuple[SectionSpec, ...]:
    """The `ownership == "prose"` sections a type declares, in declaration order."""
    declaration = section_set.types.get(type_name)
    if declaration is None:
        return ()
    return tuple(spec for spec in declaration.sections if spec.ownership == "prose")


def heading_key(spec: SectionSpec) -> str:
    """A spec's full heading, the form `prose_sections` is keyed by."""
    return f"{'#' * spec.level} {spec.heading}"


def is_unfilled(body: str | None, placeholder: str) -> bool:
    """Whether a section body still needs its first fill.

    `None` -- the section is not on the page at all -- is **not** unfilled here:
    scaffolding a missing declared section is `entities.sync`'s job, and phase 3
    could not splice into a heading that is not there.
    """
    if body is None:
        return False
    stripped = body.strip()
    return not stripped or stripped == placeholder.strip()


def _section_body(document: Document, spec: SectionSpec) -> str | None:
    section = find_section(document.body, spec.heading, level=spec.level)
    return None if section is None else section.slice(document.body)


def _repository_scoped(changed: Sequence[str]) -> list[str]:
    """*changed* narrowed to what a repository page's prose actually describes.

    A root-level file carries `Overview`; a manifest anywhere carries `Layout`.
    A change deep inside a package is neither, and the whole-repo scope this
    replaces made every such commit a guaranteed per-scan LLM call.
    """
    return [
        path
        for path in changed
        # no "/" means the path has no parent directory -- a root-level file
        if "/" not in path or PurePosixPath(path).name in MANIFEST_FILENAMES
    ]


def _render_diff(changed: Sequence[str]) -> tuple[str, tuple[str, ...]]:
    """`(rendered, kept)` for a change set, cut at `MAX_TASK_DIFF_FILES`.

    The cut reports itself. `truncate_text` already signals *character*
    truncation in the rendered prompt; the file-count cut did not, so a model
    handed 200 of 431 paths read a partial change set as complete. The marker
    leads the rendered diff rather than trailing it: `build_prose_refresh_prompt`
    runs the diff through `truncate_text(..., MAX_PROMPT_DIFF_CHARS)`, which cuts
    from the end, so a trailing marker is discarded in exactly the case that
    produces it. Leading survives that cut. The marker rides in the diff string
    the prompt renders verbatim, matching `list_repo_tree`'s truncation
    vocabulary (`prose_refresh.py`), so it needs no contract field of its own.
    `changed_files` stays truncated as before.
    """
    kept = tuple(changed[:MAX_TASK_DIFF_FILES])
    rendered = "\n".join(kept)
    if len(changed) > MAX_TASK_DIFF_FILES:
        rendered = f"[TRUNCATED after {MAX_TASK_DIFF_FILES} of {len(changed)} changed files]\n{rendered}"
    return rendered, kept


def _diff_gate(ref: _EntityRef, anchor: str) -> tuple[bool, str | None, tuple[str, ...]]:
    """`(stale, rendered_diff, changed_files)` for prose behind repository HEAD.

    `changed_files_since` diffs `anchor..HEAD`. The current head comes from the
    entity reference rather than the page's `last_updated_commit`: composite
    sync deliberately avoids provenance-only rewrites, so that page field can
    remain equal to the prose anchor when generated entity content did not
    change. A `None` diff -- git unavailable, or an anchor the repo does not
    know -- is the rewritten-history case and comes back as stale with no diff.

    The repository lane is the one lane whose `relative_root` is `""`, so
    `sub_paths` cannot scope it and its gate would otherwise be "did anything in
    this repo change". `_repository_scoped` narrows the answer instead; empty
    after narrowing is not stale, and costs no model call.
    """
    if ref.repo_path is None or ref.head is None or anchor == ref.head:
        return False, None, ()
    sub_paths = (ref.relative_root,) if ref.relative_root else ()
    changed = changed_files_since(ref.repo_path, anchor, sub_paths=sub_paths)
    if changed is None:
        return True, None, ()
    if ref.type_name == "Repository":
        changed = _repository_scoped(changed)
    if not changed:
        return False, None, ()
    rendered, kept = _render_diff(changed)
    return True, rendered, kept


def _build_task(
    *,
    reader: GraphReader,
    document: Document,
    concept_id: str,
    ref: _EntityRef,
    headings: Sequence[SectionSpec],
    trigger: str,
    diff: str | None,
    changed: tuple[str, ...],
) -> ProseRefreshTask:
    return ProseRefreshTask(
        uri=ref.uri,
        kind=ref.type_name,
        name=ref.name,
        page_path=f"{concept_id}.md",
        entity_root=ref.absolute_root,
        trigger=trigger,
        diff=diff,
        changed_files=changed,
        page_content=truncate_text(document.raw_text, MAX_TASK_PAGE_CHARS),
        prose_sections={heading_key(spec): (_section_body(document, spec) or "").strip() for spec in headings},
        graph_context=truncate_text(
            graph_tools.describe(reader, kind=ref.describe_kind, identifier=ref.describe_identifier),
            MAX_TASK_GRAPH_CHARS,
        ),
        owning_short_head=None if ref.head is None else ref.head[:_ABBREVIATED],
    )


def _attempts(document: Document) -> int:
    """`prose_refresh_attempts` as an int, or 0 for anything unreadable.

    Content, so it does not raise (the reader's own stance): a hand-edited
    string or list reads as "no attempts recorded", which retries rather than
    silently exhausting a page.
    """
    try:
        return int(document.fm_raw.get(PROSE_ATTEMPTS_KEY) or 0)
    except (TypeError, ValueError):
        return 0


@dataclass(frozen=True, slots=True)
class _PageClassification:
    """What phase 1's classification pass decided, before truncation.

    `adoptions` is the write half of `adopted` -- staged here and performed by
    `build_scan_worklist`, which is the function that knows whether this is a
    dry run.
    """

    tasks: tuple[ProseRefreshTask, ...] = ()
    skipped: tuple[SkippedPage, ...] = ()
    adopted: tuple[str, ...] = ()
    adoptions: tuple[PendingWrite, ...] = ()


def _stage_adoption(document: Document, *, member: str, bundle_root: Path, at: str) -> PendingWrite:
    """Stage one hand-written page's anchor stamp.

    A fresh parse, not `dataclasses.replace`: a shallow clone shares `fm_raw`
    with the live bundle document, and a frontmatter edit through it would
    mutate the bundle whether or not the write lands. The same reason phase 3
    re-parses.

    Adoption is a **structural** act, not a prose one -- it writes one
    frontmatter key and no body -- which is why it lives in phase 1. Phase 1
    already writes (it runs `sync`), and a `narrate=False` structural-only run
    must still adopt.

    `PROSE_ANCHOR_KEY` and `PROSE_ATTEMPTS_KEY` are written as a pair here, the
    same as phase 3's own stamp: adoption is the recovery path for a page that
    stalled out mid-refresh, so leaving a stale attempt count behind would let
    a later declared-section change find the page already exhausted, with zero
    attempts at the new section.
    """
    scratch = parse_document(document.raw_text, path=document.path)
    scratch.set(PROSE_ANCHOR_KEY, at)
    scratch.delete(PROSE_ATTEMPTS_KEY)
    return PendingWrite(member=member, path=bundle_root / member, rendered=scratch.serialize(), on_written=_noop)


def _classify_pages(
    bundle: Bundle,
    section_set: SectionSet,
    refs: Mapping[str, _EntityRef],
    reader: GraphReader,
    bundle_root: Path,
) -> _PageClassification:
    """Classify every page, in sorted concept-id order.

    Sorted so `max_entities` truncation is deterministic: the same bundle always
    drops the same tail.

    The per-page decision, in order:

    1. **Establish ownership.** A readable document must declare a code-wiki
       type and its resource must validate to this exact canonical concept ID.
       This is type/resource policy, never a guess from the directory spelling.
       `File` is structurally owned but declares no prose sections, so it exits
       silently. A canonical prose entity whose graph resource is absent or of
       another kind appends a `SkippedPage`.
    2. **Adopt.** No anchor, a `last_updated_commit`, and nothing unfilled means
       a person wrote this prose. Stamp the anchor at that commit and yield no
       task -- §5.2's intent (every page is anchor-tracked) met with no model
       call, where the literal reading would have rewritten every hand-written
       page in every existing vault on the first scan after this lands. From the
       next commit it diff-tracks like any other page (design spec D5).
    3. **Gate `first_fill` on the counter.** Unfilled sections and
       `prose_refresh_attempts >= MAX_PROSE_ATTEMPTS` means the model has
       declined this page three times; stop paying for a fourth. This is also
       what frees the `max_entities` tail: a permanently-stuck page stops
       holding a slot at the head of the sorted list. The diff trigger is
       deliberately outside this gate -- the counter bounds retrying a decline,
       not refreshing a change.
    4. **Otherwise** build the task exactly as before.
    """
    tasks: list[ProseRefreshTask] = []
    skipped: list[SkippedPage] = []
    adopted: list[str] = []
    adoptions: list[PendingWrite] = []

    for concept_id in sorted(bundle.concepts):
        document = bundle.concepts[concept_id]
        member = f"{concept_id}.md"
        if document.parse_error is not None:
            continue
        type_name = str(document.fm.type or "")
        if not is_code_wiki_type(type_name):
            continue
        resource = str(document.fm.resource or "")
        try:
            canonical_id = canonical_concept_id(context_from_resource(type_name, resource))
        except PlacementError:
            continue
        if canonical_id != concept_id or type_name not in LANE_TYPES:
            continue
        ref = refs.get(resource)
        if ref is None:
            skipped.append(SkippedPage(page=member, reason="unresolved-resource"))
            continue
        if ref.type_name != type_name:
            skipped.append(SkippedPage(page=member, reason="type-mismatch"))
            continue
        specs = prose_specs(section_set, type_name)
        if not specs:
            continue

        first_fill = [spec for spec in specs if is_unfilled(_section_body(document, spec), spec.placeholder)]
        anchor = str(document.fm_raw.get(PROSE_ANCHOR_KEY) or "")
        last_updated = str(document.fm_raw.get("last_updated_commit") or "")

        if not anchor and last_updated and not first_fill:
            adopted.append(member)
            adoptions.append(_stage_adoption(document, member=member, bundle_root=bundle_root, at=last_updated))
            continue

        diff_stale, diff, changed = _diff_gate(ref, anchor) if anchor else (False, None, ())
        if not first_fill and not diff_stale:
            continue
        if first_fill and not diff_stale and _attempts(document) >= MAX_PROSE_ATTEMPTS:
            skipped.append(SkippedPage(page=member, reason="attempts-exhausted"))
            continue

        headings = specs if diff_stale else first_fill
        tasks.append(
            _build_task(
                reader=reader,
                document=document,
                concept_id=concept_id,
                ref=ref,
                headings=headings,
                trigger="first_fill" if first_fill else "diff",
                diff=diff,
                changed=changed,
            )
        )

    return _PageClassification(
        tasks=tuple(tasks), skipped=tuple(skipped), adopted=tuple(adopted), adoptions=tuple(adoptions)
    )


def _open_reader(target: graph.GraphTarget) -> GraphReader:
    try:
        return open_reader(graph_dir=target.graph_dir)
    except (GraphNotInitializedError, SchemaMismatchError) as exc:
        raise ScanError(f"cannot open the code graph at {target.graph_dir}: {exc}") from exc


async def build_scan_worklist(
    layout: WorkspaceLayout,
    config: Config,
    *,
    today: date,
    at: datetime,
    max_entities: int | None = None,
    dry_run: bool = True,
) -> tuple[ScanWorklist, StructuralSummary]:
    """Refresh the graph, run the structural pass, and decide what needs prose.

    Returns the worklist **and** the structural pass's own summary, unaltered,
    so a caller can report the mechanical result independently of whether any
    prose ran.

    The structural pass is the standalone composite `sync_bundle`, sharing one
    open graph reader across entity and File planning. Its dry run is a real
    preview of both halves and writes nothing.

    `async` for signature uniformity with `run_scan`; nothing here awaits.

    `dry_run=True` is the default, matching the repo convention that a writer
    previews unless application is explicit. The previewed worklist still
    reads the unchanged on-disk bundle, so pages the structural preview would
    create are absent and adoption remains reported rather than written.

    Raises `ScanError` when the graph cannot be built or opened -- environment,
    not content.
    """
    target = graph.graph_target(layout)
    build_result = graph.build(target)
    if not build_result.ok:
        raise ScanError(build_result.error or "code graph build failed")

    reader = _open_reader(target)
    try:
        synced = sync_bundle(
            layout.bundle_dir,
            config=config,
            reader=reader,
            at=at.isoformat(),
            today=today,
            dry_run=dry_run,
        )
        summary = StructuralSummary.from_sync_result(synced)
        # The composite committed its writes; reload so the classifier reads the
        # pages they just created and re-stamped rather than the pre-sync
        # snapshot. Under `dry_run` neither wrote anything, so this is the same
        # snapshot.
        bundle = load_bundle(layout.bundle_dir)
        section_set = load_sections(config.declarations_dir / SECTIONS_DIRNAME)
        refs = entity_refs(reader, config)
        phase1 = _classify_pages(bundle, section_set, refs, reader, layout.bundle_dir)
    finally:
        reader.close()

    adopted = phase1.adopted
    skipped = phase1.skipped
    if phase1.adoptions and not dry_run:
        # `write_all` isolates commit failures per document (`okf_ext/writing.py`):
        # a partial failure must not leave `adopted` claiming a stamp that is
        # not actually on disk, so only the members that landed are kept, and
        # every failure is surfaced through the same channel every other
        # walked-past page is -- `SkippedPage`, not a silent drop.
        write_result = write_all(list(phase1.adoptions))
        landed = set(write_result.written)
        adopted = tuple(member for member in phase1.adopted if member in landed)
        skipped = phase1.skipped + tuple(
            SkippedPage(page=failure.path, reason="adoption-write-failed") for failure in write_result.failed
        )

    tasks = list(phase1.tasks)
    truncated = 0
    if max_entities is not None and len(tasks) > max_entities:
        truncated = len(tasks) - max_entities
        tasks = tasks[:max_entities]

    head = (head_commit(config.repos[0].path) or "") if config.repos else ""
    worklist = ScanWorklist(
        head_commit=head,
        short_head=head[:_ABBREVIATED],
        prose_tasks=tuple(tasks),
        truncated=truncated,
        skipped=skipped,
        adopted=adopted,
    )
    return worklist, summary


def _noop() -> None:
    """`PendingWrite.on_written` for a writer that reloads the bundle instead.

    Phase 3 re-reads the bundle after `write_all` for index reconciliation
    anyway, so there is no in-memory state to keep in step.
    """


def splice_sections(body: str, sections: Mapping[str, str]) -> tuple[str, int]:
    """*body* with each named section's lines replaced. Returns `(body, filled)`.

    Sections are located and replaced one at a time against the running body:
    every replace shifts the line numbers under it, so a span computed against
    the original would write into the wrong place from the second edit onward.

    The level comes from the key, which already carries it (`heading_key` emits
    `"## Purpose"`). `find_section` is level-agnostic by default, and this was
    the one reader using that default -- `_section_body` and `_still_placeholder`
    both match by level. A `level: 3` prose section would have had phase 1 read
    one section and phase 3 write a level-2 namesake. **Keys must carry their
    `#` prefix**, the form `heading_key` emits (e.g. `"## Purpose"`); a bare
    key with no leading `#` silently falls back to level-agnostic matching
    instead of raising, so a caller passing plain `"Purpose"` gets exactly the
    bug this note exists to prevent.

    `okf_ext.generators.plan_regenerate` is deliberately not the writer here. It
    only writes what a declaration *grants*, and a prose section is by definition
    what the declaration does not grant.
    """
    newline = dominant_newline(body)
    current = body
    filled = 0
    for key in sorted(sections):
        heading = key.lstrip("#").strip()
        level = len(key) - len(key.lstrip("#"))
        section = find_section(current, heading, level=level or None)
        if section is None:
            continue
        lines = split_lines(current)
        trailing = has_trailing_newline(lines)
        replaced = splice_replace(
            lines, section.body_start, section.stop, ["", *bare_lines(sections[key]), ""], newline
        )
        current = assemble(replaced, newline, trailing)
        filled += 1
    return current, filled


def _still_placeholder(body: str, specs: Sequence[SectionSpec]) -> bool:
    for spec in specs:
        section = find_section(body, spec.heading, level=spec.level)
        if section is not None and is_unfilled(section.slice(body), spec.placeholder):
            return True
    return False


def _index_directory(member: str) -> str:
    """The directory whose index lists *member*: its parent, uniformly."""
    return PurePosixPath(member).parent.as_posix()


@dataclass(frozen=True, slots=True)
class _Staged:
    member: str
    index_directory: str
    filled: int
    stamped: bool


def _log_line(applied: ApplyResult) -> str:
    return (
        f"**Scan** prose refresh — {applied.narrated} page(s) narrated, "
        f"{applied.sections_filled} section(s) filled, {applied.stamped} anchor(s) stamped"
    )


def apply_scan_results(
    worklist: ScanWorklist,
    results: ScanResults,
    bundle_root: Path,
    config: Config,
    *,
    today: date,
    dry_run: bool = True,
) -> ApplyResult:
    """Sanitize, splice, stamp, reconcile.

    `today` is a required keyword the design spec's signature omits: this
    appends a `log.md` entry, and nothing in this package reads the clock.

    Every content decision is isolated per entity -- an unusable answer, a
    missing page, an unparseable page each append to `entity_errors` while the
    rest of the run lands. The one exception is `okf_ext.writing.write_all`'s
    staging regime, which is all-or-nothing by design: if staging fails for any
    page, no live file is touched and every page is reported.

    `dry_run=True` is the default. Everything up to `write_all` runs --
    sanitizing, the splice, the refill gate, the staging list -- and then the
    write, the index reconciliation and the log append are skipped. The three
    counts describe what *would* have landed; `ApplyResult.dry_run` is what
    stops a caller reading them as what did.
    """
    section_set = load_sections(config.declarations_dir / SECTIONS_DIRNAME)
    bundle = load_bundle(bundle_root)
    by_uri = {task.uri: task for task in worklist.prose_tasks}

    pending: list[PendingWrite] = []
    staged: list[_Staged] = []
    errors: list[str] = []

    for result in results.prose:
        task = by_uri.get(result.uri)
        if task is None:
            errors.append(f"{result.uri}: no task for this uri in the worklist")
            continue
        if result.error:
            errors.append(f"{task.page_path}: {result.error}")
            continue
        clean = sanitize_prose_result(result.sections, allowed=tuple(task.prose_sections))
        if not clean:
            errors.append(f"{task.page_path}: no usable section survived sanitizing")
            continue
        document = bundle.concept(task.page_path.removesuffix(".md"))
        if document is None or document.parse_error is not None:
            errors.append(f"{task.page_path}: page is missing or did not parse")
            continue

        refreshed, filled = splice_sections(document.body, clean)
        if not filled:
            errors.append(f"{task.page_path}: no declared section could be located to splice")
            continue

        anchor = str(document.fm_raw.get("last_updated_commit") or "").strip()
        still_placeholder = _still_placeholder(refreshed, prose_specs(section_set, task.kind))
        stamp = bool(anchor) and not still_placeholder

        # A fresh parse, not `dataclasses.replace`: a shallow clone shares
        # `fm_raw` with the live bundle document, and a frontmatter edit through
        # it would mutate the bundle whether or not the write lands.
        scratch = parse_document(document.raw_text, path=document.path)
        scratch.set_body(refreshed)
        if stamp:
            scratch.set(PROSE_ANCHOR_KEY, anchor)
            # `Document.delete` is a no-op on an absent key, so the clear needs
            # no guard and the frontmatter of a page that never stalled stays
            # exactly as clean as it was.
            scratch.delete(PROSE_ATTEMPTS_KEY)
        elif still_placeholder and task.trigger == "first_fill":
            # The model declined a section it could not say something true
            # about. Count it: three declines and phase 1 stops dispatching
            # (design spec D1). A diff refresh that lands partially is a
            # different failure and is deliberately not counted.
            scratch.set(PROSE_ATTEMPTS_KEY, _attempts(document) + 1)

        pending.append(
            PendingWrite(
                member=task.page_path,
                path=bundle_root / task.page_path,
                rendered=scratch.serialize(),
                on_written=_noop,
            )
        )
        staged.append(
            _Staged(
                member=task.page_path,
                index_directory=_index_directory(task.page_path),
                filled=filled,
                stamped=stamp,
            )
        )

    if dry_run:
        return ApplyResult(
            narrated=len(staged),
            sections_filled=sum(item.filled for item in staged),
            stamped=sum(1 for item in staged if item.stamped),
            entity_errors=tuple(errors),
            dry_run=True,
        )

    write_result = write_all(pending)
    errors.extend(f"{failure.path}: {failure.kind}: {failure.error}" for failure in write_result.failed)
    written = set(write_result.written)
    landed = [item for item in staged if item.member in written]

    applied = ApplyResult(
        narrated=len(landed),
        sections_filled=sum(item.filled for item in landed),
        stamped=sum(1 for item in landed if item.stamped),
        entity_errors=tuple(errors),
    )
    if not landed:
        return applied

    reconciled = load_bundle(bundle_root)
    update_index(
        reconciled,
        directories=sorted({item.index_directory for item in landed}),
        create_missing=True,
        dry_run=False,
    )
    log_document = reconciled.logs.get("")
    if log_document is not None:
        append_log_entry(log_document, _log_line(applied), today=today, dry_run=False)
    return applied


#: Where `SubagentPool` writes its JSONL traces. Under the cache dir, which is
#: gitignored and scanner-excluded by the layout -- never a hard-coded
#: `.graph-wiki/`.
TRACES_DIRNAME = "traces"

#: The role the fan-out resolves. Its `max_concurrency` sizes the pool.
PROSE_REFRESHER_ROLE = "prose_refresher"


@dataclass(frozen=True, slots=True)
class StructuralSummary:
    """What the structural pass did, both lanes.

    A named pair rather than a three-tuple return from `build_scan_worklist`:
    both halves answer the same question -- "what did the mechanical pass
    do" -- and a pair survives the next lane without changing every caller's
    unpack.

    Runtime-only, and therefore here rather than in `scan_contract`: that
    module's contract is that nothing in it imports a package that reads a
    file, and both summaries come from `code_wiki_okf` modules that do.
    Neither is ever serialized -- `emit_scan_worklist` writes `ScanWorklist`
    alone -- so `SCHEMA_VERSION` is unaffected.
    """

    entities: SyncSummary = field(default_factory=SyncSummary)
    mirror: MirrorSummary = field(default_factory=MirrorSummary)

    @classmethod
    def from_sync_result(cls, result: SyncResult) -> StructuralSummary:
        """Adapt the domain composite without exposing it to scan callers."""
        return cls(entities=result.entities, mirror=result.mirror)

    @property
    def errors(self) -> tuple[str, ...]:
        """Every structural failure, in the shape `ScanResult.errors` takes.

        The entity lane's index refusals and the mirror lane's per-repo
        failures are both reported-not-raised, and both must reach a caller's
        exit code -- `gw scan` writing 755 pages and exiting 0 on a lane that
        failed is the defect class this whole epic closes.
        """
        return (
            tuple(f"entity sync incomplete: {error}" for error in self.entities.skipped)
            + tuple(f"{path}: {kind}" for path, kind in self.entities.catalog_declined)
            + tuple(f"{repo}: mirror sync failed: {error}" for repo, error in self.mirror.failed_repos)
        )


@dataclass(frozen=True, slots=True)
class ScanResult:
    """One whole run: the mechanical result, the worklist, and what landed.

    `structural` is both structural lanes' own summaries, so a `narrate=False`
    run still reports everything the mechanical pass did -- entity pages and
    mirror pages alike.
    """

    structural: StructuralSummary
    worklist: ScanWorklist
    applied: ApplyResult = field(default_factory=ApplyResult)
    errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors


async def run_prose_fan_out(
    tasks: Sequence[ProseRefreshTask],
    *,
    layout: WorkspaceLayout,
    model_override: str | None = None,
    graph_tools_for_refresh: Sequence[BaseTool] = (),
) -> ScanResults:
    """Phase 2: one tool loop per task, concurrency and isolation the pool's.

    The closure -- not `subagents_io.runner.run_all` -- is what makes this a
    *tool loop* per item: `run_all` does one `ainvoke` each, and `run_loop` is a
    tool loop but single-item.

    `binding.make_llm()` is called inside the closure, once per item. That is
    why `role_binding` carries a factory rather than a constructed model: one
    shared client across the whole fan-out is not this vertical's decision.
    """
    binding = role_binding(PROSE_REFRESHER_ROLE, layout=layout, model_override=model_override)
    pool = SubagentPool(trace_dir=layout.cache_dir / TRACES_DIRNAME)

    async def _refresh_one(task: ProseRefreshTask) -> ProseRefreshResult:
        return await run_prose_refresh(
            task,
            llm=binding.make_llm(),
            bundle_root=layout.bundle_dir,
            graph_tools=graph_tools_for_refresh,
        )

    fan_out = await pool.run_all(
        list(tasks),
        _refresh_one,
        PROSE_REFRESHER_ROLE,
        model_id=binding.spec.model_id,
        max_concurrency=binding.spec.max_concurrency,
    )
    return ScanResults(
        prose=tuple(value for _item, value in fan_out.successes),
        provider_errors=tuple(f"{failure.item.uri}: {failure.exception}" for failure in fan_out.errors),
    )


async def run_scan(
    layout: WorkspaceLayout,
    config: Config,
    *,
    today: date,
    at: datetime,
    narrate: bool = True,
    max_entities: int | None = None,
    model_override: str | None = None,
    graph_tools_for_refresh: Sequence[BaseTool] = (),
    dry_run: bool = True,
) -> ScanResult:
    """Phase 1 -> phase 2 -> phase 3.

    `narrate=False` stops after phase 1 -- a structural-only scan, needing no
    model and no credentials. A worklist with no tasks stops there too: there is
    nothing to construct a model for.

    `dry_run=True` -- the default -- stops after phase 1 as well, but for a
    different reason: it also skips the *structural* pass, so nothing at all is
    written. `narrate=False` still commits the structural pass; `dry_run=True`
    does not. See `build_scan_worklist` for what the previewed worklist can and
    cannot tell you.

    `graph_tools_for_refresh` is the same seam `run_ingest_source` uses: C7's
    reader-level callables are bound into langchain tools by the query vertical,
    and the CLI wires the builder in. Absent, the refresher runs on its file
    tools alone -- a narrower grounding, not a failure.
    """
    worklist, structural = await build_scan_worklist(
        layout, config, today=today, at=at, max_entities=max_entities, dry_run=dry_run
    )
    # Both lanes' reported-not-raised failures, so a mirror repo that blew up
    # mid-write exits non-zero exactly as an index refusal already does.
    result = ScanResult(structural=structural, worklist=worklist, errors=structural.errors)
    if dry_run or not narrate or not worklist.prose_tasks:
        return result

    results = await run_prose_fan_out(
        worklist.prose_tasks,
        layout=layout,
        model_override=model_override,
        graph_tools_for_refresh=graph_tools_for_refresh,
    )
    applied = apply_scan_results(worklist, results, layout.bundle_dir, config, today=today, dry_run=False)
    return replace(result, applied=applied, errors=result.errors + results.provider_errors + applied.entity_errors)


#: The worklist artifact's filename inside the scan cache directory.
WORKLIST_FILENAME = "worklist.json"

#: Where the rendered per-task briefs go, relative to that directory.
BRIEFS_DIRNAME = "briefs"

#: Where per-task result files go, relative to the scan cache directory.
RESULTS_DIRNAME = "results"

_SLUG_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def scan_cache_dir(layout: WorkspaceLayout) -> Path:
    """Where this vertical's artifacts live: `<cache>/scan`.

    Derived from the layout, never a hard-coded `.graph-wiki/` -- the cache dir
    is gitignored and scanner-excluded by the layout itself, and an override
    moves the artifacts with it.
    """
    return layout.cache_dir / "scan"


def scan_results_dir(layout: WorkspaceLayout) -> Path:
    """Where this vertical's per-task result artifacts live."""
    return scan_cache_dir(layout) / RESULTS_DIRNAME


def brief_slug(uri: str) -> str:
    """A filesystem-safe stem for one task's brief."""
    return _SLUG_UNSAFE.sub("-", uri).strip("-") or "entity"


def emit_scan_worklist(worklist: ScanWorklist, *, out_dir: Path) -> tuple[str, ...]:
    """Write `worklist.json` plus one rendered brief per task.

    Returns every path written, in write order. The brief is the same human
    message the in-process path sends, so an out-of-process agent and the pool
    are answering the same question.

    **The briefs directory is pruned first.** `worklist.json` is authoritative
    either way, but an out-of-process agent globbing `briefs/*.md` would pick up
    the previous run's dead work beside this run's. Only `*.md` directly inside
    `briefs/` is removed: a `results/` directory beside it, and anything else a
    caller keeps in the cache dir, is not this function's to delete.

    `brief_slug` strips characters a URI scheme uses (`:`, `/`) to punctuation
    a filename can hold, so two distinct URIs can sanitize to the same stem
    (e.g. two scoped-vs-plain npm dependency names). A second task landing on
    an already-used slug within this call is disambiguated with a short
    digest of its own URI rather than silently overwriting the first task's
    brief -- `worklist.json` stays the authoritative task list either way, but
    a brief silently lost would go unnoticed by whatever reads the directory.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    briefs = out_dir / BRIEFS_DIRNAME
    if briefs.is_symlink():
        briefs.unlink()
    briefs.mkdir(parents=True, exist_ok=True)
    for stale in briefs.glob("*.md"):
        # `missing_ok`: `glob` enumerating a path and this call deleting it are
        # two separate syscalls, and this directory is a multi-consumer surface
        # by design -- a concurrent run, or an out-of-process agent cleaning up
        # the brief it just consumed, must not crash the emit.
        stale.unlink(missing_ok=True)

    worklist_path = out_dir / WORKLIST_FILENAME
    if worklist_path.is_symlink():
        worklist_path.unlink()
    worklist_path.write_text(
        json.dumps(worklist_payload(worklist), indent=2, sort_keys=True),
        encoding="utf-8",
        newline="\n",
    )
    written = [str(worklist_path)]
    seen: dict[str, int] = {}
    for task in worklist.prose_tasks:
        slug = brief_slug(task.uri)
        seen[slug] = seen.get(slug, 0) + 1
        if seen[slug] > 1:
            slug = f"{slug}-{hashlib.sha1(task.uri.encode('utf-8')).hexdigest()[:8]}"
        brief = briefs / f"{slug}.md"
        brief.write_text(build_prose_refresh_prompt(task), encoding="utf-8", newline="")
        written.append(str(brief))
    return tuple(written)


def load_worklist(path: Path) -> ScanWorklist:
    """Read a worklist artifact. Raises `UnsupportedWorklistSchema` on a bad version."""
    return worklist_from_payload(json.loads(path.read_text(encoding="utf-8")))


def load_results_dir(directory: Path) -> ScanResults:
    """Every `*.json` in *directory*, read into one `ScanResults`.

    Sorted by filename and **last wins** for a repeated uri -- which is how a
    directory of results takes precedence over an earlier one for the same
    entity. A file that is not readable as a result is named in
    `provider_errors` rather than raised: a directory an external agent filled
    is input, and one bad file must not lose the other twenty.

    A missing directory is empty, not an error: phase 2 may simply not have run.
    """
    if not directory.is_dir():
        return ScanResults()
    by_uri: dict[str, ProseRefreshResult] = {}
    errors: list[str] = []
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            errors.append(f"{path.name}: unreadable result file: {exc}")
            continue
        if not isinstance(payload, dict) or "uri" not in payload:
            errors.append(f"{path.name}: result file carries no uri")
            continue
        result = result_from_payload(payload)
        by_uri[result.uri] = result
    return ScanResults(prose=tuple(by_uri.values()), provider_errors=tuple(errors))


def apply_scan_worklist(
    *,
    worklist_path: Path,
    worklist: ScanWorklist | None = None,
    results_dir: Path,
    bundle_root: Path,
    config: Config,
    today: date,
    dry_run: bool = True,
) -> ApplyResult:
    """Load a worklist and a results directory, then apply them.

    The out-of-process counterpart to `run_scan`'s third phase, composing the
    two loaders over the one `apply_scan_results` both paths share -- which is
    what makes the file surface get exactly the same sanitizing, splicing and
    refill gate the in-process path gets, `dry_run` included.

    A caller that already loaded and validated *worklist* can pass that exact
    object to prevent a second read of *worklist_path*. Existing callers omit
    it and retain the path-loading behavior.
    """
    resolved_worklist = load_worklist(worklist_path) if worklist is None else worklist
    results = load_results_dir(results_dir)
    applied = apply_scan_results(resolved_worklist, results, bundle_root, config, today=today, dry_run=dry_run)
    if not results.provider_errors:
        return applied
    return replace(applied, entity_errors=results.provider_errors + applied.entity_errors)


__all__ = [
    "BRIEFS_DIRNAME",
    "LANE_TYPES",
    "MAX_PROSE_ATTEMPTS",
    "MAX_TASK_DIFF_FILES",
    "MAX_TASK_GRAPH_CHARS",
    "MAX_TASK_PAGE_CHARS",
    "PROSE_ANCHOR_KEY",
    "PROSE_ATTEMPTS_KEY",
    "PROSE_REFRESHER_ROLE",
    "RESULTS_DIRNAME",
    "TRACES_DIRNAME",
    "WORKLIST_FILENAME",
    "ScanResult",
    "StructuralSummary",
    "apply_scan_results",
    "apply_scan_worklist",
    "brief_slug",
    "build_scan_worklist",
    "emit_scan_worklist",
    "entity_refs",
    "heading_key",
    "is_unfilled",
    "load_results_dir",
    "load_worklist",
    "prose_specs",
    "run_prose_fan_out",
    "run_scan",
    "scan_cache_dir",
    "scan_results_dir",
    "splice_sections",
]
