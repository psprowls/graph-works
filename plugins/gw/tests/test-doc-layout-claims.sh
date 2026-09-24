#!/usr/bin/env bash
# Guards against plugins/gw/ docs re-describing the pre-OKF
# workspace layout (wiki/, raw/, entities/, knowledge/) that gw bootstrap no
# longer builds. See work/tech-debt-plugin-docs-layout-claims.
# Also guards the retired page-templates/ entity-template inventory; see
# work/epic-plugin-cli-contract-docs-hygiene/children/
# tech-debt-doc-layout-denylist-retired-entity-vocabulary.
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
LAYOUT_ALLOWLIST=(
    "RELEASE-NOTES.md"                        # vendored upstream history
    "hooks/skill-doc-routing"                 # raw/ describes the donor CLI, for contrast, not this repo's layout
    "tests/hooks/test-skill-doc-routing.sh"    # raw/ + wiki/<work-path>/references/ are EXPECT_NOT_CONTAINS fixture strings
    "tests/test-doc-layout-claims.sh"         # this file's own denylist-pattern definition and explanatory comments necessarily contain the literal denylisted substrings — not drift
)

# Each sweep owns its exemptions; layout history must not exempt other drift.
# Trailing allowlist arguments work on macOS bash 3.2 (no namerefs).
sweep_denylist() {
    local name="$1" pattern="$2" message="$3"
    shift 3
    local file rel entry hits allowed before="$FAILURES"
    while IFS= read -r -d '' file; do
        rel="${file#"$PLUGIN_ROOT"/}"
        allowed=false
        for entry in "$@"; do
            if [[ "$rel" == "$entry" || "$rel" == */"$entry" ]]; then
                allowed=true
                break
            fi
        done
        "$allowed" && continue
        hits="$(grep -nE -- "$pattern" "$file" || true)"
        if [[ -n "$hits" ]]; then
            fail "$rel $message"
            echo "$hits" | sed 's/^/      /'
        fi
    done < <(find "$PLUGIN_ROOT" -type f -not -path '*/node_modules/*' -not -path '*/.git/*' -print0)
    if [[ "$FAILURES" -eq "$before" ]]; then
        pass "$name: no retired claims outside its allowlist"
    fi
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
RETIRED_DEPENDENCY_SHAPE='load_bearing|category: dependency|package_name|service_name|upstream_url|quirks|provider:|kind: ?package ?\| ?service'
RETIRED_LINT_KEYS='missing_in_vault|orphaned_in_vault|exports_drift|scanner_heading_drift|source_path_drift|guidance_lint_findings'
RETIRED_TEMPLATE_INVENTORY='page-templates|entity-(repository|package|app|agent-plugin|dependency|test-suite)'
RETIRED_LOG_HEADING='##[[:space:]]+\[(YYYY-MM-DD|[0-9]{4}-[0-9]{2}-[0-9]{2})\]'
RETIRED_LOG_GREP='\^## \\\['

echo "doc-layout-claims guard test"

# Each definition necessarily contains its own retired vocabulary.
DEPENDENCY_ALLOWLIST=("tests/test-doc-layout-claims.sh")
LINT_ALLOWLIST=("tests/test-doc-layout-claims.sh")
LOG_ALLOWLIST=("tests/test-doc-layout-claims.sh")
TEMPLATE_ALLOWLIST=("tests/test-doc-layout-claims.sh")

sweep_denylist "layout" "$DENYLIST_PATTERN" \
    "carries a stale layout claim" "${LAYOUT_ALLOWLIST[@]}"
sweep_denylist "dependency" "$RETIRED_DEPENDENCY_SHAPE" \
    "carries a retired dependency-page shape" "${DEPENDENCY_ALLOWLIST[@]}"
sweep_denylist "lint" "$RETIRED_LINT_KEYS" \
    "carries a retired lint payload key" "${LINT_ALLOWLIST[@]}"
sweep_denylist "log" "$RETIRED_LOG_HEADING|$RETIRED_LOG_GREP" \
    "carries a retired log heading or retrieval recipe" "${LOG_ALLOWLIST[@]}"
sweep_denylist "templates" "$RETIRED_TEMPLATE_INVENTORY" \
    "names a retired page-templates/ entity template" "${TEMPLATE_ALLOWLIST[@]}"

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

# Canonical authoring and retrieval must stay documented as well as retiring
# the incompatible shape. The executable recipe is exercised separately.
for reader in "skills/log/SKILL.md" "skills/graph-works/references/wiki-schema.md"; do
    assert_contains "$reader" '## YYYY-MM-DD' "$reader documents ISO day headings"
    assert_contains "$reader" '- **<op>** <title>' "$reader documents list entries"
done
for contract in 'parse_log' '--last' '--op' '--since' 'case-insensitive' 'inclusive' 'outermost'; do
    assert_contains "skills/log/SKILL.md" "$contract" "log skill documents $contract retrieval"
done

# Lint readers must explain the real lane report, not merely omit old fields.
for reader in "skills/lint/SKILL.md" "skills/graph-works/references/lint-workflow.md"; do
    for key in ok mechanical semantic open_proposals errors; do
        assert_contains "$reader" "\`$key\`" "$reader documents lint key $key"
    done
    for code in sync.missing-page render.angle-bracket sections.missing; do
        assert_contains "$reader" "$code" "$reader documents finding $code"
    done
done

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

# Repository selection is owned by item metadata and the planner. Keep the
# prerequisite and placement preview behavior visible to auto-drive readers.
assert_contains "skills/auto-drive/SKILL.md" 'repo: graph-works' \
    "auto-drive illustrates declared repository metadata"
assert_contains "skills/auto-drive/SKILL.md" 'Never launch workers from an error envelope.' \
    "auto-drive stops on repository resolution errors"
assert_contains "skills/auto-drive/SKILL.md" 'For a prelaunch refusal, use a coordinator `AskUserQuestion`' \
    "auto-drive asks for a repository choice before any worker exists"
assert_contains "skills/auto-drive/SKILL.md" 'A placement preview validates repository selection too.' \
    "auto-drive explains placement preview parity"

# Only executable examples are forbidden from passing a manual repository
# selector. Prose can name the bad workaround while explaining the rule.
if command_violations="$(python3 - "$PLUGIN_ROOT/skills/auto-drive/SKILL.md" <<'PY'
import re
import sys
from pathlib import Path

content = Path(sys.argv[1]).read_text(encoding="utf-8")
logical_lines = re.sub(r"\\\r?\n[ \t]*", " ", content).splitlines()
command = re.compile(r"^`?gw work (?:orchestrate|record-placement)\b")
inline = re.compile(r"`(gw work (?:orchestrate|record-placement)\b[^`]*)`")

for number, line in enumerate(logical_lines, 1):
    candidates = [line.strip(), *(match.group(1) for match in inline.finditer(line))]
    for candidate in candidates:
        if command.match(candidate) and "--repo-name" in candidate:
            print(f"line {number}: {candidate}")
            break
PY
)"; then
    if [[ -n "$command_violations" ]]; then
        fail "auto-drive planner and placement examples omit --repo-name"
        printf '%s\n' "$command_violations" | sed 's/^/      /'
    else
        pass "auto-drive planner and placement examples omit --repo-name"
    fi
