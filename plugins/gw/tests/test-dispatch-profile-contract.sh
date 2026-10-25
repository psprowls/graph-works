#!/usr/bin/env bash
set -euo pipefail

PLUGIN_ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
REPO_ROOT=$(CDPATH='' cd -- "$PLUGIN_ROOT/../.." && pwd)
RECIPE="$PLUGIN_ROOT/skills/auto-drive/references/launch-worker.py"
AUTO_DRIVE="$PLUGIN_ROOT/skills/auto-drive/SKILL.md"
WORKFLOW="$PLUGIN_ROOT/skills/workflow/SKILL.md"
FIXTURE=$(mktemp -d)
trap 'rm -rf "$FIXTURE"' EXIT

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

# Attend lifecycle prose contracts (Tasks 5/6): no remembered cards or unchecked receipts.
grep -F '### 2.5.3 Attend cards' "$AUTO_DRIVE" >/dev/null || fail "auto-drive reconciles attend cards every cycle"
grep -F 'attend-cards --tasks <task-list-json> --classification <classify-restart-json>' "$AUTO_DRIVE" >/dev/null || fail "attend cards come from the helper"
grep -F 'still reports `in-review`' "$AUTO_DRIVE" >/dev/null || fail "a card the human moved is never overwritten"
if grep -E 'session-local|attend-pending' "$AUTO_DRIVE" >/dev/null; then fail "auto-drive holds no remembered attend state"; fi
for contract in \
  'classify-lifecycle --op release' \
  'classify-lifecycle --op stop' \
  'closed joined attend terminal <handle> (<key>)' \
  'live and muted: <handle> (<key>)' \
  'stop_unknown' \
  'What marks a terminal user-owned' \
  'never `terminal close` for `release_pending` or `release_unknown`' \
  'result.worktree.workspaceStatus' \
  'result.worker.agentTerminalHandle' \
  'successful exact-handle close and positive confirmation' \
  'missing handle or failed/unverifiable close remains unresolved' \
  'stop_unknown` never records `stopped-verified' \
  'Refresh §2.1 after mutations before reconciling cards' \
  'If record-write rejects an abandon/close intent, do not invoke it' \
  'A confirmed close does not override a record-write verification refusal' \
  'Orca 1.4.211' \
  '| CLI tab switch followed by physical pane click, no typing | succeeded; release `retained` / `user_takeover` |'; do
  grep -F "$contract" "$AUTO_DRIVE" >/dev/null || fail "attend lifecycle contract: $contract"
done
# Keep receipt branching at each mutation site, not only in the shared table.
python3 - "$AUTO_DRIVE" <<'PY_CONTRACT'
from pathlib import Path
import sys
text = Path(sys.argv[1]).read_text(encoding="utf-8")
for start, end, expected in (
    ('### 2.5.2 Park handling', '### 2.5.3 Attend cards', 'classify-lifecycle --op stop --authority park'),
    ('- **Success branch**', '### 4.1.1', 'classify-lifecycle --op release'),
    ('### 4.1.1', '### Release and stop receipts', 'classify-lifecycle --op stop --authority <recorded stop authority>'),
    ('### 4.1.1', '### Release and stop receipts', 'classify-lifecycle --op release'),
    ('- **Stop the run**', '### 4.2', 'classify-lifecycle --op stop --authority user-authorized'),
    ('**Wrap-up**', '**User stop**', 'classify-lifecycle --op release'),
    ('**User stop**', '## Out of scope', 'classify-lifecycle --op stop --authority user-authorized'),
):
    section = text.split(start, 1)[1].split(end, 1)[0]
    assert expected in section, (start, expected)
assert 'Run §2.5.3 once now' in text.split('## 3. Dispatch mechanics', 1)[1].split('## 4.', 1)[0]
assert 'Run §2.5.3 once more' in text.split('**Wrap-up**', 1)[1]
PY_CONTRACT

