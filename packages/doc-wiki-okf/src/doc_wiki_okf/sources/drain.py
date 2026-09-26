"""The Source drain ledger: where each key claim landed, and the rule that checks it.

A Source is a staging area. Its `## Key claims` should end up as entries on
the curated pages that cite it, and `drain:` records, per top-level item,
where each one landed or why it was dropped. An ordinal with no entry is
pending. Nothing on a curated entry says which source produced it, so the
ledger is the only place "drained" can be computed from.

**No curated type or entry key is named here.** `entry_keys` is
`entry_keys_from(schema_set)`, which is read from `x-okf-about`. A ref on a
type with no `entries` mandate (Reference and HowTo carry optional
`claims:`) is looked up under every key any mandate declares.

Nothing here raises for content. A malformed ledger is a problem, a
malformed Source body is zero items, and both are findings.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from okf_ext.body import find_section, normalized_text, top_level_items
from okf_ext.schemas import SchemaSet, declared_about
from okf_io import Bundle, Document, Finding, Rule, RuleContext, Severity

from doc_wiki_okf.sources.plan import SOURCE_TYPE

KEY_CLAIMS_HEADING = "Key claims"
WHERE_CITED_HEADING = "Where it's cited in this wiki"
SOURCES_DIRECTORY = "sources/"
DROP_REASONS: tuple[str, ...] = ("history", "evidence", "duplicate", "superseded")

UNDRAINED = "sources.undrained"
INVALID = "sources.drain-invalid"
DANGLING = "sources.drain-dangling"
UNCITED = "sources.drain-uncited"
DRAIN_CODES: tuple[str, ...] = (UNDRAINED, INVALID, DANGLING, UNCITED)
_SPEC = "feature-source-drain-and-archive design §3.3"

ProblemKind = Literal["invalid", "dangling", "uncited"]
_CODE_FOR: dict[ProblemKind, str] = {"invalid": INVALID, "dangling": DANGLING, "uncited": UNCITED}


@dataclass(frozen=True, slots=True)
class DrainProblem:
    """One thing wrong with a ledger. *position* indexes `drain:`, or None for the whole key."""

    kind: ProblemKind
    message: str
    position: int | None


@dataclass(frozen=True, slots=True)
class DrainStatus:
    """One Source's ledger, measured against its own Key-claims count."""

    member: str
    items: int
    landed: frozenset[int]
    dropped: frozenset[int]
    pending: tuple[int, ...]
    cited_in_wiki: bool
    problems: tuple[DrainProblem, ...]

    @property
    def drained(self) -> bool:
        return self.items > 0 and not self.pending and not self.problems and self.cited_in_wiki

    @property
    def reason(self) -> str | None:
        """Why this Source is undrained, first cause first; None when drained or only problems block it."""
        if self.items == 0:
            return "no-key-claims"
        if self.pending:
            return "pending: " + ", ".join(str(n) for n in self.pending)
        if not self.cited_in_wiki:
            return "not cited in this wiki"
        return None


def entry_keys_from(schema_set: SchemaSet) -> dict[str, str]:
    """`{type: entries key}` for every type whose `x-okf-about` names an entry list."""
    return {
        name: mandate.entries for name, mandate in declared_about(schema_set).items() if mandate.entries is not None
    }


def is_source_member(bundle: Bundle, concept_id: str) -> bool:
    """A top-level `sources/<slug>` concept of type Source: not `_archive/`, not `references/`."""
    rest = concept_id[len(SOURCES_DIRECTORY) :] if concept_id.startswith(SOURCES_DIRECTORY) else None
    if not rest or "/" in rest:
        return False
    document = bundle.concepts.get(concept_id)
    return document is not None and (document.fm.type or "").strip() == SOURCE_TYPE


def _items(document: Document) -> int:
    section = find_section(document.body, KEY_CLAIMS_HEADING)
    return 0 if section is None else len(top_level_items(section.slice(document.body)))


def _cited_in_wiki(document: Document) -> bool:
    section = find_section(document.body, WHERE_CITED_HEADING)
    return section is not None and normalized_text(section.slice(document.body)) != ""


