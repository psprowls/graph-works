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
assert_contains "skills/auto-drive/references/dispatch-checks.md" 'A placement preview validates repository selection too.' \
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
assert_not_matches "skills/workflow/SKILL.md" 'Archive `<work-path>` now\?' \
    "workflow's Terminal handling asks no archive question"
assert_not_matches "skills/workflow/references/editing-work-items.md" 'offer `/gw:archive' \
    "the editing guide does not tell a model to offer /gw:archive"
assert_contains "skills/workflow/SKILL.md" 'This skill never runs `/gw:archive`' \
    "workflow's Terminal handling points at /gw:archive without running it"
assert_contains "skills/workflow/SKILL.md" "/gw:archive <work-path>" \
    "workflow's Terminal handling names /gw:archive <work-path> for later"

# work/feature-generic-work-edit-command: there is no `gw work edit` verb; the
# editing guide is the supported path, so every place a model would look for
# one must point at it.
assert_contains "skills/workflow/references/editing-work-items.md" \
    "## Procedure (mandatory)" \
    "the editing guide carries its mandatory post-edit procedure"

assert_contains "skills/workflow/SKILL.md" \
    "references/editing-work-items.md" \
    "the workflow skill points at the editing guide"

assert_contains "skills/using-graph-works/SKILL.md" \
    "references/editing-work-items.md" \
    "the bootstrap skill points at the editing guide"

assert_contains "skills/graph-works/references/page-formats.md" \
    "references/editing-work-items.md" \
    "page-formats §8 points hand overrides at the editing guide"

assert_contains "skills/auto-drive/SKILL.md" \
    "references/multi-repo-acceptance.md" \
    "auto-drive links the disposable multi-repository acceptance procedure"
assert_contains "skills/auto-drive/references/multi-repo-acceptance.md" \
    "installed-package adapter runtime" \
    "acceptance separates installed runtime evidence from source checks"

# The execute-coverage report has one producer (core's EXECUTE_TAIL, which
# names the file through {execute_artifact}) and two readers: auto-drive
# §4.1's Coverage read (unattended) and workflow step 4 (attended). The
# attended brief appends the dispatched prompt_tail rather than copying it
# (epic ledger D-020), so the readers are pinned to the path field, never a
# filename; see work/bug-auto-drive-execute-coverage-read.
assert_contains "skills/workflow/SKILL.md" "dispatch.profile.prompt_tail" \
    "workflow's execute bullet appends the dispatched prompt_tail"
assert_contains "skills/workflow/SKILL.md" '`{execute_artifact}` with `artifacts.execute.file`' \
    "workflow substitutes {execute_artifact} from artifacts.execute.file"
assert_contains "skills/workflow/SKILL.md" 'A `null` tail adds nothing.' \
    "workflow's execute bullet handles a null tail"
assert_not_matches "skills/workflow/SKILL.md" 'same text `EXECUTE_TAIL` gives|\*\*Execute-stage (gate|deferrals) \(step 3\)\.\*\*' \
    "workflow no longer copies EXECUTE_TAIL's bullets"
assert_contains "skills/finishing-relay/SKILL.md" "gw work gate check <work-path>" "relay R1 checks for a receipt first"
assert_contains "skills/finishing-relay/SKILL.md" "gw work gate check <work-path> --worktree <target worktree>" "relay R4 checks the merged tree"
assert_contains "skills/finishing-relay/SKILL.md" 'git log <target_branch>..<source_branch>' "relay R2 settles an empty range without gating"
assert_contains "skills/workflow/references/brief-riders.md" "gw work gate check <work-path>" "attended finish rider checks for a receipt"
assert_contains "skills/finishing-relay/SKILL.md" "except a target R2's already-contained rule settles" "relay R1 exempts already-contained targets"
assert_contains "skills/workflow/references/brief-riders.md" "except one already contained" "attended rider exempts already-contained targets"
assert_contains "skills/auto-drive/SKILL.md" "### Gates" "auto-drive states the gate rules"
assert_contains "skills/auto-drive/SKILL.md" "gw work gate wait" "auto-drive resumes gates with wait"
sweep_denylist "gate-tmp-logs" '/private/tmp/[^ ]*just-check|/private/tmp/claude' \
    "names a shared tmp gate log path" "tests/test-doc-layout-claims.sh"
