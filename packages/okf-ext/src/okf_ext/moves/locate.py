"""Re-export shim: the move locator now lives in `okf_ext.locate` (shared layer).

Kept for one minor version per okf-ext's promotion rule.
"""

from okf_ext.locate import (
    Candidate,
    RefDef,
    destinations,
    document_destinations,
    document_reference_definitions,
    reference_definitions,
)

__all__ = [
    "Candidate",
    "RefDef",
    "destinations",
    "document_destinations",
    "document_reference_definitions",
    "reference_definitions",
]
