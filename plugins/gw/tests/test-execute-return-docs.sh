#!/usr/bin/env bash
# Guards the execute-return instructions: report fresh scope, then explicitly
# reroute a settled execute Task before supervised redispatch.
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

assert_absent() {
    local file="$1" needle="$2" description="$3"
    if grep -qF -- "$needle" "$file"; then
        fail "$description"; echo "      present in $file: $needle"; else pass "$description"; fi
}

AUTO="$PLUGIN_ROOT/skills/auto-drive/SKILL.md"
WORKFLOW="$PLUGIN_ROOT/skills/workflow/SKILL.md"
PIPELINE="$REPO_ROOT/packages/graph-works-core/src/graph_works_core/workspace/pipeline.py"

echo "--- execute return"
assert_contains "$AUTO" '--return-scope' "send-back can name new plan tasks and review findings"
assert_contains "$AUTO" 'gw work reroute <key> --run <run_id> --reason "execute return <return-id>"' "settled execute Tasks require explicit reroute with Run identity"
assert_absent "$AUTO" 'redispatches `execute` naturally' "phase change does not imply redispatch"
assert_contains "$AUTO" 'A phase change alone never relaunches execute.' "send-back states the launch boundary"
assert_contains "$AUTO" 'return-scope-required' "scope refusal asks for scope"
assert_contains "$AUTO" 'return-pending' "pending return requires inspection"
assert_contains "$PIPELINE" 'Returned scope' "EXECUTE_TAIL requires returned-scope evidence"
assert_contains "$WORKFLOW" 'carried_context.slots.execute_return' "workflow recognizes returned execute"
for code in return-evidence-missing return-evidence-stale return-evidence-incomplete return-plan-changed return-metadata-invalid; do
    assert_contains "$WORKFLOW" "$code" "workflow names non-bypassable $code"
done
assert_contains "$WORKFLOW" 'Fix the report or return again; never `--skip-gate`.' "returned evidence cannot be bypassed"

if [ "$FAILURES" -gt 0 ]; then echo "FAILED: $FAILURES"; exit 1; fi
echo "All execute-return doc checks passed."
