"""Render a store's explicit values to a JSON projection.

The projection is the read surface for consumers that must not spawn a Python
stack — the caller's shell hooks read it per-tool-use. Explicit values only, no
defaults merged: a consumer applies its own fallbacks, so an absent key means
"default". `_meta` carries the store's fingerprint so a consumer can detect a
hand-edit and tell the user to re-sync.

`_meta`'s keys are `source_mtime` / `source_sha256`. The seed called them
`manifest_*`, which is caller vocabulary in a package that must not know its
caller — the same class of leak as a hardcoded key name. Renaming them is a
stated migration obligation for whoever reads this file today.

This module is the one place in the package that imports `os`, and the boundary
test exempts it by name: writing a file atomically is not the same thing as
reading the environment, which is what the band-1 rule forbids.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from config_io.store import ConfigStore

#: The conventional basename. The *directory* is always the caller's choice —
#: this package never composes a path.
PROJECTION_FILENAME = "config.json"


def write_projection(store: ConfigStore, target: Path) -> Path:
    """Regenerate `target` from `store`'s explicit values. Returns `target`."""
    payload: dict[str, object] = dict(store.read_explicit())
    fingerprint = store.fingerprint()
    payload["_meta"] = {
        "source_mtime": fingerprint.mtime if fingerprint is not None else None,
        "source_sha256": fingerprint.sha256 if fingerprint is not None else None,
    }
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    # default=str: YAML parses bare dates into datetime.date, which json
    # refuses on its own.
    rendered = json.dumps(payload, indent=2, default=str) + "\n"
    # Atomic replace: consumers read this file concurrently, so a reader must
    # never observe a truncation. Path.replace is os.replace.
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(rendered)
        # mkstemp creates the file at mode 0600; Path.replace carries that mode
        # across the rename. Consumers read this file across process/user
        # boundaries, so restore the ordinary world-readable mode a
        # regenerated file is expected to have.
        tmp.chmod(0o644)
        tmp.replace(target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return target
