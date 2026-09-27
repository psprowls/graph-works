"""Bind core's structural OrcaPort to the CLI's Orca adapter (D-002)."""

from __future__ import annotations

from graph_works_core.orchestrate.orca_port import OrcaPort
from workflow_orca.port import OrcaCliPort


def orca_port() -> OrcaPort:
    """Return the adapter; strict type checking pins its protocol shape."""
    return OrcaCliPort()
