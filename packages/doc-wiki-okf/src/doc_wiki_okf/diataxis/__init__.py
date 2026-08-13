"""The four Diátaxis types: the taxonomy, the validated decision, placement, retyping.

    from doc_wiki_okf.diataxis import RUBRIC, TYPE_NAMES, brief

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
from doc_wiki_okf.diataxis.rubric import RUBRIC, TYPE_NAMES, TypeRubric, brief

__all__ = [
    "RUBRIC",
    "TYPE_NAMES",
    "Classification",
    "RetypePlan",
    "RetypeRefusal",
    "RetypeRefusalKind",
    "RetypeResult",
    "TypeRubric",
    "Unclassified",
    "UnclassifiedReason",
    "apply_retype",
    "brief",
    "classify",
    "default_concept_id",
    "directory_for",
    "new_page_text",
    "plan_retype",
]