sweep_denylist "gate-by-hand" 'run `just check`( as the gate|,? then advance)|until grep .\^EXIT' \
    "names just check or an EXIT-grep loop as the gate" "tests/test-doc-layout-claims.sh"

assert_contains "skills/auto-drive/SKILL.md" \
    "### Coverage read (execute dispatches only)" \
    "auto-drive carries the execute-stage Coverage read section"
assert_contains "skills/auto-drive/SKILL.md" \
    "artifacts.execute.path" \
    "auto-drive's Coverage read reads the execute artifact path"
assert_contains "skills/auto-drive/SKILL.md" \
    'any line is `- [ ]`' \
    "auto-drive's Coverage read scans for unchecked items"
assert_contains "skills/auto-drive/SKILL.md" \
    "--from finish --return --no-infer-worktree" \
    "auto-drive's Coverage read sends an item back with a guarded return"
assert_contains "skills/auto-drive/SKILL.md" \
    "run the coverage read" \
    "auto-drive's success branch runs the coverage read for execute dispatches"
assert_contains "skills/workflow/SKILL.md" \
    "artifacts.execute.path" \
    "workflow step 4 reads the execute artifact path"
assert_contains "skills/workflow/SKILL.md" \
    "**Execute-stage tail (step 3).**" \
    "workflow step 3 appends the execute tail for an execute stage"
assert_contains "skills/workflow/SKILL.md" \
    "**Execute-stage coverage (step 4).**" \
    "workflow step 4 reads the coverage file back and surfaces unchecked items"

# --- curated-page claims contract ------------------------------------------
# work/epic-agent-facing-wiki-content/children/feature-curated-page-claims-contract.
# `applies_to` left the Reference seed; the readers describe `about:` and the
# `x-okf-about` mandate instead.
for reader in "skills/graph-works/references/wiki-schema.md" "skills/graph-works/references/page-formats.md"; do
    assert_not_matches "$reader" "applies_to" "$reader no longer documents the retired applies_to key"
    assert_contains "$reader" "x-okf-about" "$reader names the x-okf-about mandate"
    assert_contains "$reader" "decisions:" "$reader documents ADR decisions entries"
done

# The curated-claims backfill made about:/decisions:/claims: required on
# curated pages at error severity. Every authoring path that creates or
# updates one must say so, or the next page it writes fails lint.
# page-formats.md is checked separately below (and above, for `decisions:`) —
# it is the schema/authoring reference, not an author dispatch path, and the
# reader loop above already pins its `decisions:` mention.
CLAIMS_AUTHORS=(
    "skills/ingest/SKILL.md"
    "skills/proposals/SKILL.md"
)
for author in "${CLAIMS_AUTHORS[@]}"; do
    assert_contains "$author" "about:" \
        "$author tells the author to write about: on a curated page"
    assert_contains "$author" "decisions:" \
        "$author tells the author to write decisions: on an ADR"
    assert_contains "$author" "claims:" \
        "$author tells the author to write claims: on an explanation"
done
assert_contains "skills/graph-works/references/page-formats.md" "id: D1" \
    "page-formats' ADR template shows a decisions entry"
assert_contains "skills/graph-works/references/page-formats.md" "id: C1" \
    "page-formats' Explanation template shows a claims entry"

# --- Source drain ledger ----------------------------------------------------
# work/feature-source-drain-and-archive: every path that turns a Source's key
# claims into curated entries records where they landed.
for author in "skills/ingest/SKILL.md" "skills/proposals/SKILL.md"; do
    assert_contains "$author" "drain:" "$author tells the author to write the Source's drain: ledger"
