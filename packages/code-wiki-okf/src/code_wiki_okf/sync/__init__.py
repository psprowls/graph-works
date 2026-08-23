"""The `sync.` staleness topic -- distinct from `cli.sync()`, the write
command, the same "same last name, different things" split okf-io's own
`CLAUDE.md` documents for `okf_io._rules.links` vs `okf_io.links`.
"""

from __future__ import annotations

from code_wiki_okf.sync.rule import CODES, TOPIC, sync_rule
from code_wiki_okf.sync.run import MirrorSummary, SyncPlan, SyncResult, plan_sync, sync_bundle
from code_wiki_okf.sync.snapshot import SyncSnapshot, snapshot_bundle

__all__ = [
    "CODES",
    "TOPIC",
    "MirrorSummary",
    "SyncPlan",
    "SyncResult",
    "SyncSnapshot",
    "plan_sync",
    "snapshot_bundle",
    "sync_bundle",
    "sync_rule",
]
