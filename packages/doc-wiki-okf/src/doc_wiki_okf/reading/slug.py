"""Free text to a stable identifier."""

from __future__ import annotations

import re

SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = SLUG_RE.sub("-", text).strip("-")
    return text[:60] or "untitled"