done
assert_contains "skills/ingest/SKILL.md" "dropped:" "ingest documents dropped dispositions"
assert_contains "skills/proposals/SKILL.md" "once, after the fan-out" \
    "proposals writes each Source's drain: ledger once, after the fan-out, not from each author"
assert_contains "skills/proposals/SKILL.md" "drained Sources" \
    "proposals warns that the sweep also archives drained Sources"
assert_contains "skills/graph-works/references/page-formats.md" "drain:" \
    "page-formats documents the Source drain ledger"
assert_not_matches "skills/workflow/SKILL.md" "archives the source and repoints" \
    "workflow no longer claims the ingestor archives the source"
assert_contains "skills/workflow/SKILL.md" "drain sweep" \
    "workflow says drained sources are archived by the sweep"

# Findings are a workflow obligation shared by stock and gw-owned stages.
assert_contains "skills/workflow/references/brief-riders.md" \
    "An exit-advance refusal is an escalation to the human or coordinator" \
    "design riders escalate exit refusals instead of altering valid artifacts"
assert_contains "skills/workflow/SKILL.md" "**Out-of-scope findings.**" \
    "workflow step 3 carries the universal findings instruction"
assert_contains "skills/workflow/SKILL.md" 'Never pass `--parent-path` naming this item or any ancestor' \
    "findings cannot reopen the current ancestor gate"
assert_contains "skills/workflow/SKILL.md" "list the filed or named finding paths, or say there were none" \
    "workflow hand-off accounts for findings"

# --- structured worker asks (work/epic-coordinator-dispatch-and-relay-protocol/
# children/feature-structured-worker-ask) ---------------------------------
# Orchestrate parses a plan's `## Human checkpoints` section to warn before an
# execute dispatch that will stop for a human; the rider is the only thing
# that makes plans carry it. The STOP literal is pinned byte-for-byte because
# the paragraph lands directly above it.
assert_contains "skills/workflow/references/brief-riders.md" \
    '> The plan must contain a `## Human checkpoints` section' \
    "writing-plans rider requires a Human checkpoints section"
assert_contains "skills/workflow/references/brief-riders.md" \
    "> STOP after writing the plan — do not run the Execution Handoff. This is a" \
    "writing-plans rider STOP literal is unchanged (line 1)"
assert_contains "skills/workflow/references/brief-riders.md" \
    "> single pipeline stage; the workflow skill advances the item." \
    "writing-plans rider STOP literal is unchanged (line 2)"

# Task 10: typed asks, attend pings, execute checkpoint notices, and the
# shared grace-period protocol must describe the same dispatch behavior.
assert_contains "skills/auto-drive/SKILL.md" "### 4.3 \`question\`" \
    "auto-drive §4.3 is the general question handler"
assert_contains "skills/auto-drive/SKILL.md" 'starts with `gw-ask: `' \
    "auto-drive §4.3 keys typed asks on the gw-ask: marker"
assert_contains "skills/auto-drive/SKILL.md" "**verbatim**. Never summarize, condense or truncate it." \
    "auto-drive §4.3 prints the payload question verbatim"
assert_contains "skills/auto-drive/SKILL.md" "gw work ask-answer <resource>" \
    "auto-drive §4.3 records answers through gw work ask-answer"
assert_contains "skills/auto-drive/SKILL.md" "untyped ask from <key>" \
    "auto-drive §4.3 marks untyped asks"
assert_contains "skills/auto-drive/SKILL.md" "--by policy:auto-merge" \
    "auto-drive step 0 records the auto-merge answerer on a typed ask"
assert_contains "skills/auto-drive/SKILL.md" 'successful exit, `ok: true`, and a nonempty string `reply_body`' \
    "auto-drive gates a typed auto-answer on a usable recording result"
assert_contains "skills/auto-drive/SKILL.md" 'On refusal or an unusable `reply_body`, show the result to the human' \
    "auto-drive sends a typed auto-answer refusal to human handling"
assert_contains "skills/auto-drive/SKILL.md" 'Only after that check, print the `auto-merged` notice' \
    "auto-drive announces a typed auto-merge only after recording succeeds"
