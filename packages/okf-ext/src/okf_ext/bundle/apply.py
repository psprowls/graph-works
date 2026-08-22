"""Write either plan. One function, one write mode.

Every file either plan carries is a **create**, so each is handed to
`okf_ext.writing.write_all` with `create=True`: the parent directory is made
(`schema/` may not exist), a target that appeared *since* the plan was
computed is refused as `stale`, and staging and commit are the same two steps
every other capability's writes go through. The two I/O regimes -- probe and
staging all-or-nothing, per-file commit -- are inherited unchanged.

`on_written` is a no-op. There is no in-memory `Bundle` to keep coherent:
these documents are being created, not edited. A caller planning again against
the same `Bundle` object after an apply must reload first, exactly as
`moves.apply` and `proposals.apply` both document for their own creates.

**Unlike those two, this `apply` does not raise on a plan carrying refusals.**
A refusal here names one file the caller does not own; the other nineteen are
still theirs to write, and refusing all of them because one seed was
hand-edited is precisely the all-or-nothing behaviour this capability exists
to narrow. Refusals ride into `ApplyResult.failed` alongside any write failure
and `ApplyResult.ok` reports them, which is what a CLI reads to exit non-zero.

This module imports the shared layer and its own capability's model. It
imports no sibling capability.
"""

from __future__ import annotations

from okf_ext.bundle.model import Plan
from okf_ext.writing import ApplyResult, PendingWrite, write_all


def apply(plan: Plan) -> ApplyResult:
    """Write every file *plan* carries, in the order it carries them.

    Ordering is `write_all`'s contract, not an accident: `ApplyResult.written`
    comes back in plan order, so a caller printing it prints the sequence that
    actually happened.
    """
    pending = [
        PendingWrite(
            member=planned.member,
            path=planned.path,
            rendered=planned.content,
            on_written=lambda: None,
            create=True,
        )
        for planned in plan.writes
    ]
    return write_all(pending, failed=plan.refusals, skipped=plan.skipped)


__all__ = ["apply"]
