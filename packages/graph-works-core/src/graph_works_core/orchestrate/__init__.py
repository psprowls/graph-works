"""The orchestrate vertical: planning, advancement, placement, and receipts.

`commands.py` is the IO-free dispatch planner plus its config/git shell.
`claims.py` is the pure claim model (`Claim`, `conflicts()`) `commands.plan()`
admits every candidate through: containment-aware code scopes, observed
worktrees, the workspace; owner self-exclusion; write-vs-write.
`stage_advance.py` handles stage completion; `placement.py` records observed
placement and reader receipts, sharing `stage_advance`'s decision-owner lock,
not its code. `anchors.py` prepares and checks worktree anchors.
`finish_receipt.py` writes Git-verified finish receipts. `asks.py` writes and
answers typed-ask payloads (`gw work ask`). `orca_port.py` declares the
structural Orca seam, and `dispatch_record.py` owns the strict journal shared
by `dispatch.py` and `reroute.py`. `dispatch.py` calls
`placement.run_record_placement` / `placement.run_record_reader` in process;
otherwise the command modules do not share symbols. None is re-exported here.

Layer 2 — independent of every other vertical; imports only lower layers.
"""

from __future__ import annotations