assert_contains "skills/auto-drive/SKILL.md" 'starts with `Needs you at `' \
    "auto-drive §4.4 recognises the attend ping"
assert_contains "skills/auto-drive/SKILL.md" "needs-you <work-path> <phase> at <terminal handle>" \
    "auto-drive §4.4 prints the needs-you line"
assert_contains "skills/auto-drive/SKILL.md" "will stop for a human <N> time(s)" \
    "auto-drive §3 warns before an execute with declared checkpoints"
assert_contains "skills/auto-drive/SKILL.md" "human checkpoints unknown (<status>)" \
    "auto-drive §3 reports unknown checkpoints as unknown, never zero"
assert_not_matches "skills/auto-drive/references/grace-period-protocol.md" "folded into both" \
    "grace-period protocol no longer claims ATTEND_TAIL folds it in"
assert_contains "skills/auto-drive/references/grace-period-protocol.md" "Attend workers do not use this protocol" \
    "grace-period protocol says attend workers ask in their own terminal"
assert_contains "skills/auto-drive/references/grace-period-protocol.md" "its \`gw-ask:\` payload resource" \
    "grace-period checkpoint records the typed ask's payload resource"

# feature-nonblocking-coordinator-questions: a worker question is mirrored
# (printed), acked, and answered later by label — never a blocking prompt.
assert_contains "skills/auto-drive/SKILL.md" \
    'For a question, *handled* means *mirrored to the human*, not *answered*.' \
    "auto-drive §2.7 acks a question once it is mirrored"
assert_contains "skills/auto-drive/SKILL.md" \
    'gw work wait --run <run_id> --timeout-s 0 --json' \
    "auto-drive reads pending_questions through a zero-timeout gw work wait"
assert_contains "skills/auto-drive/SKILL.md" \
    'Resolve the label against a fresh `pending_questions` read' \
    "auto-drive §4.3 resolves a typed answer against a fresh read"
assert_contains "skills/auto-drive/SKILL.md" \
    '<label> <key> — <kind>, waiting since <asked_at>' \
    "auto-drive §4.3 prints an already-shown question as one line"
section_43="$(awk '/^### 4\.3 `question`/{on=1} /^### 4\.4 /{on=0} on' "$PLUGIN_ROOT/skills/auto-drive/SKILL.md")"
if [[ -n "$section_43" ]] && ! grep -Fq 'AskUserQuestion' <<<"$section_43"; then
    pass "auto-drive §4.3 never routes a worker question through AskUserQuestion"
else
    fail "auto-drive §4.3 never routes a worker question through AskUserQuestion"
    grep -nF 'AskUserQuestion' <<<"$section_43" | sed 's/^/      /'
fi

# Task 5 I1: pin the explicit ordering and fail-closed label contract.
assert_contains "skills/auto-drive/SKILL.md" \
    '**On delivery, before mirroring:** obtain fresh pending labels with' \
    "auto-drive I1 reads fresh labels before delivery mirroring"
assert_contains "skills/auto-drive/SKILL.md" \
    'Never invent a label or reuse a cached label when the refresh fails.' \
    "auto-drive I1 rejects invented or stale labels on refresh failure"
assert_contains "skills/auto-drive/SKILL.md" \
    'Without positive closure/reply evidence, leave its delivery unacked for replay.' \
    "auto-drive I1 preserves unprinted questions for replay"
assert_contains "skills/auto-drive/SKILL.md" \
    '**On both delivery and timeout, display pending before restarting.**' \
    "auto-drive I1 displays pending on both paths before restart"
assert_contains "skills/auto-drive/SKILL.md" \
    'Only after the pending display, restart the cycle at §2.1.' \
    "auto-drive I1 restarts only after the pending display"

