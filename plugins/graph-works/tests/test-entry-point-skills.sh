#!/usr/bin/env bash
# Guards the one-surface invariant: every graph-works entry point is a skill
# under skills/, because skills/ is the only directory every harness manifest
# declares. commands/ and agents/ did not ship to Codex; that is the whole
# reason they no longer exist. See work/tech-debt-codex-slash-command-gap.
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

assert_dir_absent() {
    local rel="$1" description="$2"
    if [[ -d "$PLUGIN_ROOT/$rel" ]]; then
        fail "$description"
        echo "      still present: $rel"
    else
        pass "$description"
    fi
}

assert_skill() {
    local name="$1" description="$2"
    local skill_file="$PLUGIN_ROOT/skills/$name/SKILL.md"
    local declared

    if [[ ! -f "$skill_file" ]]; then
        fail "$description"
        echo "      missing: skills/$name/SKILL.md"
        return
    fi

    declared="$(awk '
        NR == 1 && $0 != "---" { exit }
        NR > 1 && $0 == "---" { exit }
        NR > 1 && sub(/^name:[[:space:]]*/, "") { print; exit }
    ' "$skill_file")"

    if [[ "$declared" != "$name" ]]; then
        fail "$description"
        echo "      frontmatter name: '$declared' (expected '$name')"
        return
    fi

    pass "$description"
}

# Files that legitimately carry a retired string: vendored history, and the
# historical plan/spec archive under docs/. Extend ONLY with a comment.
SEARCH_EXCLUDES=(
    -not -path '*/.git/*'
    -not -path '*/node_modules/*'
    -not -path "$PLUGIN_ROOT/RELEASE-NOTES.md"        # vendored upstream history
    -not -path "$PLUGIN_ROOT/docs/plans/*"            # dated historical plans, not live docs
    -not -path "$PLUGIN_ROOT/docs/superpowers/*"      # dated historical plans/specs, not live docs
    -not -path "$SCRIPT_DIR/test-entry-point-skills.sh" # this file defines the patterns
)

assert_no_match() {
    local pattern="$1" description="$2"
    local hits

    hits="$(find "$PLUGIN_ROOT" -type f "${SEARCH_EXCLUDES[@]}" -print0 |
        xargs -0 grep -nE -- "$pattern" 2>/dev/null || true)"

    if [[ -n "$hits" ]]; then
        fail "$description"
        printf '%s\n' "$hits" | sed "s#^$PLUGIN_ROOT/#      #"
    else
        pass "$description"
    fi
}

echo "entry-point-skills guard test"

assert_dir_absent "agents" "agents/ is gone — its four agents are skills"
assert_dir_absent "commands" "commands/ is gone — every entry point is a skill"

for skill in scan ingest query lint file archive log status regen-index proposals onboard; do
    assert_skill "$skill" "entry point '$skill' is a skill"
done

assert_no_match 'agents/(scanner|ingestor|librarian|linter)\.md' \
    "nothing points at a deleted agent file"

assert_no_match '/graph-works:next' \
    "no /graph-works:next reference survives — the skill is named workflow"

assert_no_match 'graph-works:code-reviewer' \
    "no reference to a code-reviewer agent this plugin does not define"

if [[ "$FAILURES" -gt 0 ]]; then
    echo "STATUS: FAILED ($FAILURES failure(s))"
    exit 1
fi

echo "STATUS: PASSED"
