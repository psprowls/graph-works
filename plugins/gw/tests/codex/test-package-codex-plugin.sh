#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SCRIPT_UNDER_TEST="$REPO_ROOT/scripts/package-codex-plugin.sh"

FAILURES=0
TEST_ROOT="$(mktemp -d)"

cleanup() {
  rm -rf "$TEST_ROOT"
}
trap cleanup EXIT

pass() { echo "  [PASS] $1"; }

fail() {
  echo "  [FAIL] $1"
  FAILURES=$((FAILURES + 1))
}

assert_equals() {
  local actual="$1" expected="$2" description="$3"
  if [[ "$actual" == "$expected" ]]; then
    pass "$description"
  else
    fail "$description"
    echo "    expected: $expected"
    echo "    actual:   $actual"
  fi
}

assert_contains() {
  local haystack="$1" needle="$2" description="$3"
  if printf '%s' "$haystack" | grep -Fq -- "$needle"; then
    pass "$description"
  else
    fail "$description"
    echo "    expected to find: $needle"
  fi
}

assert_not_matches() {
  local haystack="$1" pattern="$2" description="$3"
  if printf '%s' "$haystack" | grep -Eq -- "$pattern"; then
    fail "$description"
    echo "    did not expect to match: $pattern"
  else
    pass "$description"
  fi
}

list_archive() {
  local archive_path="$1"
  case "$archive_path" in
    *.tar.gz|*.tgz) tar -tzf "$archive_path" ;;
    *) unzip -Z1 "$archive_path" ;;
  esac
}

normalize_archive_paths() { sed 's#/$##' | LC_ALL=C sort; }

extract_archive() {
  local archive_path="$1" destination="$2"
  mkdir -p "$destination"
  case "$archive_path" in
    *.tar.gz|*.tgz) tar -xzf "$archive_path" -C "$destination" ;;
    *) unzip -q "$archive_path" -d "$destination" ;;
  esac
}

read_archive_file() {
  local archive_path="$1" file_path="$2"
  case "$archive_path" in
    *.tar.gz|*.tgz) tar -xOf "$archive_path" "$file_path" ;;
    *) unzip -p "$archive_path" "$file_path" ;;
  esac
}

echo "Codex package archive tests"

archive="$TEST_ROOT/graph-works.zip"
tar_archive="$TEST_ROOT/graph-works.tar.gz"
extracted="$TEST_ROOT/extracted"
tar_extracted="$TEST_ROOT/tar-extracted"

source_hooks="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("hooks"))' "$REPO_ROOT/.codex-plugin/plugin.json")"
assert_equals "$source_hooks" "{}" "source Codex manifest suppresses local hook auto-discovery"

if output="$("$SCRIPT_UNDER_TEST" --allow-dirty --output "$archive" 2>&1)"; then
  pass "package script exits successfully"
else
  fail "package script exits successfully"
  printf '%s\n' "$output" | sed 's/^/      /'
fi

if [[ -f "$archive" ]]; then
  pass "package script writes archive"
else
  fail "package script writes archive"
fi

assert_contains "$output" "Archive:" "reports archive path"
assert_contains "$output" "Format:  zip" "reports default zip format"
assert_contains "$output" "SHA-256:" "reports archive checksum"

extract_archive "$archive" "$extracted"

archive_paths="$(list_archive "$archive" | normalize_archive_paths)"
unexpected_pattern='(^graph-works/|^\.agents/|^hooks/|^package\.json$|^\.git|^\.pytest_cache|^\.ruff_cache|^scripts/|^tests/|^docs/|^evals/|^lib/|^\.claude|^\.pi|^AGENTS\.md$|^CLAUDE\.md$|^RELEASE-NOTES\.md$|^CHANGELOG\.md$)'
assert_not_matches "$archive_paths" "$unexpected_pattern" "archive excludes source-only paths"
assert_contains "$archive_paths" ".codex-plugin/plugin.json" "archive includes Codex manifest"
assert_contains "$archive_paths" "skills/using-graph-works/SKILL.md" "archive includes skills"

# Every entry point ships. This is the regression that motivated collapsing
# commands/ and agents/ into skills/: the archive pathspec never listed those
# two directories, so eleven of thirteen entry points reached no Codex install.
# See work/tech-debt-codex-slash-command-gap.
for entry_point in scan ingest query lint file archive log status regen-index \
    proposals onboard workflow auto-drive; do
    assert_contains "$archive_paths" "skills/$entry_point/SKILL.md" \
        "archive includes the '$entry_point' entry point"
