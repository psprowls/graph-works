"""Entity-lane renderers, page creation, sync orchestration, and deletion.

One pure `render_<type>` per admitted entity type (`render.py`); new-page
skeleton creation (`pages.py`); the `GraphReader`-driven orchestrator
(`sync.py`); the deletion guard (`delete.py`); the top-level `sync()` wiring
the orchestrator and deletion guard together per lane, plus index and log
reconciliation (`lanes.py`).
"""

from __future__ import annotations