else
    fail "auto-drive planner and placement examples are inspectable"
fi

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

assert_not_matches() {
    local rel="$1" pattern="$2" description="$3" hits
    if [[ ! -f "$PLUGIN_ROOT/$rel" ]]; then
        fail "$description"
        echo "      missing file: $rel"
        return
    fi
    hits="$(grep -nE -- "$pattern" "$PLUGIN_ROOT/$rel" || true)"
    if [[ -n "$hits" ]]; then
        fail "$description"
        echo "$hits" | sed 's/^/      /'
    else
        pass "$description"
    fi
}

# --- proposal ledger shape assertions -------------------------------------
# work/epic-wiki-lint-okf-io-correctness/children/
# bug-proposal-disposition-ledger-shape. The two documents that read a
# proposal note described frontmatter the shipped writer has never emitted:
# `status`, `origins[]`, `kind`, `target_slug`, `mode`, `concept_kind`. Every
# one is a key the reader would find missing at runtime, and no other gate
# looks at document shape -- plugin-contract checks verbs, flags and
# identifiers only.
#
# The negative patterns are written narrowly on purpose. `## Origins` is a
# LEGITIMATE body heading rendered from `sources[]`, and a destination page's
# own `status:` frontmatter is ordinary; only the frontmatter key `origins[]`
# and the proposal-note spelling `status: <page state>` are drift. Hence the
# `[^a-z_]` guard, which lets `page_status: approved` through and stops
# `status: approved`.