grep -F 'references/launch-worker.py' "$AUTO_DRIVE" >/dev/null || fail "auto-drive uses the shipped launch recipe"
grep -F 'task-list --run <run_id> --json' "$AUTO_DRIVE" >/dev/null || fail "retry reads full task specs"
grep -F 'classify-restart' "$AUTO_DRIVE" >/dev/null || fail "restart uses the shipped classifier"
grep -F 'task-update --id <task_id> --status blocked' "$AUTO_DRIVE" >/dev/null || fail "deliberate skip is durable"
grep -F 'A missing terminal is normal' "$AUTO_DRIVE" >/dev/null || fail "auto-drive supports terminal-free workers"
grep -F 'gw work dispatch <key> --plan <scratch>/plan.json --run <run_id> --json' "$AUTO_DRIVE" >/dev/null || fail "new dispatches use the idempotent verb"
grep -F -- '--dispatch-record <file>' "$AUTO_DRIVE" >/dev/null || fail "restart classifier receives dispatch records"
grep -F 'rerouted-but-live' "$AUTO_DRIVE" >/dev/null || fail "rerouted live workers enter inspection"
grep -F 'gw work reroute <key> --run <run_id> --reason "<reason>"' "$AUTO_DRIVE" >/dev/null || fail "failure question offers durable reroute"
grep -F 'superseded Task' "$AUTO_DRIVE" >/dev/null || fail "reroutes appear separately in summary"
if grep -F 'dispatch-checks.md' "$AUTO_DRIVE" >/dev/null; then fail "the legacy dispatch-checks recipe is gone"; fi
grep -F 'gw work dispatch <key> --plan <plan> --run <run> --retry-of <dispatch_id> --json' "$AUTO_DRIVE" >/dev/null || fail "retry and park-resume relaunch through the dispatch verb"
grep -F '**The hold recipe for `placement-refused`.**' "$AUTO_DRIVE" >/dev/null || fail "a placement refusal holds the item"
section_three=$(sed -n '/^## 3\. Dispatch mechanics$/,/^## 4\. Delivery processing$/p' "$AUTO_DRIVE")
grep -F 'gw work dispatch <key>' <<<"$section_three" >/dev/null || fail "section three uses dispatch verb"
grep -F '### Manual ordered probe (timeout or inconclusive)' <<<"$section_three" >/dev/null || fail "timeout and inconclusive use an executable probe"
grep -F 'orca orchestration worker-show --dispatch <dispatch_id> --json' <<<"$section_three" >/dev/null || fail "manual probe checks heartbeat"
grep -F 'orca orchestration worker-read --dispatch <dispatch_id> --limit 5 --json' <<<"$section_three" >/dev/null || fail "manual probe checks structured transcript"
grep -F 'Immediately before either allowed Enter branch, run a fresh successful' <<<"$section_three" >/dev/null || fail "nudge requires fresh successful heartbeat evidence"
grep -F 'unknown, enter inspection without nudging.' <<<"$section_three" >/dev/null || fail "unknown heartbeat forbids Enter"
grep -F 'orca terminal send --terminal <agent_terminal_handle> --text "" --enter --json' <<<"$section_three" >/dev/null || fail "manual probe gives exact Enter command"
if grep -F 'references/launch-worker.py create' <<<"$section_three" >/dev/null; then
  fail "section three must not prescribe primitive task creation"
