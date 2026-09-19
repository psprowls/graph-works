"""The one JSON encoder for every result `gw … --json` prints.

Projections live in `graph-works-wire` and return plain data; encoding is the
interface's job. `indent=2` is the CLI's long-standing, byte-pinned choice
(`tests/fixtures/json/`). `cli.py`'s help payload and `describe-surface` are
introspection, not results, and encode for themselves.
"""

from __future__ import annotations

import json


def encode(payload: object) -> str:
    """Encode *payload* exactly as every `--json` result has always been encoded."""
    return json.dumps(payload, indent=2)
