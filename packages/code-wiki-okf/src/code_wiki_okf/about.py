"""The curated-page claims contract: `about:` resolves to scanner pages.

`okf_ext.schemas.declared_about` says which types carry the mandate (the
`x-okf-about` annotation doc-wiki-okf's seeds declare); this rule checks it.
The schemas describe the shape of `about:`/`decisions:`/`claims:`; whether a
key is present, and whether each URI names a page the scanner wrote, is this
rule's question -- at its own severity, so the warn-to-error flip moves this
rule alone (epic decision 010).

A URI resolves only to a page whose `type` is a code-wiki type: a Source or
curated page carrying a colliding `resource:` never satisfies it. Resolution
reads the loaded bundle and nothing else -- no graph reader.

Content never raises. A value of the wrong shape is skipped: `schema_rule`
already reports it, and guessing at it here would double-report.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from okf_ext.schemas import AboutMandate
from okf_io import Document, Finding, Rule, RuleContext, Severity

from code_wiki_okf.placement import is_code_wiki_type
from code_wiki_okf.resources import ResourceIndex, resource_index

_MISSING = "about.missing"
_UNRESOLVED = "about.unresolved"
_AMBIGUOUS = "about.ambiguous"
_CLAIMS_MISSING = "claims.missing"
_ENTRY_UNRESOLVED = "claims.about-unresolved"
_ENTRY_AMBIGUOUS = "claims.about-ambiguous"
_CONSTRAINS_MISSING = "claims.constrains-missing"
_DUPLICATE_ID = "claims.duplicate-id"

CODES = (
    _MISSING,
    _UNRESOLVED,
    _AMBIGUOUS,
    _CLAIMS_MISSING,
    _ENTRY_UNRESOLVED,
    _ENTRY_AMBIGUOUS,
    _CONSTRAINS_MISSING,
    _DUPLICATE_ID,
)

#: The two entry lists the seeds declare. Per-entry checks read both on every
#: mandated type; the schema already forbids the wrong one on each type.
_ENTRY_KEYS = ("decisions", "claims")

#: D-002: every other status -- and no status at all -- is live.
_NOT_LIVE = frozenset({"draft", "deprecated", "superseded"})

_SPEC = "epic-agent-facing-wiki-content design §3.2"


def _is_live(document: Document) -> bool:
    return (document.fm.status or "").strip() not in _NOT_LIVE


def _resolution(index: ResourceIndex, uri: str) -> tuple[str, ...] | None:
    """`None` when *uri* resolves; otherwise the claiming members (empty = none)."""
    members = index.members_by_resource.get(uri, ())
    if len(members) != 1:
        return members
    # A sole claimant is always in `by_resource`; only collisions are kept out of it.
    entry = index.by_resource[uri]
    return None if is_code_wiki_type((entry.document.fm.type or "").strip()) else ()


def _unsafe(target: str) -> bool:
    return not target or target.startswith("/") or ".." in PurePosixPath(target).parts


def _exists_under(root: Path, target: str) -> bool:
    """Whether *target* exists under *root*, without letting content raise.

    A `constrains` value is free text; a component past the OS name limit (or
    otherwise unrepresentable) makes `Path.exists()` itself raise `OSError`
    rather than return `False` (verified on Python 3.12: unlike ENOENT/ENOTDIR/
    EBADF/ELOOP, that errno is not one `exists()` swallows). Content must never
    crash the rule, so any such error is treated the same as "does not exist" --
    it is certainly not a real path under this root.
    """
    try:
        return (root / target).exists()
    except OSError:
        return False


class _Page:
    """One mandated page's findings, sharing path, severity and line lookup."""

    def __init__(self, document: Document, path: str, severity: Severity, index: ResourceIndex) -> None:
        self.document = document
        self.path = path
        self.severity = severity
        self.index = index

    def finding(self, code: str, message: str, *keys: str | int) -> Finding:
        return Finding(
            code=code,
            severity=self.severity,
            message=message,
            spec=_SPEC,
            path=self.path,
            line=self.document.frontmatter_line(*keys),
        )

    def uris(self, value: Any, *keys: str | int, codes: tuple[str, str], label: str) -> Iterator[Finding]:  # noqa: ANN401
        if not isinstance(value, list):
            return
        unresolved, ambiguous = codes
        for position, uri in enumerate(value):
            if not isinstance(uri, str):
                continue
            members = _resolution(self.index, uri)
            if members is None:
                continue
            if len(members) > 1:
                yield self.finding(
                    ambiguous,
                    f"{label} URI `{uri}` is claimed by {len(members)} members: {', '.join(sorted(members))}.",
                    *keys,
                    position,
                )
            else:
                yield self.finding(unresolved, f"{label} URI `{uri}` names no code-wiki page.", *keys, position)


