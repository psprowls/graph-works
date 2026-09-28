#!/usr/bin/env bash
# Contract: the skills relay workspace-branch placement (feature-workspace-branch-placement).
set -uo pipefail
PLUGIN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FAILURES=0
need() {  # need <file> <literal>
    if grep -qF -- "$2" "$PLUGIN_ROOT/$1"; then echo "  [PASS] $1: $2"; else echo "  [FAIL] $1 lacks: $2"; FAILURES=$((FAILURES + 1)); fi
}
need skills/auto-drive/SKILL.md 'workspace_preparations[]'
need skills/auto-drive/SKILL.md 'gw work prepare-workspace <owner_path> --apply'
need skills/auto-drive/SKILL.md '`workspace-pending`'
need skills/auto-drive/SKILL.md '--repo _workspace'
need skills/auto-drive/SKILL.md '`read-only-owner`'
need skills/workflow/SKILL.md 'gw work prepare-workspace <work-path> --apply'
need skills/workflow/SKILL.md 'Workspace content root:'
need skills/workflow/SKILL.md 'gw work merge-workspace <work-path> --apply'
need skills/finishing-relay/SKILL.md 'gw work merge-workspace <work-path> --apply'
need skills/finishing-relay/SKILL.md 'record --repo _workspace'
[ "$FAILURES" -eq 0 ] || { echo "$FAILURES failure(s)"; exit 1; }
