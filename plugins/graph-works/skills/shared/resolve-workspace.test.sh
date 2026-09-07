#!/usr/bin/env bash
# Tests for resolve-workspace.sh — the pure-bash workspace resolver.
# Run: bash resolve-workspace.test.sh   (exit 0 = all pass)
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RESOLVER="$SCRIPT_DIR/resolve-workspace.sh"

pass=0
fail=0

# assert_eq <name> <expected> <actual>
assert_eq() {
  local name="$1" expected="$2" actual="$3"
  if [[ "$expected" == "$actual" ]]; then
    pass=$((pass + 1))
    echo "ok   - $name"
  else
    fail=$((fail + 1))
    echo "FAIL - $name"
    echo "        expected: [$expected]"
    echo "        actual:   [$actual]"
  fi
}

TMP="$(mktemp -d)"
TMP="$(cd "$TMP" && pwd -P)"   # resolve /var -> /private/var so paths compare equal
trap 'rm -rf "$TMP"' EXIT

# --- (a) GRAPH_WORKS_DIR set -> echoes it, exit 0 ---------------------
out="$(GRAPH_WORKS_DIR=/some/ws bash "$RESOLVER" 2>/dev/null)"
rc=$?
assert_eq "env var: echoes value" "/some/ws" "$out"
assert_eq "env var: exit 0" "0" "$rc"

# env var beats on-disk discovery
mkdir -p "$TMP/proj_a/.git" "$TMP/proj_a/.works"
out="$(cd "$TMP/proj_a" && GRAPH_WORKS_DIR=/env/ws bash "$RESOLVER" 2>/dev/null)"
assert_eq "env var beats discovery" "/env/ws" "$out"

# --- (b) walk-up finds .git; <repo>/.works exists -> echoes it -------------
mkdir -p "$TMP/proj_b/.git" "$TMP/proj_b/.works"
out="$(cd "$TMP/proj_b" && env -u GRAPH_WORKS_DIR bash "$RESOLVER" 2>/dev/null)"
rc=$?
assert_eq "discovery: echoes <repo>/.works" "$TMP/proj_b/.works" "$out"
assert_eq "discovery: exit 0" "0" "$rc"

# nested cwd exercises the walk-up
mkdir -p "$TMP/proj_b/a/b/c"
out="$(cd "$TMP/proj_b/a/b/c" && env -u GRAPH_WORKS_DIR bash "$RESOLVER" 2>/dev/null)"
assert_eq "discovery: walk-up from nested cwd" "$TMP/proj_b/.works" "$out"

# worktree-style .git FILE (not dir) also counts as a repo root
mkdir -p "$TMP/wt/.works"
printf 'gitdir: /elsewhere/.git/worktrees/wt\n' > "$TMP/wt/.git"
out="$(cd "$TMP/wt" && env -u GRAPH_WORKS_DIR bash "$RESOLVER" 2>/dev/null)"
assert_eq "discovery: .git file (worktree) counts" "$TMP/wt/.works" "$out"

# caller-supplied start dir argument
out="$(cd "$TMP" && env -u GRAPH_WORKS_DIR bash "$RESOLVER" "$TMP/proj_b/a/b/c" 2>/dev/null)"
assert_eq "start-dir arg: walks up from arg not PWD" "$TMP/proj_b/.works" "$out"

# --- (c) repo WITHOUT .works/ -> empty (never invents a path) --------------
mkdir -p "$TMP/proj_c/.git" "$TMP/proj_c/deep"
out="$(cd "$TMP/proj_c/deep" && env -u GRAPH_WORKS_DIR bash "$RESOLVER" 2>/dev/null)"
rc=$?
assert_eq "repo without .works/: empty stdout" "" "$out"
assert_eq "repo without .works/: exit 0" "0" "$rc"

# nested repo binds to the FIRST (inner) repo root — the outer repo's
# .works/ must NOT leak through
mkdir -p "$TMP/outer/.git" "$TMP/outer/.works" "$TMP/outer/inner/.git" "$TMP/outer/inner/src"
out="$(cd "$TMP/outer/inner/src" && env -u GRAPH_WORKS_DIR bash "$RESOLVER" 2>/dev/null)"
assert_eq "nested repo: binds to inner root, empty" "" "$out"

# a .works path that exists but is a FILE does not count
mkdir -p "$TMP/proj_f/.git"
touch "$TMP/proj_f/.works"
out="$(cd "$TMP/proj_f" && env -u GRAPH_WORKS_DIR bash "$RESOLVER" 2>/dev/null)"
assert_eq ".works is a file: empty" "" "$out"

# --- (d) graph-works discovery chain ------------------------------------

# explicit argument wins over GRAPH_WORKS_DIR
mkdir -p "$TMP/r2/.git"
mkdir -p "$TMP/explicit"; : > "$TMP/explicit/workspace.yaml"
mkdir -p "$TMP/envws"; : > "$TMP/envws/workspace.yaml"
out="$(GRAPH_WORKS_DIR="$TMP/envws" bash "$RESOLVER" "$TMP/explicit" 2>/dev/null)"
assert_eq "explicit argument wins over GRAPH_WORKS_DIR" "$TMP/explicit" "$out"

# no git repo anywhere prints nothing and exits 0
mkdir -p "$TMP/bare/dir"
out="$(cd "$TMP/bare/dir" && env -u GRAPH_WORKS_DIR bash "$RESOLVER" 2>/dev/null)"
rc=$?
assert_eq "no git repo anywhere: empty stdout" "" "$out"
assert_eq "no git repo anywhere: exit 0" "0" "$rc"

