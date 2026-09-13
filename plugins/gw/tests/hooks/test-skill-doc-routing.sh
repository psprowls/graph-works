#!/usr/bin/env bash
# Tests for hooks/skill-doc-routing — the PreToolUse/Skill workspace-routing hook.
#
# Fork-only, with no upstream counterpart, which is why this is a new file
# rather than an extension of tests/hooks/test-session-start.sh: that suite was
# verbatim upstream (`git show 032a9912:plugins/PATCHES.md`, entry #21, from
# when this tree was still the vendored subtree) and coverage for a fork-only
# hook did not belong in a file we were keeping conflict-free.
#
# The JSON validator mirrors test-session-start.sh's, widened for this hook's
# two extra axes: a bare allow carries no context at all, and the misconfig /
# staleness branches carry a systemMessage.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
HOOK_UNDER_TEST="$REPO_ROOT/hooks/skill-doc-routing"
SESSION_START_HOOK="$REPO_ROOT/hooks/session-start"

FAILURES=0
TEST_ROOT="$(mktemp -d)"
TEST_ROOT="$(cd "$TEST_ROOT" && pwd -P)"   # /var -> /private/var, so paths compare equal

cleanup() {
    rm -rf "$TEST_ROOT"
}
trap cleanup EXIT

pass() {
    echo "  [PASS] $1"
}

fail() {
    echo "  [FAIL] $1"
    FAILURES=$((FAILURES + 1))
}

US=$'\037'

MATCHING_PAYLOAD='{"tool_name":"Skill","tool_input":{"skill":"brainstorming"}}'
NAMESPACED_PAYLOAD='{"tool_name":"Skill","tool_input":{"skill":"superpowers:brainstorming"}}'
NON_MATCHING_PAYLOAD='{"tool_name":"Skill","tool_input":{"skill":"test-driven-development"}}'

sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | awk '{print $1}'
    else
        openssl dgst -sha256 "$1" | awk '{print $NF}'
    fi
}

# make_workspace <name> [bundle_dir]
# Creates <TEST_ROOT>/<name> as a marked workspace and echoes its path.
make_workspace() {
    local name="$1" bundle="${2:-}"
    local ws="$TEST_ROOT/$name"
    mkdir -p "$ws"
    if [ -n "$bundle" ]; then
        printf 'version: 1\nlayout:\n  bundle_dir: %s\n' "$bundle" > "$ws/workspace.yaml"
    else
        printf 'version: 1\n' > "$ws/workspace.yaml"
    fi
    printf '%s\n' "$ws"
}

# add_local_manifest <workspace> [body]
# Creates <workspace>/workspace.local.yaml and echoes its sha256.
add_local_manifest() {
    local ws="$1" body="${2:-topic: Laptop}"
    printf '%s\n' "$body" > "$ws/workspace.local.yaml"
    sha256_of "$ws/workspace.local.yaml"
}

# write_projection <workspace> <sha256|-> [bundle_dir] [overlay_sha256|-]
# Writes <workspace>/.gw/cache/config.json in the shape config_io.write_projection
# emits (json.dumps(..., indent=2)). "-" records a null hash. Omitting the
# fourth argument writes the two-key _meta a non-layered store produces — the
# legacy workspace metadata; dispatch input records below are always complete.
write_projection() {
    local ws="$1" sha="$2" bundle="${3:-}" overlay="${4:-}"
    mkdir -p "$ws/.gw/cache"
    {
        printf '{\n'
        if [ -n "$bundle" ]; then
            printf '  "layout": {\n    "bundle_dir": "%s"\n  },\n' "$bundle"
        fi
        printf '  "_meta": {\n'
        printf '    "source_mtime": 1755000000.0,\n'
        if [ "$sha" = "-" ]; then
            printf '    "source_sha256": null'
        else
            printf '    "source_sha256": "%s"' "$sha"
        fi
        if [ -n "$overlay" ]; then
            printf ',\n    "overlay_mtime": 1755000000.0,\n'
            if [ "$overlay" = "-" ]; then
                printf '    "overlay_sha256": null\n'
            else
                printf '    "overlay_sha256": "%s"\n' "$overlay"
            fi
        else
            printf '\n'
        fi
        printf ',\n    "dispatch_inputs": {\n'
        for name in manifest manifest_local shared local; do
            case "$name" in
                manifest) input_path="$ws/workspace.yaml"; input_sha="$sha" ;;
                manifest_local) input_path="$ws/workspace.local.yaml"; input_sha="${overlay:--}" ;;
                shared) input_path="$ws/dispatch.yaml"; input_sha="-" ;;
                local) input_path="$ws/dispatch.local.yaml"; input_sha="-" ;;
            esac
            printf '      "%s": {\n        "path": "%s",\n' "$name" "$input_path"
            if [ "$input_sha" = "-" ]; then
                printf '        "exists": false,\n        "sha256": null\n'
            else
                printf '        "exists": true,\n        "sha256": "%s"\n' "$input_sha"
            fi
            if [ "$name" = "local" ]; then printf '      }\n'; else printf '      },\n'; fi
        done
        printf '    }\n'
        printf '  }\n'
        printf '}\n'
    } > "$ws/.gw/cache/config.json"
}