def _check(page: _Page, mandate: AboutMandate, repo_roots: tuple[Path, ...]) -> Iterator[Finding]:
    data = page.document.fm_data(dates="iso")
    type_name = (page.document.fm.type or "").strip()

    about = data.get("about")
    if about is None or about == []:
        yield page.finding(_MISSING, f"`{type_name}` pages must name what they are about in `about:`.", "about")
    else:
        yield from page.uris(about, "about", codes=(_UNRESOLVED, _AMBIGUOUS), label="`about:`")

    for key in _ENTRY_KEYS:
        entries = data.get(key)
        if key == mandate.entries and _is_live(page.document) and (entries is None or entries == []):
            yield page.finding(_CLAIMS_MISSING, f"A live `{type_name}` page carries no `{key}:` entry.", key)
        if not isinstance(entries, list):
            continue
        first_position: dict[str, int] = {}
        for position, entry in enumerate(entries):
            if not isinstance(entry, Mapping):
                continue
            entry_id = entry.get("id")
            label = f"`{key}[{position}]`" + (f" (`{entry_id}`)" if isinstance(entry_id, str) else "")
            if isinstance(entry_id, str):
                if entry_id in first_position:
                    yield page.finding(
                        _DUPLICATE_ID,
                        f"{label} repeats the id of `{key}[{first_position[entry_id]}]`.",
                        key,
                        position,
                        "id",
                    )
                else:
                    first_position[entry_id] = position
            yield from page.uris(
                entry.get("about"), key, position, "about", codes=(_ENTRY_UNRESOLVED, _ENTRY_AMBIGUOUS), label=label
            )
            constrains = entry.get("constrains")
            if not repo_roots or not isinstance(constrains, list):
                continue
            for target_position, target in enumerate(constrains):
                if not isinstance(target, str) or _unsafe(target):
                    continue
                if not any(_exists_under(root, target) for root in repo_roots):
                    yield page.finding(
                        _CONSTRAINS_MISSING,
                        f"{label} constrains `{target}`, which exists under no repository root.",
                        key,
                        position,
                        "constrains",
                        target_position,
                    )


def about_rule(
    mandates: Mapping[str, AboutMandate],
    *,
    repo_roots: tuple[Path, ...] = (),
    severity: Literal["error", "warning"] = "error",
) -> Rule:
    """Build the claims-contract rule over *mandates* (`declared_about`'s output).

    Honours `RuleContext.scope` for which pages are checked; the resource index
    it resolves against is always the whole bundle. An empty *repo_roots* skips
    `claims.constrains-missing`, matching `targets.affects-missing`.
    """
    severity_map: dict[Literal["error", "warning"], Severity] = {"error": "error", "warning": "warn"}
    try:
        finding_severity = severity_map[severity]
    except KeyError as exc:
        raise ValueError("severity must be 'error' or 'warning'") from exc
    frozen = dict(mandates)
    roots = tuple(repo_roots)

    def rule(context: RuleContext) -> Iterable[Finding]:
        index: ResourceIndex | None = None
        for concept_id in sorted(context.bundle.concepts):
            path = f"{concept_id}.md"
            if context.scope is not None and path not in context.scope:
                continue
            document = context.bundle.concepts[concept_id]
            if document.parse_error is not None:
                continue
            mandate = frozen.get((document.fm.type or "").strip())
            if mandate is None:
                continue
            if index is None:
                index = resource_index(context.bundle)
            yield from _check(_Page(document, path, finding_severity, index), mandate, roots)

    return rule


__all__ = ["CODES", "about_rule"]
