# workflow-orca

The Orca `DispatchBackend`. One dependency: `subagents-io`.

`workflow-<backend>` is the naming rule for the layer where vendor and system
coupling is allowed: `-io` is band-1 foundation, `-okf` is OKF-aware band 2,
and this is neither. `workflow-local` is its sibling and drives subprocesses;
this one drives Orca.

## Surface

`OrcaBackend(*, run=…, agent="claude", repo_selector=None)` opens a named,
durable session over an Orca Run — `open_session(name)` binds an existing Run
whose `objective` equals `name`, or creates one. `OrcaSession` implements the
eight `DispatchSession` methods and nothing else. Terminal release, ledger
settlement and the unsent-prompt nudge are folded into `ack()`, `close()` and
`wait()`; they are correctness, not API, and an Orca-aware coordinator with
extra methods to call would be a coordinator that no longer swaps backends.

## Fixtures

`tests/fixtures/` is JSON captured verbatim from the live `orca` CLI. Nothing
re-captures it automatically, so a change to Orca's JSON surfaces at the next
live run rather than at `just check`. `tests/fixtures/FIXTURES.md` records the
exact command behind each file.
