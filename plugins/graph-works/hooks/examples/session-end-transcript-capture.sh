#!/usr/bin/env bash
# SessionEnd hook: copy this session's transcript (+ any subagent sidechain
# transcripts) into the active work item's working directory.
#
# Add this to your project's .claude/settings.json (see README, or run
# /graph-works:onboard -> Feature 4).
#
# ## What it does
#
# Fires on the SessionEnd event. Reads `<root>/state/active-work.json` (the
# pointer `gw work advance` stamps on every real-pipeline-phase transition).
# If present, copies:
#   - the main session transcript -> work/<slug>/NN-<phase>.jsonl
#   - each subagent sidechain transcript
#     (<transcript-dir>/<session-id>/subagents/agent-<id>.jsonl)
#     -> work/<slug>/NN-<phase>-subagent-<id>.jsonl
# using work_io.paths.artifact_path (and sidechain_dir for discovery) as the
# single source of truth for the naming convention.
#
# No active-work.json (or a session that never touched `gw work advance`) ->
# silent no-op. This is the common case.
#
# Copies are overwrites, not appends — safe to re-run.
#
# ## Escape hatch
#
# Set GRAPH_WORKS_TRANSCRIPT_CAPTURE_GUARD=0 to disable at runtime.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# plugins/graph-works/hooks/examples -> repo root is four levels up. Used as
# the uv project so workspace_io/work_io are importable without relying on
# AGENT_RESEARCH_ROOT.
ROOT="${AGENT_RESEARCH_ROOT:-$(cd "${SCRIPT_DIR}/../../../.." && pwd)}"

TRACE_LOG="${GRAPH_WORKS_TRANSCRIPT_CAPTURE_TRACE_LOG:-/tmp/claude-hooks/transcript-capture-trace.log}"
mkdir -p "$(dirname "$TRACE_LOG")" 2>/dev/null || true
trace() {
    local event="${1:-?}" reason="${2:-}"
    printf '%s | session-end-transcript-capture | session=%s | %s%s\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${SESSION_ID_SHORT:-?}" "$event" \
        "${reason:+ | $reason}" >> "$TRACE_LOG" 2>/dev/null || true
}

# Fail-open: never block session end.
trap 'trace "error" "trap-ERR"; exit 0' ERR

if [[ "${GRAPH_WORKS_TRANSCRIPT_CAPTURE_GUARD:-1}" == "0" ]]; then
    trace "skip" "guard=0"
    exit 0
fi

INPUT=$(cat)

SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)
TRANSCRIPT_PATH=$(echo "$INPUT" | jq -r '.transcript_path // empty' 2>/dev/null)
SESSION_ID_SHORT="${SESSION_ID:0:8}"

if [[ -z "$TRANSCRIPT_PATH" || ! -f "$TRANSCRIPT_PATH" ]]; then
    trace "skip" "no-transcript"
    exit 0
fi

trace "enter"

RESULT=$(uv run --project "$ROOT" python -c '
import json, shutil, sys
from pathlib import Path

transcript_path = Path(sys.argv[1])

try:
    from workspace_io import config
    from workspace_io.paths import graph_dir
    from work_io.paths import artifact_path, sidechain_dir

    ws = config.resolve(Path.cwd()).workspace
    pointer_path = graph_dir(ws) / "active-work.json"
    if not pointer_path.exists():
        print("skip:no-pointer")
        sys.exit(0)

    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    slug = pointer["slug"]
    phase = pointer["phase"]

    main_dest = artifact_path(ws, slug, phase, ext="jsonl")
    main_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(transcript_path, main_dest)
    copied = [str(main_dest)]

    sc_dir = sidechain_dir(transcript_path)
    if sc_dir.is_dir():
        for agent_file in sorted(sc_dir.glob("agent-*.jsonl")):
            agent_id = agent_file.stem.removeprefix("agent-")
            dest = artifact_path(ws, slug, phase, agent=f"subagent-{agent_id}", ext="jsonl")
            shutil.copy2(agent_file, dest)
            copied.append(str(dest))

    print("ok:" + ",".join(copied))
except Exception as e:
    print("error:" + str(e).replace("\n", " "))
' "$TRANSCRIPT_PATH" 2>>"$TRACE_LOG") || RESULT="error:uv-invocation-failed"

case "$RESULT" in
    ok:*) trace "copied" "${RESULT#ok:}" ;;
    skip:*) trace "skip" "${RESULT#skip:}" ;;
    error:*) trace "error" "${RESULT#error:}" ;;
    *) trace "error" "unrecognized-result:${RESULT}" ;;
esac

exit 0