LEDGER_READERS=(
    "skills/graph-works/references/proposal-disposition.md"
    "skills/proposals/SKILL.md"
)

OBSOLETE_LEDGER_KEYS='origins\[\]|target_slug|concept_kind|create_new|update_existing|proposal promote'
OBSOLETE_STATUS_KEY='(^|[^a-z_])status: (approved|created|proposed|rejected)'

for reader in "${LEDGER_READERS[@]}"; do
    assert_not_matches "$reader" "$OBSOLETE_LEDGER_KEYS" \
        "$reader names no frontmatter key the proposal writer never emits"
    assert_not_matches "$reader" "$OBSOLETE_STATUS_KEY" \
        "$reader spells the note's state key page_status, not status"
    assert_contains "$reader" "page_status" \
        "$reader reads the page_status key the writer actually emits"
    assert_contains "$reader" "sources[]" \
        "$reader reads sources[], the key that carries a proposal's arguments"
done

# Identity is the target path: `approve`/`reject` resolve their argument
# through `_find_by_target`, never through the note's filename slug.
assert_contains "skills/graph-works/references/proposal-disposition.md" \
    "gw wiki proposal approve <target>" \
    "the disposition reference calls approve with a target, not a slug"

# `approved` is archive-eligible -- doc_wiki_okf.archive._sweep takes every
# proposal whose page_status is not `proposed`. A bare sweep eats an
# approved-but-unauthored note, so the preview is not optional.
assert_contains "skills/graph-works/references/proposal-disposition.md" \
    "gw wiki archive --dry-run" \
    "the disposition reference previews the sweep before running it"

# `## Origins` must stay allowed: it is what the review renderer writes.
assert_contains "skills/graph-works/references/proposal-disposition.md" \
    "## Origins" \
    "the disposition reference still recognises the Origins body heading"

# bug-transcript-capture-labels-the-wrong-phase: the pointer is stamped at the
# start of every stage session, not by the exit advance.
assert_contains "skills/workflow/SKILL.md" \
    "gw work touch-active-work" \
    "workflow stamps the active-work pointer before every stage skill"
assert_contains "hooks/examples/README.md" \
    "gw work touch-active-work" \
    "the transcript hook README names the pointer's start-of-session writer"
assert_contains "skills/onboard/SKILL.md" \
    "gw work touch-active-work" \
    "onboard's transcript intro names the pointer's start-of-session writer"
assert_contains "skills/workflow/SKILL.md" \
    "gate item's own phase" \
    "workflow's satisfied-gate bullet stamps the pointer before advancing"
assert_contains "skills/workflow/SKILL.md" \
    "this finish session's own" \
    "workflow's archive offer names the finish transcript archiving-in-session drops"

if [[ "$FAILURES" -gt 0 ]]; then
    echo "STATUS: FAILED ($FAILURES failure(s))"
    exit 1
fi

echo "STATUS: PASSED"
