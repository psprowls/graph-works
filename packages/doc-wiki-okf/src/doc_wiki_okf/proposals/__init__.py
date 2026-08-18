"""The proposal lane map: where new pages land based on type and content.

This subpackage handles the lane map (directory structure) and target path
generation for proposals targeting the four Diátaxis types (Tutorial, HowTo,
Reference, Explanation) plus ADRs. It also provides the ReviewRenderer, which
generates the seven-section review body while a proposal is in proposed status,
and the old-dialect migrator -- the `kind`/`mode`/`target_slug`/`origins[]`
ledger rewritten into `okf_ext.proposals` shape, as a plan you inspect before
anything is written.
"""

from __future__ import annotations

from doc_wiki_okf.proposals.filing import plan_file
from doc_wiki_okf.proposals.lanes import (
    ADR_DIRECTORY,
    ADR_TYPE,
    DIATAXIS_LANES,
    Lane,
    LaneSet,
    is_adr,
    lane_set,
)
from doc_wiki_okf.proposals.migrate import (
    MigrationOutcome,
    MigrationPlan,
    MigrationWrite,
    migrate_and_move,
    plan_migrate,
)
from doc_wiki_okf.proposals.migrate import Refusal as MigrationRefusal
from doc_wiki_okf.proposals.promote import page_render, plan_promotion
from doc_wiki_okf.proposals.render import ReviewRenderer

__all__ = [
    "ADR_DIRECTORY",
    "ADR_TYPE",
    "DIATAXIS_LANES",
    "Lane",
    "LaneSet",
    "MigrationOutcome",
    "MigrationPlan",
    "MigrationRefusal",
    "MigrationWrite",
    "ReviewRenderer",
    "is_adr",
    "lane_set",
    "migrate_and_move",
    "page_render",
    "plan_file",
    "plan_migrate",
    "plan_promotion",
]
