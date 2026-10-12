"""The scan pipeline's wire contract: what a worklist and a result *are*.

Separate from `commands.scan` because these five types cross a process
boundary. `emit_scan_worklist` writes them; an out-of-process agent reads them;
`apply_scan_worklist` reads results back. Nothing here imports a package that
reads a file, so a consumer can depend on the shape without depending on the
pipeline.

`SCHEMA_VERSION` is 5. The number is monotonic rather than reset to 1 so
`unsupported worklist schema: 3` reads unambiguously as *older than we
support* rather than *newer than we understand*. Version 4 added
`ScanWorklist.skipped` and `ScanWorklist.adopted`: phase 1's diagnostics are
part of the wire contract because an out-of-process agent reading
`worklist.json` cannot re-derive why a page is missing from a list it did not
build (design spec D3). A cached v3 artifact is refused rather than read
short, which is the behaviour `UnsupportedWorklistSchema` exists for.
Version 5 added `ProseRefreshTask.word_limits`: a v4 reader would drop the
caps silently and let an over-cap answer through, so a cached v4 artifact is
refused too.

`ScanResults` here is the batch of per-entity results; `ScanResult` in
`commands.scan` is one whole run's outcome. Both names come from the contract
this ports; the plural is the wire type.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

#: The worklist artifact's version. Bump on any breaking field change.
SCHEMA_VERSION = 5

#: Why a page is in the worklist. `first_fill` wins when both apply: an unfilled
#: section needs writing whether or not the code under it also moved.
TRIGGERS: tuple[str, ...] = ("first_fill", "diff")

_NO_SECTIONS: Mapping[str, str] = MappingProxyType({})
_NO_LIMITS: Mapping[str, int] = MappingProxyType({})

#: Why a page phase 1 walked past is not in the worklist. A closed vocabulary:
#: the first four are read failures, `attempts-exhausted` is the bound design
#: spec D1 puts on a page whose prose the model keeps declining, and
#: `adoption-write-failed` is the one entry that is not a classification
#: outcome at all -- it means phase 1 *decided* to adopt the page but the
#: write that would have stamped its anchor did not land, so the page is
#: reported here instead of silently claimed in `ScanWorklist.adopted`.
SKIP_REASONS: tuple[str, ...] = (
    "parse-error",
    "unknown-type",
    "unresolved-resource",
    "type-mismatch",
    "attempts-exhausted",
    "adoption-write-failed",
)


@dataclass(frozen=True, slots=True)
class SkippedPage:
    """One page phase 1 walked past, and why.

    On the wire deliberately (design spec D3): an out-of-process agent holding
    only `worklist.json` cannot tell a page that drifted out of the graph from
    one that is simply up to date, because it did not build the list.
    """

    page: str
    reason: str


class UnsupportedWorklistSchema(ValueError):
    """A worklist artifact this build cannot read.

    Configuration-shaped, so it raises: a caller handed us a file, and reading
    it wrong is worse than refusing it.
    """


@dataclass(frozen=True, slots=True)
class ProseRefreshTask:
    """One page's prose refresh, self-contained enough to run out of process.

    `entity_root` is an **absolute** filesystem path -- the directory the
    refresher's file tools are contained to. Phase 1 keeps the repo-relative
    form for its own diff scope and never puts it here: an out-of-process agent
    has no repo table to resolve it against.

    `prose_sections` is keyed by the section's full heading (`"## Purpose"`) and
    valued by its body *as it stands now*. The key set is also the allow-list
    phase 3 sanitizes against.

    `diff` is `None` for the rewritten-history case: the anchor SHA is unknown
    to the repo, so the range cannot be computed and the refresher is told to
    re-read the entity instead.

    `owning_short_head` is the owning repository's HEAD, abbreviated. It is
    prompt and log material only -- the refill gate stamps the page's own
    `last_updated_commit`, which entity sync set to that same repo's HEAD
    earlier in this run.

    `word_limits` maps a subset of `prose_sections`' keys to the declaration's
    `max_words` for that heading's agent section. The prompt states each limit
    and the sanitizer drops a body over it.
    """

    uri: str
    kind: str
    name: str
    page_path: str
    entity_root: str
    trigger: str
    diff: str | None = None
    changed_files: tuple[str, ...] = ()
    page_content: str = ""
    prose_sections: Mapping[str, str] = field(default=_NO_SECTIONS)
    graph_context: str = ""
    owning_short_head: str | None = None
    word_limits: Mapping[str, int] = field(default=_NO_LIMITS)


@dataclass(frozen=True, slots=True)
class ScanWorklist:
    """Everything phase 2 needs, and nothing phase 1 could not compute.

    `head_commit` is the first configured repository's HEAD -- the artifact's
    own provenance, never a stamp source. `truncated` is how many tasks
    `max_entities` dropped from the tail, so a bounded first run reports what it
    did not do.

    `skipped` names the pages phase 1 walked past and why; `adopted` names the
    pages whose hand-written prose it anchored at `last_updated_commit` with no
    model call (design spec D5). Both are diagnostics, not work: neither
    contributes a task.
    """

    schema_version: int = SCHEMA_VERSION
    head_commit: str = ""
    short_head: str = ""
    prose_tasks: tuple[ProseRefreshTask, ...] = ()
    truncated: int = 0
    skipped: tuple[SkippedPage, ...] = ()
    adopted: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProseRefreshResult:
    """One entity's answer. `error` non-`None` means nothing lands for it."""

    uri: str
    sections: Mapping[str, str] = field(default=_NO_SECTIONS)
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ScanResults:
    """Every answer this run collected.

    `provider_errors` is runtime-only and never serialized: it records fan-out
    failures the pool isolated, which a results *directory* cannot carry
    because the item that failed produced no file.
    """

    prose: tuple[ProseRefreshResult, ...] = ()
    provider_errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ApplyResult:
    """What phase 3 landed. Named for the counts a caller reports.

    `dry_run` is runtime-only -- `ApplyResult` has never been on the wire. It
    is here so a caller cannot misread previewed counts as landed ones: under
    a preview the three counts describe what *would* have been written.
    """

    narrated: int = 0
    sections_filled: int = 0
    stamped: int = 0
    entity_errors: tuple[str, ...] = ()
    dry_run: bool = False

    @property
    def ok(self) -> bool:
        return not self.entity_errors