# Final review F1: missing pending entries include terminal questions, not
# just transient refresh failures. These are prose-contract checks, not a
# simulation of Orca or proof that an agent executed the protocol.
for contract in \
    '**Missing delivery question: prove disposition before ack.**' \
    'Match `result.dispatch.id`, `runId`, and `taskId` to the delivered' \
    'orca orchestration inbox --terminal dispatch:<dispatch_id> --limit 1000 --json' \
    '`thread_id == <message_id>`, `from_handle == run:<run_id>`,' \
    '`to_handle == dispatch:<dispatch_id>`, and `type == status`' \
    '`result.worker.state` is `succeeded`, `failed`, or `stopped`' \
    'Absence, a warning, a failed/truncated read, or an unknown state alone' \
    'Do not create a label, send a new reply, or require a new answer.' \
    'Ended-before-mirror: positive ended evidence → fresh park/failure routing' \
    'Answered-before-ack/restart: matching reply evidence → report already answered' \
    'Inconclusive read: no matching positive evidence → defer, keep delivery unacked' \
    'Closure handles only this question; every other batch message still needs'
do
    assert_contains "skills/auto-drive/SKILL.md" "$contract" \
        "auto-drive F1 disposition contract: $contract"
done

# Finishing-relay's three asks are typed; hand-written --options produced
# comma-split and invented-grammar asks. Typed replies have a strict JSON body.
assert_contains "skills/finishing-relay/SKILL.md" "gw work ask <work-path> --kind choice" \
    "finishing-relay R3 prepares a typed choice ask"
assert_contains "skills/finishing-relay/SKILL.md" '--summary "Confirm discard of' \
    "finishing-relay discard confirmation is a typed free ask"
assert_contains "skills/finishing-relay/SKILL.md" '--summary "Release date for' \
    "finishing-relay release-date ask is a typed free ask"
assert_contains "skills/finishing-relay/SKILL.md" 'read `choice` from the JSON reply' \
    "finishing-relay reads the choice from the JSON reply body"
assert_not_matches "skills/finishing-relay/SKILL.md" '--options "<merge,pr,hold,discard' \
    "finishing-relay no longer hand-writes --options"
assert_contains "skills/finishing-relay/SKILL.md" 'A reply that is not JSON, including a bare option token, is treated as `hold`.' \
    "finishing-relay holds on every non-JSON typed reply"

# Reader preparation and evidence use distinct durable artifacts, not an epic
# stamp. The legacy recipe carries the helper contract; the skill carries the
# verb's.
for contract in \
    'launch-worker.py prepare-reader --dispatch <dispatch-json>' \
    '--attempt-id <preparation-attempt-id> --out-placement <fresh-placement-json>' \
    'Require exit 0 before encode, task-create or launch' \
    'File existence alone never authorizes launch' \
    'gw work record-reader <slug> --root <work-path> --phase <dispatch phase>' \
    'dispatched <key> -> <observed path> detached at <start_sha>' \
    'layout.cache_dir / "reader-receipts"'; do
    assert_contains "skills/auto-drive/references/dispatch-checks.md" "$contract" "dispatch checks document reader contract: $contract"
done
for contract in \
    'dispatched <key> -> <observed path> detached at <start_sha>' \
    'layout.cache_dir / "reader-receipts"' \
    'references/orca-placement/<key>.dispatch.json' \
    '### 4.2.1 Reader preparation failure before Task creation'; do
    assert_contains "skills/auto-drive/SKILL.md" "$contract" "auto-drive documents reader contract: $contract"
done
assert_not_matches "skills/auto-drive/SKILL.md" \
    'reads from the shared epic worktree|read-only descendant that records nothing' \
    "auto-drive retires shared-reader placement and receipt skipping"

# tech-debt-auto-drive-skill-orca-edge-cases: §2.7 waits through the tested
# `gw work wait` verb; no raw `check --wait` / `check --ack` survives anywhere
# in the skill tree. Wording pins are prose contracts, not a simulation.
raw_check_hits="$(grep -rnE -- 'orchestration check --run <run_id> --(wait|ack)|check --wait|check --ack' \
    "$PLUGIN_ROOT/skills/auto-drive" || true)"
if [[ -z "$raw_check_hits" ]]; then
    pass "auto-drive carries no raw orca check --wait / --ack loop"
