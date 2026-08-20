"""What `gw util log` and `gw util tokens` call.

Both are compositions with nowhere else to live: `okf_ext.logs.append_entry`
knows nothing about an op vocabulary, and `code_graph_io.tokens.count_tokens`
knows nothing about a bundle. The CLI is a thin Typer body over these two, so
what is here is behaviour, not presentation -- rejecting an unknown op is this
module's job, and how the refusal reads on a terminal is not.

Nothing here reads the clock: `today` is an argument, matching okf-io's own
rule the whole band inherits.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from code_graph_io.tokens import count_tokens
from code_wiki_okf.provenance import tokens_value
from okf_ext.logs import append_entry
from okf_io import Document, load_bundle

from graph_works_core.workspace.layout import WorkspaceLayout

#: The legacy op vocabulary, carried forward unchanged. A verb outside it is a
#: caller error, not a content one, so it raises rather than being reported.
VALID_OPS = frozenset({"create", "delete", "ingest", "lint", "note", "query", "scan", "update"})

#: The frontmatter key `run_tokens_update` stamps, and the one it deletes from
#: the baseline before counting.
TOKENS_KEY = "tokens"

#: Why a page was not stamped. Data, not a `[warn]` line on stderr: the CLI
#: decides how to phrase these, and `--json` carries all of them.
SkipReason = Literal["no-frontmatter", "parse-error", "unreadable", "count-failed"]


@dataclass(frozen=True, slots=True)
class LogAppendResult:
    """What one `gw util log` did. `written=False` is a refusal, not a fault."""

    path: Path
    day: date
    op: str
    title: str
    detail: str | None
    entry: str
    written: bool


@dataclass(frozen=True, slots=True)
class TokenStamp:
    page: str
    tokens: int


@dataclass(frozen=True, slots=True)
class SkippedPage:
    page: str
    reason: SkipReason


@dataclass(frozen=True, slots=True)
class TokensUpdate:
    """Three buckets, each sorted by page so the output is byte-stable."""

    updated: tuple[TokenStamp, ...]
    unchanged: tuple[TokenStamp, ...]
    skipped: tuple[SkippedPage, ...]
    dry_run: bool


def _entry_text(op: str, title: str, detail: str | None) -> str:
    """One §9 bullet: `**<op>** <title> — <detail>`.

    The em-dash tail is omitted entirely -- not left dangling -- when there is
    no detail to carry, which is the common case.
    """
    tail = f" — {detail.strip()}" if detail and detail.strip() else ""
    return f"**{op}** {title.strip()}{tail}"


def run_log(
    layout: WorkspaceLayout,
    op: str,
    title: str,
    detail: str | None = None,
    *,
    today: date,
) -> LogAppendResult:
    """Append one entry to the bundle's root `log.md`.

    Raises `ValueError` for an op outside `VALID_OPS`, before touching disk:
    the CLI turns that into a non-zero exit. Every other refusal -- no
    `log.md`, an unparseable one, an out-of-order one -- comes back as
    `written=False`, because `okf_ext.logs.append_entry` reports refusals by
    returning nothing and laundering that into an exception here would lose
    the distinction between "the log said no" and "you named a verb that does
    not exist".

    `today` is a required keyword. Legacy read the clock inside its own entry
    formatter; okf-io's never-read-the-clock rule propagates up the band
    instead of being laundered away one level above it. The CLI supplies the
    date, so this is invisible to the user.
    """
    if op not in VALID_OPS:
        raise ValueError(f"unknown op {op!r}. Valid: {sorted(VALID_OPS)}")
    entry = _entry_text(op, title, detail)
    landed = append_entry(layout.bundle_dir, entry, on=today)
    return LogAppendResult(
        path=layout.bundle_dir / "log.md",
        day=today,
        op=op,
        title=title,
        detail=detail,
        entry=entry,
        written=landed is not None,
    )


def _baseline(page: Document) -> str:
    """The page's text with any `tokens` key removed.

    A *separate* `Document` from the one that gets stamped, deliberately.
    Doing both on one object would make `delete`-then-`set` reinsert the key
    at `PREFERRED_KEY_ORDER`'s position, silently relocating `tokens` in every
    page that already carried it mid-frontmatter. Two objects means the write
    is a splice of one key and the count is of a file that never had it --
    which is what keeps the count stable across runs (D-041).
    """
    baseline = Document.parse(page.raw_text)
    baseline.delete(TOKENS_KEY)
    return baseline.serialize()


def run_tokens_update(layout: WorkspaceLayout, *, dry_run: bool = True) -> TokensUpdate:
    """Stamp `tokens: <count>` on every concept in the workspace bundle.

    One `load_bundle` walk. `index.md`, `log.md`, assets and dot-directories
    are excluded by the loader's own classification rather than by a skip list
    of this module's own (D-040), and `work/` needs no second walk because in
    this layout it sits inside the bundle.

    Skips are **data**: a page that carries no frontmatter, one that will not
    parse, a member the loader could not read, and one whose count raised each
    come back in `skipped` with a reason. Nothing here raises for content.

    `dry_run` defaults to `True`, matching every other writer in this stack.
    """
    bundle = load_bundle(layout.bundle_dir)
    updated: list[TokenStamp] = []
    unchanged: list[TokenStamp] = []
    skipped: list[SkippedPage] = [SkippedPage(page=member, reason="unreadable") for member in bundle.unreadable]

    for page_id, page in bundle.concepts.items():
        if page.parse_error is not None:
            skipped.append(SkippedPage(page=page_id, reason="parse-error"))
            continue
        if not page.has_frontmatter:
            skipped.append(SkippedPage(page=page_id, reason="no-frontmatter"))
            continue
        try:
            count = tokens_value(count_tokens(_baseline(page)))
        except Exception:
            # One bad page must not abort the vault. Broad on purpose: the
            # counter is an injected third-party encoder, and its failure
            # modes are not this module's to enumerate. `tokens_value`'s own
            # `>= 0` guard -- this key's sole invariant -- lands here too, now
            # that this is the key's one production caller (D-041; see
            # 2026-08-19-tech-debt-tokens-metric-proxy-string).
            skipped.append(SkippedPage(page=page_id, reason="count-failed"))
            continue
        if page.fm_data().get(TOKENS_KEY) == count:
            unchanged.append(TokenStamp(page=page_id, tokens=count))
            continue
        page.set(TOKENS_KEY, count)
        if not dry_run:
            page.save()
        updated.append(TokenStamp(page=page_id, tokens=count))

    return TokensUpdate(
        updated=tuple(sorted(updated, key=lambda stamp: stamp.page)),
        unchanged=tuple(sorted(unchanged, key=lambda stamp: stamp.page)),
        skipped=tuple(sorted(skipped, key=lambda skip: skip.page)),
        dry_run=dry_run,
    )
