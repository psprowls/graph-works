"""The Diátaxis vertical: the validated decision, placement, retyping.

The taxonomy itself lives in the shipped schemas' `x-okf-proposal-guidance`.

    from doc_wiki_okf.diataxis import classify, plan_retype

`diataxis/` may import `doc_wiki_okf.reading`; `reading/` may not import this.
That direction is `tests/test_reading_boundaries.py`'s, and it needs no edit here.
"""

from __future__ import annotations

from doc_wiki_okf.diataxis.classify import (
    Classification,
    Unclassified,
    UnclassifiedReason,
    classify,
)
from doc_wiki_okf.diataxis.pages import default_concept_id, directory_for, new_page_text
from doc_wiki_okf.diataxis.retype import (
    RetypePlan,
    RetypeRefusal,
    RetypeRefusalKind,
    RetypeResult,
    apply_retype,
    plan_retype,
)

__all__ = [
    "Classification",
    "RetypePlan",
    "RetypeRefusal",
    "RetypeRefusalKind",
    "RetypeResult",
    "Unclassified",
    "UnclassifiedReason",
    "apply_retype",
    "classify",
    "default_concept_id",
    "directory_for",
    "new_page_text",
    "plan_retype",
]