else
    fail "auto-drive carries no raw orca check --wait / --ack loop"
    echo "$raw_check_hits" | sed 's/^/      /'
fi
for contract in \
    'gw work wait --run <run_id> [--ack <delivery_id>] --timeout-s 600 --json' \
    'the one named exception to §2' \
    'A deferred ack is simply the next call without `--ack`' \
    'absorbed duplicate worker_done <dispatch_id>' \
    'On `status: timeout`, triage from `liveness[]`' \
    'Verb failure with code `consumer_fenced`' \
    'strips them and self-acks heartbeat-only deliveries' \
    'The verb absorbs a late duplicate only for a released single-attempt completion' \
    'stablyai/orca#14910' \
    'stablyai/orca#21226' \
    'is "unknown", not "none"'
do
    assert_contains "skills/auto-drive/SKILL.md" "$contract" \
        "auto-drive gw work wait contract: $contract"
done
assert_not_matches "skills/auto-drive/SKILL.md" \
    "so they're never delivered|Until \`tech-debt-auto-drive-skill-orca-edge-cases\` replaces" \
    "auto-drive drops the never-delivered heartbeat claim and the interim parenthetical"

# feature-decision-amend-and-relay-ledger: every stage brief carries the
# ledger obligations, and riders point at it instead of copying it.
for contract in \
    '- **Decision ledger.** For every stage, regardless of `action.skill`, add:' \
    'gw work decision amend <owner-path> <D-id> --answer … --note …' \
    'gw work decision add <path> --question … --status answered --answer … --decided-by <human>' \
    'gw work decision add <owner-path> --status assumed --question "Changes D-nnn: …"' \
    'is `supersede` or `overturn`, and only on a human'"'"'s instruction'
do
    assert_contains "skills/workflow/SKILL.md" "$contract" \
        "workflow Decision ledger bullet: $contract"
done
assert_contains "skills/workflow/references/brief-riders.md" \
    "step 3 **Decision ledger** bullet" \
    "brief-riders points at the Decision ledger bullet instead of copying it"

# The stage path is workspace data, so plugin prose must say so.
assert_contains "skills/graph-works/README.md" \
    "which stages an item walks" \
    "the plugin README says dispatch.yaml also sets the pipeline path"
assert_contains "skills/auto-drive/references/dispatch-configuration.md" \
    "pipeline.path" \
    "the dispatch-configuration reference points at the path section"
assert_contains "skills/workflow/references/editing-work-items.md" \
    "skips the plan stage under the packaged path" \
    "the editing guide's effort row names the packaged path"
assert_contains "skills/workflow/references/editing-work-items.md" \
    "by effort under the packaged path" \
    "the editing guide's phase row names the packaged path"

# Workflow branches on blocker kinds and shows resolved path candidates.
assert_contains "skills/workflow/SKILL.md" "blocker_kinds" \
    "workflow reads blocker_kinds"
assert_contains "skills/workflow/SKILL.md" '`effort-required`' \
    "workflow names the effort-required kind"
assert_contains "skills/workflow/SKILL.md" '`waiting-on-children`' \
    "workflow names the waiting-on-children kind"
assert_contains "skills/workflow/SKILL.md" '`children-open`' \
    "workflow's detach section keys on the children-open refusal"
assert_contains "skills/workflow/SKILL.md" "path_candidates" \
    "workflow's sizing question shows the path candidates"
assert_not_matches "skills/workflow/SKILL.md" 'skips the planning stage|blocker says \*\*|only blocker says|errors with \*effort required\*|refuses with \*"waiting on children"\*' \
    "workflow does not branch on blocker prose or assert the packaged path"

# --- stage-artifact paths come from gw work next ----------------------------
# Filenames are configured; these skills read the path, never spell it.
assert_contains "skills/file/SKILL.md" "artifacts.design.path" \
    "file anchor 2 writes the design to artifacts.design.path"
assert_contains "skills/file/SKILL.md" 'gw work next <work-path> --json --file ""' \
    "file anchor 2 resolves the path with gw work next"
