# AGENTS.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this package is

`workflow-local` is the first concrete implementation of `subagents_io.backend.DispatchBackend`
— a `subprocess`-based backend that runs each dispatched worker as a child process on this
machine. `subagents-io` (band 1) defines the seam as inert Protocols and frozen dataclasses only
(`DispatchBackend`, `DispatchSession`, `WorkerRecord`, the `WorkerEvent` union, `PlannedDispatch`)
and carves nothing out of its own purity for a first implementation — no `os`, no vendor SDK, no
concrete backend lives there. `workflow-<backend>` is the naming convention for where that vendor
and system coupling is *allowed* to live; `workflow-orca` is this package's future sibling, not a
second backend squeezed in here. Read `packages/subagents-io/src/subagents_io/backend.py` and
`dispatch.py` before touching this package's protocol-facing code — they're short, and this
package's job only makes sense in light of what `WorkerRecord.state`, `WorkerEvent`,
`DISPATCH_MODES`, and `WorktreeAction` mean there.

Single runtime dependency: `subagents-io` (workspace-pinned `>=0.2,<0.3`). Deliberately no
optional extras — a vendor SDK arriving as an extra would make this the package that runs two
backends, which the one-package-per-backend rule forbids.

## Commands

Root `justfile` recipes run this package under `uv run --package workflow-local`:

```bash
# types (strict) -- `just types` runs both platform arms
uv run --package workflow-local mypy --strict --platform linux packages/workflow-local/src
uv run --package workflow-local mypy --strict --platform win32 packages/workflow-local/src

# full test suite for this package
uv run --package workflow-local pytest packages/workflow-local/tests

# gated coverage (95% branch, this package only)
uv run --package workflow-local pytest packages/workflow-local/tests \
  --cov=workflow_local --cov-branch --cov-report=term-missing --cov-fail-under=95
```

Subset examples:

```bash
uv run --package workflow-local pytest packages/workflow-local/tests/test_ledger.py
uv run --package workflow-local pytest packages/workflow-local/tests/test_local_backend.py -k stop
uv run --package workflow-local pytest packages/workflow-local/tests/test_conformance.py -k local
```

