#!/usr/bin/env bash
# Quick create -> scan -> lint sweep against this repo, for manual sanity-checking `gw`.
# Not a test suite: prints output for you to read, doesn't assert anything.
set -euo pipefail

REPO_ROOT="/Users/pat/Personal/agent-workspace"
GW="$REPO_ROOT/.venv/bin/gw"
TEST_WS="$REPO_ROOT/tmp/gw-smoke-ws"

if [[ ! -x "$GW" ]]; then
  echo "error: $GW not found — run 'uv sync' first" >&2
  exit 1
fi

rm -rf "$TEST_WS"
mkdir -p "$(dirname "$TEST_WS")"

echo "== bootstrap =="
"$GW" bootstrap --topic "gw smoke test" --workspace "$TEST_WS" --repo-root "$REPO_ROOT"

echo
echo "== scan (narrated) =="
# Narrated scan calls a real model per stale entity; a nonzero exit here just
# means some entities hit entity_errors (e.g. an unparseable model response) —
# expected on a first run against a big repo, not a script bug. Keep going so
# lint/stats still run.
"$GW" scan --workspace "$TEST_WS" --json || echo "(scan reported entity_errors — see above; continuing)"

echo
echo "== entities written =="
find "$TEST_WS/wiki/entities" -maxdepth 1 -name '*.md' 2>/dev/null | sort

echo
echo "== lint =="
"$GW" wiki lint --workspace "$TEST_WS"

echo
echo "== stats =="
"$GW" wiki stats --workspace "$TEST_WS" --top 10

echo
echo "workspace left at: $TEST_WS"
