"""v0.1 migration hints (OKF v0.2 §13.1)."""

from __future__ import annotations

from collections.abc import Iterable

from okf_io._rules._common import concepts, member_path
from okf_io.validate import Finding, Rule, RuleContext

CODES: tuple[str, ...] = ("legacy.timestamp", "legacy.body-citations")


def legacy_fields(ctx: RuleContext) -> Iterable[Finding]:
    """§13.1's two breaking changes, reported as hints rather than failures.

    Both are WARN. §13.1 says a v0.1 bundle *is* consumable by a v0.2 consumer
    under the v0.1 read fallbacks, so neither is a conformance failure -- these
    tell a producer what to migrate.

    ``legacy.body-citations`` keys off ``Frontmatter.fallbacks``, the one place
    that records whether the fallback actually fired. Re-scanning the body here
    would disagree with the view on a migrated document that kept its old
    ``# Citations`` prose beside real ``sources``.

    ``legacy.timestamp`` ignores a blank or whitespace-only timestamp value
    (e.g., ``timestamp: ""`` or ``timestamp:  ``). Such a value carries no data
    to migrate, so it is treated as absent, matching how the frontmatter rules
    treat blank strings.
    """
    for concept_id, document in concepts(ctx):
        path = member_path(concept_id)
        timestamp_value = document.fm_raw.get("timestamp")
        if timestamp_value is not None:
            # Treat empty/whitespace-only strings as absent (no data to migrate)
            if isinstance(timestamp_value, str) and not (timestamp_value or "").strip():
                pass  # Empty or whitespace-only string: treat as absent
            else:
                # Non-empty string or date/datetime object: fire the finding
                yield Finding(
                    "legacy.timestamp",
                    "warn",
                    "v0.1 top-level `timestamp`; migrate to `generated: { by, at }`",
                    "§13.1",
                    path,
                )
        if "sources" in document.fm.fallbacks:
            yield Finding(
                "legacy.body-citations",
                "warn",
                "v0.1 body `# Citations` list; migrate to frontmatter `sources`",
                "§13.1",
                path,
            )


RULES: tuple[Rule, ...] = (legacy_fields,)
