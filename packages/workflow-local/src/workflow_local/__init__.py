"""workflow-local: a subprocess `DispatchBackend` for `subagents-io`.

The first concrete implementation of the band-1 dispatch seam, and deliberately
not a module inside `subagents-io`: that package's boundary walk carves nothing
out, and the first exemption is the one that decides there will be others.
`workflow-<backend>` is the naming rule — this is where vendor and system
coupling is allowed to live, which is what makes the band-1 purity structural
rather than a matter of vigilance.

    from workflow_local import LocalBackend

    backend = LocalBackend(root, argv_for=lambda d: ["claude", "-p", d.prompt])
    session = backend.open_session("auto-drive:my-epic")

See `README.md` for the child-side contract: three environment variables, one
JSONL event file, one JSONL reply file.
"""

from __future__ import annotations

__version__ = "0.1.0"

from workflow_local.backend import LocalBackend, LocalSession
from workflow_local.ledger import LedgerEntry, read_ledger, write_ledger

__all__ = [
    "LedgerEntry",
    "LocalBackend",
    "LocalSession",
    "read_ledger",
    "write_ledger",
]
