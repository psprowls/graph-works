# plugin-fork-io

Standalone provenance-preserving management for forked agent skills. The library
receives resolved paths and has no Graph Works, workspace, or LLM dependency.
Helpers in sources are inventory data: inspection never imports or executes them.

Install this package to obtain `plugin-fork`; in this repository use
`uv run --package plugin-fork-io plugin-fork --help`.

## Lifecycle

| Command | Prepare/read | Apply |
| --- | --- | --- |
| `inspect SOURCE` | Inventory skills, dependency evidence and snapshot digest | Read only |
| `fork SOURCE --selection FILE` | Select and adapt an inactive fork | `fork --apply ID` |
| `adopt --selection FILE` | Track existing content, optionally with original evidence | `adopt --apply ID` |
| `install VARIANT --agent codex --mode shared` | Bind one or more agents; choose shared or copy | `install --apply ID` |
| `status VARIANT` | Compare live content, tracking and history | Read only |
| `update VARIANT --source SOURCE` | Capture incoming and stage whole-variant three-way merge | Use accept |
| `accept VARIANT --candidate UPDATE_ID` | Validate final edits, resolutions and optional review | `accept --apply ID` |
| `rollback VARIANT` | Restore latest acceptance or recover incomplete transaction | `rollback --apply ID` |

All eight commands accept `--json`, `--content-dir`, `--state-dir`, `--project`,
and `--config`. Run each command's `--help` for its operation-specific options.
Source acquisition supports local directories and Git, with `--ref` selecting a
revision. Apply takes only its preview ID plus common options; mixed preparation
arguments refuse. An ID belongs to its command and exact observed inputs. Drift
requires preparing and approving a fresh preview.

Preparation writes private staging, never maintained content. A blocked preview
still reports its ID, candidate path and findings. JSON always uses the schema 1
Result envelope: `operation`, `variant_id`, `preview_id`, `allowed`, `applied`,
`affected_paths`, `findings`, and `data`, plus `schema_version`. Exit codes are 0
for success, 2 for usage/schema errors, 3 for policy/conflict/stale refusal, and 4
for unexpected execution failure. Text output exposes the same result data.

Defaults are `PROJECT/agent-skills` and `PROJECT/.plugin-fork`; the CLI defaults
project to cwd. Explicit options override matching personal configuration, then
configuration defaults, then these built-ins. Personal JSON lives under
`$XDG_CONFIG_HOME/plugin-fork/config.json` (or `~/.config`) on POSIX and
`%APPDATA%/plugin-fork/config.json` on Windows; `--config` overrides its path.
Configuration reads create no registry. An ambiguous store requires an explicit
`--state-dir`. Config-relative paths resolve beside that file.

## Selection and evidence

A minimal selection file is:

```json
{"skills":[{"source":"skills/review","name":"local-review"}],"resources":[],"dependencies":[],"adaptations":[],"source_links":[]}
```

Select resources/licenses explicitly. Inspect dependency evidence and its scan
limits; supply known missing requirements or approve a concrete adaptation before
installation. Adaptations record exact byte spans with hex `expected` and
`replacement` values; helpers remain inert. Renames preserve original source paths
and accepted archives. `--intent` reads a JSON array of strings documenting the
behavior to preserve.

Adoption without `--base` and supporting `--evidence` records uncertain origin.
It preserves existing content but cannot perform three-way updates. A replacement
source URL alone cannot reconstruct the accepted base. Reconciliation uses
`adopt --variant ID --base SOURCE --evidence FILE --selection FILE` with actual
original evidence: a digest and/or Git revision, a declaration, and explicit
source/local mappings. Adoption never silently overwrites an owned variant.

## Review and acceptance

Update retains original base, incoming, and local snapshots plus both comparisons:
base → incoming and maintained local → proposed candidate. Edit only the returned
candidate directory. Original evidence and ledgers belong to the CLI. Git staging
or commits do not accept an update; the CLI never stages or commits for you.

Behavioral review is optional. The result states `not_requested`, `completed_without_findings`,
or `completed_with_findings`. Keep supplied findings when reconciling them. A
review JSON object uses exactly these fields (replace the illustrative digest):

```json
{"candidate_digest":"0000000000000000000000000000000000000000000000000000000000000000","findings":[],"resolutions":[],"evidence_path":null}
```