# run_hook <cwd> <payload> [ENV=VALUE ...]
# Runs the hook in a scrubbed environment and echoes its stdout.
run_hook() {
    local cwd="$1" payload="$2"
    shift 2
    ( cd "$cwd" && printf '%s' "$payload" | env -i PATH="${PATH:-}" HOME="$TEST_ROOT/home" "$@" bash "$HOOK_UNDER_TEST" 2>&1 )
}

# assert_output <description> <shape> <context: required|absent> <contains>
#               <not_contains> <sysmsg: present|absent> <sysmsg_contains> <output>
assert_output() {
    local description="$1" shape="$2" context_mode="$3" contains="$4"
    local not_contains="$5" sysmsg_mode="$6" sysmsg_contains="$7" output="$8"

    if printf '%s' "$output" | \
        EXPECT_SHAPE="$shape" \
        EXPECT_CONTEXT="$context_mode" \
        EXPECT_CONTAINS="$contains" \
        EXPECT_NOT_CONTAINS="$not_contains" \
        EXPECT_SYSMSG="$sysmsg_mode" \
        EXPECT_SYSMSG_CONTAINS="$sysmsg_contains" \
        node -e '
const fs = require("fs");

const input = fs.readFileSync(0, "utf8");
let payload;
try {
  payload = JSON.parse(input);
} catch (error) {
  console.error(`invalid JSON: ${error.message}`);
  process.exit(1);
}

function hasOwn(object, key) {
  return Object.prototype.hasOwnProperty.call(object, key);
}

function fail(message) {
  console.error(message);
  process.exit(1);
}

const shape = process.env.EXPECT_SHAPE;
let context;

if (shape === "nested") {
  if (!hasOwn(payload, "hookSpecificOutput")) {
    fail("missing hookSpecificOutput");
  }
  if (hasOwn(payload, "additional_context") || hasOwn(payload, "additionalContext")) {
    fail("nested output also included a top-level context field");
  }
  const hookOutput = payload.hookSpecificOutput;
  if (!hookOutput || typeof hookOutput !== "object" || Array.isArray(hookOutput)) {
    fail("hookSpecificOutput is not an object");
  }
  if (hookOutput.hookEventName !== "PreToolUse") {
    fail(`unexpected hookEventName: ${hookOutput.hookEventName}`);
  }
  if (hookOutput.permissionDecision !== "allow") {
    fail(`unexpected permissionDecision: ${hookOutput.permissionDecision}`);
  }
  context = hookOutput.additionalContext;
} else if (shape === "cursor") {
  if (hasOwn(payload, "hookSpecificOutput")) {
    fail("cursor output included hookSpecificOutput");
  }
  if (!hasOwn(payload, "additional_context")) {
    fail("cursor output missing additional_context");
  }
  if (hasOwn(payload, "additionalContext")) {
    fail("cursor output included additionalContext");
  }
  context = payload.additional_context;
} else if (shape === "sdk") {
  if (hasOwn(payload, "hookSpecificOutput")) {
    fail("sdk output included hookSpecificOutput");
  }
  if (!hasOwn(payload, "additionalContext")) {
    fail("sdk output missing additionalContext");
  }
  if (hasOwn(payload, "additional_context")) {
    fail("sdk output included additional_context");
  }
  context = payload.additionalContext;
} else {
  fail(`unknown expected shape: ${shape}`);
}

const contextMode = process.env.EXPECT_CONTEXT;
if (contextMode === "absent") {
  if (typeof context === "string" && context.trim() !== "") {
    fail(`expected no injected context, got: ${context}`);
  }
} else {
  if (typeof context !== "string" || context.trim() === "") {
    fail("injected context was empty");
  }
}

const sysmsgMode = process.env.EXPECT_SYSMSG;
const sysmsg = hasOwn(payload, "systemMessage") ? payload.systemMessage : undefined;
if (sysmsgMode === "absent") {
  if (sysmsg !== undefined) {
    fail(`expected no systemMessage key, got: ${sysmsg}`);
  }
} else if (sysmsgMode === "present") {
  if (typeof sysmsg !== "string" || sysmsg.trim() === "") {
    fail("expected a non-empty systemMessage");
  }
}

const expectedTexts = (process.env.EXPECT_CONTAINS || "").split("").filter(Boolean);
for (const expectedText of expectedTexts) {
  if (!(context || "").includes(expectedText)) {
    fail(`context did not contain expected text: ${expectedText}`);
  }
}

const forbiddenTexts = (process.env.EXPECT_NOT_CONTAINS || "").split("").filter(Boolean);
for (const forbiddenText of forbiddenTexts) {
  if ((context || "").includes(forbiddenText)) {
    fail(`context contained forbidden text: ${forbiddenText}`);
  }
}

const sysmsgTexts = (process.env.EXPECT_SYSMSG_CONTAINS || "").split("").filter(Boolean);
for (const sysmsgText of sysmsgTexts) {
  if (!(sysmsg || "").includes(sysmsgText)) {
    fail(`systemMessage did not contain expected text: ${sysmsgText}`);
  }
}
'; then
        pass "$description"
    else
        fail "$description"
        echo "    output:"
        printf '%s\n' "$output" | sed 's/^/      /'
    fi
}

