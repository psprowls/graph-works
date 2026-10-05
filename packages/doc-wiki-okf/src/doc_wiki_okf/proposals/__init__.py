"""The proposal pool (types a proposal may target, read off the schema set),
the review renderer, the old-dialect migrator, and the two compositions over
`okf_ext.proposals` -- filing and promotion.

This subpackage reads the proposal pool and target path placement from the
schema declarations. It also provides the ReviewRenderer, which
generates the seven-section review body while a proposal is in proposed status,
and the old-dialect migrator -- the `kind`/`mode`/`target_slug`/`origins[]`
ledger rewritten into `okf_ext.proposals` shape, as a plan you inspect before
anything is written.
"""

from __future__ import annotations

from doc_wiki_okf.proposals.adr import adr_directory, is_adr
from doc_wiki_okf.proposals.filing import plan_file
from doc_wiki_okf.proposals.migrate import (
    MigrationOutcome,
    MigrationPlan,
    MigrationWrite,
    migrate_and_move,
    plan_migrate,
)
from doc_wiki_okf.proposals.migrate import Refusal as MigrationRefusal
from doc_wiki_okf.proposals.pool import (
    REFUSED_TYPE,
    PoolError,
    ProposalPool,
    TypeState,
    proposal_pool,
    refused_type_rule,
)
from doc_wiki_okf.proposals.promote import page_render, plan_promotion
from doc_wiki_okf.proposals.render import ReviewRenderer

__all__ = [
    "REFUSED_TYPE",
    "MigrationOutcome",
    "MigrationPlan",
    "MigrationRefusal",
    "MigrationWrite",
    "PoolError",
    "ProposalPool",
    "ReviewRenderer",
    "TypeState",
    "adr_directory",
    "is_adr",
    "migrate_and_move",
    "page_render",
    "plan_file",
    "plan_migrate",
    "plan_promotion",
    "proposal_pool",
    "refused_type_rule",
]
