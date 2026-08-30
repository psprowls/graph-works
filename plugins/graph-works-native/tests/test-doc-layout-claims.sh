#!/usr/bin/env bash
# Guards against plugins/graph-works-native/ docs re-describing the pre-OKF
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

# Positive assertions: pointers that must not go stale. The denylist above
# catches a doc describing a layout that no longer exists; these catch the
# opposite — a doc or a table naming a destination that does not exist yet.
assert_contains() {
    local rel="$1" needle="$2" description="$3"
    if [[ ! -f "$PLUGIN_ROOT/$rel" ]]; then
        fail "$description"
        echo "      missing file: $rel"
        return
    fi
    if grep -Fq -- "$needle" "$PLUGIN_ROOT/$rel"; then
        pass "$description"
    else
        fail "$description"
        echo "      $rel does not contain: $needle"
    fi
}

assert_skill_dir() {
    local name="$1" description="$2"
    if [[ -f "$PLUGIN_ROOT/skills/$name/SKILL.md" ]]; then
        pass "$description"
    else
        fail "$description"
        echo "      missing: skills/$name/SKILL.md"
    fi
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

# --- Rider-presence assertion (work/epic-unforked-plugin-skill-dispatch/
# children/feature-move-to-brief-behaviors, D-004) ---
#
# The brief riders are instruction, and nothing here measures whether a stage
# skill obeys them — epic decision 3 keeps that risk named, not mitigated. What
# this does catch is the cheaper failure: a rider silently going missing, or the
# lookup table in SKILL.md drifting out of sync with the sidecar, which would
# make the composed brief quietly stop carrying a behavior with no symptom until
# a stage misbehaves.

RIDER_SKILL="$PLUGIN_ROOT/skills/workflow/SKILL.md"
RIDER_DOC="$PLUGIN_ROOT/skills/workflow/references/brief-riders.md"

assert_riders_match() {
    local table_names sidecar_names only_table only_sidecar

    if [[ ! -f "$RIDER_SKILL" ]]; then
        fail "rider table: skills/workflow/SKILL.md exists"
        return
    fi
    if [[ ! -f "$RIDER_DOC" ]]; then
        fail "rider table: skills/workflow/references/brief-riders.md exists"
        return
    fi

    # Table rows live between the fence markers; a row is  | `name` | ... |
    table_names="$(awk '
        /^<!-- rider-table:start -->$/ { inside = 1; next }
        /^<!-- rider-table:end -->$/   { inside = 0; next }
        inside && match($0, /^\| `[^`]+`/) {
            row = substr($0, RSTART, RLENGTH)
            gsub(/^\| `/, "", row); gsub(/`$/, "", row)
            print row
        }
    ' "$RIDER_SKILL" | sort -u)"

    sidecar_names="$(sed -n 's/^## Rider: \(.*\)$/\1/p' "$RIDER_DOC" | sort -u)"

    if [[ -z "$table_names" ]]; then
        fail "rider table: SKILL.md names at least one rider"
        echo "      no rows found between the rider-table fence markers"
        return
    fi
    if [[ -z "$sidecar_names" ]]; then
        fail "rider table: brief-riders.md defines at least one rider"
        echo "      no '## Rider: <skill-name>' headings found"
        return
    fi

    only_table="$(comm -23 <(printf '%s\n' "$table_names") <(printf '%s\n' "$sidecar_names"))"
    only_sidecar="$(comm -13 <(printf '%s\n' "$table_names") <(printf '%s\n' "$sidecar_names"))"

    if [[ -n "$only_table" ]]; then
        fail "every skill in SKILL.md's rider table has a section in brief-riders.md"
        printf '%s\n' "$only_table" | sed 's/^/      missing rider: /'
    else
        pass "every skill in SKILL.md's rider table has a section in brief-riders.md"
    fi

    if [[ -n "$only_sidecar" ]]; then
        fail "every section in brief-riders.md is named by SKILL.md's rider table"
        printf '%s\n' "$only_sidecar" | sed 's/^/      orphaned rider: /'
    else
        pass "every section in brief-riders.md is named by SKILL.md's rider table"
    fi
}

assert_riders_match

# --- pointer-presence assertions -----------------------------------------
# The packaged pipeline table (graph_works_core.workspace.pipeline) dispatches
# the `epic-design` variant to a skill of this name. A table entry pointing at
# a directory that does not exist fails only at dispatch time, in a worker
# session, with no earlier signal — so assert the cheap half here.
assert_skill_dir "epic-design" \
    "the pipeline table's epic-design skill directory exists"

if [[ "$FAILURES" -gt 0 ]]; then
    echo "STATUS: FAILED ($FAILURES failure(s))"
    exit 1
fi

echo "STATUS: PASSED"