`tests/conftest.py`, `tests/conformance.py`, `tests/fake.py`, and `tests/child.py` are plain
sibling modules, not a package — that's why `pythonpath = ["tests"]` is set in `pyproject.toml`.
It's what lets `from conformance import CASES` and `from fake import FakeBackend` resolve
identically whether you run from the repo root (`uv run pytest packages/workflow-local/tests`,
which the *root* `testpaths` doesn't pick up automatically) or via `--package workflow-local`.

## Architecture

### Subprocess dispatch mechanics

`LocalBackend.open_session(name)` binds or creates a `LocalSession` at `<root>/<name>/` — binding
by name is what makes a coordinator restart a no-op rather than a second session.
`LocalSession.launch(dispatch)`:

1. Refuses a `dispatch.key` already present in this session (`BackendError`) — silently returning
   the existing record would make a re-dispatch after a failure read as a success.
2. Refuses `dispatch.worktree.path is None` (`WorktreeNotProvisioned`) — this backend never
   touches git; it expects a fully-resolved worktree from a band-3 planner. A `reuse` or `"main"`
   dispatch works end-to-end here; `fork-child`/`create-top-level` don't, until something upstream
   provisions the worktree.
3. Computes a deterministic, collision-proof `handle` via `_handle_for(key)`: sanitize the key to
   filesystem-safe characters, then suffix with a 4-byte blake2s digest of the *original* key —
   deterministic because resumption looks the directory up by key with no live process to ask;
   digest-suffixed because two different keys can sanitize to the same string.
4. Creates `<root>/<session>/<handle>/{events.jsonl,replies.jsonl,stdout.log}` and `Popen`s
   `argv_for(dispatch)` with `cwd=dispatch.worktree.path`, merged stdout+stderr into `stdout.log`,
   and three env vars overlaid on `env` (see below).
5. Starts a **daemon thread that calls `proc.wait()` and does nothing else.** This is not
   optional bookkeeping — see "the reaping-thread invariant" below.

`argv_for` and `root` are both constructor-injected rather than derived: a package that derives a
path has started discovering a workspace, and knowing what command runs a stage is
prompt-and-command assembly, which belongs a band up from this backend.

### The durable per-session ledger

`<root>/<session>/ledger.json` (`workflow_local.ledger`) is the *entire* durable state of a
session — one flat `dict[key, LedgerEntry]`, `LedgerEntry` carrying `key`, `handle`, `pid`,
`state`, `argv`, `cwd`, `started_at`, `acked_through`, `exit_code`, `last_heartbeat_at`, `detail`.
"Durable" matters concretely: `LocalSession.__init__` calls `read_ledger` and re-derives all
in-memory state from it, with **no live process of its own to ask** — `_probe_on_open()` calls
`os.kill(pid, 0)` for every non-terminal entry and sets `"running"` or `"unknown"` accordingly.
This is what makes `workers()` correct across a coordinator restart, and what makes `launch()`'s
duplicate-key refusal a backend guarantee rather than coordinator bookkeeping a crash would
discard.

`acked_through` is the field that encodes the redelivery rule: it's a *line count* of
`events.jsonl` the coordinator has acked, not a byte offset — a byte offset says where reading
stopped, which can't express "replay the tail I never confirmed." On reopen, every event at or
past `acked_through` is replayed; everything before it is not. See
`test_an_unacked_event_is_redelivered_after_a_reopen` /
`test_an_acked_event_is_not_redelivered_after_a_reopen` for the exact contract.

Writes are atomic (`tempfile.mkstemp` sibling + `Path.replace`) because a truncated ledger reads
as "these keys were never dispatched," which would relaunch work that's already running. Reading
a corrupt or wrong-shaped ledger **raises** rather than returning a partial dict, for the same
reason — a half-ledger is corruption, not something to paper over.

### JSONL event/reply protocol between parent and child

Three env vars, and nothing else, are set on every child (`_child_env`):

| Variable | Direction | Meaning |
|---|---|---|
| `SUBAGENT_EVENT_LOG` | child appends | absolute path to `<handle>/events.jsonl` |
| `SUBAGENT_REPLY_LOG` | child reads | absolute path to `<handle>/replies.jsonl` |
| `SUBAGENT_DISPATCH_KEY` | — | this worker's `PlannedDispatch.key` |

**Events**: one JSON object per line. `kind` must be one of `subagents_io.backend.EVENT_KINDS`
(`worker_done`, `question`, `escalation`, `heartbeat`); every other field is optional/defaulted.
`LocalSession._parse` treats a non-JSON line, a JSON non-object, or an unregistered `kind`
identically: **skip and count in `skipped_lines`, never raise.** A malformed line from one child
must not blind the coordinator to its siblings — this mirrors okf-io's "nothing on the content
path raises" posture, applied to the process boundary instead of YAML.

**Delivery IDs** are `"<handle>:<line_no>"`, assigned by the *reader* at drain time (`_parse`
takes `line_no` from enumerating `events.jsonl`, not from anything the child writes). `ack()`
parses the suffix after `:` — if it's not a plain digit (i.e. it's `"reaped"`), `ack()` silently
no-ops, since a synthesized event has no line to advance the cursor past.

**Replies**: the backend appends `{"reply_token": "<handle>:<line_no>", "answer": ...}` to
`replies.jsonl`. `reply_token` is reused literally as the question event's `delivery_id`, so no
second correlation scheme exists. **Correlation on the child side is positional, not by token**:
`tests/child.py`'s `next_reply()` reads reply line N for its own Nth unconsumed question — the
child never learns its own `handle` and cannot filter by token. This means a real child *must*
consume replies strictly in the order it asked questions; concurrent/out-of-order questions from
one worker are not supported by this protocol as implemented.

### Wait/poll loop ordering — drain before reap

