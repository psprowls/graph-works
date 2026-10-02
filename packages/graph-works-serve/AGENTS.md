# AGENTS.md — graph-works-serve

The loopback HTTP sidecar. Band: interface (above `graph-works-core`, beside
`graph-works-cli` and `graph-works-wire`).

## Modules

| Module | Holds |
|---|---|
| `context` | `ServeContext` and framework-neutral `Reply`. |
| `params` | `Param`, `ParamError`, and query/JSON body parsing. |
| `errors` | exit/status mappings, error calls, and refusals. |
| `guard` | pure ASGI token/Host/CORS middleware and its pure checks. |
| `routes` | `RouteSpec`, handlers, and `ROUTES`. |
| `mutations` | shared plan/apply engine, digest, clock seam, and lock. |
| `mutation_specs` | concrete advance, archive, proposal-decision, decision-answer, section-write, and repository-scan adapters. |
| `catalog` | route description payload. |
| `app` | `build_app()`; the sole Starlette import. |
| `discovery_file` | sidecar record lifecycle and liveness checks. |
| `main` | `gw-serve` argparse/uvicorn entry point; the sole uvicorn import. |

## Gotchas

- `app.py` is the only Starlette import and `main.py` is the only uvicorn import; the boundary test enforces this.
- Adding a route means adding a `RouteSpec` to `routes.ROUTES` and regenerating `tests/fixtures/routes.golden.json`.
- `/v1/wiki/citations` has no event kind of its own: citations derive from a page, so a `page` event already means they may be stale. `/v1/code/excerpt` is not live -- the sidecar does not watch code repositories.
- `/v1/code-graph/neighborhood` is not live, because the sidecar does not watch `code.db`. `/v1/wiki/lint` is mechanical only (no model call). `/v1/query/brief` is lexical when the embedder fails, and its payload says so (`retrieval: "lexical"`).
- Read refusals use the error envelope with the projection as `payload`: `unknown-page`, `unknown-repository`, `no-proposal` and `not-in-graph` answer `404` (`unresolved`); `no-resource` and `no-graph` answer `422` (`refused`).
- `graph-works-cli` is a dev-group dependency for twin tests only.
- `next` is `dry_run=True`, so `normalized` is always `null`.
- `discovery_file` uses POSIX `fcntl.flock` to serialize record replacement
  and ownership cleanup; native Windows remains outside its declared platform boundary.

## Guard order and CORS

`check_host` and `check_token` are separate because a CORS preflight has to be
answered between them. Per request: the `Host` check, then — only when
`gw-serve --allow-origin` supplied an allow-list — a token-free `204` for an
allowed origin's preflight (`403` for any other origin's), then the token
check. Every response to an allowed origin, refusals and the event stream
included, carries `Access-Control-Allow-Origin` and `Vary: Origin`. With no
allow-list none of that runs and no CORS header is ever sent. `check()` still
composes both halves for callers that want one call. Origins match exactly
(`scheme://host[:port]`, or the literal `null` for an opaque origin such as a
`file://` renderer). `null` needs no guard code: the guard only compares and
echoes strings, so `parse_origin` in `main.py` is the one place that knows
about it.

## Change stream (`/v1/events`)

`coalesce` derives a deterministic identity-only batch; `hub` fans it out with
bounded subscriber queues; `sse` frames bytes and runs a subscriber stream.
These three modules import no HTTP framework. `watch` is the only watchfiles
importer. `app` owns the hub and watcher lifespan, and `main._Server.handle_exit`
closes the hub before uvicorn drains connections. Uvicorn must enable lifespan.

The stream sends `ready` first with `retry: 2000`, then `changes` batches,
`resync` (`overflow`, `rewatch`, or `watcher-error`), and idle `: ping` comments
every 15 seconds. There are no event IDs or replay; a reconnect's `ready` tells
the client to fetch its current view again. Each subscriber has 64 slots; overflow
replaces its backlog with one resync without delaying other subscribers.

watchfiles batches are unordered. The disk at flush decides the net change:

| File at flush | Changes seen | Net change |
|---|---|---|
| Present | Only added | added |
| Present | Anything else | modified |
| Absent | Anything | deleted |

The debounce uses 300 ms quiet / 800 ms maximum windows. Bundle watching is
recursive with dot-prefixed path segments excluded. The event filter also excludes
`repositories/<name>/references/git` itself and every descendant, including
deleted paths, before coalescing and publication. Adjacent repository references
still emit. This filter does not prune native watch registration, polling traversal,
or raw backend event collection. Config watching covers the
exact manifest, local manifest, projection, dispatch, and local dispatch paths
through their parent directories, non-recursively: watching the directory
survives atomic replacement of `config.json`. `.gw/worktrees` and
`.gw/cache/traces` are never recursively watched and their changes never emit.
Manifest edits re-resolve the watch sets; invalid replacements retain the old
sets. A watcher failure broadcasts resync and retries with 1–30 second backoff.

