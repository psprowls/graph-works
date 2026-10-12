"""The graph surface: `graph_tools` takes an open `GraphReader` and knows
nothing about a workspace; `commands` composes it with a `WorkspaceLayout`
into `build`/`describe`/`find`/`export` and the hoisted `GraphResult`,
`GraphTarget`, `graph_target`. Layer 1, shared — consumed by `ingest`
(indirectly), `query`, and `scan` alike; imports only `workspace`.
"""

from __future__ import annotations