fi
grep -F 'Preflight — confirm the skill resolves.' "$WORKFLOW" >/dev/null || fail "workflow preserves skill preflight"
grep -F '<!-- rider-table:start -->' "$WORKFLOW" >/dev/null || fail "workflow preserves stage riders"
grep -F 'One stage per invocation' "$WORKFLOW" >/dev/null || fail "workflow preserves one-stage advancement"
grep -F 'This is reporting only' "$WORKFLOW" >/dev/null || fail "workflow reports routing without changing session"
# Every executable advance site carries the phase captured for that action.
grep -F 'JSON null maps to CLI `none`.' "$WORKFLOW" >/dev/null || fail "workflow must map absent phase"
grep -F 'Preserve the captured expectation across retries, including sizing answers.' "$WORKFLOW" >/dev/null || fail "workflow retry expectation must stay fixed"
grep -F 'On phase-mismatch, stop this action and replan; never retry it without a guard.' "$WORKFLOW" >/dev/null || fail "workflow must stop stale actions"
grep -F 'gw work advance <work-path> --from <expected-phase> --effort <value>' "$WORKFLOW" >/dev/null || fail "workflow sizing must be guarded"
grep -F 'gw work advance <work-path> --from <expected-phase>` directly (step 5)' "$WORKFLOW" >/dev/null || fail "workflow satisfied gate must be guarded"
grep -F 'dispatching: run `gw work advance <work-path> --from <expected-phase>`' "$WORKFLOW" >/dev/null || fail "workflow dispatch must be guarded"
grep -F 'For non-finish stages and satisfied gates, run `gw work advance <work-path> --from <expected-phase>`' "$WORKFLOW" >/dev/null || fail "workflow completion must be guarded"
grep -F '| Attended finish | `merge` (a successful `gw work integrate` by any strategy, tests green on the merged result) | `gw work advance <work-path> --from finish --resolved-in <merge commit SHA>` |' "$WORKFLOW" >/dev/null || fail "attended merge must be guarded"
grep -F '| Attended finish | `confirm` (commits already on the merge target) | `gw work advance <work-path> --from finish --resolved-in <HEAD SHA>` |' "$WORKFLOW" >/dev/null || fail "attended confirm must be guarded"
grep -F 'Revalidate the planned gate, return, or repair condition against the fresh next result before acting.' "$AUTO_DRIVE" >/dev/null || fail "fresh phase is not authorization for a stale action"
grep -F 'gw work advance <path from entry> --from <expected-phase>' "$AUTO_DRIVE" >/dev/null || fail "coordinator gate must be guarded"
grep -F 'gw work advance <work-path> --from <expected-phase> --effort <value>' "$AUTO_DRIVE" >/dev/null || fail "coordinator sizing must be guarded"
# Finish outcomes must not resolve unintegrated work or double-advance relay.
RIDERS="$PLUGIN_ROOT/skills/workflow/references/brief-riders.md"
RELAY="$PLUGIN_ROOT/skills/finishing-relay/SKILL.md"
grep -F '| Attended finish | `merge` (a successful `gw work integrate` by any strategy, tests green on the merged result) |' "$WORKFLOW" >/dev/null || fail "attended merge requires verified integration"
grep -F '| Attended finish | `confirm` (commits already on the merge target) |' "$WORKFLOW" >/dev/null || fail "attended confirm resolves already integrated commits"
grep -F '| Attended finish | `pr`, `keep`, `discard`, `none` | **No advance**' "$WORKFLOW" >/dev/null || fail "attended non-integration outcomes hold"
grep -F '| `gw:finishing-relay` | Any | **No advance**' "$WORKFLOW" >/dev/null || fail "relay owns its advance"
grep -F 'missing or ambiguous, treat it as `none`' "$WORKFLOW" >/dev/null || fail "missing finish evidence holds"
grep -F 'attended or relay' "$WORKFLOW" >/dev/null || fail "held-item hand-off covers both finish paths"
grep -F 'merge target: `<merge target>`' "$WORKFLOW" >/dev/null || fail "held-item hand-off names the target"
grep -F '**Complete finish targets.**' "$RIDERS" >/dev/null || fail "attended finish uses complete repository targets"
grep -F 'never reconstruct targets from scalar frontmatter' "$RIDERS" >/dev/null || fail "attended finish refuses scalar fallback"
for document in "$RIDERS" "$RELAY"; do
  grep -F 'finish-receipt.py inspect' "$document" >/dev/null || fail "finish inspects persisted evidence"
  grep -F 'finish-receipt.py record' "$document" >/dev/null || fail "finish records each repository"
done
grep -F '**Confirm integrated.**' "$RIDERS" >/dev/null || fail "on-target menu can confirm integration"
grep -F 'in any checkout, including a linked worktree' "$RIDERS" >/dev/null || fail "on-target check includes shared epic worktrees"
grep -F 'Finish outcome: <merge|confirm|pr|keep|discard|none>; merge target: <branch>; resolved_in: <SHA|none>' "$RIDERS" >/dev/null || fail "attended finish reports an explicit outcome"
grep -F 'only a verified integration resolves — same rule' "$RELAY" >/dev/null || fail "relay cites the shared integration rule"
grep -F 'Include the merge target in every held-outcome body' "$RELAY" >/dev/null || fail "relay held reports preserve the target"
# A stamped Epic/Release root finishes its own integration branch (D-002).
grep -F '**Integration-branch case**' "$RELAY" >/dev/null || fail "relay merges any branch that differs from its target"
grep -F 'a stamped Epic or Release root finishing its own integration branch' "$RELAY" >/dev/null || fail "relay covers a stamped root's integration branch"
grep -F 'Never merge a branch into itself.' "$RELAY" >/dev/null || fail "relay never self-merges a same-target stamp"
# Check R2's actual target-worktree contract, independent of prose wrapping.
python3 - "$RELAY" <<'PY' || fail "relay validates the supplied target checkout and escalates ambiguity"
import sys
from pathlib import Path

