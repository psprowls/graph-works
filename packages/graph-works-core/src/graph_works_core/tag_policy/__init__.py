"""Tag retention policy: the rule, the reviewable disposition, and its apply.

Tier 3 by necessity -- see `rule`'s module docstring. Free functions over
frozen data, matching `graph_works_core.wiki_stats` and `okf_ext.tags`.
"""

from __future__ import annotations

from graph_works_core.tag_policy.commands import Phase, apply_phase, draft_disposition, plan_phase, undeclared
from graph_works_core.tag_policy.disposition import DispositionError, load, loads, render
from graph_works_core.tag_policy.model import Disposition, Reason, TagVerdict, Verdict
from graph_works_core.tag_policy.rule import (
    DEFAULT_CEILING,
    DEFAULT_FLOOR,
    draft,
    entity_names,
    field_values,
)

__all__ = [
    "DEFAULT_CEILING",
    "DEFAULT_FLOOR",
    "Disposition",
    "DispositionError",
    "Phase",
    "Reason",
    "TagVerdict",
    "Verdict",
    "apply_phase",
    "draft",
    "draft_disposition",
    "entity_names",
    "field_values",
    "load",
    "loads",
    "plan_phase",
    "render",
    "undeclared",
]