done

# Icon filenames are child 3's call, so assert against the manifest's own values.
while IFS= read -r icon_rel; do
  assert_contains "$archive_paths" "$icon_rel" "archive includes $icon_rel"
done < <(python3 -c '
import json, sys
m = json.load(open(sys.argv[1]))["interface"]
for key in ("composerIcon", "logo"):
    print(m[key].lstrip("./"))
' "$REPO_ROOT/.codex-plugin/plugin.json")

# No openai.yaml metadata anywhere: decision 1 removed the whole apparatus.
assert_not_matches "$archive_paths" 'agents/openai\.yaml' "archive ships no OpenAI portal metadata"

manifest_summary="$(read_archive_file "$archive" .codex-plugin/plugin.json | python3 -c 'import json,sys; d=json.load(sys.stdin); print("\t".join([d["name"], d["version"], d["skills"], str(d.get("hooks"))]))')"
assert_equals "$manifest_summary" "gw	0.1.0	./skills/	{}" "archive manifest preserves source identity and empty hooks"

skill_count="$(find "$extracted/skills" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')"
if [[ "$skill_count" -gt 0 ]]; then
  pass "archive ships at least one skill"
else
  fail "archive ships at least one skill"
fi

if [[ -x "$extracted/skills/shared/resolve-workspace.sh" ]]; then
  pass "archive preserves executable script mode"
else
  fail "archive preserves executable script mode"
fi

zip_times="$(python3 - "$archive" <<'PY'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as archive:
    print("\n".join(sorted({str(info.date_time) for info in archive.infolist()})))
PY
)"
assert_equals "$zip_times" "(1980, 1, 1, 0, 0, 0)" "zip archive normalizes entry timestamps"

if tar_output="$("$SCRIPT_UNDER_TEST" --allow-dirty --format tar.gz --output "$tar_archive" 2>&1)"; then
  pass "package script writes explicit tar.gz archive"
else
  fail "package script writes explicit tar.gz archive"
  printf '%s\n' "$tar_output" | sed 's/^/      /'
fi
assert_contains "$tar_output" "Format:  tar.gz" "reports explicit tar.gz format"

extract_archive "$tar_archive" "$tar_extracted"
tar_archive_paths="$(list_archive "$tar_archive" | normalize_archive_paths)"
assert_equals "$tar_archive_paths" "$archive_paths" "zip and tar.gz archives contain the same paths"

tar_task_brief_mode="$(tar -tzvf "$tar_archive" skills/shared/resolve-workspace.sh | awk '{print $1}')"
assert_equals "$tar_task_brief_mode" "-rwxr-xr-x" "tar.gz archive preserves executable script mode"

tar_metadata_times="$(python3 - "$tar_archive" <<'PY'
import sys, tarfile
with tarfile.open(sys.argv[1]) as archive:
    print(sorted({member.mtime for member in archive.getmembers()}))
PY
)"
assert_equals "$tar_metadata_times" "[0]" "tar.gz archive normalizes entry timestamps"

monorepo_root="$(git -C "$REPO_ROOT" rev-parse --show-toplevel)"
repo_prefix="$(git -C "$REPO_ROOT" rev-parse --show-prefix)"
dirty_repo="$TEST_ROOT/dirty-repo"
git clone -q --no-local "$monorepo_root" "$dirty_repo"
dirty_plugin_root="$dirty_repo/$repo_prefix"
printf '\n# dirty fixture\n' >>"$dirty_plugin_root/tests/test-entry-point-skills.sh"
set +e
dirty_output="$(
  cd "$dirty_plugin_root"
  scripts/package-codex-plugin.sh --output "$TEST_ROOT/dirty.zip" 2>&1
)"
dirty_status=$?
set -e
if [[ "$dirty_status" -ne 0 ]]; then
  pass "package script rejects dirty worktree by default"
else
  fail "package script rejects dirty worktree by default"
fi
assert_contains "$dirty_output" "Working tree has uncommitted changes:" "dirty worktree reports changed files"

if [[ "$FAILURES" -eq 0 ]]; then
  echo "All Codex package archive tests passed"
else
  echo "$FAILURES Codex package archive test(s) failed"
  exit 1
fi
