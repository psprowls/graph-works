"""The ingest vertical: `entity_match` -> `suggest_pages` ->
`proposal_reasoner` -> `commands` is the real call chain
(`commands.run_ingest_source` calls `suggest_pages.plan_suggestions` and
`suggest_pages.apply_suggestions`, which call
`proposal_reasoner.run_proposal_reasoner`). Layer 2 — independent of every
other vertical; imports only `workspace`, `agent_substrate`, and the shared
`prompts`.
"""

from __future__ import annotations
