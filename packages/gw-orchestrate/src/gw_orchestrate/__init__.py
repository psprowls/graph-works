"""The orchestrate vertical: planning, advancement, placement, and receipts.

`commands.py` is the IO-free dispatch planner plus its config/git shell.
`claims.py` is the pure claim model (`Claim`, `conflicts()`) `commands.plan()`
admits every candidate through: containment-aware code scopes, observed
worktrees, the workspace; owner self-exclusion; write-vs-write.
`wait.py` is the coordinator's event wait (`gw work wait`) over `OrcaPort`.
`stage_advance.py` handles stage completion; `placement.py` records observed
placement, sharing `stage_advance`'s decision-owner lock, not its code.
`placing.py` is the one binding to the gw-pipeline placement function and engine;
`anchors.py` verifies owner stamps for the gate and selects workspace branches.
`finish_receipt.py` writes Git-verified finish receipts. `asks.py` writes and
answers typed-ask payloads (`gw work ask`). `orca_port.py` declares the
structural Orca seam, and `dispatch_record.py` owns the strict journal shared
by `dispatch.py` and `reroute.py`. `dispatch.py` realises placement through
`placing.realize` and writes no stamp; otherwise the command modules do not share symbols. None is re-exported here.

Layer 2 — independent of every other vertical; imports only lower layers.
"""

from __future__ import annotations