state = Path(sys.argv[1]).read_text(encoding="utf-8").split("## R2 — Detect state", 1)[1].split("## R3", 1)[0]
state = " ".join(state.split())
for requirement in (
    "A `null` target_worktree means the target is not checked out; hold at R2 without merging.",
    "The target checkout is the entry's supplied `target_worktree`, which gw computed from placement.",
    "the R4 merge executes there, not in this worker's own worktree.",
    "A missing `target_worktree` enters the **Escalation path**.",
    "Never check the target out yourself and never merge from this worker's worktree.",
):
    assert requirement in state, requirement
PY
grep -F 'Never invent a release date' "$RELAY" >/dev/null || fail "relay obtains a real release date"
grep -F -- '--released-at <date>' "$RELAY" >/dev/null || fail "relay passes the release date to the resolving advance"
if grep -Fq 'Forked-child case' "$RELAY"; then fail "relay must not limit merging to forked children"; fi
grep -F 'The coordinator performs no merge at wrap-up.' "$AUTO_DRIVE" >/dev/null || fail "wrap-up never merges the epic branch"
grep -F "computed placement owns the integration targets" "$AUTO_DRIVE" >/dev/null || fail "placed root finish owns integration"
grep -F 'An owner without a code' "$AUTO_DRIVE" >/dev/null || fail "unplaced root keeps its direct finish"
if grep -Fq 'epic branch to `develop`' "$AUTO_DRIVE"; then fail "wrap-up must not hardcode a merge target"; fi
if grep -Fq -- '--agent claude' "$AUTO_DRIVE"; then fail "auto-drive must use the planned agent"; fi
if grep -Fq 'permission_mode' "$AUTO_DRIVE"; then fail "auto-drive must not promise permission-mode control"; fi
ONBOARD="$PLUGIN_ROOT/skills/onboard/SKILL.md"
for document in "$AUTO_DRIVE" "$ONBOARD"; do
  if grep -Eq 'workflow\.pipeline|workflow\.auto_drive\.permission_mode' "$document"; then
    fail "$document must not recommend retired dispatch settings"
  fi
  grep -F 'workflow.local.yaml' "$document" >/dev/null || fail "$document explains local dispatch rules"
  grep -F 'gw config sync' "$document" >/dev/null || fail "$document syncs dispatch changes"
done
grep -F "Permissions remain the selected agent's existing settings" "$ONBOARD" >/dev/null || fail "onboarding preserves agent-owned permissions"
grep -F 'Release eligibility uses both the fresh classifier and its matching worker row:' "$AUTO_DRIVE" >/dev/null || fail "wrap-up rechecks launch proof before release"
grep -F '| `rerouted` without `recovery` | Release only when `workerState: succeeded` and `dispatchStatus: completed`' "$AUTO_DRIVE" >/dev/null || fail "successful superseded dispatches remain release eligible"
grep -F '| `rerouted` with `recovery.reason: verified` | Already released; never release again. |' "$AUTO_DRIVE" >/dev/null || fail "superseded recovered completions are not released twice"
grep -F '| Other `rerouted` rows | Retain; reroute alone does not establish successful completion. |' "$AUTO_DRIVE" >/dev/null || fail "reroute alone never authorizes release"
# Final-review protocol: each real operation journals intent and its original identity.
for operation in worker-stop worker-release task-update; do
  grep -F "Before invoking $operation, persist its intent" "$AUTO_DRIVE" >/dev/null || fail "$operation journals intent before invocation"
  grep -F "After $operation returns, append its original request ID" "$AUTO_DRIVE" >/dev/null || fail "$operation journals its original receipt"
done
grep -F 'If invocation or receipt persistence is interrupted with no original request ID' "$AUTO_DRIVE" >/dev/null || fail "interrupted operations preserve unknown identity"
grep -F 'Never invent a request ID or pass a new ID as a first-use `--retry-request`' "$AUTO_DRIVE" >/dev/null || fail "unknown identity never becomes a fabricated retry"
grep -F 'A refusal-only annotation changes only `history` and `unresolved`' "$AUTO_DRIVE" >/dev/null || fail "historical refusal cannot launder protected proof"
grep -F 'Record-derived success requires fresh `projection.liveness.verdict: exited`' "$AUTO_DRIVE" >/dev/null || fail "recovery requires positive current liveness evidence"

