#!/usr/bin/env bash
# Guards the finish-obligations contract (work/epic-decision-and-context-continuity/
# children/feature-finish-obligations-and-caveats): execute records deferred steps,
# plans mark them, the relay drafts its question from the diff and lists obligations,
# and auto-drive's accept path writes nothing.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$PLUGIN_ROOT/../.." && pwd)"
FAILURES=0

pass() { echo "  [PASS] $1"; }
fail() { echo "  [FAIL] $1"; FAILURES=$((FAILURES + 1)); }

assert_contains() {
    local file="$1" needle="$2" description="$3"
    if grep -qF -- "$needle" "$file"; then pass "$description"; else
        fail "$description"; echo "      missing in $file: $needle"; fi
}

RELAY="$PLUGIN_ROOT/skills/finishing-relay/SKILL.md"
WF="$PLUGIN_ROOT/skills/workflow/SKILL.md"
RIDER="$PLUGIN_ROOT/skills/workflow/references/brief-riders.md"
AUTO="$PLUGIN_ROOT/skills/auto-drive/SKILL.md"
PIPELINE="$REPO_ROOT/packages/graph-works-core/src/graph_works_core/workspace/pipeline.py"

DEFER_WF='When the plan marks a step `Deferred to finish`, record it with `gw work obligation add <work-path> --text "<the step>" --apply` instead of doing it or leaving it in prose.'
DEFER_TAIL='"When the plan marks a step `Deferred to finish`, record it with "'

echo "--- execute deferral"
assert_contains "$WF" "$DEFER_WF" "workflow execute bullet carries the deferral sentence"
assert_contains "$PIPELINE" "$DEFER_TAIL" "EXECUTE_TAIL carries the same deferral sentence"

echo "--- writing-plans rider"
assert_contains "$RIDER" "— Deferred to finish" "rider tells plans to head deferred tasks"

echo "--- finishing-relay"
assert_contains "$RELAY" 'git diff --stat <merge-base>..HEAD' "R2 collects the diffstat"
assert_contains "$RELAY" '## What the diff does' "R3 question opens with the diff section"
assert_contains "$RELAY" '## Finish obligations' "R3 question lists finish obligations"
assert_contains "$RELAY" 'carried_context.slots.finish_obligations.lines' "R3 names where obligations are read"
assert_contains "$RELAY" 'finish obligation(s)' "R3 summary counts obligations"

echo "--- auto-drive"
assert_contains "$AUTO" 'finish_obligations' "Accept anyway notes the recorded obligations"

if [ "$FAILURES" -gt 0 ]; then echo "FAILED: $FAILURES"; exit 1; fi
echo "All finish-obligations doc checks passed."