def task_payload(task: ProseRefreshTask) -> dict[str, Any]:
    """One task as plain data. `json.dumps`-able with no encoder."""
    return {
        "uri": task.uri,
        "kind": task.kind,
        "name": task.name,
        "page_path": task.page_path,
        "entity_root": task.entity_root,
        "trigger": task.trigger,
        "diff": task.diff,
        "changed_files": list(task.changed_files),
        "page_content": task.page_content,
        "prose_sections": dict(task.prose_sections),
        "graph_context": task.graph_context,
        "owning_short_head": task.owning_short_head,
        "word_limits": dict(task.word_limits),
    }


def task_from_payload(payload: Mapping[str, Any]) -> ProseRefreshTask:
    diff = payload.get("diff")
    head = payload.get("owning_short_head")
    return ProseRefreshTask(
        uri=str(payload["uri"]),
        kind=str(payload["kind"]),
        name=str(payload["name"]),
        page_path=str(payload["page_path"]),
        entity_root=str(payload["entity_root"]),
        trigger=str(payload["trigger"]),
        diff=None if diff is None else str(diff),
        changed_files=tuple(str(item) for item in payload.get("changed_files", ())),
        page_content=str(payload.get("page_content", "")),
        prose_sections=MappingProxyType(
            {str(key): str(value) for key, value in dict(payload.get("prose_sections", {})).items()}
        ),
        graph_context=str(payload.get("graph_context", "")),
        owning_short_head=None if head is None else str(head),
        word_limits=MappingProxyType(
            {str(key): int(value) for key, value in dict(payload.get("word_limits", {})).items()}
        ),
    )


def worklist_payload(worklist: ScanWorklist) -> dict[str, Any]:
    return {
        "schema_version": worklist.schema_version,
        "head_commit": worklist.head_commit,
        "short_head": worklist.short_head,
        "truncated": worklist.truncated,
        "prose_tasks": [task_payload(task) for task in worklist.prose_tasks],
        "skipped": [{"page": item.page, "reason": item.reason} for item in worklist.skipped],
        "adopted": list(worklist.adopted),
    }


def worklist_from_payload(payload: Mapping[str, Any]) -> ScanWorklist:
    """Read a worklist artifact back.

    Raises `UnsupportedWorklistSchema` for any version but `SCHEMA_VERSION`.
    """
    version = int(payload.get("schema_version", 0))
    if version != SCHEMA_VERSION:
        raise UnsupportedWorklistSchema(f"unsupported worklist schema: {version} (this build reads {SCHEMA_VERSION})")
    return ScanWorklist(
        schema_version=version,
        head_commit=str(payload.get("head_commit", "")),
        short_head=str(payload.get("short_head", "")),
        prose_tasks=tuple(task_from_payload(item) for item in payload.get("prose_tasks", ())),
        truncated=int(payload.get("truncated", 0)),
        skipped=tuple(
            SkippedPage(page=str(item["page"]), reason=str(item["reason"])) for item in payload.get("skipped", ())
        ),
        adopted=tuple(str(item) for item in payload.get("adopted", ())),
    )


def result_payload(result: ProseRefreshResult) -> dict[str, Any]:
    return {"uri": result.uri, "sections": dict(result.sections), "error": result.error}


def result_from_payload(payload: Mapping[str, Any]) -> ProseRefreshResult:
    error = payload.get("error")
    return ProseRefreshResult(
        uri=str(payload["uri"]),
        sections=MappingProxyType({str(key): str(value) for key, value in dict(payload.get("sections", {})).items()}),
        error=None if error is None else str(error),
    )


__all__ = [
    "SCHEMA_VERSION",
    "SKIP_REASONS",
    "TRIGGERS",
    "ApplyResult",
    "ProseRefreshResult",
    "ProseRefreshTask",
    "ScanResults",
    "ScanWorklist",
    "SkippedPage",
    "UnsupportedWorklistSchema",
    "result_from_payload",
    "result_payload",
    "task_from_payload",
    "task_payload",
    "worklist_from_payload",
    "worklist_payload",
]
