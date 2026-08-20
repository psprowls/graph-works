---
name: next
description: Drive a work item through its pipeline — compute the next stage with `gw work next`, dispatch the stage skill, verify the artifact, advance with `gw work advance`. One stage per invocation; clear context and re-run between stages. Usage /graph-works:next <slug> [--descend]
---

Invoke the graph-works:workflow skill with the given slug and follow it exactly as presented to you. If the arguments include `--descend`, pass it through to every `gw next <slug> --json` invocation the skill makes (opt-in auto-descend into the next actionable child).