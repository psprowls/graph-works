# AGENTS.md — gw-wiki

Band 3 application slice. Permitted dependencies: gw-kernel and declared foundation and OKF packages.
Forbidden edges: gw-pipeline, gw-orchestrate, gw-work and interfaces at runtime.
See the [root band table](../../AGENTS.md) and the root `pyproject.toml` contracts
"The application band is a tree of five slices beneath the interfaces" and
"Pipeline consumers never import the wiki slice".

## What this package is

`gw-wiki` is an application slice that receives resolved kernel primitives.
It never discovers a workspace root or constructs manifest stores. Runtime
imports point down to `gw_kernel`, the OKF lanes and declared foundation
packages. No callable is re-exported from the package front door.

## Module layout

Independent verticals: `scan`, `ingest`, `query`, `lint_drift`, `proposals`,
`wiki_page`, `code_read`, `repositories`, `agent_config`, `util`.
Shared support: `agent_substrate` and `prompts`. The role catalog is packaged
as `gw_wiki/models.toml` and loaded through `resources.files("gw_wiki")`.
Repository scan-generation stamps intentionally keep `version("gw-kernel")`.

`query/` — Query briefs and the retrieval front half of `run_query` read through `open_read_session`; `run_query`'s answer roles retain the loaded full bundle. On the persisted-index backend, `query/lexical_store.py` keeps BM25 postings in `search.db`: each query diffs the session's SHA-256 hashes against stored hashes, refreshes changed documents, then scores postings inside the verifying transaction. Bundle sessions and a busy lexical store use the in-memory `okf_ext.search` retrieval path. That path is also the equivalence oracle in `tests/query/test_query_session.py`; tokenizer or weight changes must bump `okf_ext.search.SCORING_VERSION`.

`wiki_page/` — `run_page_read` / `run_wiki_tree` open a read session around `page_read(session, layout, page_id)` / `wiki_tree(session, layout)`: on a warm index backend an unchanged exact page, or root `index.md` for the tree, uses verified session content with zero `Document.parse` calls (the tree still runs `outline` over that body), links come from the session's stored edges and tree titles from stored member rows; the sidecar can call the session-taking pair with its own session; `run_wiki_citations` (`citations.py`): the page's `path:N` inline-code citations with body-relative lines, resolved against `workspace.repo_files`; spans cached by the read index's page hash and inventories cached by `workspace/display_cache` (`display.db`). `section.py`: `run_section_write` replaces one existing prose-owned `##` section, plan by default, with `before_apply` and a workspace commit; `declarations.py` reads the workspace's `.gw/sections` declarations once per call. The citation grammar and resolution live in `workspace/citations.py`, below the verticals, so `wiki_page` and `proposals` share one copy.

`code_read/` — `run_code_excerpt`: a context window of one declared repository's tracked file; refusals are results. `tree.py`: `run_code_graph_tree` and `run_code_graph_search` read the scanned `code-graph/` pages. `neighborhood.py`: `run_code_graph_neighborhood` reads one page's depends-on neighbourhood from `code.db`. `workspace/repo_files.py` is the file set both share (tracked files minus ignore globs) and the confinement check; `repo_files` caches the file set by a stat key.

`repositories/` — `run_repo_add`, `run_repo_restore`, `run_repo_advance`. Resolves git through `provenance.gate_git`, hands it to `repositories_okf.git`, runs clone and fetch outside the bundle lock, writes pages under `held_bundle_lock` through `writes.WriteLog` (rollback-able), and commits the written paths with `commit_pending`. `restore` writes no page and makes no commit. The incoming clone is staged under `<cache_dir>/repo-incoming/`. Per-repository operation locks under `<cache_dir>/repository-operations/` serialize clone HEAD changes through apply and rollback, including restore. Operation ownership always precedes the bundle lock; network calls hold no bundle lock.

`repositories/adopt.py` is `gw repo adopt`: it renames an existing primary checkout declared in `repositories:` to the lane's clone location, links its working checkout, repairs every linked worktree to relative links and records page, manifest and log in one commit. The manifest edit is a text splice (`workspace/manifest_edit.py`), not `set_value`, so authored comments survive. Every refusal is preflight; every failure after the first git step rolls back to the old layout, and rollback never deletes the tree — it is the user's only clone.

`proposals/` — plan-by-default proposal file/decide (`run_proposal_file`, `run_proposal_decide`, which also supersedes with a replacement and takes a reviewer note); no clock. `review.py`: `run_proposal_checks` and `run_proposal_preview`, read-only mechanical review of one proposal.

`scan/repo_scan.py` — `run_repo_scan`: structural scan of one declared repository through plan/apply, scoped so no other repository's pages, catalogs or indexes change. `gw_work/affecting.py` — `run_work_affecting`: the active work items in one repository whose `affects` meet a path.