# Rejected or unprovable worker_done: evidence-gated settlement recovery (§4.1.1).
grep -F 'classify-report --message <message-json>' "$AUTO_DRIVE" >/dev/null || fail "worker_done reports are classified before their outcome"
grep -F '### 4.1.1 Completion claimed, settlement unconfirmed' "$AUTO_DRIVE" >/dev/null || fail "auto-drive has a claimed-but-unsettled branch"
grep -F 'record-write --path <record-file> --record <record-json>' "$AUTO_DRIVE" >/dev/null || fail "recovery records are written through the helper"
grep -F -- '--recovery-record <record-json>' "$AUTO_DRIVE" >/dev/null || fail "restart classification reads recovery records"
grep -F 'spec-hash --tasks <task-list-json> --task <task_id>' "$AUTO_DRIVE" >/dev/null || fail "records bind the full task spec"
grep -F 'references/orca-settlement/<dispatch_id>.json' "$AUTO_DRIVE" >/dev/null || fail "recovery records live under the dispatched item"
grep -F 'never infer identity from a terminal-handle prefix' "$AUTO_DRIVE" >/dev/null || fail "caller identity is never guessed"
grep -F 'A failed stop never authorizes release' "$AUTO_DRIVE" >/dev/null || fail "release needs verified settlement"
grep -F 'There is no automatic abandon fallback' "$AUTO_DRIVE" >/dev/null || fail "abandon is not cleanup"
grep -F 'Task completed plus worker stopped alone never earns it' "$AUTO_DRIVE" >/dev/null || fail "recovered-settled needs a verified record"
grep -F 'never issue a second `worker-release` for `recovered-settled`' "$AUTO_DRIVE" >/dev/null || fail "recovered terminals are not released twice"
grep -F 'A timeout never launches a replacement' "$AUTO_DRIVE" >/dev/null || fail "unresolved recovery never relaunches"
grep -F 'coordinator-recovered completions' "$AUTO_DRIVE" >/dev/null || fail "wrap-up distinguishes recovered completion"
grep -F 'retry-request' "$AUTO_DRIVE" >/dev/null || fail "lost mutation responses retry by request identity"
grep -F "A lost mutation response is recovered with Orca's request-show / \`--retry-request\`" "$AUTO_DRIVE" >/dev/null || fail "lost mutation responses use request identity"
grep -F 'historical caller-identity cause remains unverified' "$AUTO_DRIVE" >/dev/null || fail "auto-drive keeps the evidence limit explicit"

WORKFLOW="$PLUGIN_ROOT/skills/workflow/SKILL.md"
if grep -F 'pin-detached' "$AUTO_DRIVE" >/dev/null; then fail "auto-drive dispatches no detached readers"; fi
grep -F 'PLACEMENT UNRECORDED <key>' "$AUTO_DRIVE" >/dev/null || fail "auto-drive reports an unrecorded placement"
if grep -Fq -- '--no-infer-worktree' "$AUTO_DRIVE"; then fail "auto-drive advances carry no retired placement flag"; fi

# Placement never depends on where the coordinator runs.
if grep -Fq 'new-child' "$AUTO_DRIVE"; then fail "auto-drive must never launch Orca's caller-context child mode"; fi
if grep -Fq "coordinator's own worktree context" "$AUTO_DRIVE"; then fail "auto-drive must not infer parentage from the coordinator"; fi
if grep -Fq 'find the entry whose path matches the' "$AUTO_DRIVE"; then fail "the repo selector comes from the plan, not §0"; fi

if grep -Fq 'Do not substitute the observed values downstream' "$AUTO_DRIVE"; then fail "observed placement is recorded now"; fi
if grep -Fq 'explicitly on its own' "$AUTO_DRIVE"; then fail "no worker is told to state its own placement"; fi
grep -F 'Dispatch key:' "$WORKFLOW" >/dev/null || fail "workflow detects a supervised dispatch"
if grep -Fq -- '--no-infer-worktree' "$WORKFLOW"; then fail "workflow carries no retired placement flag"; fi
sed -n '/^### 2\. /,/^### 3\. /p' "$WORKFLOW" | grep -F 'gw work place <work-path> --ensure --json' >/dev/null || fail "workflow step 2 places through gw work place"

# Scope the opt-out to R5's resolving command, retaining both integration arguments.
sed -n '/^## R5 — /,/^## /p' "$RELAY" | grep -F 'gw work advance <work-path> --from finish --resolved-in <resolved_in from receipt inspection> [--released-at <date>]' >/dev/null || fail "relay R5 resolving advance guards finish and preserves integration arguments"
grep -F 'On phase-mismatch, enter the **Escalation path**' "$RELAY" >/dev/null || fail "relay must escalate stale settlement"
grep -F 'include the merge SHA in the' "$RELAY" >/dev/null || fail "relay escalation must preserve merge evidence"
grep -F 'escalation body (or say the commits were already on the target in the trunk case)' "$RELAY" >/dev/null || fail "relay escalation must cover trunk integration"
grep -F 'Do not claim settlement or send `worker_done` while escalating' "$RELAY" >/dev/null || fail "relay must not report false settlement"


