#!/usr/bin/env bash
# Manual, opt-in live probe; NOT run by just check. One case per invocation.
# Run from a dedicated, bound Orca terminal for every case: run-create needs
# an active sender terminal. For restart, use a separate external read-only
# observer across the app restart; an external shell cannot launch this script.
# Before restart, save Run/Dispatch and terminal identities plus receipts.
# After restart, re-list handles and worker-show; resume only with verified
# live identity and control. Neither controller nor worker survival is assumed.
# If this script dies, preserve evidence for manual recovery; never relaunch
# the entire case blindly.
# Requires an existing disposable LOCAL worktree and a new private output dir.
set -euo pipefail

usage() {
  echo "Usage: $0 <scratch-worktree-path> <new-out-dir> <case> [agent]"
  echo 'Cases: no-interaction focus-only keystroke-after-ready keystroke-before-ready restart-no-input stop-clean stop-joined'
}
die() { echo "error: $*" >&2; exit 1; }
confirm() {
  local answer
  printf '%s\nType yes to continue: ' "$1"
  IFS= read -r answer || die 'input closed; preserve resources for inspection'
  [[ "$answer" == yes ]] || die 'cancelled; preserve resources for inspection'
}
field() {
  uv run --no-project --python 3.12 python - "$1" "$2" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as stream:
    value = json.load(stream)
if value.get("ok") is not True:
    raise SystemExit("Orca returned non-ok JSON; inspect receipt")
for key in sys.argv[2].split("."):
    value = value[key]
if not isinstance(value, str) or not value:
    raise SystemExit("Missing nonempty string field")
print(value)
PY
}
# Capture errors and exit codes too; never replace CLI JSON with invented data.
capture() {
  local name="$1" rc=0
  shift
  "$@" > "$out/$name.json" 2> "$out/$name.stderr" || rc=$?
  printf '%s\n' "$rc" > "$out/$name.exit-code"
  return "$rc"
}

if [[ "${1:-}" == --help ]]; then usage; exit 0; fi
[[ $# -ge 3 && $# -le 4 ]] || { usage >&2; exit 2; }
# Check before mkdir, run-create, or any Orca command. Never wait unattended.
[[ -t 0 && -t 1 ]] || die 'interactive stdin and stdout terminals required'
worktree="$1"; out="$2"; trigger="$3"; agent="${4:-claude}"
case "$trigger" in
  no-interaction|focus-only|keystroke-after-ready|keystroke-before-ready|restart-no-input|stop-clean|stop-joined) ;;
  *) die "unknown case: $trigger" ;;
esac
command -v orca >/dev/null || die 'orca not on PATH'
command -v uv >/dev/null || die 'uv not on PATH'
[[ -d "$worktree" ]] || die 'scratch worktree must already exist'
worktree=$(cd "$worktree" && pwd -P)
[[ ! -e "$out" ]] || die 'use a NEW output directory; never overwrite evidence'
confirm "Authorized live case $trigger in disposable worktree $worktree? This creates a worker and may stop/release only that worker. Use a dedicated coordinator terminal; run-create rebinds its Run."
umask 077
mkdir -p "$out"
out=$(cd "$out" && pwd -P)
run='not-created'; dispatch='not-known'
trap 'printf "Probe ended. Run: %s Dispatch: %s Evidence: %s\nNo automatic stop, abandon, retry, terminal close, or worktree deletion. Inspect residual resources before another case.\n" "$run" "$dispatch" "$out" >&2' EXIT
orca --version > "$out/orca-version.txt"
printf '%s\n' "$trigger" > "$out/case.txt"
date -u +%Y-%m-%dT%H:%M:%SZ > "$out/started-at.txt"
capture run-create orca orchestration run-create --objective "gw manual takeover probe $trigger" --json
run=$(field "$out/run-create.json" result.run.id)
# Files are barriers, not recorded receipts. The worker stamps its exact Dispatch
# ID so a stale/wrong marker cannot authorize either stop or settlement.
printf -v spec '%s\n' \
  'You are a controlled lifecycle probe in a disposable local worktree. Edit no repository files, create no subworkers, do not finish immediately.' \
  "Your sole writable area is this private probe directory: $out" \
  "Once you have read your injected preamble, write ONLY your exact dispatch ID plus newline to $out/ready (UTF-8 LF)." \
  "Then wait for $out/settle containing that same dispatch ID. Poll using bounded waits of at most 30 seconds; obey preamble heartbeats and coordinator checks. Do not send worker_done before this barrier exists and matches." \
  'On that exact settlement permission, send worker_done once using the injected preamble authority, outcome succeeded, with a three-sentence summary of readiness, trigger observation limits, and completion; then idle.' \
  'The operator may deliberately stop this probe instead; do not restart or recover yourself. Human pane input is a test stimulus, not permission to settle.'
