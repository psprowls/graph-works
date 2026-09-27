"""The orchestrate vertical: no shared symbols between its shells.

`commands.py` is the IO-free dispatch planner plus its config/git shell.
`stage_advance.py` is the stage-completion shell `gw work advance` routes
through. `placement.py` is the placement-only recorder `gw work record-placement`
routes through; it shares `stage_advance`'s decision-owner lock, not its code.
`claims.py` is the pure claim model (`Claim`, `conflicts()`) `commands.plan()`
admits every candidate through: containment-aware code scopes, observed
worktrees, the workspace; owner self-exclusion; write-vs-write.
None re-exports another.

Layer 2 — independent of every other vertical; imports only `workspace`.
"""

from __future__ import annotations