`LocalSession.wait(timeout_s)` polls `_poll()` on `poll_interval_s` (default 0.25s) until it
returns at least one event or the deadline passes (returns `[]` at timeout, never raises — a
coordinator's outer loop stays a plain `while`). `_poll()` does, in this order:

1. `_drain(key)` for every key — tail `events.jsonl` from `self._cursors[key]`, parse new lines,
   fold each event into the in-memory `LedgerEntry` via `_apply` (mutates `state`/`detail` for
   `WorkerDone`/`Heartbeat`), advance the cursor.
2. `_reap(key)` for every key — only for entries **not already terminal** and **not alive**
   (`_alive` checks the tracked `Popen.poll()` first, falling back to `os.kill(pid, 0)` for a
   resumed session with no `Popen` handle).

**This ordering is load-bearing**: draining before reaping means a child that wrote `worker_done`
and then exited settles via its own event, not via a synthesized `reaped` one. Reversing the order
would race a fast-exiting child against its own final event.

### The reaper — settling a silent child

"Silent" means: the process is no longer alive (by `_alive`) but its `LedgerEntry.state` never
reached a terminal state via a parsed `worker_done` event — i.e., it exited (crashed, was killed,
forgot to emit) without ever writing its own settlement. `_reap` synthesizes a `WorkerDone` for
it: `outcome` is `"succeeded"` iff the tracked `Popen.returncode == 0`, else `"failed"` (and always
`"failed"` if there's no tracked `Popen` at all, e.g. after a resume with the exit code unknown).
`summary` is the last `SUMMARY_TAIL_CHARS` (2000) characters of `stdout.log`. Its `delivery_id` is
`"<handle>:reaped"` (a sentinel, not a line number) — `ack()` recognizes and no-ops on this
suffix. The guarantee this buys: **no key can sit live forever** — a crashed or killed child
always eventually settles on the next `wait()`.

### The reaping-thread invariant (read this before touching `launch()` or `stop()`)

`launch()` starts a **daemon thread whose only job is `proc.wait()`** (a blocking `waitpid`).
This is not incidental — on POSIX, nothing else reaps a terminated child. Without it, a process
killed externally (operator `kill -9`, a crashed coordinator, a test harness) becomes a zombie:
still visible to `os.kill(pid, 0)` (so `_pid_alive`/`_alive` would keep reporting it alive)
until *something* calls `waitpid` on it. `stop()`'s SIGTERM→SIGKILL escalation loop
(`_terminate`) polls `_pid_alive(pid)` to detect death — and that loop's progress *depends on*
this daemon thread actually observing the exit via the real `waitpid` syscall. If you ever remove
or short-circuit that thread, both `stop()` and the ordinary reap path silently stop detecting
process death. It also captures the *true* exit status before anyone else can observe a stale one.

### Probing on open — `"unknown"` as a first-class state

`_probe_on_open()` runs once per `LocalSession.__init__` (i.e., on every `open_session`), and is
the only place `"unknown"` is assigned. It is deliberately distinct from `"failed"`: a resumed
session has a recorded `pid` but no live `Popen`, so all it can do is `os.kill(pid, 0)`. A dead pid
with no terminal state becomes `"unknown"` rather than `"failed"`, and the *next* `wait()`'s
`_reap` pass settles it for real. This package explicitly does not corroborate a process start
time, so **pid reuse can misreport a dead worker as running** on a long-lived machine — documented
in the README as a known, unaddressed limitation, not a bug to fix reflexively.

### Other invariants worth knowing before editing

- **`close()` never kills children.** It flushes the ledger and closes this process's file
  handles only — "a coordinator restart should find its workers where it left them" is the whole
  point of ledger-based resumption. Only `stop()` kills.
- **`env=None` inherits `os.environ`; passing a mapping replaces it wholesale** (not merged) —
  the three `SUBAGENT_*` vars are always overlaid on top regardless.
- **POSIX only.** `_pid_alive` and `_terminate` both go through `os.kill`/`signal.SIGKILL`, which
  don't exist on Windows. No compatibility shim is planned within this package.
- **`stdout.log`** is the merged stdout+stderr of the child, used only as the reaper's summary
  tail — it is not part of the structured event protocol.

## Testing structure

`tests/conformance.py` defines `CASES` — one `BackendCase` per backend (`"fake"` via
`tests/fake.py`'s in-memory `FakeBackend`, `"local"` via this package's `LocalBackend` driving
`tests/child.py` as a real subprocess) — and `test_conformance.py` runs the same scenarios against
both, parametrized. This is intentional: a Protocol proven against one implementation is a
Protocol its one implementation defined, so `FakeBackend` exists specifically to be a second,
independent implementation (and to reach vocabulary corners `LocalBackend` can't, e.g.
`WORKER_STATES`'s `"pending"` and `UnsupportedMode` via a narrowed `supported_modes`).
`tests/child.py` is a tiny scriptable worker: argv items like `heartbeat:<phase>`,
`question:<text>`, `done:<outcome>`, `garbage`, `sleep:<seconds>` compose real-process scenarios
(kill-and-resume, malformed lines, SIGTERM-ignored escalation to SIGKILL) that only mean something
against the `"local"` case — `conformance.py`'s `BackendCase.real_processes` flag gates those.

`test_local_backend.py` and `test_ledger.py` cover this package's own internals (ledger atomicity,
reaping edge cases, env var isolation) that don't apply across backends and so don't belong in the
shared conformance suite.
