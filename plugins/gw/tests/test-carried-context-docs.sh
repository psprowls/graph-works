#!/usr/bin/env bash
# Contract: /gw:workflow renders carried_context generically (feature-carried-context-channel, ledger D-003).
set -uo pipefail
PLUGIN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SKILL=skills/workflow/SKILL.md
FAILURES=0
need() {  # need <file> <literal>
    if grep -qF -- "$2" "$PLUGIN_ROOT/$1"; then echo "  [PASS] $1: $2"; else echo "  [FAIL] $1 lacks: $2"; FAILURES=$((FAILURES + 1)); fi
}
refuse() {  # refuse <file> <literal>
    if grep -qF -- "$2" "$PLUGIN_ROOT/$1"; then echo "  [FAIL] $1 names: $2"; FAILURES=$((FAILURES + 1)); else echo "  [PASS] $1 does not name: $2"; fi
}
need "$SKILL" '`carried_context`'
need "$SKILL" '## Carried context'
need "$SKILL" '### <title>'
need "$SKILL" 'Omit the block when no slot has non-empty `lines`.'
need "$SKILL" 'Surface every slot warning and every `carried_context.warnings` entry to the user as a plain note.'
refuse "$SKILL" 'landed_since'
refuse "$SKILL" 'finish_obligations'
[ "$FAILURES" -eq 0 ] || { echo "$FAILURES failure(s)"; exit 1; }