# `gw work dispatch` encodes and launches; the plugin only decodes a saved spec.
python3 - "$FIXTURE/spec" <<'PY'
import json, sys
envelope = {
    "version": 2,
    "dispatch_key": "work/example#execute",
    "agent": "codex",
    "model": "provider model/id",
    "reasoning_effort": "high",
    "placement_argv": ["--worktree", "path:/tmp/allocated example"],
    "mode": "autonomous",
    "worktree_path": "/tmp/allocated example",
}
with open(sys.argv[1], "w", encoding="utf-8", newline="") as stream:
    stream.write("GW_LAUNCH_V1 " + json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
                 + "\nDo the work.\r\nKeep this line.\r\n\r\n")
PY
python3 "$RECIPE" decode --spec "$FIXTURE/spec" >"$FIXTURE/decoded.json"
python3 - "$FIXTURE/decoded.json" <<'PY'
import json, sys
decoded = json.load(open(sys.argv[1], encoding="utf-8"))
assert decoded["envelope"]["placement_argv"] == ["--worktree", "path:/tmp/allocated example"]
assert decoded["envelope"]["worktree_path"] == "/tmp/allocated example"
assert decoded["envelope"]["agent"] == "codex" and decoded["envelope"]["reasoning_effort"] == "high"
assert decoded["prompt"] == "Do the work.\r\nKeep this line.\r\n\r\n"
PY
for removed in encode create launch place prepare prepare-reader settle-placement; do
  if python3 "$RECIPE" "$removed" --help >/dev/null 2>&1; then
    fail "launch-worker.py keeps no $removed command"
  fi
done

python3 "$RECIPE" classify-restart \
  --tasks "$REPO_ROOT/packages/workflow-orca/tests/fixtures/task_list.json" \
  --workers "$REPO_ROOT/packages/workflow-orca/tests/fixtures/worker_list.json" \
  >"$FIXTURE/captured-restart.json"
python3 - "$FIXTURE/captured-restart.json" <<'PY'
import json, sys
rows = {row["task_id"]: row for row in json.load(open(sys.argv[1], encoding="utf-8"))}
assert rows["task_5ca8c19ffa5e"]["action"] == "recovery-inspection"
assert rows["task_338800a1fa14"]["action"] == "live"
assert rows["task_0a0b0c0d0e0f"] == {
    "task_id": "task_0a0b0c0d0e0f", "task_title": "2026-08-13-orphan#plan",
    "display_name": "2026-08-13-orphan · plan", "dispatch_id": None,
    "action": "recovery-inspection"
}
PY

cat >"$FIXTURE/tasks.json" <<'JSON'
{"id":"task-list-envelope","ok":true,"result":{"tasks":[
  {"id":"task-reserved","task_title":"work/reserved#execute","status":"ready"},
  {"id":"task-no-worker-skip","task_title":"work/no-worker-skip#execute","status":"blocked"},
  {"id":"task-failed-skip","task_title":"work/failed-skip#execute","status":"blocked"},
  {"id":"task-stopped-skip","task_title":"work/stopped-skip#execute","status":"blocked"},
  {"id":"task-live-blocked","task_title":"work/live-blocked#execute","status":"blocked"},
  {"id":"task-unknown-blocked","task_title":"work/unknown-blocked#execute","status":"blocked"}
]}}
JSON
cat >"$FIXTURE/workers.json" <<'JSON'
{"id":"worker-list-envelope","ok":true,"result":{"workers":[
  {"taskId":"task-failed-skip","dispatchId":"dispatch-failed","workerState":"failed","dispatchStatus":"failed"},
  {"taskId":"task-stopped-skip","dispatchId":"dispatch-stopped","workerState":"stopped","dispatchStatus":"stopped"},
  {"taskId":"task-live-blocked","dispatchId":"dispatch-live","workerState":"running","dispatchStatus":"dispatched"},
  {"taskId":"task-unknown-blocked","dispatchId":"dispatch-unknown","workerState":"running","dispatchStatus":"outcome_unknown"}
]}}
JSON
python3 "$RECIPE" classify-restart \
  --tasks "$FIXTURE/tasks.json" --workers "$FIXTURE/workers.json" >"$FIXTURE/restart.json"
