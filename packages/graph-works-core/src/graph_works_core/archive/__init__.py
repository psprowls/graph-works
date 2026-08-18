"""The archive vertical: `ArchiveRun`/`run_archive`, one file. `provenance`
stays in `workspace/` — both `archive` and `orchestrate` need it, so neither
owns it. Layer 2 — independent of every other vertical; imports only
`workspace`.
"""

from __future__ import annotations