capture task-create orca orchestration task-create --run "$run" --task-title "probe-$trigger" --spec "$spec" --json
task=$(field "$out/task-create.json" result.task.id)
if [[ "$trigger" == keystroke-before-ready ]]; then
  confirm 'Prepare to type one key in the NEW probe pane during launch, BEFORE agent readiness. If that window is missed, record invalid timing and stop this case for manual cleanup.'
  capture start orca orchestration worker-start --task "$task" --run "$run" --worktree "path:$worktree" --agent "$agent" --json &
  start_pid=$!
  echo 'Launch in progress: perform early input now. Do not touch any other worker.'
  # Always collect launch result before permitting cancellation; never retry it.
  wait "$start_pid" || die 'launch failed/unknown; inspect start receipt and residualResources, never relaunch blindly'
else
  capture start orca orchestration worker-start --task "$task" --run "$run" --worktree "path:$worktree" --agent "$agent" --json \
    || die 'launch failed/unknown; inspect start receipt and residualResources, never relaunch blindly'
fi
dispatch=$(field "$out/start.json" result.dispatchId)
printf '%s\n' "$dispatch" > "$out/dispatch-id.txt"
echo "Probe Dispatch: $dispatch"
# Bounded readiness barrier. A timeout preserves the live worker for inspection.
deadline=$((SECONDS + 180))
until [[ -f "$out/ready" ]] && [[ "$(cat "$out/ready")" == "$dispatch" ]]; do
  (( SECONDS < deadline )) || die 'readiness barrier timed out; inspect worker, do not stop from absence'
  sleep 2
done
capture ready-show orca orchestration worker-show --dispatch "$dispatch" --json
[[ "$(field "$out/ready-show.json" result.worker.state)" == ready ]] \
  || die 'worker not ready; inspect evidence before any trigger'
uv run --no-project --python 3.12 python - "$out/ready-show.json" <<'PYTERMINAL'
import json, sys
with open(sys.argv[1], encoding="utf-8") as stream:
    result = json.load(stream)["result"]
terminal = result.get("terminal")
if not isinstance(terminal, dict) or terminal.get("connected") is not True:
    raise SystemExit("No connected worker terminal; preserve worker and inspect manually")
PYTERMINAL
case "$trigger" in
  no-interaction|stop-clean) instruction='Do not focus or type in the worker pane.' ;;
  focus-only) instruction='Focus/click the probe pane now; type nothing there.' ;;
  keystroke-after-ready|stop-joined) instruction='Type one key in the READY probe pane now.' ;;
  keystroke-before-ready) instruction='Confirm the earlier input happened BEFORE agent readiness, not merely before this file barrier. If uncertain, answer no; timing is unverified.' ;;
  restart-no-input) instruction='Before quitting Orca, save Run/Dispatch and controller/worker terminal identities plus receipts; start a separate external read-only observer. Quit and relaunch Orca without probe-pane input. Re-list handles and worker-show after restart; continue only with verified live identity and control. If this script or worker did not survive, preserve evidence for manual recovery and do not restart the whole case blindly.' ;;
esac
confirm "$instruction Trigger completed with known timing?"
printf 'Operator confirmed trigger %s at %s\n' "$trigger" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$out/trigger-observation.txt"
capture triggered-show orca orchestration worker-show --dispatch "$dispatch" --json
case "$trigger" in
  stop-clean|stop-joined)
    confirm "Explicitly stop ONLY scratch Dispatch $dispatch while its settlement barrier is closed?"
    capture stop orca orchestration worker-stop --dispatch "$dispatch" --json \
      || die 'stop uncertain; inspect receipt, no abandon or repeated stop'
    # User-owned/unknown results require an operator recovery decision. Never
    # abandon automatically just to make a desired fixture appear.
    ;;
  *) printf '%s\n' "$dispatch" > "$out/settle" ;;
esac
# Require authoritative settlement, not a pane prompt, readiness or user input.
deadline=$((SECONDS + 180))
while :; do
  capture settled-show orca orchestration worker-show --dispatch "$dispatch" --json
  state=$(field "$out/settled-show.json" result.worker.state)
  case "$state" in succeeded|failed) break ;; esac
  case "$trigger" in stop-*) die 'stop did not produce releasable settlement; inspect stop receipt and decide recovery manually' ;; esac
  (( SECONDS < deadline )) || die 'settlement unproven; no release; inspect worker and inbox'
  sleep 2
done
confirm "Worker state is $state. Inspect $out/settled-show.json and its completion evidence; accept settlement and release ONLY $dispatch?"
capture release orca orchestration worker-release --dispatch "$dispatch" --json \
  || die 'release uncertain; follow receipt recovery, do not blindly retry'
release_state=$(field "$out/release.json" result.state)
case "$release_state" in
  released|already_released)
    capture release-again orca orchestration worker-release --dispatch "$dispatch" --json
    case "$trigger" in
      stop-clean|stop-joined) capture stop-again orca orchestration worker-stop --dispatch "$dispatch" --json ;;
    esac
    ;;
  *) die "cleanup $release_state; preserve retained/pending resources and decide manually before another case" ;;
esac
capture final-show orca orchestration worker-show --dispatch "$dispatch" --json
echo 'Case recorded. Inspect raw receipts and actual state/reason before copying fixtures; never rename an expectation into evidence.'
