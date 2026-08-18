"""The scan vertical: `commands` builds and applies the scan worklist,
`scan_contract` is its result/task dataclasses, `prose_refresh` drives the
per-page prose-refresh loop against `prose_refresher`'s prompt. Layer 2 —
independent of every other vertical; imports `workspace`, `agent_substrate`,
and `graph` (for the shared graph surface).
"""

from __future__ import annotations
