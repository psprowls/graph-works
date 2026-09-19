# AGENTS.md

## What this package is

`plugin-fork-io` is a standalone Band 1 package for inspecting and managing
provenance-preserving forks of agent skills. It never imports another workspace
package, discovers a graph-works workspace, reads `workspace.yaml`, or calls an
LLM. Resolved paths and machine services enter through typed arguments.

`AgentAdapter.config` (`AgentConfigConventions`) is a pure data table recording where each agent keeps its settings layers and trust record. `graph-works-core`'s `agent_config` vertical is its consumer. plugin-fork-io itself never reads, parses or merges agent configuration; keep it that way. A convention change is a one-row edit here plus the pinned test in `test_adapters.py`.

Helpers beneath a skill are inventory data and must never be imported or executed.
The full lifecycle uses inactive previews and explicit apply, with original source
evidence separated from adapted maintained content.

## Commands

```bash
uv run --package plugin-fork-io pytest packages/plugin-fork-io/tests
uv run --package plugin-fork-io pytest packages/plugin-fork-io/tests \
  --cov=plugin_fork_io --cov-branch --cov-report=term-missing --cov-fail-under=95
uv run --package plugin-fork-io mypy --strict --platform linux packages/plugin-fork-io/src
uv run --package plugin-fork-io mypy --strict --platform win32 packages/plugin-fork-io/src
```

## Platform

Native Windows, macOS, and Linux are supported. Keep platform-specific machine
access in `machine.py`, behind the `Services` boundary, and run both strict mypy
platform arms. Git is an optional external program: local-source inspection
does not require it, and commands that eventually need it must report its
absence as a structured refusal or failure rather than making package import or
unrelated commands fail.

## Module layout

- `records.py` owns frozen public records and JSON value types.
- `validation.py` safely parses skill metadata and returns located findings.
- `machine.py` owns injected filesystem, clock, ID, and later native services.
- `inspection.py` inventories local skill directories without writing or
  loading helper code.
- `cli.py` owns command routing, the single result renderer, and exit mapping.

Task 2 adds `git.py` (isolated bare acquisition), `snapshots.py` (exact evidence,
validated archives, contained link materialization), and frozen snapshot records.
Only `git.py` invokes Git. Archives store link text in regular data members;
reading an archive never extracts links or writes source contents. Materializing
link evidence is an in-memory operation; selection approval belongs to Task 3.
Git tree permission mode is zero because Git records no directory permissions.

Task 3 adds inactive selective staging: `references.py` locates Markdown and host
metadata dependency evidence; `adaptations.py` performs exact byte-span replay;
`fork.py` prepares the original base and adapted candidate; `previews.py` validates
closed frozen records and immutable artifact digests. There is no apply operation
until Task 4. A prepared candidate with known missing requirements remains on disk
with `allowed=False` and its preview ID.

`Selection.resources` maps source paths to explicit destination paths. Owned paths
are skill roots plus separately mapped resources/licenses, never every sibling in
a discovery parent. Optional `DiscoveryContext` receives resolved roots and scan
limits; absent context produces an explicit incomplete-discovery warning. JSON
adaptation `expected` and `replacement` values are hex-encoded bytes, with byte
`start`/`end`; permission adaptations encode ASCII octal modes in those bytes.
Source links are approved through `source_links` and yield generated materialization
evidence. `materialize_link(..., evidence_paths=set())` can collect the original
reachable paths for a selective, replayable base archive.

Preview evidence stays immutable. `load_preview` verifies fork/application candidate
bytes and modes, but intentionally permits editing an **update** candidate directory;
its initial digest remains historical evidence for the later accept preparation.
`Preview` carries original source/base identity, selection, adaptations, dependency
evidence, explicit ownership, generations, observed inventories, expected absences,
ancestor identities and staged operations for the transaction slice to consume.

Final preview eligibility is recomputed by `fork.validate_candidate` against staged
bytes in mapped candidate coordinates, including metadata/folder-name equality and
required references. `Preview.dependencies` remains original source evidence;
`Preview.candidate_dependencies` contains final requirements and satisfaction. Use
the latter for current unresolved requirements. Original content findings carry a
`source.` prefix and warning severity, so an approved repair can remove the current
blocker without erasing the original evidence. Unlocated semantic references retain
zero-width spans; zero-width evidence never authorizes an automatic byte edit.

