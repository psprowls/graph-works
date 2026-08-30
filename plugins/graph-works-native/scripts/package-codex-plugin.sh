#!/usr/bin/env bash
#
# Package the Graph Works Codex plugin as a rootless archive for upload.
#
# The archive is standalone: .codex-plugin/, assets/, skills/ sit at the
# archive root. Source-only repo files, hooks, tests, docs, and other harness
# manifests are intentionally not shipped.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

REF="HEAD"
OUTPUT=""
FORMAT=""
ALLOW_DIRTY=0
KEEP_STAGE=0

usage() {
  cat <<'EOF'
Usage:
  scripts/package-codex-plugin.sh [options]

Options:
  --output PATH   Write archive to PATH.
                  Default: ../_tmp/gw-codex-packaging/graph-works-VERSION.zip
  --format FORMAT Archive format: zip or tar.gz. Default: zip.
                  If --output ends in .zip, .tar.gz, or .tgz, that extension is
                  used when --format is omitted.
  --ref REF       Git ref to package. Default: HEAD.
  --allow-dirty   Permit a dirty working tree. The archive still uses --ref.
  --keep-stage    Print and keep the temporary staging directory.
  -h, --help      Show this help.

The archive is rootless: .codex-plugin/, assets/, and skills/ sit at the
archive root. Source-only repo files, hooks, tests, docs, and other harness
manifests are intentionally not shipped.
EOF
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --output)
      [[ $# -ge 2 ]] || die "--output requires a path"
      OUTPUT="$2"
      shift 2
      ;;
    --format)
      [[ $# -ge 2 ]] || die "--format requires a value"
      case "$2" in
        zip) FORMAT="zip" ;;
        tar.gz|tgz) FORMAT="tar.gz" ;;
        *) die "--format must be zip or tar.gz" ;;
      esac
      shift 2
      ;;
    --ref)
      [[ $# -ge 2 ]] || die "--ref requires a value"
      REF="$2"
      shift 2
      ;;
    --allow-dirty) ALLOW_DIRTY=1; shift ;;
    --keep-stage)  KEEP_STAGE=1; shift ;;
    -h|--help)     usage; exit 0 ;;
    *)
      echo "Unknown arg: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

infer_format_from_output() {
  local output_path="$1"
  case "$output_path" in
    *.tar.gz|*.tgz) printf '%s\n' "tar.gz" ;;
    *.zip)          printf '%s\n' "zip" ;;
    *)              return 1 ;;
  esac
}

if [[ -z "$FORMAT" ]]; then
  FORMAT="$(infer_format_from_output "$OUTPUT" || true)"
  if [[ -z "$FORMAT" ]]; then
    FORMAT="zip"
  fi
else
  output_format="$(infer_format_from_output "$OUTPUT" || true)"
  if [[ -n "$output_format" && "$output_format" != "$FORMAT" ]]; then
    die "--output extension does not match --format $FORMAT: $OUTPUT"
  fi
fi

command -v git >/dev/null || die "git not found in PATH"
command -v jq >/dev/null || die "jq not found in PATH"
command -v tar >/dev/null || die "tar not found in PATH"
command -v gzip >/dev/null || die "gzip not found in PATH"
command -v shasum >/dev/null || die "shasum not found in PATH"
if [[ "$FORMAT" == "zip" ]]; then
  command -v zip >/dev/null || die "zip not found in PATH"
  command -v unzip >/dev/null || die "unzip not found in PATH"
fi

git -C "$REPO_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1 ||
  die "repo root is not inside a git checkout: $REPO_ROOT"
git -C "$REPO_ROOT" rev-parse --verify "$REF^{commit}" >/dev/null ||
  die "git ref does not resolve to a commit: $REF"

if [[ "$ALLOW_DIRTY" -ne 1 ]]; then
  dirty_status="$(git -C "$REPO_ROOT" status --porcelain --untracked-files=all -- .)"
  if [[ -n "$dirty_status" ]]; then
    echo "Working tree has uncommitted changes:" >&2
    printf '%s\n' "$dirty_status" | sed 's/^/  /' >&2
    die "commit or stash changes first, or pass --allow-dirty to package $REF anyway"
  fi
fi

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/graph-works-codex-package.XXXXXX")"
STAGE="$WORK_DIR/payload"
ARCHIVE_LIST="$WORK_DIR/archive-list"

cleanup() {
  if [[ "$KEEP_STAGE" -eq 1 ]]; then
    echo "Keeping staging directory: $WORK_DIR" >&2
  else
    rm -rf "$WORK_DIR"
  fi
}
trap cleanup EXIT

