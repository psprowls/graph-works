---
name: using-graph-works
description: Use when starting any conversation - establishes how to find and use skills, and how the graph-works work-item pipeline is driven
---

<SUBAGENT-STOP>
If you were dispatched as a subagent to execute a specific task, ignore this skill.
</SUBAGENT-STOP>

<EXTREMELY-IMPORTANT>
If you think there is even a 1% chance a skill might apply to what you are doing, you ABSOLUTELY MUST invoke the skill.

IF A SKILL APPLIES TO YOUR TASK, YOU DO NOT HAVE A CHOICE. YOU MUST USE IT.
</EXTREMELY-IMPORTANT>

## The rule

**Invoke relevant or requested skills BEFORE any response or action** — including
clarifying questions, exploring the codebase, or checking files. If a skill turns
out to be wrong for the situation, you do not have to follow it.

Announce "Using [skill] to [purpose]" and follow the skill exactly. If it has a
checklist, create a todo per item.

## Skill priority

Process skills come first — they set the approach; implementation skills then
carry it out.

- "Let's build X" → brainstorming first, then implementation skills.
- "Fix this bug" → systematic-debugging first, then domain skills.

## The work-item pipeline

Work is tracked as **path-native work items** in a graph-works workspace, driven
through the `gw` CLI. `gw work next <work-path>` computes which pipeline stage an
item is at and which skill runs it; `gw work advance <work-path>` moves it on.

`/graph-works:workflow <work-path>` drives **exactly one stage per session**. That
is the design, not a limitation: each stage gets a fresh context window, and the
work item plus the artifacts under its owned `references/` directory are the only
durable state between stages. Never chain two stages in one session. Clear context
(`/clear`) between them.

**Stage skills may belong to a different plugin.** `gw work next` returns the stage
skill in its `action.skill` field, and that name is authoritative — invoke it as
returned. Do not assume a stage skill lives in this plugin.

## Where artifacts go

Every work item owns a directory. Design and plan artifacts belong under its
`references/` directory, at the exact path a pipeline dispatch hands you. The
`PreToolUse` routing hook injects the resolved absolute location; use it.
