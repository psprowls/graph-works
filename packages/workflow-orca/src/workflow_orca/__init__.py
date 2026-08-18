"""workflow-orca: the Orca implementation of `subagents_io`'s dispatch seam.

This is the package where vendor coupling is permitted to live. `subagents-io`
holds the protocol and the vocabularies; nothing there knows what Orca is, and
nothing here is importable from there — `pyproject.toml`'s `forbidden` contract
holds that direction mechanically.

    from workflow_orca import OrcaBackend

    backend = OrcaBackend()
    session = backend.open_session("auto-drive:my-slug")
    record = session.launch(planned_dispatch)
    for event in session.wait(timeout_s=300):
        ...
        session.ack(event)

Every call out is `subprocess` against the `orca` CLI, behind an injected
runner (`OrcaBackend(run=…)`) so the suite replays JSON captured verbatim from
the live CLI and asserts the argv this package builds. Argv is the actual
contract with Orca; a façade's method calls are not.
"""

from __future__ import annotations

from workflow_orca._cli import OrcaCliError, OrcaResult
from workflow_orca.backend import OrcaBackend, OrcaSession

__version__ = "0.1.0"

__all__ = ["OrcaBackend", "OrcaCliError", "OrcaResult", "OrcaSession"]
