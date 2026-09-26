#!/usr/bin/env bash
# The skills say "gw commits; never commit workspace main" (feature-single-workspace-commit-authority).
set -euo pipefail
cd "$(dirname "$0")/.."
fail=0
if grep -rn "normal workspace commit" skills; then
  echo "FAIL: a skill still points at a 'normal workspace commit procedure'"; fail=1
fi
for file in skills/workflow/SKILL.md skills/finishing-relay/SKILL.md skills/workflow/references/brief-riders.md; do
  grep -q "commits the receipt itself; do not commit the workspace" "$file" || { echo "FAIL: $file lacks the record-commits line"; fail=1; }
done
grep -q "gw verbs commit their own workspace writes" skills/workflow/SKILL.md || { echo "FAIL: workflow Steps line"; fail=1; }
grep -q "The coordinator commits nothing" skills/auto-drive/SKILL.md || { echo "FAIL: auto-drive §4.1"; fail=1; }
if grep -q "the worker's gw verbs committed its workspace writes" skills/auto-drive/SKILL.md; then
  echo "FAIL: auto-drive assumes every gw commit succeeded"; fail=1
fi
grep -q "status --porcelain -- okf/work" skills/auto-drive/SKILL.md || { echo "FAIL: auto-drive wrap-up dirty check"; fail=1; }
grep -q "next committing same-item gw verb" skills/auto-drive/SKILL.md || { echo "FAIL: auto-drive lacks same-item reference recovery"; fail=1; }
grep -q "retry the owning operation or run a gw verb that writes those exact paths" skills/auto-drive/SKILL.md || { echo "FAIL: auto-drive lacks other-path recovery"; fail=1; }
if grep -q "owning item's next gw verb commits them" skills/auto-drive/SKILL.md; then
  echo "FAIL: auto-drive claims the next verb commits unrelated dirty paths"; fail=1
fi
grep -q "record-placement commits" skills/auto-drive/SKILL.md || { echo "FAIL: auto-drive placement note"; fail=1; }
grep -q "AGENTS.md" skills/workflow/references/editing-work-items.md || { echo "FAIL: editing-work-items commit pointer"; fail=1; }
[ "$fail" -eq 0 ] && echo "ok: workspace commit authority"
exit "$fail"
