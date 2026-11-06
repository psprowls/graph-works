#!/usr/bin/env bash
# Toolchain preflight: fail in the first second, with one unambiguous line, when
# the host (not the code) is broken. Bash on purpose -- it cannot assume Python.
# Silent and exit 0 when every check passes.
set -u

fail() {
  echo "TOOLCHAIN PREFLIGHT FAILED (host, not code): $1 — $2" >&2
  exit 1
}

git_rc=0
git --version >/dev/null 2>&1 || git_rc=$?
if [ "$git_rc" -eq 69 ]; then
  fail "git --version exited 69" "accept the Xcode licence (\`sudo xcodebuild -license\`) or put a working git (e.g. \`/opt/homebrew/bin\`) first on PATH"
elif [ "$git_rc" -ne 0 ]; then
  fail "git --version" "\`git --version\` failed with exit $git_rc"
fi

command -v uv >/dev/null 2>&1 || fail "uv not found" "install uv and put it on PATH"

# The pin belongs to this repository, whatever directory the script runs from.
pin_file="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.python-version"
pin=$(tr -d '[:space:]' 2>/dev/null <"$pin_file") || pin=""
[ -n "$pin" ] || fail "python interpreter" "cannot read a pin from $pin_file"

py_version=$(uv run python -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null) || py_version=""
py_version=${py_version%$'\r'} # Python on Windows prints CRLF; $(...) strips only the LF
if [ "$py_version" != "$pin" ]; then
  fail "python interpreter" "uv resolved Python ${py_version:-unknown}; .python-version pins $pin"
fi
