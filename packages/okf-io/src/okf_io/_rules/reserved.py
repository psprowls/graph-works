"""Rules for the reserved filenames `index.md` and `log.md` (OKF v0.2 §8, §9, §12)."""

from __future__ import annotations

from collections.abc import Iterable

from okf_io._md import parse_body
from okf_io.log import iso_date
from okf_io.validate import Finding, Rule, RuleContext

CODES: tuple[str, ...] = (
    "reserved.index-frontmatter",
    "reserved.index-extra-keys",
    "reserved.okf-version-unknown",
    "reserved.log-heading-not-date",
)

#: §12. A version outside this set is a best-effort read, not a rejection.
_KNOWN_VERSIONS = frozenset({"0.1", "0.2"})

#: §8's single exception: the one key a bundle-root index may carry.
_VERSION_KEY = "okf_version"


def _reserved_path(directory_id: str, name: str) -> str:
    return f"{directory_id}/{name}" if directory_id else name


def indexes(ctx: RuleContext) -> Iterable[Finding]:
    """§8 and §12: index files carry no frontmatter, except a root `okf_version`."""
    for directory_id in sorted(ctx.bundle.indexes):
        document = ctx.bundle.indexes[directory_id]
        if not document.has_frontmatter:
            continue
        path = _reserved_path(directory_id, "index.md")
        if directory_id:
            yield Finding(
                "reserved.index-frontmatter",
                "error",
                "A non-root `index.md` carries frontmatter",
                "§8",
                path,
            )
            continue
        extra = sorted(str(key) for key in document.fm_raw if str(key) != _VERSION_KEY)
        if extra:
            yield Finding(
                "reserved.index-extra-keys",
                "error",
                f"A root `index.md` carries keys beyond `okf_version`: {', '.join(extra)}",
                "§8",
                path,
            )
        version = document.fm_raw.get(_VERSION_KEY)
        if version is not None and str(version).strip() not in _KNOWN_VERSIONS:
            yield Finding(
                "reserved.okf-version-unknown",
                "warn",
                f"`okf_version` {str(version).strip()!r} is neither 0.1 nor 0.2",
                "§12",
                path,
            )


def logs(ctx: RuleContext) -> Iterable[Finding]:
    """§9: date headings MUST use ISO 8601 `YYYY-MM-DD`.

    Reads the body through the memoized ``parse_body``. This is the one place a
    rule indexes a body the graph does not hold -- ``LinkGraph.bodies`` is
    keyed by concept id, and a log is not a concept -- so the memo is what
    keeps "one parse per body" true.

    `log.md` frontmatter is deliberately not flagged: §8 restricts it in
    `index.md`, §9 says nothing of the sort, and `acme_retail/log.md` carries
    `type: Log`.
    """
    for directory_id in sorted(ctx.bundle.logs):
        document = ctx.bundle.logs[directory_id]
        path = _reserved_path(directory_id, "log.md")
        offset = document.body_line_offset
        for heading in parse_body(document.body).headings:
            if heading.level != 2:
                continue
            if iso_date(heading.text) is None:
                yield Finding(
                    "reserved.log-heading-not-date",
                    "error",
                    f"Log heading {heading.text!r} is not an ISO 8601 date",
                    "§9",
                    path,
                    heading.line + offset,
                )


RULES: tuple[Rule, ...] = (indexes, logs)