Task 4 adds `store.py` (closed typed Ledger decoding and owned inventories),
`transactions.py` (ordered ownership/variant locks and per-path journal promotion),
`recovery.py` (explicit incomplete-journal rollback previews), and `status.py`
(read-only live/tracking comparison). `Change` carries before/after bytes, kind and
mode; sealed journals durably retain that backup evidence before live replacement.
Completed and rolled-back journals consume the original preview and are retained.
Initial fork application is the only live forward operation in this slice; later
slices extend the engine with their own operation validation.

`machine.replace(..., absent=True)` promotes a sibling staged file through an
exclusive hard link, then removes the staging name. Lack of hardlink support is a
reported I/O failure, with no overwriting fallback. Ordinary `replace` is reserved
for tool-owned journal updates or known-state replacement. Narrow native exemptions
include `os.link`, `os.O_RDONLY`, and `os.close`, with boundary/race tests. Directory
fsync is attempted on POSIX; Python exposes no equivalent on native Windows.

Task 5 adds `adoption.py` (tracking-only adoption and explicit uncertain-origin
reconciliation) and `config.py` (pure injected settings resolution). Adoption
selection skill paths refer to existing content paths. Known adoption evidence
contains `digest` (whole original snapshot digest) and/or `revision` (resolved
Git commit), `declaration`, and explicit `mappings`. The original archive retains
selected original paths and inert license/manifest evidence. `observation.tar.gz`
is current-content evidence, never an inferred upstream base. Omitted adoption
intent preserves reconciliation intent; an explicit empty tuple clears it.

`History` is a sealed portable after-image at
`state/forks/<variant-id>/history/<event-id>.json`, promoted as the final change
by the existing journal engine. Its tracking paths are relative to
`state/forks/<variant-id>`. Status discovers these files independently of the
ledger's history list. Completed machine journals are optional on transfer;
retained journals are consistency-checked, and incomplete journals still require
explicit recovery. Do not discard incomplete journals during relocation.

`Ledger.content_root=None` represents different Windows volumes: resolve an
explicit local content binding before operating (CLI `--content-dir` or personal
JSON). A relative portable binding is preferred when representable. Unknown
bindings of other owners refuse overlap checks rather than guessing. Settings
resolution receives the absolute project/home and environment; JSON reads and cwd
resolution belong to CLI, and no settings operation creates a personal registry.

Task 6 adds `updates.py` (whole-variant candidate preparation) and `merge.py`
(three-way bytes/kinds/modes). `GitRunner.merge_text(base, local, incoming)`
returns `(bytes, conflicted)`; only `git.py` launches bounded `git merge-file`
over private files. Positive conflict counts remain conflicts; execution errors
are structured failures. No update application exists in this slice.

`plan_update` maps old source paths to incoming source paths only through explicit
`mappings`. All existing components and resource mappings participate; selection
may add resources, dependency declarations, and reviewed adaptations. Nested file
moves retain their local destination through `Preview.source_mappings`, which is
the complete incoming-source-to-local mapping set for later acceptance. Recorded
byte edits replay on original base and incoming copies; shifted spans relocate
only when their expected bytes have exactly one occurrence. Original archives
remain unadapted.

An update preview retains immutable `base.tar.gz` (byte-for-byte accepted archive),
`incoming.tar.gz` (new original evidence), `local.tar.gz` (observed live inputs),
and `comparisons.json` (both diffs including hashes/kinds/modes and mappings).
`Preview.source` and `base_digest` identify the **incoming** original evidence to
be accepted later; observed inventories bind the current ledger/base and all live
owned paths. `Preview.conflicts` contains typed IDs, paths, codes, each input's
hash/kind/mode, and an explicit final path/hash or deletion resolution requirement.
Candidate content remains editable; immutable metadata/input artifacts remain
validated by `load_preview`. Status/ledger/base/content do not advance during
preparation, even when a candidate is mechanically clean.

Task 7 adds `acceptance.py`: final candidate validation, explicit conflict/hash
resolutions, captured behavioral review, whole-variant acceptance, and latest
acceptance rollback. `Review` binds `candidate_digest` and preserves original
findings/resolutions; its optional `evidence_path` is a machine-local live-file
check only. Portable history stores the review with that path cleared. Review
findings remain evidence even after reconciliation; mechanical requirements and
file conflicts independently block acceptance.

