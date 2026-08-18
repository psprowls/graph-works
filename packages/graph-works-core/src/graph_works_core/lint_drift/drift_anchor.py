"""The drift propagator's per-entity anchor: entity concept id -> the commit
its curated targets were last judged at.

`<cache_dir>/drift/propagated.json`, not a frontmatter stamp. The ported code
wrote `drift_propagated_commit` onto each entity page, but that key is outside
`code_wiki_okf.entities.sync`'s owned set (`generated`, `last_updated_commit`,
`tokens`), so a re-scan would drop it — preserving it would mean widening a
sibling package's provenance contract for a key that lane has no other use
for. `layout.gitignore_entries` already derives `/_cache/`, so the anchor is
gitignored and scanner-excluded with no new entry.

**Nothing here raises.** The anchor is regenerable state: a lost anchor costs
one extra judging pass and nothing else, so every read failure — absent file,
unreadable file, invalid JSON, wrong shape — is the empty mapping.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

#: The anchor file, relative to the workspace `cache_dir`.
ANCHOR_RELATIVE_PATH = "drift/propagated.json"


def anchor_path(cache_dir: Path) -> Path:
    """Where the anchor lives for a given `layout.cache_dir`."""
    return cache_dir / ANCHOR_RELATIVE_PATH


def read_anchors(cache_dir: Path) -> dict[str, str]:
    """Entity concept id -> last-propagated commit. `{}` on any failure.

    Entries whose key or value is not a non-empty string are dropped rather
    than coerced: a bad entry would compare unequal to every real commit and
    silently force a re-judge, which is the same outcome as dropping it, with
    less to explain.
    """
    path = anchor_path(cache_dir)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        key: value
        for key, value in raw.items()
        if isinstance(key, str) and key.strip() and isinstance(value, str) and value.strip()
    }


def write_anchors(cache_dir: Path, anchors: Mapping[str, str]) -> None:
    """Replace the anchor file with *anchors*, creating `<cache_dir>/drift/`.

    A whole-file replace rather than a merge: the caller computed the mapping
    it wants persisted, and a writer that merged would make "forget this
    entity" unexpressible.
    """
    path = anchor_path(cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(sorted(anchors.items())), indent=2) + "\n", encoding="utf-8")


__all__ = ["ANCHOR_RELATIVE_PATH", "anchor_path", "read_anchors", "write_anchors"]
