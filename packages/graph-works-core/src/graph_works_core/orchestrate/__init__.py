"""The orchestrate vertical: two modules, one seam.

`commands.py` is the IO-free dispatch planner plus its config/git shell.
`stage_advance.py` is the stage-completion shell `gw work advance` routes
through. They share no module-level symbol, and neither re-exports the other —
the planner lane owns one file and the stage-gate lane the other.

Layer 2 — independent of every other vertical; imports only `workspace`.
"""

from __future__ import annotations