# --- (e) parity with graph_works_core.workspace.discovery.resolve_root ----
#
# This script is a second implementation of a discovery chain that also exists
# in Python, and two implementations of one rule drift. The matrix below is the
# price of having them: it pins the relationship between the two rather than
# testing either alone.
#
# The relationship is not equality, because the two answer slightly different
# questions. `resolve_root` returns the *prospective* root and never checks
# whether it exists; this script declines to name a directory that is not
# there. So:
#
#   - when this script emits a path, `resolve_root` returns the same path;
#   - when it emits nothing, `resolve_root`'s answer is not a directory.
#
# Two documented divergences keep the matrix honest and are excluded from it
# rather than papered over:
#
#   - The single argument is overloaded here — an explicit workspace when it
#     is marked, a start directory otherwise — where Python takes `workspace`
#     and `cwd` separately. Each row therefore states which Python parameter
#     its argument corresponds to.
#   - `GRAPH_WORKS_DIR` is echoed verbatim here and `expanduser().resolve()`d
#     in Python. The matrix uses already-absolute, already-resolved values, so
#     a row that disagrees is real drift and not that.
#
# `uv` is a hard requirement, not a conditional skip: this suite runs from the
# checkout, and a gate that silently skips reports green while covering nothing.
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"

# Path-vocabulary boundary. Under Git Bash the shell speaks MSYS paths
# (/tmp/...) while a native-Windows python.exe speaks Win32 paths (C:/...);
# stdin is the one channel MSYS does not convert for us, so convert here.
# Identity on POSIX, where cygpath does not exist, and on an empty value.
to_py() {
  [ -z "$1" ] && return 0
  if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi
}

# Rows: name | bash arg | bash cwd | GRAPH_WORKS_DIR | python workspace= | python cwd=
PARITY_ROWS=(
  "env var|||$TMP/envws|$TMP/envws|"
  "repo with .works||$TMP/proj_b|||$TMP/proj_b"
  "walk-up from nested cwd||$TMP/proj_b/a/b/c|||$TMP/proj_b/a/b/c"
  "worktree .git file||$TMP/wt|||$TMP/wt"
  "start-dir argument|$TMP/proj_b/a/b/c|$TMP|||$TMP/proj_b/a/b/c"
  "explicit marked workspace|$TMP/explicit|$TMP||$TMP/explicit|"
  "repo without .works||$TMP/proj_c/deep|||$TMP/proj_c/deep"
  "nested repo, inner root||$TMP/outer/inner/src|||$TMP/outer/inner/src"
  ".works is a file||$TMP/proj_f|||$TMP/proj_f"
  "no repo anywhere||$TMP/bare/dir|||$TMP/bare/dir"
)

# One uv spin-up for the whole matrix: the rows go in on stdin, one resolved
# path per row comes back out.
python_answers=$(
  for row in "${PARITY_ROWS[@]}"; do
    IFS='|' read -r _name _arg _cwd _env py_workspace py_cwd <<< "$row"
    printf '%s\t%s\t%s\n' "$(to_py "$py_workspace")" "$(to_py "$py_cwd")" "$(to_py "$_env")"
  done | uv run --project "$REPO_ROOT" python -c '
import sys
from pathlib import Path

from graph_works_core.workspace.discovery import resolve_root

for line in sys.stdin.read().splitlines():
    workspace, cwd, env = line.split("\t")
    environ = {"GRAPH_WORKS_DIR": env} if env else {}
    # Win32 paths come back with backslashes; cygpath -m emits forward
    # slashes, so normalise the separator to match before comparing.
    print(str(resolve_root(
        workspace=workspace or None,
        cwd=cwd or Path.cwd(),
        environ=environ,
    )).replace("\\", "/"))
' 2>/dev/null | tr -d '\r'
)

if [[ -z "$python_answers" ]]; then
  fail=$((fail + 1))
  echo "FAIL - parity: could not run graph_works_core.workspace.discovery.resolve_root"
  echo "        uv run --project $REPO_ROOT failed; the parity matrix covered nothing"
else
  # No mapfile: this suite must run under macOS's bash 3.2.
  python_paths=()
  while IFS= read -r answer; do python_paths+=("$answer"); done <<< "$python_answers"
  index=0
  for row in "${PARITY_ROWS[@]}"; do
    IFS='|' read -r name arg cwd env_value _py_workspace _py_cwd <<< "$row"
    python_path="${python_paths[$index]}"
    index=$((index + 1))

    if [[ -n "$env_value" ]]; then
      bash_out="$(cd "$cwd" 2>/dev/null || cd "$TMP"; GRAPH_WORKS_DIR="$env_value" bash "$RESOLVER" ${arg:+"$arg"} 2>/dev/null)"
    else
      bash_out="$(cd "${cwd:-$TMP}" && env -u GRAPH_WORKS_DIR bash "$RESOLVER" ${arg:+"$arg"} 2>/dev/null)"
    fi

    if [[ -n "$bash_out" ]]; then
      assert_eq "parity ($name): bash path == resolve_root" "$python_path" "$(to_py "$bash_out")"
    elif [[ -d "$python_path" ]]; then
      fail=$((fail + 1))
      echo "FAIL - parity ($name): bash declined but resolve_root named a real directory"
      echo "        resolve_root: [$python_path]"
    else
      pass=$((pass + 1))
      echo "ok   - parity ($name): both decline ($python_path is not a directory)"
    fi
  done
fi

echo "-------------------------------------"
echo "pass=$pass fail=$fail"
[[ "$fail" -eq 0 ]]
