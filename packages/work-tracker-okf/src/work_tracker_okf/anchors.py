"""Spec-text anchors -- the git shas a design spec carries about itself.

Two pure scans over spec markdown. Neither knows what a repository is: they
read headings and a bold-prefixed line, and the caller decides whether the sha
they name is reachable anywhere.

They live here rather than in `decisions.py` because they scan spec headings
for git shas -- nothing to do with the ledger. `decisions.py` already owns the
`D-nnn` pattern and `id_number`, and a module holding both concerns would be a
module named after neither.

Nothing raises. A spec with no `## Reconciled` heading and no baseline line is
the ordinary case for a spec that has never been reconciled, not an error.
"""

from __future__ import annotations

import re

#: `## Reconciled 2026-08-12 (de2dd11f..f5279f78)` -- the heading a reconcile
#: pass appends. Both shas are captured; only the head, the second, is read:
#: it is the code-repo tip that pass reconciled through.
_RECONCILED_RE = re.compile(
    r"^##\s+Reconciled\b.*\(([0-9a-fA-F]{7,40})\.\.([0-9a-fA-F]{7,40})\)\s*$",
    re.MULTILINE,
)

#: The spec's own `**Baseline commit:** `<sha>`` line. The backticks are
#: required: without them there is no delimiter saying where the sha ends, and
#: a narrative line would yield a truncated prefix that looks like an answer.
_BASELINE_RE = re.compile(r"\*\*Baseline commit:\*\*\s*`([0-9a-fA-F]{7,40})`")


def last_reconciled_head(spec_text: str) -> str | None:
    """The head sha of the most recent `## Reconciled <date> (<a>..<b>)` heading.

    That is the code-repo tip a previous pass reconciled through, and it is the
    anchor fallback when the spec file itself has no history in the repository
    being diffed -- the split topology, where the workspace is a separate
    directory. `None` when the spec has never been reconciled.
    """
    matches = _RECONCILED_RE.findall(spec_text)
    return matches[-1][1] if matches else None


def baseline_commit(spec_text: str) -> str | None:
    """The sha from the spec's `**Baseline commit:**` line -- the last-resort
    anchor for a spec that has never been reconciled.

    A free-form or narrative baseline line, not backticked after the bold
    prefix, is deliberately not recognized and returns `None`.
    """
    match = _BASELINE_RE.search(spec_text)
    return match.group(1) if match else None


__all__ = ["baseline_commit", "last_reconciled_head"]
