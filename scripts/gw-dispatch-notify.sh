#!/usr/bin/env bash
# OPTIONAL Notification hook for gw-dispatch.
#
# Polling `claude agents --json` already detects `state: "blocked"`, so this
# hook is not required -- it only shortens the latency between a background
# stage asking a question and you learning about it.
#
# Install by adding to your *user* settings (~/.claude/settings.json), NOT to
# the graph-wiki plugin -- a plugin hook would fire for every session of every
# user of the plugin, which is not what this is for:
#
#   "hooks": {
#     "Notification": [
#       { "matcher": "*", "hooks": [
#           { "type": "command",
#             "command": "/Users/pat/Personal/agent-research/scripts/gw-dispatch-notify.sh" } ] }
#     ]
#   }
#
# The Notification payload shape is not pinned across Claude Code versions, so
# this records the event verbatim rather than picking fields out of it. Read
# the resulting JSONL to learn the real shape on your version, then narrow this
# if you want. Verified present in the 2.1.220 binary: the notification types
# `agent_needs_input` and `agent_completed`.
#
# A hook must never break the session that fired it: every path exits 0.

set -uo pipefail

WS="${GRAPH_WIKI_WORKSPACE:-}"
if [[ -z "$WS" ]]; then
    exit 0
fi

OUT="$WS/.graph-wiki/dispatch/notifications.jsonl"
mkdir -p "$(dirname "$OUT")" 2>/dev/null || exit 0

PAYLOAD=$(cat 2>/dev/null) || exit 0
[[ -z "$PAYLOAD" ]] && exit 0

STAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# Wrap the raw payload under a timestamp. If jq is unavailable or the payload
# is not valid JSON, fall back to recording it as an opaque string so nothing
# is ever lost.
if command -v jq >/dev/null 2>&1 \
   && printf '%s' "$PAYLOAD" | jq -e . >/dev/null 2>&1; then
    printf '%s' "$PAYLOAD" \
        | jq -c --arg at "$STAMP" '{at: $at, payload: .}' >> "$OUT" 2>/dev/null
else
    python3 -c '
import json, sys
sys.stdout.write(json.dumps({"at": sys.argv[1], "raw": sys.stdin.read()}) + "\n")
' "$STAMP" <<< "$PAYLOAD" >> "$OUT" 2>/dev/null
fi

# macOS banner only when the session is actually asking for a human.
if [[ "$PAYLOAD" == *agent_needs_input* ]] && command -v osascript >/dev/null 2>&1; then
    osascript -e 'display notification "A background stage needs input — run: claude agents" with title "gw-dispatch"' >/dev/null 2>&1
fi

exit 0
