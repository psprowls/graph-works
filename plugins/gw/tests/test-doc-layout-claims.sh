#!/usr/bin/env bash
# Guards against plugins/gw/ docs re-describing the pre-OKF
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

# hooks/skill-doc-routing's auto-file clause points a standalone brainstorming
# session at this section by name. A renamed or deleted section leaves the hook
# naming a destination that is not there, and the failure would only show up as
# a session quietly not filing anything.
assert_contains "skills/file/SKILL.md" "## Auto-file mode (hook-triggered)" \
    "the file skill carries the section the hook's auto-file clause names"

# The grace-period protocol doc is referenced by GRACE_PERIOD_TAIL and must
# document the key CLI calls that workers use to implement it.
assert_contains "skills/auto-drive/references/grace-period-protocol.md" "gw work decision add" \
    "grace-period-protocol.md must document the --hold park CLI call"

assert_contains "skills/auto-drive/references/grace-period-protocol.md" "orca orchestration ask --resume" \
    "grace-period-protocol.md must document the --resume re-arm call"

grep -q "grace-period-protocol.md" "$PLUGIN_ROOT/skills/finishing-relay/SKILL.md" \
  || fail "finishing-relay/SKILL.md R3 must point at the shared grace-period protocol"

# --- park/resume seam assertions -----------------------------------------
# These four pin seams that the whole grace-period feature hangs off and that
# nothing else can catch: each one is prose that has to agree with a mechanism
# living somewhere else, and a silent edit to either side breaks resume with no
# test failure anywhere.

# `launch --retry-of` reuses the ORIGINAL frozen Task spec -- `worker-start`'s
# --task and --spec are mutually exclusive -- so `resume-spec`'s augmented
# prompt never reaches the resumed worker. The `send --to dispatch:<new id>`
# follow-up is the only thing that actually delivers the answer; without it the
# resumed worker re-asks the question this feature exists to stop it re-asking.
assert_contains "skills/auto-drive/SKILL.md" "orca orchestration send --to dispatch:<new dispatch_id>" \
    "auto-drive §2.6 sends the resume context to the newly-launched dispatch"

# A parked item's Task still exists, so §2.6's ordinary dedupe rule would
# filter it out before the resume amendment ever ran. The exemption has to be
# written down, keyed on the classifier action the helper actually emits.
assert_contains "skills/auto-drive/SKILL.md" '- `parked`:' \
    "auto-drive §2.1 documents the parked classification"
assert_contains "skills/auto-drive/references/launch-worker.py" '"parked"' \
    "launch-worker.py's classifier emits the parked action §2.1 documents"

# D-004's "the coordinator parks itself when nothing else can proceed": without
# an explicit exit the loop spins on `check --wait` forever with nothing live.
assert_contains "skills/auto-drive/SKILL.md" "### 2.6.1 Self-park" \
    "auto-drive has a main-cycle exit for a run with only parked work left"

# A dispatch key is one-way. `--display-name "<work-path> · <phase>"` is the
# only durable key-to-path carrier, and both §2.5.2 and §4.3 depend on it.
assert_contains "skills/auto-drive/SKILL.md" 'split it on the first ` · `' \
    "auto-drive resolves a key back to a work path through the Task display name"

# `launch --recovery-placement` opens its argument as a FILE PATH; an inline
# JSON literal fails. §2.6 must hand it the temp file, not a quoted array.
if grep -Fq -- "--recovery-placement '" "$PLUGIN_ROOT/skills/auto-drive/SKILL.md"; then
    fail "auto-drive passes --recovery-placement a file path, never an inline JSON literal"
else
    pass "auto-drive passes --recovery-placement a file path, never an inline JSON literal"
fi

if [[ "$FAILURES" -gt 0 ]]; then
    echo "STATUS: FAILED ($FAILURES failure(s))"
    exit 1
fi

echo "STATUS: PASSED"
