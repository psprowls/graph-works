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

grep -F 'references/launch-worker.py' "$AUTO_DRIVE" >/dev/null || fail "auto-drive uses the shipped launch recipe"
grep -F 'launch.requested' "$AUTO_DRIVE" >/dev/null || fail "auto-drive checks requested launch proof"
grep -F 'launch.effective' "$AUTO_DRIVE" >/dev/null || fail "auto-drive checks effective launch proof"
grep -F 'task-list --run <run_id> --json' "$AUTO_DRIVE" >/dev/null || fail "retry reads full task specs"
grep -F 'classify-restart' "$AUTO_DRIVE" >/dev/null || fail "restart uses the shipped classifier"
grep -F 'task-update --id <task_id> --status blocked' "$AUTO_DRIVE" >/dev/null || fail "deliberate skip is durable"
grep -F 'A missing terminal is normal' "$AUTO_DRIVE" >/dev/null || fail "auto-drive supports terminal-free workers"
grep -F 'Preflight — confirm the skill resolves.' "$WORKFLOW" >/dev/null || fail "workflow preserves skill preflight"
grep -F '<!-- rider-table:start -->' "$WORKFLOW" >/dev/null || fail "workflow preserves stage riders"
grep -F 'One stage per invocation' "$WORKFLOW" >/dev/null || fail "workflow preserves one-stage advancement"
grep -F 'This is reporting only' "$WORKFLOW" >/dev/null || fail "workflow reports routing without changing session"
if grep -Fq -- '--agent claude' "$AUTO_DRIVE"; then fail "auto-drive must use the planned agent"; fi
if grep -Fq 'permission_mode' "$AUTO_DRIVE"; then fail "auto-drive must not promise permission-mode control"; fi
ONBOARD="$PLUGIN_ROOT/skills/onboard/SKILL.md"
for document in "$AUTO_DRIVE" "$ONBOARD"; do
  if grep -Eq 'workflow\.pipeline|workflow\.auto_drive\.permission_mode' "$document"; then
    fail "$document must not recommend retired dispatch settings"
  fi
  grep -F 'dispatch.local.yaml' "$document" >/dev/null || fail "$document explains local dispatch rules"
  grep -F 'gw config sync' "$document" >/dev/null || fail "$document syncs dispatch changes"
done
grep -F "Permissions remain the selected agent's existing settings" "$ONBOARD" >/dev/null || fail "onboarding preserves agent-owned permissions"
grep -F 'Only dispatches classified `settled` by this fresh proof check may be released' "$AUTO_DRIVE" >/dev/null || fail "wrap-up rechecks launch proof before release"

cat >"$FIXTURE/dispatch.json" <<'JSON'
{"key":"work/example#execute","agent":"codex","model":"provider model/id","reasoning_effort":"high","prompt":"Do the work.\r\nKeep this line.\r\n\r\n"}
JSON
printf '%s\n' '["--worktree","new-top-level","--name","feature example","--base-branch","develop","--repo","repo-1"]' >"$FIXTURE/placement.json"

python3 "$RECIPE" encode \
  --dispatch "$FIXTURE/dispatch.json" \
  --placement "$FIXTURE/placement.json" >"$FIXTURE/spec"

python3 - "$FIXTURE/spec" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8", newline="") as stream:
    text = stream.read()
line, prompt = text.split("\n", 1)
assert line.startswith("GW_LAUNCH_V1 ")
assert json.loads(line.removeprefix("GW_LAUNCH_V1 ")) == {
    "version": 1,
    "dispatch_key": "work/example#execute",
    "agent": "codex",
    "model": "provider model/id",
    "reasoning_effort": "high",
    "placement_argv": ["--worktree", "new-top-level", "--name", "feature example",
                       "--base-branch", "develop", "--repo", "repo-1"],
}
assert prompt == "Do the work.\r\nKeep this line.\r\n\r\n"
PY

cat >"$FIXTURE/fake-create-orca" <<'PY'
#!/usr/bin/env python3
import json, os, sys
with open(os.environ["ARGV_JSON"], "w", encoding="utf-8") as stream:
    json.dump(sys.argv[1:], stream)
print('{"result":{"task":{"id":"task-1"}}}')
PY
chmod +x "$FIXTURE/fake-create-orca"
ARGV_JSON="$FIXTURE/create.argv.json" python3 "$RECIPE" create \
  --orca "$FIXTURE/fake-create-orca" --spec "$FIXTURE/spec" \
  --run run-1 --task-title work/example#execute \
  --display-name 'Example · execute' >"$FIXTURE/create.out"
python3 - "$FIXTURE/create.argv.json" "$FIXTURE/spec" <<'PY'
import json, sys
argv = json.load(open(sys.argv[1], encoding="utf-8"))
with open(sys.argv[2], encoding="utf-8", newline="") as stream:
    spec = stream.read()
assert argv == ["orchestration", "task-create", "--run", "run-1", "--spec", spec,
                "--task-title", "work/example#execute", "--display-name",
                "Example · execute", "--json"]
PY

cat >"$FIXTURE/fake-orca" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
: >"$ARGV_LOG"
for arg in "$@"; do printf '%s\n' "$arg" >>"$ARGV_LOG"; done
printf '%s\n' '{"result":{"dispatchId":"dispatch-new","launch":{"requested":{"agent":"codex","model":"provider model/id","effort":"high"},"effective":{"agent":"codex","model":"provider model/id","effort":"high"}}}}'
SH
chmod +x "$FIXTURE/fake-orca"