## Tests and commands

```bash
uv run --package gw-wiki --group dev pytest packages/gw-wiki/tests
uv run --package gw-wiki mypy --strict --platform linux packages/gw-wiki/src
uv run --package gw-wiki mypy --strict --platform win32 packages/gw-wiki/src
```

`asyncio_mode = "auto"` is declared locally because package pytest rootdir
resolves to this manifest. Pytest remains a development dependency.
The conftest explicitly re-exports shared fixtures from `gw_kernel.testing`.
Transaction and seed-copy helpers live in `gw_kernel.testing.transactions`
and `.seed_copy`; never import another package's test tree. Remaining bare
helper imports are wiki-owned, including repository lint fixtures and query
collection support. Cross-work lint tests use the `gw-work` dev dependency.

## Candidate validation

Proposal decide supports `before_apply` on the actual candidate before any
writes. Dry runs never call it; rejecting exceptions propagate. Preserve
byte-for-byte bundle state and absence of active-work cache on rejection.

## The agent substrate

Everything the verticals share, so they compose one substrate instead of each
re-deriving it.

```python
from gw_wiki.agent_substrate.roles import make_llm, role_binding
from gw_wiki.agent_substrate.agent_loop import run_tool_loop
from gw_wiki.agent_substrate.agent_tools import build_catalog
from gw_wiki import prompts

llm = make_llm("librarian", layout=layout)  # layout is optional: None is packaged-only
binding = role_binding("librarian", layout=layout)  # what SubagentPool wants — a factory per item
catalog = build_catalog(bundle, lanes=("concepts", "sources", "entities"))
system = "\n\n".join([prompts.IRON_RULES, prompts.render_architecture_overview(layout)])
```

| Piece | What it is |
|---|---|
| `agent_substrate.roles` | packaged `models.toml` → `workspace.yaml` `roles.<name>.<field>` → explicit argument, merged by `subagents_io.resolve_role_spec` |
| `role_spec` / `role_binding` / `make_llm` | the three constructors over that merge: a provider-free `RoleSpec`, a `RoleBinding` (spec plus a factory `SubagentPool` calls once per item), or a constructed `BaseChatModel` |
| `agent_substrate.agent_loop` | the capped tool-call loop: two distinct cap outcomes, and a tool-name coercion that is written back into the replayed history |
| `agent_substrate.agent_tools` | catalog / bounded page read / chunking, all over an `okf_io.Bundle` |
| `prompts` | seven shared fragments plus two renderers |

**`layout` is an argument, never a discovery call.** `None` means
packaged-only; passing a layout is how a caller opts into workspace overrides.

The role override lives in `workspace.yaml` as five wildcard catalog keys, so
`gw config set roles.librarian.model_id …` works through config-io's validated,
rollback-safe path. `Manifest` itself is untouched: roles are not layout.

Bedrock is the only backend the packaged catalog names, so `models-io[bedrock]`
is a hard dependency and the gateway arrives through the `vercel` extra.
`AI_GATEWAY_API_KEY` is read here — models-io never reads the environment —
and its name is handed back as `credential_hint` so the refusal names it.


## Ingesting a document

One document in, one Source page plus its reference copy out, and a set of
lane-targeted proposals arguing for what the source justifies.

```python
import asyncio
from datetime import date, datetime, timezone
from pathlib import Path

from gw_kernel import resolve
from gw_wiki.ingest.commands import run_ingest_source

layout = resolve(workspace=Path(".works"))
result = asyncio.run(
    run_ingest_source(
        Path("~/Downloads/some-spec.md").expanduser(),
        layout=layout,
        repo=Path("."),
        today=date.today(),
        at=datetime.now(timezone.utc),
        source_kind="spec",  # a hint; the model classifies, the bundle's enum validates
        origin="https://example.com/some-spec",
        backend_override="bedrock",
    )
)
print(result.page, result.copy, result.proposal_status)
```

`backend_override` is required here because `ingestor` now defaults to the
brief-only `claude_code` backend — `run_ingest_source` itself always runs the
full write pipeline regardless of backend, so a caller must explicitly opt
into a real model backend (`"bedrock"` or `"vercel"`) via `backend_override`
or an equivalent workspace/CLI override.

`today` and `at` are required and have no default — nothing in this package
reads the clock.

Three seams, all defaulted so the call above is the whole API:

| Seam | Default | Supplied by |
|---|---|---|
| `match_entity` | this package's own matcher over the configured code graph | `entity_matcher(reader, schema_set)` |
| `state_gate` | `None` — no drift stamp | `state_gate_adapter(config)` |
| `graph_tools` | `()` — a narrower reasoner, not a failure | the CLI, from the graph-tool builder |

