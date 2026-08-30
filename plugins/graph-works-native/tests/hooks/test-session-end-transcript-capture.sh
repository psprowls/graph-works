#!/usr/bin/env bash
# Tests for hooks/examples/session-end-transcript-capture.sh — the opt-in
# SessionEnd hook `gw config hooks enable transcript` registers.
#
# The Python copy step needs a resolved workspace and a stamped active-work
# pointer, which is graph-works-core's territory (packages/graph-works-core/
# tests/test_hooks.py covers registration and the wheel). This suite pins the
# fail-open shell contract around it: every exit is 0, nothing is written
# outside the trace log, and each skip path names itself in the trace.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
HOOK="$PLUGIN_ROOT/hooks/examples/session-end-transcript-capture.sh"

FAILURES=0
TEST_HOME="$(mktemp -d)"
trap 'rm -rf "$TEST_HOME"' EXIT

pass() { echo "  [PASS] $1"; }
fail() { echo "  [FAIL] $1"; FAILURES=$((FAILURES + 1)); }

# run_hook <trace-log> <stdin-json> [env assignments...]
run_hook() {
    local trace="$1" input="$2"
    shift 2
    env -i PATH="$PATH" HOME="$TEST_HOME" \
        GRAPH_WORKS_TRANSCRIPT_CAPTURE_TRACE_LOG="$trace" "$@" \
        bash "$HOOK" <<<"$input"
}

echo "hooks/examples/session-end-transcript-capture.sh"

if [[ -x "$HOOK" ]]; then pass "script exists and is executable"; else fail "script missing or not executable: $HOOK"; fi

# The vendored fork's copy derives ROOT from its own four-deep path and honours
# a legacy AGENT_RESEARCH_ROOT override that nothing in this repo sets. Both are
# mechanical tells that this file was re-copied from there rather than edited
# here; the depth happens to match, so the path string is what distinguishes them.
if grep -q "graph-works-native/hooks/examples" "$HOOK" && ! grep -q "AGENT_RESEARCH_ROOT" "$HOOK"; then
    pass "root derivation names the native layout and no retired override"
else
    fail "root derivation looks re-copied from the vendored fork"
fi

trace="$TEST_HOME/guard.log"
if run_hook "$trace" '{"session_id":"abcdef123456","transcript_path":"/nonexistent"}' \
        GRAPH_WORKS_TRANSCRIPT_CAPTURE_GUARD=0 && grep -q "skip | guard=0" "$trace"; then
    pass "GRAPH_WORKS_TRANSCRIPT_CAPTURE_GUARD=0 exits 0 and traces the skip"
else
    fail "guard=0 did not fail open with a traced skip"
fi

trace="$TEST_HOME/no-transcript.log"
if run_hook "$trace" '{"session_id":"abcdef123456"}' && grep -q "session=abcdef12 | skip | no-transcript" "$trace"; then
    pass "missing transcript_path exits 0 and traces no-transcript"
else
    fail "missing transcript_path did not fail open with a traced skip"
fi

trace="$TEST_HOME/empty.log"
if run_hook "$trace" '' && grep -q "skip | no-transcript" "$trace"; then
    pass "empty stdin exits 0 and traces no-transcript"
else
    fail "empty stdin did not fail open"
fi

# A real transcript but no workspace/pointer: the Python step runs and must
# still fail open. Pin the interpreter to a python that lacks graph_works_core
# so the test never depends on a resolved workspace.
transcript="$TEST_HOME/t.jsonl"
echo '{}' > "$transcript"
trace="$TEST_HOME/nopython.log"
fake_py="$TEST_HOME/nopy"
printf '#!/bin/sh\nexit 1\n' > "$fake_py"; chmod +x "$fake_py"
if (cd "$TEST_HOME" && run_hook "$trace" "{\"session_id\":\"abcdef123456\",\"transcript_path\":\"$transcript\"}" \
        GRAPH_WORKS_PYTHON="$fake_py") && grep -q " | enter" "$trace" && grep -q " | error | " "$trace"; then
    pass "a failing interpreter still exits 0 and traces the error"
else
    fail "a failing interpreter did not fail open"
fi

echo
if [[ $FAILURES -eq 0 ]]; then echo "All tests passed"; exit 0; fi
echo "$FAILURES test(s) failed"; exit 1