echo "skill-doc-routing hook output tests"

mkdir -p "$TEST_ROOT/home"

# --- 1. non-matching skill name -> bare allow, no context ------------------
# Also the short-circuit assertion: GRAPH_WORKS_DIR names a real marked
# workspace here, so a hook that resolved before matching would inject context.
marked_ws="$(make_workspace ws_short_circuit)"
out="$(run_hook "$TEST_ROOT" "$NON_MATCHING_PAYLOAD" \
    CLAUDE_PLUGIN_ROOT="$REPO_ROOT" GRAPH_WORKS_DIR="$marked_ws")"
assert_output \
    "non-matching skill: bare allow, no context, no resolution" \
    "nested" "absent" "" "AUTO-FILE" "absent" "" \
    "$out"

# --- 2. no workspace resolves -> allow, SILENT ----------------------------
# A repository that never ran `gw bootstrap` is the ordinary case, not a
# misconfiguration, so it must not warn on every matching Skill call.
mkdir -p "$TEST_ROOT/no_workspace"
out="$(run_hook "$TEST_ROOT/no_workspace" "$MATCHING_PAYLOAD" \
    CLAUDE_PLUGIN_ROOT="$REPO_ROOT")"
assert_output \
    "no workspace: allow, silent (no systemMessage)" \
    "nested" "absent" "" "" "absent" "" \
    "$out"

# --- 3. marked workspace, no projection -> context, bundle_dir defaults ----
ws_default="$(make_workspace ws_default)"
out="$(run_hook "$TEST_ROOT" "$MATCHING_PAYLOAD" \
    CLAUDE_PLUGIN_ROOT="$REPO_ROOT" GRAPH_WORKS_DIR="$ws_default")"
