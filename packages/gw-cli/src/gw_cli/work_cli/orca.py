"""Bind core's structural OrcaPort to the CLI's Orca adapter (D-002)."""

from __future__ import annotations

import shutil

from gw_orchestrate.orca_port import OrcaPort
from gw_pipeline.placement import NullNotifier, PlacementNotifier
from workflow_orca.placement import OrcaPlacementNotifier
from workflow_orca.port import OrcaCliPort


def orca_port() -> OrcaPort:
    """Return the adapter; strict type checking pins its protocol shape."""
    return OrcaCliPort()


def placement_notifier() -> PlacementNotifier:
    """Notify Orca when its CLI is available; plain git placement works without it."""
    return OrcaPlacementNotifier() if shutil.which("orca") is not None else NullNotifier()
