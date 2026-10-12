"""The query vertical: `commands` (retrieval, guardrails, pipelines) and
`query_orchestrator` (the multi-step agentic path), each with their own
prompt in `prompts/` and their own agent-loop adapter in `adapters/` —
nested rather than flattened because a command, a prompt, and an adapter
all separately need the name `query_orchestrator`. Layer 2 — independent of
every other vertical; imports `workspace`, `agent_substrate`, and `graph`.
"""

from __future__ import annotations
