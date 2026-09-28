#!/usr/bin/env bash
# Guards the finish-time cleanup contract (work/epic-workspace-write-ownership/
# children/feature-auto-drive-wrap-up-cleanup): cleanup runs only after a
# resolved advance, only through the core plan, and never forces anything.
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

# The regex form of assert_contains, for ordering and co-occurrence checks on one line.
assert_matches() {
    local rel="$1" pattern="$2" description="$3"
    if grep -qE -- "$pattern" "$PLUGIN_ROOT/$rel"; then pass "$description"; else
        fail "$description"; echo "      no line in $rel matches: $pattern"; fi
}

CONTRACT=skills/finishing-relay/references/finish-cleanup.md
RELAY=skills/finishing-relay/SKILL.md

echo "--- execution contract"
assert_contains "$CONTRACT" "never \`--force\`" "contract forbids --force"
assert_contains "$CONTRACT" "never \`-D\`" "contract forbids branch -D"
assert_contains "$CONTRACT" "orca worktree rm --worktree path:<worktree> --json" "contract uses orca worktree rm for Orca-managed rows"
assert_contains "$CONTRACT" "git -C <repo> branch -d <branch>" "contract deletes branches with -d"
assert_contains "$CONTRACT" "never retried" "contract never retries a refusal"
assert_absent "$CONTRACT" "--run-hooks" "contract passes no hooks flags"

echo "--- finishing-relay"
assert_matches "$RELAY" "finish-receipt\.py cleanup .*--runner-cwd" "relay runs the cleanup helper with --runner-cwd"
assert_contains "$RELAY" "Cleanup (\`merge\` only, after the successful advance)" "relay cleanup is merge-only and after the advance"
assert_contains "$RELAY" "\`pr\` / \`hold\` / \`discard\` run no cleanup" "held outcomes run no cleanup"
assert_contains "$RELAY" "never its own worktree" "ownership rule: relay never removes its own worktree"
assert_contains "$RELAY" "the coordinator removes the relay's own worktree" "ownership rule: coordinator removes the deferred row"
assert_absent "$RELAY" "never removes worktrees and never deletes branches" "old ownership rule is gone"
assert_absent "$RELAY" "Preserve source branches and worktrees until final verification." "old preservation sentence is gone"
assert_absent "$RELAY" "Add one line per removed, skipped and deferred row (path and" "worker_done body no longer takes appended per-row lines"
assert_contains "$RELAY" '.gw/cache/auto-drive/<run_id>/finish-cleanup/<dispatch_id>.md' "relay writes a run-specific report outside tracked work content"
assert_contains "$RELAY" 'mkdir -p "$(dirname "$cleanup_report")"' "relay explicitly creates the report directory"
assert_absent "$RELAY" "references/04-finish-cleanup.md" "relay does not dirty tracked work references"
assert_contains "$RELAY" "\`--report-path\` set to the file above" "relay's worker_done points at the cleanup report via --report-path"
assert_contains "$RELAY" "required exactly-three-sentence" "relay's worker_done body stays exactly three sentences"

AUTO=skills/auto-drive/SKILL.md
echo "--- auto-drive"
assert_contains "$AUTO" "### Finish cleanup (finish dispatches only)" "Success branch has a finish-cleanup step"
assert_contains "$AUTO" 'run **Finish cleanup** (below) only when the release classifier action is' "Success cleanup follows release classification"
assert_contains "$AUTO" "only when \`gw work next <path> --json\` reports \`work_status: resolved\`" "finish cleanup only for a resolved finish"
assert_contains "$AUTO" 'classifier action is `done` (`released` or `already_released`)' "Success cleanup requires confirmed terminal release"
assert_contains "$AUTO" 'For every other classifier action, skip Finish cleanup' "all unreleased states skip cleanup"
assert_contains "$AUTO" "including the relay's \`deferred\` row" "coordinator removes the relay's deferred worktree"
assert_contains "$AUTO" 'the coordinator cwd is inside an item worktree, its row comes back `deferred`' "coordinator cwd may defer a row"
assert_contains "$AUTO" "under one confirmation" "wrap-up sweep has one confirmation"
assert_contains "$AUTO" "orca terminal close --terminal <handle>" "wrap-up closes retained terminals"
assert_contains "$AUTO" 'positively verify each terminal is closed before executing its rows' "wrap-up verifies closes before removal"
assert_contains "$AUTO" 'If a terminal close fails or cannot be verified, skip its affected rows' "failed close preserves affected rows"
assert_contains "$AUTO" 'rev-list --count "<target_branch>@{upstream}..<target_branch>"' "wrap-up checks unpushed target branches using the named branch's own upstream"
assert_contains "$AUTO" 'print the command failure verbatim' "unpushed warning reports command failures truthfully"
assert_contains "$AUTO" "Wrap-up never pushes." "wrap-up never pushes"
assert_contains "$AUTO" "**Unresolved-release items:**" "wrap-up separates unresolved-release items from leftover rows"
assert_contains "$AUTO" "never eligible for automatic removal" "unresolved-release items are never auto-removable"
assert_matches "$AUTO" "not.*Unresolved-release items.*own dispatch" "leftover rows collection is gated on release evidence"
assert_contains "$AUTO" "**Unresolved-release items never execute, under either answer**" "wrap-up confirmation never executes unresolved-release items"

WF=skills/workflow/SKILL.md
echo "--- workflow"
assert_contains "$WF" "**Finish cleanup (attended).**" "step 5 has a cleanup paragraph"
assert_matches "$WF" "finish-receipt\.py cleanup .*--runner-cwd" "step 5 runs the cleanup helper"
assert_contains "$WF" "remove it from another directory with \`git worktree remove <path>\` (or \`orca worktree rm\`)" "step 5 names a deferred own-cwd worktree"
assert_contains "$WF" "Held outcomes run no cleanup" "held outcomes skip cleanup"
assert_absent "$WF" "Preserve source branches and worktrees until final verification." "old preservation sentence is gone"

echo
if [[ $FAILURES -gt 0 ]]; then echo "FAILED: $FAILURES"; exit 1; fi
echo "all finish-cleanup contract checks passed"