assert_contains "skills/epic-design/SKILL.md" "the brief's \`artifact.path\`" \
    "epic-design writes to the brief's artifact.path"
assert_contains "skills/epic-design/SKILL.md" "which its \`gw work next\` reports" \
    "epic-design names a child's design artifact by stage"
assert_contains "skills/planning-epics/SKILL.md" "artifacts.design.path" \
    "planning-epics reads the design at artifacts.design.path"
assert_contains "skills/planning-epics/SKILL.md" "artifacts.plan.path" \
    "planning-epics falls back to artifacts.plan.path"

CONTEXT_RIDER='**Context.** Read each skill file once; re-open only a named section. Hand work to subagents as files, not pasted text.'
assert_contains "skills/workflow/references/brief-riders.md" "$CONTEXT_RIDER" \
    "SDD/TDD riders carry the Context paragraph"
context_count=$(grep -cF -- "$CONTEXT_RIDER" "$PLUGIN_ROOT/skills/workflow/references/brief-riders.md")
if [[ "$context_count" -eq 2 ]]; then
    pass "Context rider paragraph appears in exactly the SDD and TDD riders"
else
    fail "Context rider paragraph appears $context_count times, expected 2 (SDD and TDD)"
fi

# feature-schema-driven-proposal-pool: filing and disposition use schema types.
for doc in "skills/graph-works/references/ingest-workflow.md" "skills/ingest/SKILL.md"; do
    assert_contains "$doc" "gw wiki proposal file --type" \
        "$doc files a declined page by its schema type"
    assert_not_matches "$doc" '^[[:space:]]*(gw wiki proposal file.*--lane|--lane )' \
        "$doc no longer teaches the deprecated --lane command"
done
assert_not_matches "skills/graph-works/references/proposal-disposition.md" 'Diataxis lanes and ADR' \
    "the disposition reference describes the pool, not five lanes"
for doc in "skills/graph-works/references/proposal-disposition.md" "skills/proposals/SKILL.md"; do
    for contract in 'x-okf-accept-proposals' 'target_type' 'type_refusal' 'resolved `type`' 'record the chosen' 'gw work file --kind' 'sources[]' 'leave the note `approved`'; do
        assert_contains "$doc" "$contract" "$doc documents $contract disposition"
    done
    assert_not_matches "$doc" 'with no `target_type` is ambiguous' \
        "$doc allows unambiguous legacy type resolution"
    assert_contains "$doc" 'preserve its recorded `target`' "$doc preserves existing targets"
    assert_contains "$doc" 'editing-work-items.md' "$doc follows the work-item editing protocol"
    # First-page custom Runbooks may forbid about and require service from
    # promotion metadata. Existing pages cannot be the declaration source.
    for contract in '.gw/schema/<type>.schema.json' '.gw/sections/<type>.yaml' 'including `$ref`/`allOf`' 'first page of a custom type' 'x-okf-proposal-promotion' '`frontmatter`' '`{on}`' '`dated` defaults to `false`' 'Only a new page with `dated: true`' 'preserve the recorded `target`' 'only when the applicable declarations mandate them' 'original `id` and `resource`' 'citation entries' 'stage artifacts'; do
        assert_contains "$doc" "$contract" "$doc covers schema-driven custom authoring: $contract"
    done
    assert_not_matches "$doc" 'requires the destination page.s `about:`|reads an existing page of the same type as the format template|ADR numbers|ADRs are dated, never numbered' \
        "$doc avoids universal curated fields, first-page templates, and type-specific dating"
done
assert_contains "skills/workflow/references/editing-work-items.md" 'original `id` and `resource`' \
    "the editing guide preserves proposal citation identities alongside stage artifacts"
assert_contains "skills/graph-works/references/page-formats.md" 'target_type' \
    "page formats records the proposal destination type"

if [[ "$FAILURES" -gt 0 ]]; then
    echo "STATUS: FAILED ($FAILURES failure(s))"
    exit 1
fi

echo "STATUS: PASSED"
