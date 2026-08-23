#!/usr/bin/env bash
# Deterministic bootstrap -> scan -> validate -> rescan smoke against this repo.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd -P)"
GW="$REPO_ROOT/.venv/bin/gw"
TEST_WS="$(mktemp -d "${TMPDIR:-/tmp}/gw-smoke.XXXXXX")"
FIRST_OKF="${TEST_WS}.first-okf"
CONTEXT_BACKUP="$TEST_WS/.source-context"
CLAUDE_EXISTED=0
AGENTS_EXISTED=0
REPO_KEY="$(basename "$REPO_ROOT")"
if REMOTE_URL="$(git -C "$REPO_ROOT" remote get-url origin 2>/dev/null)"; then
  REPO_KEY="$(basename "${REMOTE_URL%.git}")"
fi

# Machine-readable git paths must not be C-quoted. This checkout deliberately
# carries a non-ASCII normalization fixture that exercises the distinction.
export GIT_CONFIG_COUNT=1
export GIT_CONFIG_KEY_0=core.quotePath
export GIT_CONFIG_VALUE_0=false

mkdir -p "$CONTEXT_BACKUP"
if [[ -f "$REPO_ROOT/CLAUDE.md" ]]; then
  cp -p "$REPO_ROOT/CLAUDE.md" "$CONTEXT_BACKUP/CLAUDE.md"
  CLAUDE_EXISTED=1
fi
if [[ -f "$REPO_ROOT/AGENTS.md" ]]; then
  cp -p "$REPO_ROOT/AGENTS.md" "$CONTEXT_BACKUP/AGENTS.md"
  AGENTS_EXISTED=1
fi

restore_context() {
  if [[ "$CLAUDE_EXISTED" == "1" ]]; then
    cp -p "$CONTEXT_BACKUP/CLAUDE.md" "$REPO_ROOT/CLAUDE.md"
  else
    rm -f -- "$REPO_ROOT/CLAUDE.md"
  fi
  if [[ "$AGENTS_EXISTED" == "1" ]]; then
    cp -p "$CONTEXT_BACKUP/AGENTS.md" "$REPO_ROOT/AGENTS.md"
  else
    rm -f -- "$REPO_ROOT/AGENTS.md"
  fi
}

cleanup() {
  restore_context
  if [[ "${KEEP_SMOKE_WS:-0}" == "1" ]]; then
    echo "workspace retained at: $TEST_WS"
    echo "first-run snapshot retained at: $FIRST_OKF"
    return
  fi
  rm -rf -- "$TEST_WS" "$FIRST_OKF"
}
trap cleanup EXIT

if [[ ! -x "$GW" ]]; then
  echo "error: $GW not found — run 'uv sync' first" >&2
  exit 1
fi

run_scan() {
  local result
  result="$("$GW" scan --no-narrate --workspace "$TEST_WS" --json)"
  echo "$result"
  if ! grep -Fq '"ok": true' <<<"$result"; then
    echo "error: scan did not report a successful JSON result" >&2
    return 1
  fi
}

echo "== bootstrap =="
"$GW" bootstrap --topic "gw smoke test" --workspace "$TEST_WS" --repo-root "$REPO_ROOT"
if [[ "$REPO_KEY" != "$(basename "$REPO_ROOT")" ]]; then
  "$GW" config set "repositories.${REPO_KEY}.path" "$REPO_ROOT" --workspace "$TEST_WS" >/dev/null
  "$GW" config unset "repositories.$(basename "$REPO_ROOT").path" --workspace "$TEST_WS" >/dev/null
fi

echo
echo "== fresh structural scan =="
run_scan

echo
echo "== complete deterministic human-owned smoke prose =="
uv run --package graph-works-core python "$SCRIPT_DIR/complete_prose.py" "$TEST_WS"

echo
echo "== reconcile completed prose into catalogs =="
run_scan

echo
echo "== placement tree and strict validation =="
uv run --package graph-works-core python "$SCRIPT_DIR/assert_contract.py" "$TEST_WS"

cp -R "$TEST_WS/okf" "$FIRST_OKF"

echo
echo "== idempotent post-completion scan =="
run_scan
git diff --no-index --exit-code "$FIRST_OKF" "$TEST_WS/okf"

echo
echo "post-completion scan left okf/ byte-identical"