**An uninitialized graph is not an error.** The ingest completes without an
`entity_uri` and without the forward link. Whether that should be fatal is a
CLI policy question, not a library one.

**The page and its reference copy are one plan.** They land together or not at
all, and re-recording material whose page already exists is refused rather than
merged — provided the model's title still resolves to that same page. Since the
ingestor's `title` decides the path, a re-ingest whose model retitles the
material lands a second page instead. Keying the refusal on `origin` instead is
an open question on graph-works-cli.

**The suggest phase is best-effort.** A reasoner or extractor failure yields
zero proposals, records the reason in `IngestResult.proposal_status` (not on the page),
and never fails the ingest.

**Skill ingest is not here.** The two-pass planner→synthesizer flow that writes
guidance pages is the guidance layer, one above this one, and is deferred with
it.


## Custom-type provenance

An LLM-extracted page has no ground truth to reconcile against: it goes stale
silently, and no shipped check catches it. This is the model that fixes that.
The capability ships in `okf-ext`, later and not here — what this package owes
is the contract, because it owns the drift machinery and the contract has to
exist before the extractor does.

**The contract.** An extracted page carries one `sources[]` entry per code
resource it was read from, each with the commit it was read at:

```yaml
type: Endpoint
sources:
  - id: src-handler
    resource: /src/api/checkout.py
    at_commit: a1b2c3d
  - id: src-router
    resource: /src/api/router.py
    at_commit: a1b2c3d
```

**Why this and nothing else.** Declaring the resource set *is* the ground
truth, and it is the same ground truth the graph-derived lanes already have.
`code_wiki_okf.sync.snapshot_bundle` can answer "did `/src/api/checkout.py`
change since `a1b2c3d`?", so `sync.stale-page` fires with no new checker, no
new frontmatter key and no new lane. The untrusted region becomes
drift-checkable using machinery that already ships.

Two alternatives were rejected: making the provenance block a JSONSchema
requirement in `schema/`, which enforces mechanically but pushes a graph-works
opinion about extraction into okf-ext's deliberately generic schema capability;
and a quarantine lane with a TTL, which is cheap to specify but accepts a
permanently untrusted region — the outcome this model exists to avoid.

**The re-extraction trigger** is that same finding, routed to the extractor
rather than to a proposal. A `sync.stale-page` on a graph-derived page means "a
sync run would update it"; on an extracted page it means "a re-extraction run
would update it". The routing key is the page's `type` being a declared custom
type rather than a graph-derived one.

**The failure mode, named.** A page that under-declares its sources is silently
under-checked: it will not go stale when code it actually describes changes,
only when code it *admitted to reading* changes. This is strictly better than
the status quo, where nothing is checked at all, but it is not airtight and
should not be sold as if it were. The mitigation, if it proves necessary, is
extraction-side rather than lint-side — have the extractor record every file it
opened, not every file it cited.

**One surface already writes it.**
`gw_wiki.lint_drift.propagate_drift` writes this
staleness-checkable shape (`id`, `resource`, `at_commit`, plus `title` and
`rationale` for its own presentation needs) into every proposal it files. It
is the only one: `gw_wiki.ingest.suggest_pages` and
`gw_wiki.ingest.commands`, through `doc_wiki_okf.sources`, each build
`sources[]` independently, with different key sets and no `at_commit`. Whether
they should carry one is an open question, not a settled shape.

Out of scope here: the extractor, the schema for custom types, the lane the
extracted pages live in, and any enforcement that a page declares provenance at
all.

### Read-only agent configuration reports

`agent_config.read_project()` and `show()` model the selected on-disk Claude,
Codex and Pi layers, keeping raw parsed values and per-key origins. They do not
reproduce running-session flags, headless/SDK acceptance, remote policy sources,
model alias resolution or nested Codex configuration traversal.

Claude local settings use repository/main-checkout identity, retaining an existing
legacy starting-directory file below the root file. The caller supplies `user_id`
for POSIX ownership checks; when placement cannot be established, the report
includes an `agent-config.local-location` finding and excludes that uncertain
local layer from effective values. Windows and home-root exceptions keep the
starting-directory file. Repository lookup failures yield unknown trust with the
existing explicit assumed-trust finding. State-only custom git probes still work,
but need a `RepositoryContext` (or a `context()` method) to establish identity.

Object and array nesting is limited to 64 levels before conversion and merging.
Deeper or unreadable content becomes a layer error, leaving other layers readable
and report values safe for the normal wire/JSON encoding path. `modelSettings`
effort resolves against each file's top-level `effortLevel`; derived effort keys
retain empty `defined_in` when no file authored that exact path.
