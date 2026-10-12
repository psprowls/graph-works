"""Query's own role prompts: librarian, synthesizer, the two code-reader
system prompts, and the query-orchestrator system prompt. Nested under
`query/` because `query_orchestrator` also names a command module and an
adapter module — flattening any pair of the three would collide.
"""

from __future__ import annotations
