# Manual smoke test: `gw` (graph-works-cli) against this repo

Goal: exercise the **new** `gw` (this repo's `packages/graph-works-cli`) end to end —
bootstrap, scan (all three modes), lint, query, work — against `agent-workspace` itself,
and eyeball the results. No eval harness, no automated assertions: this is a runbook,
not a test suite.

## 0. Why this matters

Your shell's `gw` almost certainly resolves to the **legacy** tool (a separate,
already-installed package). This repo's `gw` only exists inside
`.venv/bin/gw` until you `uv sync` / activate this workspace's venv. Every command below
uses `.venv/bin/gw` explicitly so you never accidentally exercise the old implementation.

```bash
cd /Users/pat/Personal/agent-workspace
which gw            # <- probably NOT what you want
.venv/bin/gw --help  # <- this repo's build
```

If `.venv/bin/gw` doesn't exist yet: `uv sync`.

**Model credentials required for narrated modes.** Narrated `scan`, `query`, and `wiki
lint`'s semantic pass all call a real chat model — Bedrock by default, or Vercel AI
Gateway per role once configured (see the next section). With no working credentials
for whichever backend a role resolves to, you'll see every entity land in
`entity_errors` ("prose refresher output did not parse" etc.) rather than a clean
failure, and `query` can hang for minutes retrying. `--no-narrate` scan and everything
else in this guide (bootstrap, lint's mechanical checks, stats, graph, work, config)
needs no model access at all — start there if you just want to confirm the mechanical
plumbing works.

## 1. Pick a scratch workspace location

Use `tmp/` — it's gitignored, so a generated wiki (dozens of entity pages, state/ artifacts,
log.md) never shows up in `git status`. `scratch/` (where this file lives) is **not**
gitignored, so don't point `--workspace` there.

```bash
export GW=/Users/pat/Personal/agent-workspace/.venv/bin/gw
export TEST_WS=/Users/pat/Personal/agent-workspace/tmp/gw-smoke-ws
rm -rf "$TEST_WS"   # start clean each run; safe, it's disposable
mkdir -p "$(dirname "$TEST_WS")"
```

`--repo-root` will point at the real `agent-workspace` checkout (`/Users/pat/Personal/agent-workspace`)
so the scan has real code/packages/entities to walk — the workspace itself lives outside
the repo it catalogs, same as `$GRAPH_WIKI_WORKSPACE` does for the legacy tool.

## 2. Bootstrap

Dry run first — this only prints a plan, writes nothing:

```bash
$GW bootstrap --topic "gw smoke test" \
  --workspace "$TEST_WS" \
  --repo-root /Users/pat/Personal/agent-workspace \
  --dry-run
```

Then for real:

```bash
$GW bootstrap --topic "gw smoke test" \
  --workspace "$TEST_WS" \
  --repo-root /Users/pat/Personal/agent-workspace
```

Inspect what got created:

```bash
find "$TEST_WS" -maxdepth 3 | sort
cat "$TEST_WS/workspace.yaml" 2>/dev/null
```

You should see a `.gw/` control-plane dir (`cache/`, `worktrees/`, `schema/`, `sections/`,
`tags.yaml` — all gitignore-shaped, safe to blow away) plus an `okf/` content root — that's
`layout.bundle_dir`, confirmable with `$GW config list --workspace "$TEST_WS" | grep bundle_dir`.
From here on every command takes `--workspace "$TEST_WS"`.

## Provider configuration: Bedrock vs Vercel AI Gateway

`gw` resolves every model call through a **role catalog** — ten logical roles
(`prose_refresher` for scan narration, `linter` for `wiki lint`'s semantic pass,
`librarian`/`code_reader`/`synthesizer` for `query`, `ingestor`/`proposal_reasoner`/
`extractor` for ingest, `drift_propagator` for cross-page drift) each resolving to a
`{model_id, backend, region, max_tokens, max_concurrency}` spec. Packaged defaults (in
`graph_works_core/models.toml`) put every role on Bedrock. Workspace overrides live at
`roles.<role>.<field>` in `workspace.yaml`, written with `gw config set` — same
mechanism as any other config key, no separate flag. Skip this section entirely if
you're only testing against Bedrock with no per-role overrides — that's the packaged
default and needs nothing further.

### Vercel AI Gateway

**The environment variable is `AI_GATEWAY_API_KEY`, not `VERCEL_AI_KEY`.**
`graph_works_core.agent_substrate.roles` reads exactly that name
(`GATEWAY_API_KEY_ENV = "AI_GATEWAY_API_KEY"`) and hands it to `models-io`, which never
reads the environment itself — so if `.envrc` only exports `VERCEL_AI_KEY`, every
gateway-backed role fails with a `GatewayAccessDenied` that names `AI_GATEWAY_API_KEY`
as the fix, which reads as "nothing is configured" even though a key exists under a
different name. Export it as:

