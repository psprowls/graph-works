"""The lint and drift-propagation vertical: `lint` iterates the lanes
`lanes.compose_lanes` assembles; `propagate_drift` diffs against the
per-entity anchors `drift_anchor` reads and writes. `linter` and
`drift_propagator` are their respective judge/system prompts. Layer 2 —
independent of every other vertical; imports `workspace` and `agent_substrate`.
"""

from __future__ import annotations
