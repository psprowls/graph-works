"""The validation pipeline: the finding model, the report, and the rule runner.

Nothing here rejects a bundle. Every rule reports; the caller decides. That is
spec §11's tolerance requirement made structural, and the reason there is no
``raise`` anywhere on the content path.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import date
from functools import cache
from typing import Literal

from okf_io.bundle import Bundle
from okf_io.links import LinkGraph, build

#: Data on the finding, never encoded in the code. A `Literal` rather than an
#: `Enum`, matching `TrustTier`, `ActorKind` and `DateMode` -- and it survives
#: `json.dumps` with no encoder, which `fm_data(dates="iso")` established as
#: this package's habit.
Severity = Literal["error", "warn"]


@dataclass(frozen=True, slots=True)
class Finding:
    """One thing that does not conform, and where.

    ``code`` is a dotted, topic-prefixed slug (``links.broken``,
    ``provenance.source-uncited``). Self-describing, so output needs no lookup
    table; the topic is literally the prefix, so ``code.startswith("trust.")``
    filters; and an external rule set claims a prefix by claiming a word.

    Severity being separate data is what makes strict mode cost nothing and
    lets a rule's severity change without renaming a code people already filter
    on. okf-schema's ``Report(errors=[...], warnings=[...])`` encodes it
    structurally instead; that is the precedent this rejects.
    """

    code: str
    severity: Severity
    message: str
    spec: str  # the citation, e.g. "§5.1"
    path: str | None  # bundle-relative posix member
    line: int | None = None


@dataclass(frozen=True, slots=True)
class Report:
    """The ordered findings, with severity views derived rather than stored."""

    findings: tuple[Finding, ...]

    @property
    def errors(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.severity == "error")

    @property
    def warnings(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.severity == "warn")

    @property
    def ok(self) -> bool:
        """No findings of severity ``error``. Broken links never make this False."""
        return not any(f.severity == "error" for f in self.findings)

    def by_code(self, code: str) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.code == code)


@dataclass(frozen=True, slots=True)
class RuleContext:
    """Everything a rule may read. Nothing here touches the filesystem or a clock."""

    bundle: Bundle
    links: LinkGraph
    today: date


#: An external rule is any callable of this shape. It runs through the same
#: pipeline, is sorted with the built-ins, and produces findings
#: shape-identical to theirs -- indistinguishable is the requirement.
Rule = Callable[[RuleContext], Iterable[Finding]]


@cache
def _registry() -> tuple[tuple[Rule, ...], frozenset[str]]:
    """The built-in catalog, imported lazily.

    ``_rules`` imports ``Finding`` and ``RuleContext`` from this module, so a
    module-level import here would be a cycle. This is the only place the
    package's downward dependency rule is inverted, and it is contained to
    these three lines.
    """
    from okf_io import _rules

    return _rules.RULES, _rules.TOPICS


def _sort_key(finding: Finding) -> tuple[str, int, str, str]:
    return (finding.path or "", finding.line or 0, finding.code, finding.message)


def validate(
    bundle: Bundle,
    *,
    today: date,
    extra_rules: Sequence[Rule] = (),
    strict: bool = False,
) -> Report:
    """Run the catalog over *bundle*.

    ``today`` is required and keyword-only, with no default. A prior child set
    that rule for ``is_stale`` -- a required argument leaves no code path where
    a hidden ``date.today()`` can survive review -- and the pipeline inherits it
    rather than softening it.

    Findings are sorted after collection, not merely appended, so the output
    does not depend on rule registration order, dict iteration, or the order
    the filesystem happened to hand back files.

    ``strict`` promotes every ``warn`` to ``error`` in one pass over the
    collected findings. Rules always emit their natural severity; because
    severity is data, this needs no second code namespace.

    Two guardrails on ``extra_rules``. A rule emitting a built-in topic prefix
    raises ``ValueError`` -- a namespace collision is a plugin-author error
    caught the moment a colliding finding is actually yielded, not a mystery
    finding attributed to the core three releases later. This is a runtime
    guard, not a registration-time one: a rule that only sometimes emits a
    colliding code passes cleanly on a bundle that never triggers it. And an
    exception from a rule propagates: tolerance is a promise about bundle
    *content*, and swallowing a plugin bug would leave a silently incomplete
    report, which is worse than a traceback.
    """
    builtin, topics = _registry()
    context = RuleContext(bundle=bundle, links=build(bundle), today=today)

    findings: list[Finding] = []
    for rule in builtin:
        findings.extend(rule(context))
    for rule in extra_rules:
        for found in rule(context):
            topic = found.code.split(".", 1)[0]
            if topic in topics:
                reserved = ", ".join(repr(t) for t in sorted(topics))
                raise ValueError(
                    f"External rule emitted {found.code!r}, which claims the built-in "
                    f"topic prefix {topic!r}. Choose a prefix the core does not define. "
                    f"Reserved prefixes: {reserved}."
                )
            findings.append(found)

    if strict:
        findings = [replace(found, severity="error") for found in findings]
    findings.sort(key=_sort_key)
    return Report(findings=tuple(findings))