mkdir -p "$STAGE"

# Pin tar.umask and extract with -p so staged modes are canonical 755/644
# regardless of the builder's git config or process umask.
#
# The native plugin root's top-level shape (prerequisite P3) has no
# README.md, LICENSE, or CODE_OF_CONDUCT.md, so only these three paths are
# listed. `git archive` errors on a pathspec that matches nothing.
git -C "$REPO_ROOT" -c tar.umask=0022 archive --format=tar "$REF" -- \
  .codex-plugin \
  assets \
  skills \
  | tar -xpf - -C "$STAGE"

VERSION="$(jq -r '.version // empty' "$STAGE/.codex-plugin/plugin.json")"
[[ -n "$VERSION" ]] || die "could not read version from .codex-plugin/plugin.json"

if [[ -z "$OUTPUT" ]]; then
  case "$FORMAT" in
    zip)    OUTPUT="$REPO_ROOT/../_tmp/gw-codex-packaging/graph-works-$VERSION.zip" ;;
    tar.gz) OUTPUT="$REPO_ROOT/../_tmp/gw-codex-packaging/graph-works-$VERSION.tar.gz" ;;
  esac
fi
mkdir -p "$(dirname "$OUTPUT")"
OUTPUT="$(cd "$(dirname "$OUTPUT")" && pwd)/$(basename "$OUTPUT")"

skill_count="$(find "$STAGE/skills" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')"

(
  cd "$STAGE"
  {
    find . -mindepth 1 -type d | sed 's#^\./##' | LC_ALL=C sort
    find . -mindepth 1 -type f | sed 's#^\./##' | LC_ALL=C sort
  } >"$ARCHIVE_LIST"
)

case "$FORMAT" in
  zip)
    # ZIP cannot represent dates earlier than 1980.
    TZ=UTC find "$STAGE" -exec touch -t 198001010000 {} +
    (
      cd "$STAGE"
      rm -f "$OUTPUT"
      COPYFILE_DISABLE=1 zip -X -q - -@ <"$ARCHIVE_LIST" >"$OUTPUT"
    )
    ;;
  tar.gz)
    # Deterministic tar entry metadata: ustar entries with uid/gid 0 and empty
    # uname/gname. GNU tar and bsdtar (macOS) spell those flags differently.
    if tar --version 2>/dev/null | grep -q 'GNU tar'; then
      TAR_METADATA_FLAGS=(--owner=:0 --group=:0 --numeric-owner)
    else
      TAR_METADATA_FLAGS=(--uid 0 --gid 0 --uname '' --gname '')
    fi
    TZ=UTC find "$STAGE" -exec touch -t 197001010000 {} +
    (
      cd "$STAGE"
      rm -f "$OUTPUT"
      COPYFILE_DISABLE=1 tar -cf - --no-recursion --format ustar "${TAR_METADATA_FLAGS[@]}" -T "$ARCHIVE_LIST" |
        gzip -9n >"$OUTPUT"
    )
    ;;
esac

if command -v xattr >/dev/null 2>&1; then
  xattr -c "$OUTPUT" 2>/dev/null || true
fi

case "$FORMAT" in
  zip)    archive_paths="$(unzip -Z1 "$OUTPUT" | sed 's#/$##')" ;;
  tar.gz) archive_paths="$(tar -tzf "$OUTPUT")" ;;
esac

# Denylist guard. Note this is a denylist, not an allowlist: a source-only
# directory added to the plugin root after this script ships will be packaged
# silently unless it is added here. See the design's Risks section.
unexpected_paths="$(
  printf '%s\n' "$archive_paths" |
    grep -E '(^graph-works/|^\.agents/|^hooks/|package\.json$|^\.git|^\.pytest_cache|^\.ruff_cache|^scripts/|^tests/|^docs/|^evals/|^lib/|^\.claude|^\.pi|^AGENTS\.md$|^CLAUDE\.md$|^RELEASE-NOTES\.md$|^CHANGELOG\.md$)' || true
)"
if [[ -n "$unexpected_paths" ]]; then
  printf '%s\n' "$unexpected_paths" | sed 's/^/  /' >&2
  die "archive contains source-only paths"
fi

entry_count="$(printf '%s\n' "$archive_paths" | wc -l | tr -d ' ')"
checksum="$(shasum -a 256 "$OUTPUT" | awk '{print $1}')"

echo "Archive: $OUTPUT"
echo "Format:  $FORMAT"
echo "Version: $VERSION"
echo "Entries: $entry_count"
echo "Skills:  $skill_count"
echo "SHA-256: $checksum"
