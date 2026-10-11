"""A disposable SQLite read index over an OKF bundle.

No `TOPIC`, no `CODES`: this is a primitive, not a validation rule. D-001
relies on okf-io's public entry points; this capability imports no sibling
capability and not `okf_ext.context`. Bodies are never stored; `content` serves them
from the file, verified against the stored hash. The database can always be
discarded and rebuilt from the bundle.
"""

from okf_ext.readindex.content import StoredContent, content_of, read_content, serve
from okf_ext.readindex.model import (
    Diagnostics,
    IndexBusy,
    IndexUnavailable,
    MemberRow,
    PageContent,
    RebuildReason,
    Reconcile,
)
from okf_ext.readindex.project import member_row
from okf_ext.readindex.store import ReadIndex, open_index
from okf_ext.readindex.sync import reconcile, verify
from okf_ext.readindex.view import IndexView, read

__all__ = [
    "Diagnostics",
    "IndexBusy",
    "IndexUnavailable",
    "IndexView",
    "MemberRow",
    "PageContent",
    "ReadIndex",
    "RebuildReason",
    "Reconcile",
    "StoredContent",
    "content_of",
    "member_row",
    "open_index",
    "read",
    "read_content",
    "reconcile",
    "serve",
    "verify",
]