TestClient buffers entire responses, so route tests must close the hub.
Incremental delivery and signal shutdown are tested over a real socket.
Uvicorn drains connections before lifespan shutdown, so only closing the hub
from the signal hook can end open streams in time for that shutdown.

Native integration was exercised on macOS with watchfiles 1.2.0 / FSEvents.
A raw non-recursive probe emitted immediate children but omitted a modified
nested file; `recursive=False` was honored. Linux was not available in this
execution environment; both Linux and Windows typing arms are checked.

## Mutation routes

All twelve mutation routes accept `POST` with a JSON body and require a header-only
`Authorization: Bearer` token. Query tokens are accepted only on `GET /v1/events`.

| Operation | Plan | Apply |
|---|---|---|
| Advance work | `/v1/work/advance/plan` | `/v1/work/advance/apply` |
| Archive work | `/v1/work/archive/plan` | `/v1/work/archive/apply` |
| Decide proposal | `/v1/wiki/proposal/decide/plan` | `/v1/wiki/proposal/decide/apply` |
| Answer decision | `/v1/work/decision/answer/plan` | `/v1/work/decision/answer/apply` |
| Write section | `/v1/wiki/section/plan` | `/v1/wiki/section/apply` |
| Scan one repository | `/v1/scan/plan` | `/v1/scan/apply` |

Proposal decide takes `target` and `decision` (`approve`, `reject` or `supersede`), plus `superseded_by` (required with `supersede`, `400` otherwise) and an optional reviewer `note`. A body with neither field keeps its earlier shape and digest. Section write replaces one existing prose-owned `##` section; scan is structural only (`narrate=False`) and scoped to one declared repository.

`plan` returns `{as_of, digest, plan}` without writing. The digest contract is:

- `digest = "sha256:" + sha256(canonical_json({"route", "params", "as_of", "plan"})).hexdigest()`. `route` is the path without `/plan` or `/apply`. `params` are the normalized body parameters without `as_of` and `digest`. `plan` is the dry-run projection.
- `canonical_json` = `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")`.

`as_of` pins one server clock read to UTC whole seconds, serialized as
`YYYY-MM-DDTHH:MM:SSZ`; core receives `today = as_of.date()` and proposal decide
receives `at = as_of`. Apply echoes the plan's `as_of` and `digest` alongside
its parameters. The admission window is inclusive:
`now - TTL <= as_of <= now`, where `TTL = timedelta(minutes=15)`.
Outside that window, the response includes a fresh plan at a new `as_of`.

Apply re-plans with the echoed `as_of`, under `_LOCK`, and never reads the clock
for core inputs. One process-wide `threading.Lock` covers re-plan → compare →
apply across all mutation routes; plan never takes it. Each adapter forwards
`before_apply` to core, which passes its actual candidate before domain writes.
Serve projects that candidate using the unchanged shared wire dry-run projection
and checks its digest again; a mismatch raises an internal refusal and returns
409 with that candidate as the fresh plan. This is not another dry run. Core
then applies the same candidate, subject to existing snapshot preconditions and
live-only gates (advance's commit gate may refuse with 422). Those checks do not
provide general filesystem atomicity, and the digest does not cover unprojected
bytes. A new mutation adapter must support this pre-write callback contract.
Tests pin the clock through the `mutations.now` seam. A volatile field in a projection makes
every apply 409 — fix the projection, never add a digest exclusion.

| Condition | HTTP status |
|---|---|
| Plan, including a refused, blocked, or conflicting plan | `200` |
| Successful apply | `200` |
| `stale-plan` (changed digest, expired or future `as_of`) | `409` |
| `refused` / `conflict` / `incomplete` on apply | `422` |
| `incomplete-apply` | `500` |
| Malformed body | `400` |
| Non-JSON `Content-Type` | `415` |

## Read routes added for gw-orca

All `GET`, read-only, token-guarded like the other reads.

| Route | Answers |
|---|---|
| `/v1/wiki/lint` | Mechanical lint findings with counts. |
| `/v1/query/brief` | The retrieval brief for a query, optionally pinned to a `page`; hybrid or lexical. |
| `/v1/code-graph/tree` | One repository's scanned code-graph tree. |
| `/v1/code-graph/search` | Search over a repository's scanned code-graph pages. |
| `/v1/code-graph/neighborhood` | The depends-on neighbourhood of one code-graph page, depth 1..3. |
| `/v1/work/affecting` | Active work items in a repository whose `affects` meet a path. |
| `/v1/wiki/proposal/checks` | Mechanical review checks for one proposal. |
| `/v1/wiki/proposal/preview` | The would-be page a promotion produces, with its diff. |

Advance always passes `infer_worktree=False`. Archive uses `wiki_slugs=()`;
omitted or `null` paths mean sweep, while an empty paths list is `400`.
Adding a mutation = one `MutationSpec` in `mutation_specs.py` +
`mutation_routes()` in `ROUTES` + a golden regen.