assert_output \
    "marked workspace, no projection: context and refresh notice" \
    "nested" "required" \
    "$ws_default/okf/<work-path>/references/" \
    "wiki/<work-path>/references/${US}raw/" \
    "present" "gw config sync" \
    "$out"

# --- 3b. namespaced payload carries the auto-file clause ------------------
# After the cutover the stage skill is `superpowers:brainstorming` — a foreign
# plugin's skill, reached through a namespaced id. The fast-path regex is
# payload-wide so it still matches, but nothing asserted that until now, and
# the spike flagged it as the untested load-bearing step.
#
# The three anchors are asserted individually rather than as one blob: each is
# a separate instruction the session must be able to act on, and a clause that
# silently lost one would still pass a single substring check.
out="$(run_hook "$TEST_ROOT" "$NAMESPACED_PAYLOAD" \
    CLAUDE_PLUGIN_ROOT="$REPO_ROOT" GRAPH_WORKS_DIR="$ws_default")"
assert_output \
    "namespaced brainstorming: routing context plus the auto-file clause" \
    "nested" "required" \
    "AUTO-FILE (standalone brainstorming only)${US}gw:file${US}gw work advance <work-path> --effort${US}do not invoke writing-plans${US}/gw:workflow <work-path>${US}$ws_default/okf/<work-path>/references/${US}Filed as <work-path>" \
    "" \
    "present" "gw config sync" \
    "$out"

# The mode check must be in the clause: a pipeline-dispatched session has to be
# able to recognise itself and skip the whole thing.
assert_output \
    "namespaced brainstorming: the clause carries its own mode check" \
    "nested" "required" \
    "STOP after writing the spec${US}work-item brief" \
    "" \
    "present" "gw config sync" \
    "$out"

# --- 4. projection overrides bundle_dir -----------------------------------
ws_custom="$(make_workspace ws_custom custom)"
write_projection "$ws_custom" "$(sha256_of "$ws_custom/workspace.yaml")" custom
out="$(run_hook "$TEST_ROOT" "$MATCHING_PAYLOAD" \
    CLAUDE_PLUGIN_ROOT="$REPO_ROOT" GRAPH_WORKS_DIR="$ws_custom")"
assert_output \
    "projection layout.bundle_dir: context names <ws>/custom/<work-path>/references/" \
    "nested" "required" \
    "$ws_custom/custom/<work-path>/references/" \
    "/okf/work/" \
    "absent" "" \
    "$out"

# --- 5. named root that is not a workspace -> systemMessage, still allow ---
mkdir -p "$TEST_ROOT/unmarked"
out="$(run_hook "$TEST_ROOT" "$MATCHING_PAYLOAD" \
    CLAUDE_PLUGIN_ROOT="$REPO_ROOT" GRAPH_WORKS_DIR="$TEST_ROOT/unmarked")"
assert_output \
    "unmarked root: systemMessage, allow, no context" \
    "nested" "absent" "" "" "present" \
    "$TEST_ROOT/unmarked${US}workspace.yaml" \
    "$out"

# The misconfig message must not prescribe a command that cannot do the job.
# Two CLIs are in play until the cutover epic lands: the `gw` on PATH is still
# the donor CLI, whose `bootstrap` builds a vault rather than writing a
# `workspace.yaml`, while graph-works-cli's `bootstrap` does write one. The same
# verb name means different things on either side of the cutover; the marker
# file is true on both. Delete these rows once one `gw` is unambiguous.
for forbidden in "gw bootstrap" "gw workspace init" "gw init"; do
    if printf '%s' "$out" | grep -Fq "$forbidden"; then
        fail "unmarked root: message does not prescribe '$forbidden'"
        printf '%s\n' "$out" | sed 's/^/      /'
    else
        pass "unmarked root: message does not prescribe '$forbidden'"
    fi
done

# --- 6. stale projection -> staleness notice naming `gw config sync` -------
ws_stale="$(make_workspace ws_stale)"
write_projection "$ws_stale" "$(printf 'a%.0s' {1..64})"
out="$(run_hook "$TEST_ROOT" "$MATCHING_PAYLOAD" \
    CLAUDE_PLUGIN_ROOT="$REPO_ROOT" GRAPH_WORKS_DIR="$ws_stale")"