```bash
# .envrc
export AI_GATEWAY_API_KEY="$VERCEL_AI_KEY"   # or just export AI_GATEWAY_API_KEY directly
```

Then, per role, set the backend and a **gateway-shaped** model id — Vercel's catalog uses
provider-prefixed slugs (`openai/gpt-4o-mini`, `anthropic/claude-sonnet-4.5`, etc.), not
Bedrock's dotted model ids (`moonshotai.kimi-k2.5`):

```bash
$GW config set roles.prose_refresher.backend vercel --workspace "$TEST_WS"
$GW config set roles.prose_refresher.model_id "openai/gpt-4o-mini" --workspace "$TEST_WS"
$GW config set roles.linter.backend vercel --workspace "$TEST_WS"
$GW config set roles.linter.model_id "openai/gpt-4o-mini" --workspace "$TEST_WS"

$GW config get roles.prose_refresher.backend --workspace "$TEST_WS"   # confirm it landed
```

Verified failure mode with a bad/placeholder key (useful for recognizing the error, and
for confirming the request actually reaches Vercel rather than being refused locally —
a `401` from the gateway itself means the wiring is right and only the key is wrong):

```
! page_quality: Vercel AI Gateway access denied.
  Gateway base URL: https://ai-gateway.vercel.sh/v1
  Set a valid bearer key in the AI_GATEWAY_API_KEY environment variable.
  Original error: Error code: 401 - {'error': {'message': 'Authentication failed. ...'}}
```

**`Original error: None`** (same message, different tail) is a different failure and worth
telling apart from the 401 above: it's `make_gateway_llm`'s *preflight* refusal — `if not
api_key: raise ...` — fired before any network call, because `AI_GATEWAY_API_KEY` was
empty/unset **in the process that ran `gw`**. This bit us for real: `.envrc` had been
edited to add the export, but the shell running `gw` hadn't picked it up (`direnv allow`
not yet run after the edit, or a stale/background shell). Check the exact shell invoking
`gw`, not an adjacent terminal:

```bash
echo "${AI_GATEWAY_API_KEY:+set}"   # prints "set" or nothing
```

If a role resolves and the key reaches the gateway but the account is capped, you'll see
a real HTTP error through, e.g. a free-tier cap on a specific model:

```
page_quality: Error code: 429 - {'error': {'message': 'Free tier requests on this model
are rate-limited. Upgrade to paid credits at https://vercel.com/...', 'type':
'rate_limit_exceeded', ...}}
```

That's a Vercel account/billing limit, not a `gw` or config problem — same fix path as
any other gateway HTTP error (different model, paid credits, or just confirm the wiring
works and fall back to Bedrock for the actual run).

Revert a role to its packaged (Bedrock) default:

```bash
$GW config unset roles.prose_refresher.backend --workspace "$TEST_WS"
$GW config unset roles.prose_refresher.model_id --workspace "$TEST_WS"
```

### Bedrock

Default backend, no `roles.*` overrides needed. Credentials resolve via `boto3`'s normal
chain (`AWS_PROFILE`, `~/.aws/credentials`, SSO session, etc.) — `models-io` never touches
the environment for Bedrock either; that's entirely boto3 below it. Confirmed working on
this machine's default profile (a real narrated scan below actually reached Bedrock and
narrated 20 entities with no explicit credential setup). Confirm the active identity
before a narrated run:

```bash
aws sts get-caller-identity   # confirm which account/profile gw will actually use
```

If Bedrock needs a non-default profile: `export AWS_PROFILE=<name>` before invoking `$GW`.

### Comparing the two on the same workspace

Since `roles.*` overrides are per-workspace, the cleanest A/B is two scans against the
same bootstrapped workspace, flipping one role's backend between runs and diffing
`entity_errors` / narrated-section quality:

```bash
# Bedrock (default) run
$GW scan --workspace "$TEST_WS" --json | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["narrated"], len(d["entity_errors"]))'

# Switch prose_refresher to Vercel, re-run
$GW config set roles.prose_refresher.backend vercel --workspace "$TEST_WS"
$GW config set roles.prose_refresher.model_id "openai/gpt-4o-mini" --workspace "$TEST_WS"
$GW scan --workspace "$TEST_WS" --json | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["narrated"], len(d["entity_errors"]))'
```

Two caveats on what this actually compares, both observed on a real run against this repo:

- Only the **6 admitted entity kinds** (repository, package, app, agent_plugin,
  dependency, test_suite — roughly 60 pages here) go through `prose_refresher` at all.
  The other ~1500+ pages this scan wrote are per-file/per-dir mirror pages, generated
  mechanically, never narrated by any role — don't expect `narrated` to approach the
  total page count.
- Stamping (`last_updated_commit`) is refill-gated: an entity only stops being "stale"
  once **every** declared section fills successfully. On the first run here, 20 of ~60
  eligible entities narrated but only 15 stamped (some had a section a model returned
  unparseable). At an unchanged HEAD, a second run **skips** the 15 stamped ones and
  **retries** everything still unstamped — which is exactly what you want for this
  comparison (does switching backend fix entities the first backend choked on), but
  means the two runs' `entity_errors` counts aren't directly comparable totals unless
  you bootstrap two separate fresh workspaces and scan each once.

## 3. Scan — mode 1: normal, narrated

The default path: builds the graph, writes entity pages, and runs the diff-gated prose
refresh **in-process** (this is the "with narration" mode — it talks to a model directly,
no fan-out).

```bash
$GW scan --workspace "$TEST_WS" --json | tee "$TEST_WS/../scan-normal.json"
```

Watch stderr with `-v` if you want to see it work:

```bash
$GW -v scan --workspace "$TEST_WS"
```

Inspect. Entity pages land under `okf/repositories/<repo-name>/<packages|apps|test-suites|agent-plugins>/`,
mirroring the repo's own directory shape (not a flat `entities/` folder with URI-derived
filenames — that's a docs-vs-code mismatch worth knowing about, see step 6):

```bash
find "$TEST_WS/okf/repositories" -name '*.md' | sort | head -20
cat "$TEST_WS/okf/repositories/agent-workspace/packages/graph-works-cli.md"
tail -20 "$TEST_WS/okf/log.md"
```

## 4. Scan — mode 2: no narration (mechanical only)

Rebuilds/refreshes entity pages, skips the prose-refresh pass entirely — fastest, purely
mechanical, good for "did the graph walk work" checks.

```bash
rm -rf "$TEST_WS" && $GW bootstrap --topic "gw smoke test" --workspace "$TEST_WS" \
  --repo-root /Users/pat/Personal/agent-workspace   # fresh workspace, see step 6 for why
$GW scan --no-narrate --workspace "$TEST_WS" --json
```

Diff entity pages against a `--no-narrate` re-run to confirm mechanical fields
(`uri`, `kind`, `depends_on`, etc.) are stable while `## Narrative` / `## Purpose` stay
empty placeholders.

## 5. Scan — mode 3: emit → subagent fan-out → apply

This is the interesting one — the three-phase pipeline the plugin drives for real
(see `plugins/graph-works/skills/graph-works/references/scan-workflow.md`).

**Phase 1 — emit:**

```bash
$GW scan --emit-worklist --workspace "$TEST_WS"
```

This prints a JSON payload with `worklist_path`, `briefs_dir`, `results_dir`, `short_head`
(emit/apply are always-JSON; `--json` is normal-mode-only and errors if you pass it here).
Capture the short head for later:

```bash
SHORT_HEAD=$($GW scan --emit-worklist --workspace "$TEST_WS" | python3 -c 'import json,sys; print(json.load(sys.stdin)["short_head"])')
echo "$SHORT_HEAD"
ls "$TEST_WS"/.gw/cache/scan/briefs/     # one <page-stem>.md per stale entity
cat "$TEST_WS"/.gw/cache/scan/briefs/*.md | head -60   # eyeball a brief — this is exactly what a subagent would read
```

**Phase 2 — fan-out.** Two ways to test this, pick based on what you're checking:

- **Fast/mechanical check** (does phase 3 correctly consume results?) — use the stub
  script below to synthesize a `results/<stem>.json` per brief without calling any model.
- **Real check** (does the actual subagent prompt/contract work end to end?) — dispatch
  real Claude subagents, one per brief, each told to follow its brief file and write
  *only* `results/<page-stem>.json` (per the brief's own contract — Read/Grep/Glob only,
  no other writes). From inside a Claude Code session this is the `graph-works:scanner`
  agent's job; for an ad hoc check you can hand one brief to a general-purpose subagent
  and see if it produces a valid result file.

Stub script (fast path) — writes a trivial-but-valid result for every emitted brief:

```bash
python3 scratch/gw-smoke/stub_results.py "$TEST_WS"
```

(See `scratch/gw-smoke/stub_results.py` — reads `worklist.json`'s `prose_tasks`, writes
one `{"uri": ..., "sections": {...}}` per task into `results/<page-stem>.json`.)

**Phase 3 — apply:**