ARGV_LOG="$FIXTURE/initial.argv" python3 "$RECIPE" launch \
  --orca "$FIXTURE/fake-orca" --spec "$FIXTURE/spec" \
  --task task-1 --dispatch-key work/example#execute --run run-1 >"$FIXTURE/initial.out"

cat >"$FIXTURE/expected-initial.argv" <<'EOF'
orchestration
worker-start
--task
task-1
--agent
codex
--run
run-1
--worktree
new-top-level
--name
feature example
--base-branch
develop
--repo
repo-1
--model
provider model/id
--effort
high
--json
EOF
diff -u "$FIXTURE/expected-initial.argv" "$FIXTURE/initial.argv"

# Editing the current dispatch rules/plan between attempts cannot affect retry:
# retry consumes the full original task spec and its frozen placement.
cat >"$FIXTURE/dispatch.json" <<'JSON'
{"key":"work/example#execute","agent":"claude","model":"changed","reasoning_effort":"low","prompt":"changed"}
JSON
if ARGV_LOG="$FIXTURE/retry-without-recovery.argv" python3 "$RECIPE" launch \
  --orca "$FIXTURE/fake-orca" --spec "$FIXTURE/spec" \
  --task task-1 --dispatch-key work/example#execute --run run-1 \
  --retry-of dispatch-old >"$FIXTURE/retry-without-recovery.out" 2>"$FIXTURE/error"; then
  fail "retry without recovery-approved placement must be blocked"
fi
printf '%s\n' '["--worktree","path:/tmp/allocated-example"]' >"$FIXTURE/recovery-placement.json"
ARGV_LOG="$FIXTURE/retry.argv" python3 "$RECIPE" launch \
  --orca "$FIXTURE/fake-orca" --spec "$FIXTURE/spec" \
  --task task-1 --dispatch-key work/example#execute --run run-1 --retry-of dispatch-old \
  --recovery-placement "$FIXTURE/recovery-placement.json" >"$FIXTURE/retry.out"
cat >"$FIXTURE/expected-retry.argv" <<'EOF'
orchestration
worker-start
--task
task-1
--agent
codex
--run
run-1
--worktree
path:/tmp/allocated-example
--model
provider model/id
--effort
high
--retry-of
dispatch-old
--json
EOF
diff -u "$FIXTURE/expected-retry.argv" "$FIXTURE/retry.argv"

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
    "task_id": "task_0a0b0c0d0e0f", "dispatch_id": None, "action": "recovery-inspection"
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
    "task_id": "task-reserved", "dispatch_id": None, "action": "recovery-inspection"
}
assert rows["task-no-worker-skip"]["action"] == "deliberate-skip"
assert rows["task-failed-skip"] == {
    "task_id": "task-failed-skip", "dispatch_id": "dispatch-failed", "action": "deliberate-skip"
}
assert rows["task-stopped-skip"]["action"] == "deliberate-skip"
assert rows["task-live-blocked"] == {
    "task_id": "task-live-blocked", "dispatch_id": "dispatch-live", "action": "live"
}
assert rows["task-unknown-blocked"] == {
    "task_id": "task-unknown-blocked", "dispatch_id": "dispatch-unknown", "action": "recovery-inspection"
}
PY

cat >"$FIXTURE/effort-only.json" <<'JSON'
{"key":"work/example#execute","agent":"codex","model":null,"reasoning_effort":"high","prompt":"Do the work."}
JSON
if python3 "$RECIPE" encode --dispatch "$FIXTURE/effort-only.json" \
    --placement "$FIXTURE/placement.json" >"$FIXTURE/bad-spec" 2>"$FIXTURE/error"; then
  echo "expected effort without model to be refused" >&2
  exit 1
fi
grep -F "Set a model or clear reasoning_effort." "$FIXTURE/error" >/dev/null

if python3 "$RECIPE" launch --orca "$FIXTURE/fake-orca" --spec "$FIXTURE/spec" \
    --task task-1 --dispatch-key work/wrong#execute --run run-1 >"$FIXTURE/wrong-key.out" 2>"$FIXTURE/error"; then
  fail "a task title that disagrees with the saved dispatch key must be blocked"
fi
grep -F "does not match the task title" "$FIXTURE/error" >/dev/null || fail "wrong-key recovery reason is actionable"

cat >"$FIXTURE/bad-receipt-orca" <<'SH'
#!/usr/bin/env bash
printf '%s\n' '{"result":{"dispatchId":"dispatch-new","launch":{"requested":{"agent":"codex","model":"provider model/id","effort":"high"},"effective":{"agent":"claude","model":"provider model/id","effort":"high"}}}}'
SH
chmod +x "$FIXTURE/bad-receipt-orca"
if python3 "$RECIPE" launch --orca "$FIXTURE/bad-receipt-orca" \
    --spec "$FIXTURE/spec" --task task-1 --dispatch-key work/example#execute \
    --run run-1 >"$FIXTURE/bad.out" 2>"$FIXTURE/error"; then
  echo "expected mismatched launch receipt to be refused" >&2
  exit 1
fi
grep -F "effective agent" "$FIXTURE/error" >/dev/null

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
                worker = workers["result"]["workers"][1]
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
                    "dispatch_id": worker.get("dispatchId"),
                    "action": "settled" if case in {"matching", "defaults"} else "recovery-inspection",
                }])


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
PY

echo "dispatch profile contract: ok"