assert_output \
    "stale projection: staleness notice names gw config sync, context still injected" \
    "nested" "required" \
    "$ws_stale/okf/<work-path>/references/" "" \
    "present" \
    "graph-works-config-stale${US}gw config sync${US}workspace.yaml" \
    "$out"

# --- 7. hash agrees -> no staleness notice --------------------------------
ws_fresh="$(make_workspace ws_fresh)"
write_projection "$ws_fresh" "$(sha256_of "$ws_fresh/workspace.yaml")"
out="$(run_hook "$TEST_ROOT" "$MATCHING_PAYLOAD" \
    CLAUDE_PLUGIN_ROOT="$REPO_ROOT" GRAPH_WORKS_DIR="$ws_fresh")"
assert_output \
    "fresh projection: no staleness notice" \
    "nested" "required" \
    "$ws_fresh/okf/<work-path>/references/" "" \
    "absent" "" \
    "$out"

# A missing source fingerprint now requires refresh while routing still injects.
ws_nullsha="$(make_workspace ws_nullsha)"
write_projection "$ws_nullsha" "-"
out="$(run_hook "$TEST_ROOT" "$MATCHING_PAYLOAD" \
    CLAUDE_PLUGIN_ROOT="$REPO_ROOT" GRAPH_WORKS_DIR="$ws_nullsha")"
assert_output \
    "null source fingerprint: refresh notice, context still injected" \
    "nested" "required" \
    "$ws_nullsha/okf/<work-path>/references/" "" \
    "present" "gw config sync" \
    "$out"

# --- 7b. overlay present and recorded correctly -> no notice ---------------
ws_overlay_ok="$(make_workspace ws_overlay_ok)"
overlay_sha="$(add_local_manifest "$ws_overlay_ok")"
write_projection "$ws_overlay_ok" "$(sha256_of "$ws_overlay_ok/workspace.yaml")" "" "$overlay_sha"
out="$(run_hook "$TEST_ROOT" "$MATCHING_PAYLOAD" \
    CLAUDE_PLUGIN_ROOT="$REPO_ROOT" GRAPH_WORKS_DIR="$ws_overlay_ok")"
assert_output \
    "overlay hash agrees: no staleness notice" \
    "nested" "required" \
    "$ws_overlay_ok/okf/<work-path>/references/" "" \
    "absent" "" \
    "$out"

# --- 7c. overlay present, projection predates it -> notice names it --------
ws_overlay_new="$(make_workspace ws_overlay_new)"
write_projection "$ws_overlay_new" "$(sha256_of "$ws_overlay_new/workspace.yaml")"
add_local_manifest "$ws_overlay_new" >/dev/null
out="$(run_hook "$TEST_ROOT" "$MATCHING_PAYLOAD" \
    CLAUDE_PLUGIN_ROOT="$REPO_ROOT" GRAPH_WORKS_DIR="$ws_overlay_new")"
assert_output \
    "local manifest appeared after the projection: notice names workspace.local.yaml" \
    "nested" "required" \
    "$ws_overlay_new/okf/<work-path>/references/" "" \
    "present" \
    "graph-works-config-stale${US}gw config sync${US}workspace.local.yaml" \
    "$out"

# --- 7d. overlay recorded but the file is gone -> notice -------------------
ws_overlay_gone="$(make_workspace ws_overlay_gone)"
write_projection "$ws_overlay_gone" "$(sha256_of "$ws_overlay_gone/workspace.yaml")" "" "$(printf 'b%.0s' {1..64})"
out="$(run_hook "$TEST_ROOT" "$MATCHING_PAYLOAD" \
    CLAUDE_PLUGIN_ROOT="$REPO_ROOT" GRAPH_WORKS_DIR="$ws_overlay_gone")"
assert_output \
    "recorded local manifest removed: notice names workspace.local.yaml" \
    "nested" "required" \
    "$ws_overlay_gone/okf/<work-path>/references/" "" \
    "present" \
    "graph-works-config-stale${US}gw config sync${US}workspace.local.yaml" \
    "$out"

