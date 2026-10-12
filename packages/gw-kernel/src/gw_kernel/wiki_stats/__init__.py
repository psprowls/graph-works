"""The wiki-stats vertical: `commands.compute_stats` derives structural
metrics -- page/edge counts, connected components, hub rankings, orphans,
sinks -- from an already-loaded bundle's link graph. Layer 2 -- independent
of every other vertical; imports only `okf_io`, no `workspace` or
`agent_substrate` -- the caller resolves and loads the bundle itself.
"""

from __future__ import annotations
