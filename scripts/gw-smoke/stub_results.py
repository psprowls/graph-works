#!/usr/bin/env python3
"""Synthesize a trivial-but-valid results/<page-stem>.json per emitted prose task.

Lets you exercise `gw scan --apply` without spinning up a real subagent for every
brief. For testing the *contract* (does apply correctly consume results), not for
testing prose quality — the sections it writes are placeholders.

Matching is by the `uri` field inside each result file's JSON content, not the
filename (see `load_results_dir` in `graph_works_core.scan.commands`) — so the
output filename here just needs to be unique, not derived from anything.

Usage: python3 stub_results.py <workspace-path>
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

_SLUG_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _brief_slug(uri: str) -> str:
    """Mirrors `graph_works_core.scan.commands.brief_slug`."""
    return _SLUG_UNSAFE.sub("-", uri).strip("-") or "entity"


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2

    workspace = Path(sys.argv[1])
    worklist_path = workspace / ".gw" / "cache" / "scan" / "worklist.json"
    results_dir = workspace / ".gw" / "cache" / "scan" / "results"

    if not worklist_path.exists():
        print(f"error: no worklist at {worklist_path} — run `gw scan --emit-worklist` first", file=sys.stderr)
        return 1

    worklist = json.loads(worklist_path.read_text(encoding="utf-8"))
    tasks = worklist.get("prose_tasks", [])
    if not tasks:
        print("no prose_tasks in worklist — nothing to stub")
        return 0

    results_dir.mkdir(parents=True, exist_ok=True)
    seen: dict[str, int] = {}
    written = 0
    for task in tasks:
        uri = task["uri"]
        sections = {heading: f"(stub) {heading.lstrip('# ')} for {uri}." for heading in task.get("prose_sections", {})}
        result = {"uri": uri, "sections": sections, "error": None}

        slug = _brief_slug(uri)
        seen[slug] = seen.get(slug, 0) + 1
        if seen[slug] > 1:
            slug = f"{slug}-{hashlib.sha1(uri.encode('utf-8')).hexdigest()[:8]}"

        out_path = results_dir / f"{slug}.json"
        out_path.write_text(json.dumps(result, indent=2), encoding="utf-8", newline="\n")
        written += 1

    print(f"wrote {written} stub result(s) to {results_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
