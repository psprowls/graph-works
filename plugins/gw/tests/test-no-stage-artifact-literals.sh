#!/usr/bin/env bash
# Stage-artifact filenames are configured (`pipeline.artifacts.<stage>.file`),
# so no plugin surface may spell one: skills, riders and hooks take the path
# from `gw work next --json` (`artifact.path`, `artifacts.<stage>.path`).
# Scope is every file under plugins/gw except tests/ — no allowlist
# (epic-configurable-pipeline-path ledger D-022). Non-stage managed artifacts
# (00-decisions.md, 03-execute-results.md, …) are not configurable and stay.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_ROOT="${PLUGIN_ROOT_OVERRIDE:-$(cd "$SCRIPT_DIR/.." && pwd)}"
PATTERN='01-design\.md|02-plan\.md|03-execute-coverage\.md'
HITS=0

while IFS= read -r -d '' file; do
    rel="${file#"$PLUGIN_ROOT"/}"
    while IFS= read -r hit; do
        [[ -z "$hit" ]] && continue
        echo "  [FAIL] $rel:$hit"
        HITS=$((HITS + 1))
    done < <(grep -nE -- "$PATTERN" "$file" || true)
done < <(find "$PLUGIN_ROOT" -type f \
    -not -path "$PLUGIN_ROOT/tests/*" \
    -not -path '*/node_modules/*' \
    -not -path '*/.git/*' -print0)

if [[ "$HITS" -gt 0 ]]; then
    echo "STATUS: FAILED ($HITS hit(s)) — name the stage artifact, or read artifacts.<stage>.path from gw work next --json"
    exit 1
fi
echo "  [PASS] no stage-artifact filename literal under plugins/gw outside tests/"
echo "STATUS: PASSED"
