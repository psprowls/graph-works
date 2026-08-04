# gw-dispatch — background stage automation

Runs the `plan` and `execute` phases of the graph-wiki work pipeline in fresh
background Claude Code sessions, so you can keep designing new work items in the
foreground while filed items advance on their own.

`scripts/gw_dispatch.py` watches every work item in the vault. When an item's
next stage is automatable, it dispatches a background session running
`/graph-wiki:next <slug>`, then tracks that session to completion.

## Why background sessions and not subagents

`plugins/graph-wiki/skills/workflow/SKILL.md` states the constraint this
mechanizes:

> One stage per invocation, by design. Never chain stages in a session — each
> stage gets a fresh context window. The work item plus `raw/` artifacts are the
> durable state between sessions; nothing depends on conversation memory.

`claude --bg` gives exactly that: a full Claude Code session with its own
session id, transcript, plugin load, and SessionStart hooks. The `Agent` tool's
`claude` subagent type would *not* — it runs inside an existing session and
skips those hooks.

## Setup

### One-time: accept the bypassPermissions disclaimer

`--bg` refuses `bypassPermissions` until the disclaimer has been accepted
interactively:

```
--bg with bypassPermissions requires accepting the disclaimer first.
Run `claude --dangerously-skip-permissions` once interactively.
```

Run that once in a terminal, accept, and quit. The dispatcher detects this
condition and exits with the same instruction rather than failing obscurely.

### Workspace

The dispatcher needs `GRAPH_WIKI_WORKSPACE` (or `--workspace`). For this repo it
is already pinned in `.claude/settings.local.json`.

## Use

```bash
# See what would be dispatched, change nothing
scripts/gw_dispatch.py --once --dry-run

# Watch loop
scripts/gw_dispatch.py --notify

# Ledger rollup, including which sessions need input
scripts/gw_dispatch.py --status

# Clear a stalled/failed entry so it can be retried
scripts/gw_dispatch.py --reset <slug>
```

Ctrl-C stops the watcher only. Background sessions it started keep running —
`claude agents` to review them.

### Options worth knowing

| Flag | Default | Notes |
|---|---|---|
| `--phases` | `plan,execute` | `design` is excluded on purpose (see below) |
| `--max-parallel` | `2` | concurrent background stages |
| `--interval` | `30` | watch loop poll seconds |
| `--owner` | `$USER` | standing answer for the execute dispatch transition |
| `--permission-mode` | `bypassPermissions` | |
| `--model` | inherit | model override for dispatched sessions |

## What it will and will not run

**Automated:** `plan` (`writing-plans`, `planning-epics`) and `execute`
(`subagent-driven-development`, `test-driven-development`,
`systematic-debugging`).

**Never automated:**

- **`design`** — brainstorming is a conversation. This is the phase you keep in
  the foreground.
- **`finish`** — `finishing-a-development-branch` presents merge options and
  requires a typed `discard` confirmation. Those should block on a human.

**Skipped silently:** epics blocked *waiting on children*. Their children are
separate slugs and get picked up on their own, so descending is unnecessary.

## The two questions that otherwise stall every run

The workflow skill has exactly two routine asks before dispatch. Both are
pre-answered:

- **Owner** (`SKILL.md:76`) — *"ask the user if no owner is known"*. Supplied by
  `--owner`, injected via `--append-system-prompt`.
- **Effort** (`SKILL.md:50`) — the skill is told to *"never pick an effort
  yourself"*, and the dispatcher does not override that. An item missing effort
  surfaces as `waiting` and is never dispatched. Set it during design;
  `brainstorming/SKILL.md:73` already instructs the design stage to do so
  precisely so `/graph-wiki:next` is not blocked later.

## How completion is judged

**Not** by session exit state. A session can exit having accomplished nothing,
so `state: "done"` is treated as "finished running", not "succeeded".

When a run finishes, the dispatcher re-reads `gw work next <slug> --json` and
confirms the phase actually advanced. Outcomes:

| Ledger state | Meaning |
|---|---|
| `completed` | phase advanced (or item reached a terminal status) |
| `stalled` | session ended but phase did not move — needs a look |
| `blocked` | session is waiting on a human; `claude attach <id>` |
| `failed` | session reported failure |
| `lost` | session vanished and `gw work next` was unreadable |

`stalled`, `failed`, and `lost` are **sticky**: the slug is not re-dispatched
until you `--reset` it. This prevents a systematically failing item from
burning tokens in a redispatch loop.

The durable wiki state is the source of truth — which is what the pipeline's
"nothing depends on conversation memory" design already assumes.

## Answering a blocked stage

A background session that hits `AskUserQuestion` does **not** deadlock. It moves
to `state: "blocked"`, which the dispatcher reports:

```
[2026-08-02T18:04:11Z] !! blocked    2026-06-28-eval-scenarios...  id=78747b94  hint=claude attach 78747b94
```

Attach, answer the question, and detach — the stage continues from where it
paused, and the next poll flips it back to `running`.

This is why background sessions are used rather than headless `claude -p`: in
print mode there is nobody to answer `AskUserQuestion`, so the same situation
would require a custom question-serialization protocol.

## State files

Under `<workspace>/.graph-wiki/dispatch/`:

- `ledger.json` — one entry per slug: session id, phases, state
- `events.jsonl` — append-only lifecycle record
- `notifications.jsonl` — only if the optional hook below is installed

## Optional: push instead of poll

`scripts/gw-dispatch-notify.sh` is a `Notification` hook that records events the
moment they fire, rather than up to `--interval` seconds later. Polling already
works, so this is purely a latency improvement.

Install it in your **user** settings (`~/.claude/settings.json`) — *not* in the
graph-wiki plugin, where it would fire for every session of every user of the
plugin:

```json
{
  "hooks": {
    "Notification": [
      { "matcher": "*", "hooks": [
        { "type": "command",
          "command": "/Users/pat/Personal/agent-research/scripts/gw-dispatch-notify.sh" } ] }
    ]
  }
}
```

The Notification payload shape is not pinned across Claude Code versions, so the
hook records events verbatim rather than picking fields out of them. Read the
resulting JSONL to learn the real shape on your version. Verified present in the
2.1.220 binary: notification types `agent_needs_input` and `agent_completed`.

## Deliberate non-goals

- **`claude logs` is never parsed.** Background sessions run in a pty; the log
  is raw ANSI terminal capture, not a data feed. Session state comes from
  `claude agents --json`; progress comes from `gw work next`.
- **No retry-on-failure.** Sticky states require a human `--reset`.
