"""A disposable SQLite read index over an OKF bundle.

No `TOPIC`, no `CODES`: this is a primitive, not a validation rule. D-001
relies on okf-io's public entry points; this capability imports no sibling
capability and not `okf_ext.context`. Bodies are never stored, and the database
can always be discarded and rebuilt from the bundle.
"""

from okf_ext.readindex.model import Diagnostics, IndexBusy, IndexUnavailable, MemberRow, RebuildReason, Reconcile
from okf_ext.readindex.store import ReadIndex, open_index
from okf_ext.readindex.sync import reconcile, verify
from okf_ext.readindex.view import IndexView, read

__all__ = [
    "Diagnostics",
    "IndexBusy",
    "IndexUnavailable",
    "IndexView",
    "MemberRow",
    "ReadIndex",
    "RebuildReason",
    "Reconcile",
    "open_index",
    "read",
    "reconcile",
    "verify",
]