An accept preview owns a fresh immutable candidate and also observes the editable
update candidate. Changes to either candidate, the supplied review file, maintained
owned files (including additions), tracking, or generation invalidate that preview.
The accepted base is the original unadapted incoming archive. `History` now carries
portable before-content and before-tracking images, operation and explicit reversal
identity. Normal rollback restores the original ledger fields, including `files`
(and thus preaccept dirty warnings), except for advancing generation/history and
retaining the current machine binding. Rollback never depends on completed machine
journals or original preview directories. Incomplete journals still take priority.

The existing transaction journal also handles existing directories, deletes, mode
changes and file/directory replacements. Replacement intermediates are recoverable;
unknown additions or changed bytes still refuse recovery. `owned_paths()` removes
nested mapping duplicates, while complete source mappings remain on the ledger for
future incoming-path replay. Shared links observe the same maintained root; no
installation destinations or independent variants are changed by acceptance.

Task 8 adds `adapters.py` and `installation.py`. Adapters receive resolved
project/home/configured discovery roots; missing/inaccessible roots and unenumerated
plugin/admin sources/stores remain explicit scan limits. `DiscoveryContext.state_roots`
optionally identifies additional known stores. No adapter launches an agent or helper.

`plan_install` stages shared direct bindings/relative links or independent copies.
Distinct copy roots receive independent IDs and portable History; identical roots
share one owner across requested agents. Copy content starts from the current local
snapshot while original source/base/adaptations remain upstream provenance. Adoption
uncertainty survives copies. Resources remain explicitly owned; manifest evidence
never becomes a discovery binding. Copies normalize nested adopted skill locations
to standalone names and revalidate references; no content references are rewritten.

Machine-local bindings live at `state/bindings/<variant-id>.json`, separate from
portable `forks/<id>` history. Explicit installation of independently moved known
content requires exact portable inventory/ownership evidence and absence of the old
owned content; only applying the preview persists the new binding. Source ledger
identity and history do not advance just because a binding was added. Portable
transfers carry the fork record/content; machine-local bindings need local resolution.

Install previews retain scoped inventories, shallow discovery/owner-store snapshots,
all known owner generations, and deterministic store/variant lock sets. The existing
journal handles every destination and copy record; recovery retains the same lock
set. Source-only journal participants do not falsely attest a new source ledger.
Relative links use the native mode observed by a disposable private staging probe;
link refusal suggests explicit copy mode, never fallback or elevation. Git
`core.symlinks=false` is reported without changing Git configuration.

The CLI exposes inspect/fork/adopt/install/status/update/accept/rollback with common
resolution options, one Result renderer and exit codes 0/2/3/4. Apply validates
command/preview identity before entering the transaction engine. Keep business
policy in library operations. Usage errors requested as JSON preserve the command
and envelope; blocked text output includes candidate location.

`test_cli_lifecycle.py` runs the installed console script in an external temporary
Git client and verifies CLI operations never stage or commit. Ordinary client
commits remain independent of acceptance. `test_companion_contracts.py` enforces
metadata and command examples for optional skills in `plugins/plugin-fork`; these
are not runtime dependencies or part of GW plugin registration/packaging.

## Lifecycle evidence and interrupted recovery

`Ledger.dependencies` persists typed explicit selection declarations;
`Ledger.unresolved` is derived unresolved evidence, never the source of supplied
context. Creation/adoption/reconciliation, accepted updates and copied portable
History retain the declarations; rollback restores the previous ledger choice.
Status observes recorded direct/shared bindings without claiming adjacent paths.
Installation may prepare explicit repairs despite binding findings, but still
validates owner content and refuses unrelated existing destinations.

`Journal.recovery_directories` records `(staging_path, final_path_identity)` before
restoration changes live content. Recovery recognizes only that inode at staging
or final location, and rechecks content before completing. Exclusive directory
rename lives only in `machine.py`: narrow stdlib ctypes calls to Linux renameat2
or macOS renamex_np, Windows os.rename. Unsupported operations refuse; never add
a POSIX rename/replace fallback that can overwrite a foreign empty directory.
