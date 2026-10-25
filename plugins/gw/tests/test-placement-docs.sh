#!/usr/bin/env bash
# Contract: shipped skill text describes computed placement (gw work place) and names no retired placement surface.
set -uo pipefail
PLUGIN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FAILURES=0
RETIRED=(record-placement prepare-workspace record-baseline merge-workspace no-infer-worktree prepare-reader settle-placement record-preparation repo_stamps workspace_preparations workspace-pending pin-detached fork-child create-top-level new-top-level "Workspace content root" '`_workspace`' "--repo _workspace" "orca worktree create" "orca worktree rm" "git worktree add" "git worktree remove" "git worktree move" "git worktree list" "frontmatter names worktree")
for t in "${RETIRED[@]}"; do
    hits="$(grep -rnF --exclude-dir=tests -e "$t" "$PLUGIN_ROOT" 2>/dev/null)"
    if [ -n "$hits" ]; then
        echo "  [FAIL] retired term still present: $t"
        echo "$hits" | cut -c1-160 | sed 's/^/         /'
        FAILURES=$((FAILURES + 1))
    else
        echo "  [PASS] absent: $t"
    fi
done
need() {  # need <file under plugin> <literal>
    if grep -qF -- "$2" "$PLUGIN_ROOT/$1"; then
        echo "  [PASS] $1 has: $2"
    else
        echo "  [FAIL] $1 lacks: $2"
        FAILURES=$((FAILURES + 1))
    fi
}
for lit in 'gw work place <work-path> --ensure --json' 'gw work place <work-path> --start --json' 'Code root:' 'placement: null' 'refresh-conflict' 'gw work archive'; do
    need skills/workflow/SKILL.md "$lit"
done
RIDERS="$PLUGIN_ROOT/skills/workflow/references/brief-riders.md"
rider_block() {  # rider_block <skill name>: the **Rider.** block of one rider section
    awk -v h="## Rider: $1" '
        $0 == h { insec = 1; next }
        insec && /^## Rider:/ { exit }
        insec && /^\*\*Rider\.\*\*/ { inblock = 1 }
        insec && inblock && /^---$/ { exit }
        insec && inblock { print }
    ' "$RIDERS"
}
SDD="$(rider_block subagent-driven-development)"
TDD="$(rider_block test-driven-development)"
if [ -n "$SDD" ] && [ "$SDD" = "$TDD" ]; then
    echo "  [PASS] execute riders' Rider blocks are identical"
else
    echo "  [FAIL] execute riders' Rider blocks differ or are empty"
    FAILURES=$((FAILURES + 1))
fi
for lit in 'Code root:' "already starts in the item's worktree"; do
    if printf '%s' "$SDD" | grep -qF -- "$lit"; then
        echo "  [PASS] execute rider has: $lit"
    else
        echo "  [FAIL] execute rider lacks: $lit"
        FAILURES=$((FAILURES + 1))
    fi
done
need skills/auto-drive/SKILL.md 'gw work place'
need skills/auto-drive/SKILL.md ' --prune --json'
need skills/auto-drive/SKILL.md 'references/finish-cleanup.md'
need skills/auto-drive/SKILL.md 'gw work archive'
need skills/finishing-relay/SKILL.md 'target_worktree'
need skills/finishing-relay/references/finish-cleanup.md 'deferred'
[ "$FAILURES" -eq 0 ] || { echo "$FAILURES failure(s)"; exit 1; }