# --- 7e. both layers drifted -> one notice naming both --------------------
ws_both="$(make_workspace ws_both)"
write_projection "$ws_both" "$(printf 'a%.0s' {1..64})" "" "$(printf 'b%.0s' {1..64})"
add_local_manifest "$ws_both" >/dev/null
out="$(run_hook "$TEST_ROOT" "$MATCHING_PAYLOAD" \
    CLAUDE_PLUGIN_ROOT="$REPO_ROOT" GRAPH_WORKS_DIR="$ws_both")"
assert_output \
    "both layers drifted: one notice naming both files" \
    "nested" "required" \
    "$ws_both/okf/<work-path>/references/" "" \
    "present" \
    "graph-works-config-stale${US}workspace.yaml${US}workspace.local.yaml${US}gw config sync" \
    "$out"

# Legacy projections must never silently report fresh.
ws_old="$(make_workspace ws_old)"
mkdir -p "$ws_old/.gw/cache"
printf '{"_meta":{"source_sha256":"%s"}}\n' "$(sha256_of "$ws_old/workspace.yaml")" > "$ws_old/.gw/cache/config.json"
out="$(run_hook "$TEST_ROOT" "$MATCHING_PAYLOAD" CLAUDE_PLUGIN_ROOT="$REPO_ROOT" GRAPH_WORKS_DIR="$ws_old")"
assert_output "old metadata requests refresh" "nested" "required" "" "" "present" "gw config sync" "$out"

# --- 8. the three platform shapes ----------------------------------------
out="$(run_hook "$TEST_ROOT" "$MATCHING_PAYLOAD" \
    CLAUDE_PLUGIN_ROOT="$REPO_ROOT" GRAPH_WORKS_DIR="$ws_default")"
assert_output \
    "Claude Code emits nested PreToolUse additionalContext" \
    "nested" "required" "" "" "present" "gw config sync" \
    "$out"

out="$(run_hook "$TEST_ROOT" "$MATCHING_PAYLOAD" \
    CURSOR_PLUGIN_ROOT="$REPO_ROOT" CLAUDE_PLUGIN_ROOT="$REPO_ROOT" \
    GRAPH_WORKS_DIR="$ws_default")"
assert_output \
    "Cursor emits top-level additional_context only" \
    "cursor" "required" "" "" "present" "gw config sync" \
    "$out"

out="$(run_hook "$TEST_ROOT" "$MATCHING_PAYLOAD" \
    COPILOT_CLI=1 CLAUDE_PLUGIN_ROOT="$REPO_ROOT" GRAPH_WORKS_DIR="$ws_default")"
assert_output \
    "Copilot CLI / SDK emits top-level additionalContext only" \
    "sdk" "required" "" "" "present" "gw config sync" \
    "$out"

# --- 9. regression: no hook imports the retired workspace_io / work_io ----
# The E7 landing replaced both modules; an import of either resolves nothing
# and the silent branch above would hide the regression rather than report it.
for module in workspace_io work_io; do
    if grep -Fq "$module" "$HOOK_UNDER_TEST" "$SESSION_START_HOOK"; then
        fail "no hook references the retired module '$module'"
        grep -Fn "$module" "$HOOK_UNDER_TEST" "$SESSION_START_HOOK" | sed 's/^/      /'
    else
        pass "no hook references the retired module '$module'"
    fi
done

# Neither hook may spawn a Python stack: the installed plugin tree
# (~/.claude/plugins/.../graph-works/hooks) has no uv project to run in.
for hook in "$HOOK_UNDER_TEST" "$SESSION_START_HOOK"; do
    if grep -Eq '(^|[^-[:alnum:]])uv run' "$hook"; then
        fail "$(basename "$hook") does not shell out to uv"
        grep -En '(^|[^-[:alnum:]])uv run' "$hook" | sed 's/^/      /'
    else
        pass "$(basename "$hook") does not shell out to uv"
    fi
done

if [[ "$FAILURES" -gt 0 ]]; then
    echo "STATUS: FAILED ($FAILURES failure(s))"
    exit 1
fi

echo "STATUS: PASSED"
