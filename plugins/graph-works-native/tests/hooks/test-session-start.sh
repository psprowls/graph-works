#!/usr/bin/env bash
# Tests for hooks/session-start — the Graph Works-native SessionStart bootstrap.
#
# Authored fresh rather than derived from the vendored subtree's suite: the
# native plugin carries nothing upstream-derived except the attributed
# hooks/run-hook.cmd. The subtree's own tests/hooks/test-session-start.sh
# stays where it is and goes on testing the subtree's unchanged hook until
# the cutover deletes both.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
HOOK="$PLUGIN_ROOT/hooks/session-start"
WRAPPER="$PLUGIN_ROOT/hooks/run-hook.cmd"
HOOKS_JSON="$PLUGIN_ROOT/hooks/hooks.json"
PLUGIN_JSON="$PLUGIN_ROOT/.claude-plugin/plugin.json"
BOOTSTRAP="$PLUGIN_ROOT/skills/using-graph-works/SKILL.md"

FAILURES=0
TEST_HOME="$(mktemp -d)"
trap 'rm -rf "$TEST_HOME"' EXIT

pass() { echo "  [PASS] $1"; }
fail() { echo "  [FAIL] $1"; FAILURES=$((FAILURES + 1)); }

# assert_shape <description> <expected-shape> <env-assignments...> -- <command...>
# Runs the hook in a scrubbed environment and validates the JSON it prints.
# EXPECT_SHAPE selects which single context field must be present, and which
# two must be absent — Claude Code reads both additional_context and
# hookSpecificOutput without deduplication, so emitting more than one
# double-injects.
assert_shape() {
    local description="$1" shape="$2"; shift 2
    local output
    if ! output="$(env -i PATH="${PATH:-}" HOME="$TEST_HOME" "$@" 2>&1)"; then
        fail "$description"
        echo "      hook exited non-zero:"
        printf '%s\n' "$output" | sed 's/^/        /'
        return
    fi
    if printf '%s' "$output" | EXPECT_SHAPE="$shape" node -e '
const payload = JSON.parse(require("fs").readFileSync(0, "utf8"));
const has = (o, k) => Object.prototype.hasOwnProperty.call(o, k);
const die = (m) => { console.error(m); process.exit(1); };

const shape = process.env.EXPECT_SHAPE;
const fields = { nested: "hookSpecificOutput", cursor: "additional_context", sdk: "additionalContext" };
if (!(shape in fields)) die(`unknown shape: ${shape}`);
for (const [name, key] of Object.entries(fields)) {
  if (name !== shape && has(payload, key)) die(`${shape} output also carried ${key}`);
}
if (!has(payload, fields[shape])) die(`missing ${fields[shape]}`);

let context;
if (shape === "nested") {
  const out = payload.hookSpecificOutput;
  if (!out || typeof out !== "object" || Array.isArray(out)) die("hookSpecificOutput is not an object");
  if (out.hookEventName !== "SessionStart") die(`unexpected hookEventName: ${out.hookEventName}`);
  context = out.additionalContext;
} else {
  context = payload[fields[shape]];
}
if (typeof context !== "string" || context.trim() === "") die("injected context was empty");
if (!context.includes("gw:using-graph-works")) die("context does not label the bootstrap skill");
if (!context.includes("gw work next")) die("context does not carry the bootstrap body");
if (context.includes("using-superpowers")) die("context still references using-superpowers");
'; then
        pass "$description"
    else
        fail "$description"
        echo "      output:"
        printf '%s\n' "$output" | sed 's/^/        /'
    fi
}

echo "native SessionStart hook tests"

# Registration shape. shell:"bash" is what makes Claude Code on Windows
# dispatch through Git Bash instead of PowerShell/cmd.exe, whose parsers break
# on the quoted command string.
if node -e '
const hooks = JSON.parse(require("fs").readFileSync(process.argv[1], "utf8"));
const entry = hooks.hooks.SessionStart[0].hooks[0];
if (hooks.hooks.SessionStart[0].matcher !== "startup|clear|compact") {
  console.error(`unexpected SessionStart matcher: ${hooks.hooks.SessionStart[0].matcher}`);
  process.exit(1);
}
if (entry.shell !== "bash") {
  console.error(`SessionStart hook shell is ${JSON.stringify(entry.shell)}, expected "bash"`);
  process.exit(1);
}
if (!/run-hook\.cmd" session-start$/.test(entry.command)) {
  console.error(`unexpected SessionStart command shape: ${entry.command}`);
  process.exit(1);
}
' "$HOOKS_JSON"; then
    pass "hooks.json registers SessionStart with shell:bash dispatch"
else
    fail "hooks.json registers SessionStart with shell:bash dispatch"
fi

if node -e '
const hooks = JSON.parse(require("fs").readFileSync(process.argv[1], "utf8"));
const entry = hooks.hooks.PreToolUse[0].hooks[0];
if (hooks.hooks.PreToolUse[0].matcher !== "Skill") {
  console.error(`unexpected PreToolUse matcher: ${hooks.hooks.PreToolUse[0].matcher}`);
  process.exit(1);
}
if (!/run-hook\.cmd" skill-doc-routing$/.test(entry.command)) {
  console.error(`unexpected PreToolUse command shape: ${entry.command}`);
  process.exit(1);
}
' "$HOOKS_JSON"; then
    pass "hooks.json registers PreToolUse Skill routing"
else
    fail "hooks.json registers PreToolUse Skill routing"
fi

if node -e '
const plugin = JSON.parse(require("fs").readFileSync(process.argv[1], "utf8"));
if (plugin.name !== "gw") {
  console.error(`unexpected plugin.json name: ${plugin.name}`);
  process.exit(1);
}
if (plugin.version !== "0.1.0") {
  console.error(`unexpected plugin.json version: ${plugin.version}`);
  process.exit(1);
}
' "$PLUGIN_JSON"; then
    pass "plugin.json parses as valid JSON with name gw and version 0.1.0"
else
    fail "plugin.json parses as valid JSON with name gw and version 0.1.0"
fi

if [[ -f "$BOOTSTRAP" ]]; then
    pass "skills/using-graph-works/SKILL.md exists"
else
    fail "skills/using-graph-works/SKILL.md exists"
fi

assert_shape "Claude Code emits nested SessionStart additionalContext" nested \
    CLAUDE_PLUGIN_ROOT="$PLUGIN_ROOT" bash "$HOOK"

assert_shape "run-hook.cmd dispatches the named session-start script" nested \
    CLAUDE_PLUGIN_ROOT="$PLUGIN_ROOT" bash "$WRAPPER" session-start

assert_shape "Cursor emits top-level additional_context only" cursor \
    CURSOR_PLUGIN_ROOT="$PLUGIN_ROOT" CLAUDE_PLUGIN_ROOT="$PLUGIN_ROOT" bash "$HOOK"

assert_shape "Copilot CLI emits top-level additionalContext only" sdk \
    COPILOT_CLI=1 CLAUDE_PLUGIN_ROOT="$PLUGIN_ROOT" bash "$HOOK"

if [[ "$FAILURES" -gt 0 ]]; then
    echo "STATUS: FAILED ($FAILURES failure(s))"
    exit 1
fi

echo "STATUS: PASSED"