def _cites(page: Document, member: str) -> bool:
    sources = page.fm_data(dates="iso").get("sources")
    if not isinstance(sources, list):
        return False
    stem = member[:-3]
    for entry in sources:
        resource = entry.get("resource") if isinstance(entry, Mapping) else None
        if isinstance(resource, str):
            target = resource.split("#", 1)[0].lstrip("/")
            if target in (member, stem):
                return True
    return False


def _entry_ids(page: Document, keys: Iterable[str]) -> frozenset[str]:
    data = page.fm_data(dates="iso")
    ids: set[str] = set()
    for key in keys:
        entries = data.get(key)
        if isinstance(entries, list):
            ids.update(e["id"] for e in entries if isinstance(e, Mapping) and isinstance(e.get("id"), str))
    return frozenset(ids)


def _check_ref(
    bundle: Bundle, member: str, ref: object, entry_keys: Mapping[str, str], position: int
) -> DrainProblem | None:
    if not isinstance(ref, str) or not ref.startswith("/") or "#" not in ref:
        return DrainProblem("dangling", f"landed ref {ref!r} is not `/<page>.md#<entry id>`.", position)
    page_path, fragment = ref[1:].split("#", 1)
    page = bundle.concepts.get(page_path[:-3]) if page_path.endswith(".md") else None
    if page is None or not fragment:
        return DrainProblem("dangling", f"landed ref `{ref}` names no page.", position)
    page_type = (page.fm.type or "").strip()
    keys = (entry_keys[page_type],) if page_type in entry_keys else tuple(sorted(set(entry_keys.values())))
    if fragment not in _entry_ids(page, keys):
        message = f"landed ref `{ref}` names no entry `{fragment}` under {', '.join(keys)}."
        return DrainProblem("dangling", message, position)
    if not _cites(page, member):
        return DrainProblem("uncited", f"`{page_path}` has no `sources[]` entry for `/{member}`.", position)
    return None


