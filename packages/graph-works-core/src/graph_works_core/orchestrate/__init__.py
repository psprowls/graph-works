"""The orchestrate vertical: three modules, no shared symbols.

`commands.py` is the IO-free dispatch planner plus its config/git shell.
`stage_advance.py` is the stage-completion shell `gw work advance` routes
through. `placement.py` is the placement-only recorder `gw work record-placement`
routes through; it shares `stage_advance`'s decision-owner lock, not its code.
None re-exports another.

Layer 2 — independent of every other vertical; imports only `workspace`.
"""

from __future__ import annotations
