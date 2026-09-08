#!/usr/bin/env bash
# Resolve the graph-works workspace directory — pure bash, no Python.
# Usage: resolve-workspace.sh [start-dir]
#
# Echoes the resolved absolute workspace path to stdout and exits 0.
# Echoes NOTHING (empty stdout, exit 0) when no workspace resolves —
# callers decide their own fallback; this helper never invents one.
#
# Resolution chain (mirrors graph_works_core.discovery.resolve()):
#   1. An explicit argument naming a directory that contains workspace.yaml.
#   2. Else $GRAPH_WORKS_DIR set and non-empty -> echo it.
#   3. Else walk up from the given start-dir argument if present, else $PWD,
#      for a git repo root (.git may be a dir, or a file in
#      worktrees/submodules). At the FIRST repo root found: echo
#      <repo>/.works if that directory exists, else echo nothing. Never bind
#      to a parent repo's workspace.
#   4. Else echo nothing.
#
# Kept dependency-free so it works in projects where the graph-works Python
# stack is not installed.

# 1. Explicit argument, when it names a marked workspace.
if [[ -n "${1:-}" && -d "$1" && -f "$1/workspace.yaml" ]]; then
  (cd "$1" && pwd -P)
  exit 0
fi

# 2. Environment variable.
if [[ -n "${GRAPH_WORKS_DIR:-}" ]]; then
  echo "$GRAPH_WORKS_DIR"
  exit 0
fi

# 3. Walk up from the start dir looking for a git repo root.
dir="${1:-$PWD}"
dir="$(cd "$dir" 2>/dev/null && pwd -P)" || exit 0
while [[ -n "$dir" ]]; do
  if [[ -e "$dir/.git" ]]; then
    if [[ -d "$dir/.works" ]]; then
      echo "$dir/.works"
    fi
    exit 0
  fi
  [[ "$dir" == "/" ]] && break
  dir="$(dirname "$dir")"
done

# 4. Nothing resolved.
exit 0