python3 - "$FIXTURE/restart.json" <<'PY'
import json, sys
rows = {row["task_id"]: row for row in json.load(open(sys.argv[1], encoding="utf-8"))}
assert rows["task-reserved"] == {
    "task_id": "task-reserved", "task_title": "work/reserved#execute",
    "display_name": None, "dispatch_id": None, "action": "recovery-inspection"
}
assert rows["task-no-worker-skip"]["action"] == "deliberate-skip"
assert rows["task-failed-skip"] == {
    "task_id": "task-failed-skip", "task_title": "work/failed-skip#execute",
    "display_name": None, "dispatch_id": "dispatch-failed", "action": "deliberate-skip"
}
assert rows["task-stopped-skip"]["action"] == "deliberate-skip"
assert rows["task-live-blocked"] == {
    "task_id": "task-live-blocked", "task_title": "work/live-blocked#execute",
    "display_name": None, "dispatch_id": "dispatch-live", "action": "live"
}
assert rows["task-unknown-blocked"] == {
    "task_id": "task-unknown-blocked", "task_title": "work/unknown-blocked#execute",
    "display_name": None, "dispatch_id": "dispatch-unknown",
    "action": "recovery-inspection"
}
PY

printf '%s\n%s\n' 'GW_LAUNCH_V1 {"version":2,"dispatch_key":"work/example#execute","agent":"codex","model":null,"reasoning_effort":"high","placement_argv":[],"mode":"autonomous","worktree_path":"/tmp/w"}' 'Do the work.' >"$FIXTURE/effort-only.spec"
if python3 "$RECIPE" decode --spec "$FIXTURE/effort-only.spec" >"$FIXTURE/bad-spec" 2>"$FIXTURE/error"; then
  echo "expected effort without model to be refused" >&2
  exit 1
fi
grep -F "Set a model or clear reasoning_effort." "$FIXTURE/error" >/dev/null

python3 - "$FIXTURE/spec" "$FIXTURE/wrong-key-tasks.json" <<'PY'
import json, sys
spec = open(sys.argv[1], encoding="utf-8", newline="").read()
tasks = {"ok": True, "result": {"tasks": [{"id": "task-1", "task_title": "work/wrong#execute", "spec": spec}]}}
json.dump(tasks, open(sys.argv[2], "w", encoding="utf-8"))
PY
if python3 "$RECIPE" spec-hash --tasks "$FIXTURE/wrong-key-tasks.json" --task task-1 >"$FIXTURE/wrong-key.out" 2>"$FIXTURE/error"; then
  fail "a task title that disagrees with the saved dispatch key must be blocked"
fi
grep -F "does not match the task title" "$FIXTURE/error" >/dev/null || fail "wrong-key recovery reason is actionable"

