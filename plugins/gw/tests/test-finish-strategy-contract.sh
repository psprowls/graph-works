#!/usr/bin/env bash
# Guards the finish merge-strategy contract (work/epic-pipeline-gate-integrity/
# children/feature-finish-merge-strategy): code repositories integrate through
# `gw work integrate` with a validated strategy token, and unverifiable
# evidence routes to `gw work accept-integration`, never a hand advance.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
FAILURES=0

pass() { echo "  [PASS] $1"; }
fail() { echo "  [FAIL] $1"; FAILURES=$((FAILURES + 1)); }

assert_contains() {
    local rel="$1" needle="$2" description="$3"
    if grep -qF -- "$needle" "$PLUGIN_ROOT/$rel"; then pass "$description"; else
        fail "$description"; echo "      missing in $rel: $needle"; fi
}

assert_absent() {
    local rel="$1" needle="$2" description="$3"
    if grep -qF -- "$needle" "$PLUGIN_ROOT/$rel"; then
        fail "$description"; echo "      still in $rel: $needle"; else pass "$description"; fi
}

RELAY=skills/finishing-relay/SKILL.md
WF=skills/workflow/SKILL.md
RIDER=skills/workflow/references/brief-riders.md
AUTO=skills/auto-drive/SKILL.md

echo "--- finishing-relay"
assert_contains "$RELAY" '--option "squash=Squash into one commit on the target branch"' "R3 offers squash"
assert_contains "$RELAY" '--option "merge=Merge commit (--no-ff) into the target branch"' "R3 offers merge as a merge commit"
assert_contains "$RELAY" '--option "ff=Fast-forward the target branch (refuses if it diverged)"' "R3 offers ff"
assert_contains "$RELAY" "listed first" "R3 lists the default strategy first"
assert_contains "$RELAY" 'gw work integrate <work-path> --repo <this target'"'"'s repository> --strategy <choice> --apply --json' "R4 integrates code targets through gw"
assert_absent "$RELAY" 'git -C <target worktree path from R2> merge' "R4 no longer runs git merge for code targets"
assert_contains "$RELAY" "Never run \`git merge\` for a code repository" "relay forbids worker-run code merges"
assert_contains "$RELAY" "gw work accept-integration" "relay routes unverifiable evidence to accept-integration"
assert_absent "$RELAY" "ancestry-preserving integration" "relay drops the ancestry-only paragraph"
assert_contains "$RELAY" "drop \`squash\`, \`merge\` and \`ff\`" "detached targets drop all three strategy tokens"

echo "--- workflow and attended rider"
assert_absent "$WF" "ancestry-preserving integration" "workflow drops the ancestry-only paragraph"
assert_contains "$WF" "gw work integrate" "workflow names gw work integrate"
assert_contains "$WF" "gw work accept-integration" "workflow names accept-integration"
assert_absent "$RIDER" "requires ancestry-preserving fast-forward or merge commits" "rider drops the ancestry-only sentence"
assert_contains "$RIDER" "gw work integrate <work-path> --repo <name> --strategy <choice> --apply --json" "rider integrates through gw"
assert_contains "$RIDER" "never with a hand \`gw work advance\`" "rider forbids the hand advance"
assert_contains "$RIDER" "Use \`merge\` after a successful integration by any strategy" "outcome line keeps merge for every strategy"

echo "--- auto-drive"
assert_contains "$AUTO" "default_strategy" "auto-merge answers the dispatch's default strategy"
assert_contains "$AUTO" "--choice <token> --by policy:auto-merge" "auto-merge records the strategy token"

echo
if [[ $FAILURES -gt 0 ]]; then echo "FAILED: $FAILURES"; exit 1; fi
echo "all finish-strategy contract checks passed"
