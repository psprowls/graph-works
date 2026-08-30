#!/usr/bin/env bash
# Guards against plugins/graph-works/ docs re-describing the pre-OKF
# workspace layout (wiki/, raw/, entities/, knowledge/) that gw bootstrap no
# longer builds. See work/tech-debt-plugin-docs-layout-claims.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

FAILURES=0

pass() {
    echo "  [PASS] $1"
}

fail() {
    echo "  [FAIL] $1"
    FAILURES=$((FAILURES + 1))
}

# File-level allowlist: files that legitimately contain a denylisted string
# for a reason unrelated to layout-claim staleness. Extend this list ONLY
# with a comment explaining why the match is not drift.
ALLOWLIST=(
    "RELEASE-NOTES.md"                        # vendored upstream history
    "hooks/skill-doc-routing"                 # raw/ describes the donor CLI, for contrast, not this repo's layout
    "tests/hooks/test-skill-doc-routing.sh"    # raw/ + wiki/<work-path>/references/ are EXPECT_NOT_CONTAINS fixture strings
    "tests/test-doc-layout-claims.sh"         # this file's own denylist-pattern definition and explanatory comments necessarily contain the literal denylisted substrings — not drift
)

is_allowlisted() {
    local rel="$1" entry
    for entry in "${ALLOWLIST[@]}"; do
        [[ "$rel" == "$entry" || "$rel" == */"$entry" ]] && return 0
    done
    return 1
}

DENYLIST_PATTERN='workspace>/wiki|repo>/graph-works|wiki/entities|knowledge/|raw/|(^|[^a-zA-Z_.>/-])wiki/[a-z]'

echo "doc-layout-claims guard test"

while IFS= read -r -d '' file; do
    rel="${file#"$PLUGIN_ROOT"/}"
    is_allowlisted "$rel" && continue
    hits="$(grep -nE "$DENYLIST_PATTERN" "$file" || true)"
    if [[ -n "$hits" ]]; then
        fail "$rel carries a stale layout claim"
        echo "$hits" | sed 's/^/      /'
    fi
done < <(find "$PLUGIN_ROOT" -type f -not -path '*/node_modules/*' -not -path '*/.git/*' -print0)

if [[ "$FAILURES" -eq 0 ]]; then
    pass "no stale layout claims outside the allowlist"
fi

if [[ "$FAILURES" -gt 0 ]]; then
    echo "STATUS: FAILED ($FAILURES failure(s))"
    exit 1
fi

echo "STATUS: PASSED"