Obtain the final snapshot digest with `inspect CANDIDATE --json` (`data.digest`).
Each finding has `code`, `severity` (`warn` or `error`), `path` (string or null),
`line` (integer or null), and `message`. The CLI checks the supplied live review
file when preparing/applying acceptance; portable history retains its contents
with the machine-local path cleared.

`--resolutions` reads an array, also usable as the review's `resolutions`. Each
entry binds a recorded conflict ID to the final path and SHA-256 of final file
bytes; deletion instead requires `hash: null` and `deleted: true`:

```json
[{"conflict_id":"CONFLICT_ID","path":"local-review/SKILL.md","hash":"0000000000000000000000000000000000000000000000000000000000000000","deleted":false}]
```

Removing merge markers alone does not resolve recorded conflicts. Every edit
requires fresh hashes/review evidence and a new acceptance preview. Present both
diffs, final digest, review status, findings, resolutions, affected owners and exact
acceptance ID for approval, then apply that ID. Rollback likewise needs preview
approval; it restores immediate preaccept content and tracking (including local
edits and dirty warnings), while advancing generation. Independent copies are
separate variants with separate review and rollback histories.

## Storage and portability

Track maintained content plus the entire portable `STATE/forks/VARIANT/` tree:
`ledger.json`, original `base.tar.gz`, and `history/` records. Preserve IDs and
relative layout when transferring; externally stored state must travel with the
content even if it lives in a separate repository or backup. Portable History
attests content/tracking and supports rollback without old previews or completed
machine journals. Typed `ledger.dependencies` preserves supplied dependency
declarations separately from derived `ledger.unresolved`; later install and
default update validation reuse that context while scanning current content.

Suggested ignores, relative to the chosen state root, are `previews/`, `locks/`,
`transactions/`, and `bindings/`. These are suggestions; the CLI writes no `.gitignore`.
Do not discard an incomplete journal: recover it before transfer. Machine bindings
contain local paths and must be resolved on the destination. For independently
moved known content/state, use an explicit install preview with `--content-dir`
and `--state-dir`; portable identity/ownership must match, and only apply persists
the new binding. Unknown ownership refuses instead of guessing.

Shared installation maintains one writable content root and binds agent discovery
roots directly or through relative links. Copy installation creates independent
IDs for distinct destinations, cloning original provenance and portable history.
Copies aimed at an identical root share one ID. Discovery is bounded to reported
project/home/configured roots; scan limits disclose unenumerated stores.
`status` checks each recorded discovery skill/resource root and selected `SKILL.md`, reports
missing/retargeted/replaced/inaccessible bindings, and includes unresolved
dependency evidence. Ordinary descendant edits and deletions remain `content.modified`
evidence and can enter normal update/accept validation. Missing shared links can
be repaired by an explicit install
preview; foreign content is preserved and refuses replacement.

## Platform

Native Windows, macOS, and Linux are supported. Git is optional until acquisition
or text merge needs it. Relative links require native link support/permissions;
refusal never silently copies or elevates privileges. Explicit copy mode is the
alternative. Git `core.symlinks=false` can prevent portable link checkout and is
reported without modifying Git settings. External management state avoids markers
in client trees, even when agent discovery links live there.

Interrupted rollback is restartable: before restoring a deleted directory it
records a private staging directory’s identity durably, then moves that same
directory without replacing any existing destination. Keep incomplete journals
and their recorded staging directories until recovery completes. Native exclusive
directory promotion uses Linux `renameat2(RENAME_NOREPLACE)`, macOS
`renamex_np(RENAME_EXCL)`, or Windows `os.rename`. Unsupported native calls or
filesystems refuse and retain recovery evidence; there is no overwriting fallback.
See the [Linux rename contract](https://man7.org/linux/man-pages/man2/rename.2.html),
[Apple rename flags](https://raw.githubusercontent.com/apple-oss-distributions/xnu/main/bsd/sys/stdio.h),
and [Python Windows rename contract](https://docs.python.org/3/library/os.html#os.rename).

The optional [companion workflows](../../plugins/plugin-fork/README.md) guide human
choices and approval. They are not Python dependencies and are not self-installed.

[Verification evidence](docs/verification.md) records native platform results,
archived byte-exact regressions, distribution checks, and live host discovery.
Unavailable Windows and host invocation/menu cells remain explicitly unverified.
