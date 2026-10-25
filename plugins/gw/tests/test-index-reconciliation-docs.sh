#!/usr/bin/env bash
# Contract: auto-drive wrap-up reconciles the work-lane indexes between the leftover sweep and the dirty-workspace check.
set -uo pipefail
PLUGIN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FAILURES=0
FILE="$PLUGIN_ROOT/skills/auto-drive/SKILL.md"
need() {  # need <file> <literal>
    if grep -qF -- "$2" "$PLUGIN_ROOT/$1"; then echo "  [PASS] $1: $2"; else echo "  [FAIL] $1 lacks: $2"; FAILURES=$((FAILURES + 1)); fi
}
need skills/auto-drive/SKILL.md 'gw work regen-index --json'
sweep=$(grep -n 'Sweep leftovers' "$FILE" | tail -1 | cut -d: -f1)
regen=$(grep -n 'gw work regen-index --json' "$FILE" | head -1 | cut -d: -f1)
dirty=$(grep -n 'Dirty-workspace check' "$FILE" | head -1 | cut -d: -f1)
if [ -n "$sweep" ] && [ -n "$regen" ] && [ -n "$dirty" ] && [ "$sweep" -lt "$regen" ] && [ "$regen" -lt "$dirty" ]; then
    echo "  [PASS] regen-index step sits between Sweep leftovers and Dirty-workspace check"
else
    echo "  [FAIL] regen-index step not between Sweep leftovers ($sweep) and Dirty-workspace check ($dirty): $regen"
    FAILURES=$((FAILURES + 1))
fi
[ "$FAILURES" -eq 0 ] || { echo "$FAILURES failure(s)"; exit 1; }
