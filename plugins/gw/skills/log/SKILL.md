---
name: log
description: Use when explicitly invoked as /gw:log [--last N] [--op scan|ingest|query|lint|...] [--since YYYY-MM-DD] to read recent workspace log entries. Explicit invocation only.
---

# Show the wiki log

Show recent entries from `<workspace>/okf/log.md`. Use one ISO date section per
day, newest dates first, with each operation recorded as a list item:

```
## YYYY-MM-DD

- **<op>** <title> — <detail>
  Optional continuation paragraphs and nested bullets belong inside this entry.
```

## Usage

```
/gw:log                          # Claude Code — last 10 entries
$log                                       # Codex — last 10 entries
/gw:log --last 20
/gw:log --op scan --last 10      # only scan entries
/gw:log --op ingest              # recent ingests
/gw:log --since 2026-04-01
```

Prefer `gw util log --op <op> --title <title> --detail <detail>` in the intended
workspace for appending. Bold operation labels are a graph-works convention;
unlabelled list entries are also valid OKF. `gw util log` is append-only.

## Read-only retrieval

Resolve the workspace, then pass its absolute log path as an argument. Run the
recipe below using a Python environment containing `okf_io`; in this repository
use `uv run --package graph-works-cli python` in place of `python`. Forward the
requested `--last`, `--op`, and `--since` arguments after the path. These are
recipe arguments, not `gw util log` flags. This reads the file without writing.

`--last` counts logical entries (default 10; zero shows none; negatives fail).
`--since YYYY-MM-DD` is inclusive. Operation matching is case-insensitive and
recognizes leading bold labels and historical plain `op | title` entries;
without `--op`, unlabelled entries remain visible. Filters apply before the
limit. Entries sort by date descending, then source line descending within a
day, including duplicate dated sections. Source position is deterministic
display order, not a historical timestamp.

The Markdown parser handles fences and source spans. Selecting outermost list
items retains continuation paragraphs, code, and nested bullets exactly once.
Invalid sections are reported separately on stderr with body-relative lines
and excluded from matches; they never inherit a neighbouring date.

```bash
python - "/absolute/workspace/okf/log.md" --last 10 <<'PY'
import argparse
import re
import sys
from pathlib import Path

from okf_io import load, parse_log
from okf_io.log import iso_date

parser = argparse.ArgumentParser(description="Read workspace log entries")
parser.add_argument("path")
parser.add_argument("--last", type=int, default=10)
parser.add_argument("--op", type=str.casefold,
                    choices=("scan", "ingest", "query", "lint", "create", "update", "delete", "note"))
parser.add_argument("--since")
args = parser.parse_args()
if args.last < 0:
    parser.error("--last must be nonnegative")
since = iso_date(args.since) if args.since is not None else None
if args.since is not None and since is None:
    parser.error("--since must be a valid YYYY-MM-DD date")

document = load(Path(args.path))
lines = document.body.splitlines(keepends=True)
matches = []
for section in parse_log(document).sections:
    if section.date is None:
        print(f"Invalid log section at body line {section.line}: {section.heading}", file=sys.stderr)
        continue
    if since is not None and section.date < since:
        continue
    previous_end = 0
    for entry in section.entries:
        if entry.line <= previous_end:
            continue
        previous_end = entry.end
        label = re.match(r"^\*\*([^*]+)\*\*(?:\s|$)|^([A-Za-z]+)\s*\|", entry.text)
        op = (label.group(1) or label.group(2)).casefold() if label else None
        if args.op is None or args.op == op:
            matches.append((section.date, entry.line, entry.end))

selected = sorted(matches, reverse=True)[:args.last]
if not selected:
    print("No matching log entries.")
previous_date = None
for day, start, end in selected:
    if day != previous_date:
        print(f"## {day.isoformat()}\n")
        previous_date = day
    print("".join(lines[start - 1:end]).rstrip("\r\n") + "\n")
PY
```

## Valid ops

- `scan` — a `/gw:scan` pass ran
- `ingest` — a source was read and integrated
- `query` — a question was answered (when filed back)
- `lint` — a health check ran
- `create` — a new page was created outside an ingest
- `update` — a page was updated outside an ingest
- `delete` — a page was removed
- `note` — freeform note (contradictions flagged, thesis revisions)

## Example output

```
## 2026-04-20

- **lint** weekly health check
  Code drift: 2 new packages un-documented. 3 orphans, 1 stale roadmap page.

- **ingest** Auth Migration Spec
  Added sources/2026-04-auth-migration-spec.md. Updated docs/explanations/global-context,
  repositories/my-monorepo/packages/shared-aws-node-ts.md, adrs/0014-jwt-sessions (new).

## 2026-04-19

- **scan** detected 3 new packages
  Added repositories/my-monorepo/packages/timeline-native-ts.md, repositories/my-monorepo/packages/timeline-data-node-ts.md, repositories/my-monorepo/packages/timeline-domain-ts.md.
```

## Reference

→ `../graph-works/SKILL.md`
