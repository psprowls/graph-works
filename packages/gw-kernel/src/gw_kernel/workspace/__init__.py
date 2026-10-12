"""What a workspace is: discovery, the layout object, the manifest, init,
provenance, and the workflow-pipeline table. Layer 0 — every other directory
in this package sits above it and may import it; it imports nothing else
here.

`pipeline` lives here rather than with `orchestrate/`, which is its only
other consumer: `init.plan_init` seeds a new workspace's manifest with
`pipeline.RELAY_TAIL_SEED`, and a layer-0 module cannot import a vertical
without breaking the `layers` contract in the root `pyproject.toml`.
"""

from __future__ import annotations
