"""Rules for the work-lifecycle axis: `work_status`, `phase`, and the key
each state must be accompanied by.

**The module name is the code prefix**, asserted mechanically in
`test_lane_catalog.py` exactly as okf-io's `test_catalog.py` asserts its own
eight. `lifecycle` was not available -- okf-io owns it, and `validate()` raises
the moment an external rule claims a built-in prefix -- which is the push that
turned a rename of `work_io.lifecycle_lint` into a split.
"""

from __future__ import annotations

from collections.abc import Iterable

from okf_io import Finding, Rule, RuleContext, Severity

from work_tracker_okf._rules._common import LaneConfig, active, days_since, text_key, with_documents
from work_tracker_okf.items import WorkItem
from work_tracker_okf.vocabulary import TERMINAL_STATUSES

CODES: tuple[str, ...] = (
    "state.phase-status-incoherent",
    "state.in-progress-without-owner",
    "state.resolved-without-ref",
    "state.superseded-without-link",
    "state.mitigated-without-mitigation",
    "state.wontfix-without-rationale",
    "state.stuck-open",
    "state.stuck-accepted",
    "state.archive-eligible",
)

#: `Finding.spec` is "the thing that says so". None of these nine cites an OKF
#: section: an item whose `work_status` is `superseded` with no
#: `superseded_by` is a perfectly conformant OKF v0.2 document. They cite the
#: module that defines them -- what `okf_ext.health` does with its own `_SPEC`,
#: for the same reason. Inventing a section number for a lane invariant would be
#: a false citation that outlives the person who wrote it.
_SPEC = "work_tracker_okf._rules.state"

#: Which phases each constrained `work_status` admits. Statuses absent from
#: the map -- `open`, `mitigated`, `wontfix`, `superseded` -- are unconstrained.
#:
#: Module-private with one consumer, exactly as `okf_io._rules.lifecycle` keeps
#: `_KNOWN_STATUS` local. It is bonded to `workflow.route()` by
#: `test_phase_compat.py` rather than derived from it: the epic spec's argument
#: for keeping the routing table at tier 3 is that the lint rules encode the
#: same state machine, and that argument is worth nothing unless the two cannot
#: drift (C5-I).
_PHASE_COMPAT: dict[str, frozenset[str]] = {
    "accepted": frozenset({"execute", "finish", "done"}),
    "in-progress": frozenset({"execute", "finish"}),
    "resolved": frozenset({"done"}),
}

#: work-io's two thresholds, unchanged.
_STUCK_OPEN_DAYS = 30
_STUCK_ACCEPTED_DAYS = 60


def _finding(code: str, severity: Severity, item: WorkItem, message: str) -> Finding:
    """Every code in this module reports the item page and no line: the facts are
    about the item, not about one key's position in it."""
    return Finding(code=code, severity=severity, message=message, spec=_SPEC, path=item.page_path, line=None)


def coherence(ctx: RuleContext) -> Iterable[Finding]:
    """22: a `phase` its `work_status` does not admit.

    `warn`, not `error`: humans hand-edit `work_status`, and the pair is a
    statement about where the item is rather than about whether its page
    conforms.
    """
    for item in active(ctx):
        allowed = _PHASE_COMPAT.get(item.work_status)
        if item.phase is None or allowed is None or item.phase in allowed:
            continue
        yield _finding(
            "state.phase-status-incoherent",
            "warn",
            item,
            f"`work_status` {item.work_status!r} expects `phase` in {sorted(allowed)}, got {item.phase!r}",
        )


def companions(ctx: RuleContext) -> Iterable[Finding]:
    """5-9: the key each of five `work_status` values must be accompanied by.

    One function for five codes because it is one question asked of five values
    -- which key must accompany this state -- and answering it once per item is
    one pass rather than five.

    Three notes a rewrite drops by not knowing about them:

    - **The epic exemption on `resolved`.** An epic resolves through the
      children-terminal gate, not a `resolved_in` ref, so requiring one would
      fire on every correctly-finished epic.
    - **Rule 5 narrows to `owner` alone** (C5-C). work-io also accepted
      `related_prs`; `sources[]` is the lane's pointer surface now, and a second
      parallel list of references is the duplication the port exists to remove.
    - `mitigation` and `rationale` are read off the document. They are declared
      in `_base.schema.json` -- child 5's one edit to a child-1 asset -- but the
      projection does not carry them.
    """
    for item, document in with_documents(ctx):
        status = item.work_status
        if status == "in-progress" and not item.owner:
            yield _finding(
                "state.in-progress-without-owner", "error", item, "`work_status: in-progress` with no `owner`"
            )
        if status == "resolved" and item.type != "Epic" and not item.resolved_in:
            yield _finding("state.resolved-without-ref", "warn", item, "`work_status: resolved` with no `resolved_in`")
        if status == "superseded" and not item.superseded_by:
            yield _finding(
                "state.superseded-without-link", "error", item, "`work_status: superseded` with no `superseded_by`"
            )
        if status == "mitigated" and not text_key(document, "mitigation"):
            yield _finding(
                "state.mitigated-without-mitigation", "error", item, "`work_status: mitigated` with no `mitigation`"
            )
        if status == "wontfix" and not text_key(document, "rationale"):
            yield _finding(
                "state.wontfix-without-rationale", "warn", item, "`work_status: wontfix` with no `rationale`"
            )


def staleness(ctx: RuleContext) -> Iterable[Finding]:
    """12, 13: an item that has not moved. `ctx.today` is the only clock."""
    for item in active(ctx):
        age = days_since(item.updated, ctx.today)
        if item.work_status == "open" and age > _STUCK_OPEN_DAYS:
            yield _finding(
                "state.stuck-open",
                "warn",
                item,
                f"`work_status: open` with no update in {age} days (over {_STUCK_OPEN_DAYS})",
            )
        if item.work_status == "accepted" and age > _STUCK_ACCEPTED_DAYS:
            yield _finding(
                "state.stuck-accepted",
                "warn",
                item,
                f"`work_status: accepted` with no update in {age} days (over {_STUCK_ACCEPTED_DAYS})",
            )


def terminal(ctx: RuleContext) -> Iterable[Finding]:
    """14: a terminal item still under `work/`.

    `warn`, where work-io graded it `info` (C5-D). okf-io has two severities and
    this cannot be the harder one: it would sink child 6's zero-errors gate on an
    item that is merely finished and not yet swept. `active` already excludes
    archived items, which is what makes "still under `work/`" the claim.
    """
    for item in active(ctx):
        if item.work_status in TERMINAL_STATUSES:
            yield _finding(
                "state.archive-eligible",
                "warn",
                item,
                f"`work_status: {item.work_status}` is terminal; the item is still under `work/`",
            )


def rules(config: LaneConfig) -> tuple[Rule, ...]:
    """Every topic exports `rules(config)`, including the two that inject nothing
    (C5-E). A heterogeneous registry -- some tuples, some callables -- would make
    the catalog-completeness test special-case half its own subjects, and that
    test is the whole reason the shape is worth constraining."""
    del config  # this topic injects nothing
    return (coherence, companions, staleness, terminal)