python3 - "$REPO_ROOT" <<'PY'
"""Exercise restart settlement against captured Orca payload shapes."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(sys.argv[1])
FIXTURES = ROOT / "packages/workflow-orca/tests/fixtures"
HELPER = runpy.run_path(str(ROOT / "plugins/gw/skills/auto-drive/references/launch-worker.py"))


class RestartProofTests(unittest.TestCase):
    def test_success_requires_full_envelope_and_durable_matching_receipt(self):
        cases = (
            "matching", "defaults", "missing-spec", "malformed-spec", "malformed-json",
            "missing-field", "extra-field", "invalid-version", "invalid-placement",
            "effort-only", "blank-agent", "null-key", "truncated", "wrong-key",
            "missing-receipt", "malformed-receipt", "missing-requested", "missing-effective",
            "wrong-requested-agent", "wrong-effective-agent", "wrong-requested-model",
            "wrong-effective-model", "wrong-requested-effort", "wrong-effective-effort",
            "show-failed", "show-invalid-json", "show-error-envelope", "show-missing-worker",
            "missing-dispatch-id", "show-os-error",
        )
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                tasks = json.loads((FIXTURES / "task_list.json").read_text(encoding="utf-8"))
                workers = json.loads((FIXTURES / "worker_list.json").read_text(encoding="utf-8"))
                shown = json.loads((FIXTURES / "worker_show_settled.json").read_text(encoding="utf-8"))
                task = tasks["result"]["tasks"][0]
                tasks["result"]["tasks"] = [task]
                worker = next(
                    row for row in workers["result"]["workers"]
                    if row["dispatchId"] == "ctx_817ed5bf5986"
                )
                workers["result"]["workers"] = [worker]
                shown["result"]["dispatch"].update(id=worker["dispatchId"], task_id=task["id"])
                shown["result"]["worker"].update(dispatch_id=worker["dispatchId"], state="succeeded")
                request = {
                    "version": 1, "dispatch_key": task["task_title"], "agent": "codex",
                    "model": "provider model/id", "reasoning_effort": "high",
                    "placement_argv": ["--worktree", "path:/tmp/example"],
                }
                receipt = {
                    side: {"agent": "codex", "model": "provider model/id", "effort": "high"}
                    for side in ("requested", "effective")
                }
                if case == "defaults":
                    request.update(model=None, reasoning_effort=None)
                elif case == "missing-field":
                    del request["model"]
                elif case == "extra-field":
                    request["unexpected"] = "value"
                elif case == "invalid-version":
                    request["version"] = True
                elif case == "invalid-placement":
                    request["placement_argv"] = "--worktree path:/tmp/example"
                elif case == "effort-only":
                    request["model"] = None
                elif case == "blank-agent":
                    request["agent"] = " "
                elif case == "null-key":
                    request["dispatch_key"] = None
                elif case == "wrong-key":
                    request["dispatch_key"] = "work/wrong#execute"
                elif case == "missing-dispatch-id":
                    del worker["dispatchId"]
                task["spec"] = "GW_LAUNCH_V1 " + json.dumps(request) + "\nDo the work.\r\n"
                if case == "missing-spec":
                    del task["spec"]
                elif case == "malformed-spec":
                    task["spec"] = "MALFORMED"
                elif case == "malformed-json":
                    task["spec"] = "GW_LAUNCH_V1 {broken\nDo the work."
                elif case == "truncated":
                    task["spec_truncated"] = True
                if case.startswith("wrong-requested-") or case.startswith("wrong-effective-"):
                    _, side, field = case.split("-")
                    receipt[side][field] = "different"
                elif case in {"missing-requested", "missing-effective"}:
                    del receipt[case.removeprefix("missing-")]
                shown["result"]["worker"]["startOptions"] = {"launch": receipt}
                if case == "missing-receipt":
                    shown["result"]["worker"]["startOptions"] = {}
                elif case == "malformed-receipt":
                    shown["result"]["worker"]["startOptions"]["launch"] = []
                elif case == "show-missing-worker":
                    del shown["result"]["worker"]
                elif case == "show-error-envelope":
                    shown["ok"] = False
                files = []
                for name, payload in (("tasks", tasks), ("workers", workers)):
                    path = Path(directory) / (name + ".json")
                    path.write_text(json.dumps(payload), encoding="utf-8", newline="")
                    files.append(str(path))

                def show(argv, **kwargs):
                    self.assertEqual(argv, [
                        "fixture-orca", "orchestration", "worker-show", "--dispatch",
                        "ctx_817ed5bf5986", "--json",
                    ])
                    if case == "show-os-error":
                        raise OSError("worker-show unavailable")
                    return subprocess.CompletedProcess(
                        argv, 1 if case == "show-failed" else 0,
                        "invalid JSON" if case == "show-invalid-json" else json.dumps(shown), "",
                    )

                output = io.StringIO()
                with patch("subprocess.run", side_effect=show), contextlib.redirect_stdout(output):
                    HELPER["classify_restart"](argparse.Namespace(tasks=files[0], workers=files[1], orca="fixture-orca"))
                self.assertEqual(json.loads(output.getvalue()), [{
                    "task_id": "task_5ca8c19ffa5e",
                    "task_title": "2026-08-13-test-gap-code-wiki-smoke-script#execute",
                    "display_name": "2026-08-13-test-gap-code-wiki-smoke-script · execute",
                    "dispatch_id": worker.get("dispatchId"),
                    "action": "settled" if case in {"matching", "defaults"} else "recovery-inspection",
                }])


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
PY

python3 "$PLUGIN_ROOT/tests/test_settlement_recovery.py" || fail "settlement recovery helper suite"

echo "dispatch profile contract: ok"

# Legacy existing Tasks have no observed placement in the result.
grep -F 'If `status: existing` has `placement: null`' "$AUTO_DRIVE" >/dev/null || fail "legacy results branch before placement narration"
grep -F 'placement was not read back' "$AUTO_DRIVE" >/dev/null || fail "legacy narration states missing observation"