def _claim(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else None


def _ledger(
    bundle: Bundle,
    member: str,
    raw: Any,  # noqa: ANN401
    items: int,
    entry_keys: Mapping[str, str],
) -> tuple[frozenset[int], frozenset[int], tuple[DrainProblem, ...]]:
    if raw is None:
        return frozenset(), frozenset(), ()
    if not isinstance(raw, list):
        return frozenset(), frozenset(), (DrainProblem("invalid", "`drain` is not a list.", None),)
    landed: set[int] = set()
    dropped: set[int] = set()
    seen: set[int] = set()
    problems: list[DrainProblem] = []
    for position, entry in enumerate(raw):
        if not isinstance(entry, Mapping):
            problems.append(DrainProblem("invalid", f"drain[{position}] is not a mapping.", position))
            continue
        claim = _claim(entry.get("claim"))
        if claim is None or claim > items:
            message = f"drain[{position}] claim {entry.get('claim')!r} is not in 1..{items}."
            problems.append(DrainProblem("invalid", message, position))
            continue
        if claim in seen:
            problems.append(DrainProblem("invalid", f"claim {claim} appears twice in `drain`.", position))
            continue
        seen.add(claim)
        has_landed, has_dropped = "landed" in entry, "dropped" in entry
        if has_landed == has_dropped:
            message = f"claim {claim} needs exactly one of `landed` / `dropped`."
            problems.append(DrainProblem("invalid", message, position))
            continue
        if has_dropped:
            if entry["dropped"] not in DROP_REASONS:
                message = f"claim {claim} dropped reason {entry['dropped']!r} is not one of {', '.join(DROP_REASONS)}."
                problems.append(DrainProblem("invalid", message, position))
                continue
            dropped.add(claim)
            continue
        refs = entry["landed"]
        if not isinstance(refs, list) or not refs:
            problems.append(DrainProblem("invalid", f"claim {claim} `landed` is not a non-empty list.", position))
            continue
        ref_problems = [p for ref in refs if (p := _check_ref(bundle, member, ref, entry_keys, position)) is not None]
        problems.extend(ref_problems)
        if not ref_problems:
            landed.add(claim)
    return frozenset(landed), frozenset(dropped), tuple(problems)


def drain_status(bundle: Bundle, member: str, entry_keys: Mapping[str, str]) -> DrainStatus:
    """*member* (`sources/<slug>.md`) measured against its ledger."""
    document = bundle.concepts[member[:-3]]
    items = _items(document) if document.parse_error is None else 0
    raw = document.fm_data(dates="iso").get("drain") if document.parse_error is None else None
    landed, dropped, problems = _ledger(bundle, member, raw, items, entry_keys)
    pending = tuple(n for n in range(1, items + 1) if n not in landed and n not in dropped)
    return DrainStatus(member, items, landed, dropped, pending, _cited_in_wiki(document), problems)


def drain_statuses(bundle: Bundle, entry_keys: Mapping[str, str]) -> tuple[DrainStatus, ...]:
    """Every top-level Source in *bundle*, sorted by member."""
    return tuple(
        drain_status(bundle, f"{cid}.md", entry_keys)
        for cid in sorted(bundle.concepts)
        if is_source_member(bundle, cid)
    )


def _findings(status: DrainStatus, document: Document, undrained: Severity) -> Iterator[Finding]:
    for problem in status.problems:
        keys: tuple[str | int, ...] = ("drain",) if problem.position is None else ("drain", problem.position)
        yield Finding(
            code=_CODE_FOR[problem.kind],
            severity="error",
            message=problem.message,
            spec=_SPEC,
            path=status.member,
            line=document.frontmatter_line(*keys),
        )
    if not status.drained:
        done = len(status.landed) + len(status.dropped)
        reason = status.reason or "ledger has problems"
        yield Finding(
            code=UNDRAINED,
            severity=undrained,
            message=f"{done}/{status.items} dispositioned; {reason}.",
            spec=_SPEC,
            path=status.member,
            line=document.frontmatter_line("drain"),
        )


def drain_rule(entry_keys: Mapping[str, str], *, severity_undrained: Literal["warning", "error"] = "warning") -> Rule:
    """The `sources.*` rule over every top-level Source. Honours `RuleContext.scope`."""
    severity_map: dict[str, Severity] = {"warning": "warn", "error": "error"}
    try:
        undrained = severity_map[severity_undrained]
    except KeyError as exc:
        raise ValueError("severity_undrained must be 'warning' or 'error'") from exc
    keys = dict(entry_keys)

    def rule(context: RuleContext) -> Iterable[Finding]:
        for concept_id in sorted(context.bundle.concepts):
            member = f"{concept_id}.md"
            if context.scope is not None and member not in context.scope:
                continue
            if not is_source_member(context.bundle, concept_id):
                continue
            document = context.bundle.concepts[concept_id]
            yield from _findings(drain_status(context.bundle, member, keys), document, undrained)

    return rule


def drop_only_ledger(
    value: object, *, items: int, reasons: Collection[str] = DROP_REASONS
) -> tuple[tuple[dict[str, object], ...] | None, str | None]:
    """An LLM-emitted `drain:` narrowed to what an unattended ingest may write.

    Returns `(ledger, None)` when every entry is `{claim: 1..items, dropped: <reason>}`
    with no duplicate claim and a reason in *reasons*, `(None, None)` when
    *value* is absent or empty, and `(None, why)` otherwise. All or nothing: a
    partly bad ledger is dropped whole.

    *reasons* narrows the allowlist below `DROP_REASONS` for a caller whose
    writer may judge only some of them.
    """
    if value in (None, [], ()):
        return None, None
    if not isinstance(value, list):
        return None, "drain is not a list"
    seen: set[int] = set()
    out: list[dict[str, object]] = []
    for entry in value:
        if not isinstance(entry, Mapping) or set(entry) != {"claim", "dropped"}:
            return None, "drain entries must be exactly {claim, dropped}"
        claim = _claim(entry["claim"])
        if claim is None or claim > items or claim in seen:
            return None, f"drain claim {entry['claim']!r} is out of range 1..{items} or repeated"
        if entry["dropped"] not in reasons:
            return None, f"drain dropped reason {entry['dropped']!r} is not one of {', '.join(reasons)}"
        seen.add(claim)
        out.append({"claim": claim, "dropped": entry["dropped"]})
    return tuple(out), None
