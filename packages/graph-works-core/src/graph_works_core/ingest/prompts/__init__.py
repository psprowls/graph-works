"""Ingest's own prompt modules: the ingestor's system prompt, the extractor,
and the proposal reasoner. Nested under `ingest/` (rather than flattened)
because `proposal_reasoner.py` also names an ingest *command* module — the
two would collide as one file if flattened into `ingest/` directly.
"""

from __future__ import annotations
