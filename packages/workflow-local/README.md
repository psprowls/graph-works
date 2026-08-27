# workflow-local

A `subagents_io.backend.DispatchBackend` that runs each worker as a child
process on this machine.

`workflow-<backend>` is the naming rule: `-io` is band-1 foundation, `-okf` is
OKF-aware band 2, and `workflow-<backend>` is **where vendor and system
coupling is allowed to live**. `subagents-io` carves nothing out of its band-1
boundary — no module there imports `os` or a sibling package — so the first
concrete backend ships here instead, and `workflow-orca` will be its sibling
rather than a second implementation squeezed into the same module.

## Using it

```python
from pathlib import Path
from workflow_local import LocalBackend

backend = LocalBackend(
    Path("/var/run/dispatch"),
    argv_for=lambda d: ["claude", "--bg", "-p", d.prompt],
)
session = backend.open_session("auto-drive:my-epic")  # binds or creates
record = session.launch(planned_dispatch)
for event in session.wait(timeout_s=30.0):
    handle(event)
    session.ack(event)
```

`root` is a constructor argument, not something this package derives: a package
that derives a path has started discovering a workspace. `argv_for` is injected
for the same reason — what command runs a stage is prompt-and-command assembly,
a band up.

`env=None` (the default) inherits this process's environment. Passing a mapping
replaces it wholesale. Either way the three contract variables below are
overlaid on top.

## Layout under `root`

```
<root>/<session>/ledger.json               # key -> handle, pid, state, argv, cwd, started_at, acked_through
<root>/<session>/<handle>/events.jsonl     # the child appends; the backend tails
<root>/<session>/<handle>/replies.jsonl    # the backend appends; the child tails
<root>/<session>/<handle>/stdout.log       # the child's merged stdout+stderr
```

`ledger.json` is what makes `workers()` correct across a coordinator restart
with no live process at all, and what makes redelivery correct rather than
best-effort: it persists `acked_through` per worker, so reopening a session
replays every unacked event and no acked one.

## The child-side contract

Three environment variables are set on every child, and nothing else:

| Variable | Meaning |
|---|---|
| `SUBAGENT_EVENT_LOG` | absolute path the child **appends** JSONL events to |
| `SUBAGENT_REPLY_LOG` | absolute path the child **reads** JSONL replies from |
| `SUBAGENT_DISPATCH_KEY` | this worker's `PlannedDispatch.key`, `"<slug>#<phase>"` |

A child that writes no events still settles — see *The reaper* below — so the
contract is opt-in per capability rather than all-or-nothing.

### Event lines

One JSON object per line, appended to `SUBAGENT_EVENT_LOG`. `kind` is required
and must be one of `subagents_io.backend.EVENT_KINDS`:

```json
{"kind": "heartbeat", "phase": "implementing"}
{"kind": "question", "question": "merge or hold?", "options": ["merge", "hold"]}
{"kind": "escalation", "subject": "Blocked: no worktree", "body": "..."}
{"kind": "worker_done", "outcome": "succeeded", "summary": "...", "files_modified": ["a.py"], "report_path": null}
```

Every other key is optional and defaulted. **A line that fails to parse, or
carries an unregistered `kind`, is skipped and counted — never raised.** A
malformed line from one child must not blind the coordinator to its siblings.
The count is on `LocalSession.skipped_lines`.

### Replies

The backend appends `{"reply_token": "<handle>:<line_no>", "answer": "..."}` to
`SUBAGENT_REPLY_LOG`. The token is the question's own `delivery_id`, so a
coordinator needs no second correlation scheme.

**A child consumes replies in order**: the Nth reply line answers the child's
Nth question. The child never learns its own `handle`, so it cannot match on
the token — per-worker ordering is what correlates them. A child that asks two
questions before reading either answer gets them back in the order it asked.

### The reaper

A process that exits without ever emitting `worker_done` gets one synthesized
on the next `wait()`: `outcome` is `"succeeded"` on exit code 0 and `"failed"`
otherwise, with the tail of `stdout.log` as the summary. A crashed child always
settles, so no key can sit live forever. Its `delivery_id` is
`"<handle>:reaped"` rather than a line number, because there is no line.

## What this package does not do

- **It does not provision worktrees.** `launch()` requires a concrete
  `dispatch.worktree.path` and raises `WorktreeNotProvisioned` otherwise. Git
  belongs beside `graph_works_core.provenance`, the one module declared to run
  it. Until that function exists, a `reuse` dispatch runs end-to-end here and a
  `fork-child` one does not.
- **It does not decide what to dispatch.** That is a band-3 planner's, and it
  arrives here as a fully-resolved `PlannedDispatch`.
- **It does not survive pid reuse.** Resumption probes a recorded pid; on a
  long-lived machine that pid may since belong to something else. Corroborating
  a process start time is platform-specific work this package will not do.
  `"unknown"` exists in `WORKER_STATES` so the honest answer is expressible,
  but the probe itself cannot always reach it.
- **It refuses to run on Windows.** `stop()`'s SIGTERM/SIGKILL escalation and
  the pid liveness probe both go through `os.kill`/`signal.SIGKILL`, and
  `os.kill(pid, 0)` maps to `TerminateProcess` there — a liveness probe that
  would kill the very worker it is asked to observe. Both `LocalBackend()` and
  `LocalSession()` (via `open_session()`) raise `BackendError` at construction
  on `sys.platform == "win32"`, before touching the ledger. Use
  `workflow-orca` as the Windows dispatch backend instead; `workflow_local.ledger`
  is pure `json`/`pathlib` and stays usable on Windows for inspecting a
  POSIX-written ledger.
