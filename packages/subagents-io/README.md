# subagents-io

A bounded async fan-out pool for role-bound model dispatch: per-item failure
isolation, a JSONL trace record for every invocation, and opt-in USD cost
accounting.

Band 1 of the graph-works layering. This package **never reads the process
environment, never discovers a workspace, and never knows a file or directory
name.** The trace directory is a constructor argument and the price table is an
injected callable. `packages/subagents-io/tests/test_boundaries.py` holds that
mechanically by walking this package's own AST — no module imports `os`, and no
module imports a sibling workspace package.

## Install

```bash
pip install subagents-io
```

**One runtime dependency, and no extras.** `runner.py` constructs
`SystemMessage` and `HumanMessage`, so `langchain-core` is declared rather than
hidden: it is pure Python, drags in no provider stack and no `boto3`, and above
all is not a workspace package. `pool.py`'s `RunnableConfig` is still a
`TypedDict` imported under `TYPE_CHECKING`, because that one really is
annotation-only.

There is deliberately **no `[project.optional-dependencies]`**. An optional
dependency is still a declared edge, so there is no `subagents-io[bedrock]`:
model construction does not sit behind an extra, it ascends to the consumer.

## Usage

```python
import asyncio
from pathlib import Path

from models_io.pricing import cost_for_usage
from subagents_io import SubagentPool, TaskResult


async def summarize(page):
    response = await llm.ainvoke(page.text)
    return TaskResult(value=response.content, response=response)


pool = SubagentPool(trace_dir=Path("traces"), price_lookup=cost_for_usage)
result = await pool.run_all(
    items=pages,
    task=summarize,
    role="librarian",
    model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0",
    max_concurrency=5,
)
# result.successes -> [(item, "…summary…"), ...]
# result.errors    -> [PerItemError(item=…, exception=…), ...]
```

One item's failure never cancels its siblings — `run_all` gathers with
`return_exceptions=True` and returns both lists. A task may take `(item)` or
`(item, config)`; arity is detected once per fan-out.

Return a bare scalar and it flows into `successes` verbatim, with null tokens
in the trace. Return `TaskResult(value=…, response=…)` and the raw response's
`usage_metadata` is what gets traced, while `successes` still carries the
scalar.

## Cost accounting is opt-in

`price_lookup` is a keyword-only argument on both `SubagentPool` and
`write_trace_record`, and it defaults to `None`. **Omit it and `cost_usd` is
null in every record written** — no error, no warning. That is the one sharp
edge here, so it is named in the first example above.

`models_io.pricing.cost_for_usage` satisfies the `PriceLookup` shape:

```python
PriceLookup = Callable[[str, Mapping[str, int]], float]
```

This package never imports a price table. Cost is a caller's concern injected
at construction, which is why `models-io` is not a dependency and why
`test_boundaries.py` needs no carve-out. A lookup that raises `KeyError` — as
`UnknownModelError` does — leaves `cost_usd` null rather than failing the run.

## The trace logger

Every `run_all` writes one JSONL file per call, named
`{epoch}_{8-hex}.jsonl` — epoch prefix for chronological ordering, hex suffix
for uniqueness inside a single second. Every record carries
`schema_version: 1`.

Per-item completion lines are *also* emitted at `INFO` to a dedicated logger,
exported as `TRACE_LOGGER_NAME`; per-item starts go to the module logger at
`DEBUG`. **The pool always logs — the installed handler decides what shows.**
Attach a bare-message handler to surface live progress:

```python
import logging
from subagents_io import TRACE_LOGGER_NAME

handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("%(message)s"))
trace = logging.getLogger(TRACE_LOGGER_NAME)
trace.addHandler(handler)
trace.propagate = False
```

Bind to the constant rather than to the string. A package rename then fails at
import instead of silently detaching your handler.

`render_trace_record` is the single source of truth for that line format,
shared by the live log and any post-hoc viewer, so the two cannot drift.

## The dispatch seam

`dispatch.py` holds what a planner hands a runner: `PlannedDispatch` and its
nested `WorktreeAction`, both frozen and method-free, plus the two vocabularies
their string fields draw from.

```python
from subagents_io import DISPATCH_MODES, WORKTREE_ACTIONS, PlannedDispatch, WorktreeAction
```

The field names spell work-item concepts — `slug`, `phase`, `kind`, `effort` —
and that is deliberate. They are inert data. This package stores a string
called `kind`; it never decides what a kind is.

`PlannedDispatch.agent`, `model`, and `reasoning_effort` are already resolved
launch preferences. `effort` separately carries the work-item estimate. Backends
consume these values; this package does not choose an agent or permissions.

`routing.py` remains available for generic callers using its existing
first-match model-routing contract. Graph Works work-item dispatch instead uses
core's ordered profile resolver; these two APIs have distinct semantics:


```python
from subagents_io import resolve_model, validate_rules

rules = {
    "models": {"design": "claude-fable-5"},
    "overrides": [{"match": {"phase": "plan", "kind": "epic"}, "model": "claude-fable-5"}],
}
attrs = {"phase": "plan", "kind": "epic", "effort": "large"}

resolve_model(rules, attrs, default_key="phase")  # -> ModelResolution("claude-fable-5", None)
validate_rules(rules, {"phase": PHASES, "kind": KINDS, "effort": EFFORTS}, default_key="phase")
```

First-match-wins over `overrides` (scalar = equality, list = membership, absent
match key = wildcard), then the `models` tier keyed by `default_key`, then
`None` — meaning *inherit the session model*.

**The dimensions are the caller's, not this package's.** `resolve_model` matches
whatever keys the rules name against whatever attributes you pass;
`validate_rules` checks them against vocabularies you supply. A constraint on
an attribute you did not supply never matches, and a match key outside your
vocabularies is reported rather than silently dead. Adding a fourth dimension
needs no change here.

## The run context

`RunContext` is generic over its reader, bounded only by `Closeable`:

```python
from code_graph_io import GraphReader, open_reader
from subagents_io import RunContext

ctx: RunContext[GraphReader] = RunContext(workspace=ws, repo_root=repo, wiki=wiki, open_reader=open_reader)
reader = ctx.graph_reader()  # opened lazily, exactly once
ctx.close()  # idempotent
```

The reader is injected because this package knows nothing about graphs and
intends to keep it that way — a narrow `Protocol` here would have to mirror the
record types the adapters read off it, which is a shadow model of the graph
living in the package defined by not having one.

Build the context with `open_reader=None` for an adapter that never queries the
graph. Asking such a context for a reader raises **`NoGraphReader`**, naming the
keyword to pass — not an `AttributeError` on `None`.

Python 3.12 has no type-parameter defaults and `mypy --strict` implies
`disallow_any_generics`, so a bare `RunContext` is a type error. Parameterize
every annotation.

## Roles resolve here; models are built by the caller

`resolve_role_spec` merges a role's packaged entry with a workspace override,
field by field, and returns a frozen `RoleSpec`. **Both mappings arrive as
arguments.** This package opens no data file, reads no manifest, and names no
provider:

```python
from models_io import make_bedrock_llm
from subagents_io import RoleBinding, resolve_role_spec

spec = resolve_role_spec("librarian", packaged_entry, workspace_override)
binding = RoleBinding(spec, make_llm=lambda: make_bedrock_llm(spec.model_id, region=spec.region))
```

`RoleSpec.region` defaults to `None`, not `"us-east-1"` — that literal is a
Bedrock fact and `models_io.make_bedrock_llm` already defaults it. A second copy
here would be foundation code knowing a provider's geography.

`RoleBinding` carries a *factory*, not a constructed model, because `run_all`
calls it once per item. Share one client by closing over it; that is the
caller's decision, not this package's.

## The runner

Three entry points over an `Adapter`, all taking their model and their spec
from a `RoleBinding`:

```python
from subagents_io import run_all, run_loop, run_single

outcome = await run_single(
    adapter, ctx, item, binding=binding, do_parse=True, on_chunk=print, price_lookup=cost_for_usage
)
result = await run_all(adapter, ctx, binding=binding, trace_dir=traces, price_lookup=cost_for_usage)
loop = await run_loop(loop_adapter, ctx, item, binding=binding, trace_dir=traces)
```

`trace_dir` is a keyword on both fan-out entry points for the same reason the
pool takes one: this package never derives a path. `price_lookup` is optional
and defaults to `None`, in which case `cost_usd` is `None` — the same silence
the pool keeps, deliberately.

`stream_and_parse` is exported separately: it streams, accumulates usage, and
parses without ever raising on a parser failure, which lands in `parse_error`.

## Boundaries

`tests/test_boundaries.py` walks `src/subagents_io` with `ast` and asserts four
things: no module imports `os`; no module imports a sibling workspace package
(siblings derived from the filesystem, not hardcoded); `langchain_core` is the
*only* non-stdlib root any module imports, at runtime or otherwise; and the
module list is complete, so adding a module is a decision rather than an
omission. The third of those is an allowlist, which is what makes a second
runtime dependency fail a test before it reaches `pyproject.toml`.

The root `[tool.importlinter]` carries the workspace half of the same rule: an
`independence` contract over the five band-1 packages, which forbids edges in
every direction among the modules it names without ordering them at all. The
AST walk above is not redundant with it — the walk derives its sibling set from
the filesystem and so covers a package added later, while the contract's
`modules` list is enumerated but catches an edge introduced from the other
side, in a package this suite never opens.