```bash
$GW scan --apply \
  --results-dir "$TEST_WS"/.gw/cache/scan/results \
  --short-head "$SHORT_HEAD" \
  --workspace "$TEST_WS"
```

Check the sections actually landed:

```bash
grep -A2 "^## Purpose" "$TEST_WS"/okf/repositories/agent-workspace/packages/graph-works-cli.md
```

Try it again with a **stale** short head to confirm the guard rail fires (exit code 2,
per the README's exit-code table):

```bash
$GW scan --apply --results-dir "$TEST_WS"/.gw/cache/scan/results --short-head deadbeef --workspace "$TEST_WS"
echo "exit: $?"   # expect 2
```

## 6. Lint / stats / index

```bash
$GW wiki lint --workspace "$TEST_WS" --json | python3 -m json.tool
$GW wiki lint --workspace "$TEST_WS"          # human-readable report
$GW wiki stats --workspace "$TEST_WS" --top 10
$GW wiki index --workspace "$TEST_WS"
```

A real end-to-end run against this repo (1627 pages, 2089 edges) came back with only
`warn`-level findings (missing `description`, unfilled `## Purpose` placeholders on
pages the narrated pass errored on) — no `error`-level lint failures. That's a good
sign for the mechanical + lint path.

One thing worth flagging: `plugins/graph-works/skills/graph-works/references/scan-workflow.md`
(§"What gets written") describes entity pages landing in a flat `<workspace>/wiki/entities/`
folder with URI-derived filenames (`pkg_<name>.md`). What this build actually writes is
`<workspace>/okf/repositories/<repo>/<packages|apps|test-suites|agent-plugins>/<name>.md` —
a directory tree mirroring the repo, under `okf/` (`layout.bundle_dir`), not a flat `wiki/entities/`.
That doc opens by saying "treat a disagreement between this page and [the epic work item]
as this page being stale" — so this is likely just a stale doc, not a `gw` bug, but it's
exactly the kind of doc/code drift worth confirming against the epic item before assuming
either side is right.

## 7. Query

Model-backed — `synthesizer`, `librarian`, and `code_reader` roles, Bedrock by default or
Vercel if you overrode them above. Unlike everything else in this guide, this can take a
while and, without working credentials for whichever backend those roles resolve to, may
hang for many minutes rather than failing fast (observed: still running after 6+ minutes
on Bedrock with no valid session, `Ctrl-C` to bail). Confirm credentials first — `aws sts
get-caller-identity` for Bedrock roles, a real `AI_GATEWAY_API_KEY` for Vercel-backed ones:

```bash
$GW query --query "what does graph-works-cli depend on" --workspace "$TEST_WS"
```

## 8. Graph verbs (bypass the wiki, hit the code graph directly)

```bash
$GW graph build --workspace "$TEST_WS"
$GW graph describe graph-works-cli --kind package --workspace "$TEST_WS"   # --kind required: a bare name that's
                                                                            # both a package and an app is ambiguous
$GW graph find --kind package --workspace "$TEST_WS"
$GW graph export --out "$TEST_WS/../graph.graphml" --workspace "$TEST_WS"  # ~12k nodes / ~80k edges for this repo
```

## 9. Work items (independent of scan, but part of "everything the plugin does")

```bash
$GW work file --title "smoke test item" --kind Spike \
  --summary "exercise gw work file" --affects "packages/graph-works-cli" \
  --effort small --slug-words "gw smoke test item" \
  --workspace "$TEST_WS" --dry-run
$GW work status --workspace "$TEST_WS"
$GW work lint --workspace "$TEST_WS"
```

Filed items land at `okf/work/<slug>.md` (not `wiki/work/`, same `okf/` root as everything
else). Drop `--dry-run` to actually file one.

## 10. Config

```bash
$GW config list --workspace "$TEST_WS"
$GW config get workflow.commit_strategy --workspace "$TEST_WS" 2>&1 || true
```

## Cleanup

Everything lives under `tmp/gw-smoke-ws`, gitignored and disposable:

```bash
rm -rf /Users/pat/Personal/agent-workspace/tmp/gw-smoke-ws
```

## Automating the create → scan → lint sweep

`scratch/gw-smoke/run.sh` does steps 1–6 (fresh bootstrap, plain scan, lint, stats) in one
shot and prints the lint report at the end — useful as a quick "did I break `gw`" check
after touching `graph-works-core`/`graph-works-cli`. Run it with:

```bash
bash scratch/gw-smoke/run.sh
```

It intentionally stops short of the emit/apply subagent-fan-out path (step 5) — that one's
worth doing by hand at least once so you actually read a brief and a result, not just a
pass/fail.
