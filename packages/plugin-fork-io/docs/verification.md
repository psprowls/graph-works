# Verification evidence

Recorded 2026-09-12. Evidence below distinguishes filesystem/runtime checks,
programmatic host discovery, interactive menus, and actual invocation. Intended
Windows/macOS/Linux support is not a claim that every native cell has passed.

## Package and distribution

The native macOS package suite exercises source snapshots, explicit selection and
dependencies, byte-span adaptation, previews, transaction failures/recovery,
adoption, three-way updates, final reviewed acceptance, rollback, discovery and
shared/copy bindings. The external CLI lifecycle uses ordinary caller Git commits
and verifies that each CLI call leaves HEAD and the index untouched.

Final Task 10 native macOS gate: **721 passed in 29.22s; branch coverage 95.02%**,
using `uv run --package plugin-fork-io pytest packages/plugin-fork-io/tests
--cov=plugin_fork_io --cov-branch --cov-report=term-missing --cov-fail-under=95`.
The named archived/relocation/platform suite passed 18 tests in 2.08s before the
last four persisted-authority cases were added. Ruff check/format passed (55
files), both strict mypy platform arms passed (23 source files each), and staged
`git diff --check` passed. No coverage exclusions or no-op production branches
were introduced. The controller subsequently completed root `just check` on
`b305b37a` (exit 0). That result predates the final lifecycle correction wave;
root/native Linux reruns for the corrected source are pending controller review.

`uv build --package plugin-fork-io` built both
`plugin_fork_io-0.1.0.tar.gz` and `plugin_fork_io-0.1.0-py3-none-any.whl`.
A disposable environment outside the workspace installed that wheel with `uv pip
install --python ENV/bin/python WHEEL`. Its `plugin-fork --help`, inspect,
fork/apply, status, update, accept/apply and final status passed against a wholly
external client/store. The installed package path was inside that temporary
environment, and no Graph Works or OKF distribution was present. The wheel
contains `plugin_fork_io/py.typed`. Direct runtime requirements were exactly
`markdown-it-py>=3.0`, `ruamel-yaml>=0.18`, `typer>=0.12`; the isolated resolver
selected Typer 0.27.2 (workspace checks used the lockfile’s 0.27.1).

| Native environment | Evidence | Remaining check |
| --- | --- | --- |
| macOS arm64 / Python 3.12.13 | Task 10: 721 tests / 95.02%; final lifecycle correction evidence below | Final controller root gate for corrected source |
| Linux aarch64 / python:3.12-slim | Commit 93ed7638: 721 passed in 22.04s; nonroot uid 65534, Git installed, frozen workspace sync | Historical Task 10 runtime; final lifecycle corrections change runtime behavior and require the controller native Linux rerun |
| Windows | **Unverified**: no native runner available | On native Windows with Git/Python 3.12+, sync frozen workspace and run full package suite, branch coverage, installed-wheel external lifecycle, and direct/shared/copy discovery; record native symlink permission/refusal behavior |

Both strict mypy platform arms check types; neither substitutes for native Windows
execution. Tests preserve CRLF/BOM/binary bytes and observed executable metadata;
mode bits are not Windows ACLs. Relocation tests delete original sources and old
previews/completed journals, move content and state independently, explicitly
rebind project/personal direct/shared roots, and roll back using portable History.
Existing transaction/platform suites additionally exercise case collisions,
`core.symlinks=false`, source/destination links, stale locks, mode drift and fault
recovery. A test using an injected platform flag establishes only that branch's
contract, not a live operating-system result.

## Final lifecycle correction wave

The final review reproduced three gaps despite the earlier passing gates: supplied
external dependency evidence was lost, status did not inspect installed discovery
bindings, and a second interruption could prevent kind-transition recovery.
The correction persists supplied declarations separately from unresolved evidence,
checks recorded discovery roots and selected SKILL.md files, and journals staged
restoration directory identities before exclusive native promotion. Ordinary
ancillary edits/deletions remain maintained-content changes and can be updated
and accepted. Unknown replacements remain preserved/refused.

Final corrected native macOS suite: **764 passed in 33.04s; branch coverage
95.08%**, using the same full-package coverage command above. Both strict type
arms passed (23 source files), Ruff check/format passed (58 files), and the
affected root normalization/text-I/O/line-ending/platform-declaration gates passed.
Wheel/sdist and the external installed lifecycle were refreshed for corrected
production source.

Public regressions cover supplied fork→install→default update→accept, independent
copy and portable rollback, adoption reconciliation, missing/retargeted/foreign
bindings, explicit missing-link repair, direct/shared ancillary deletion, repeated
recovery interruptions, exact original tracking/content bytes and foreign inode
refusal. Native macOS exclusive rename is tested on real directories; injected
Linux/Windows API branches establish only dispatch/refusal contracts. The native
Linux rerun and final root gate remain controller-owned after the correction
commit. No new native Windows or host invocation/menu result is claimed.

## Archived experiments

[Fixture attribution and exact hashes](../tests/fixtures/experiments/README.md)
pin archive SHA-256
`8da6eb76802116c9bf0904fe0bebc3b27b9fcbcc8218b9d16c8e90e1fc78131b`.
All 181 members were inventoried before bounded byte extraction; archived helpers
were never executed. The minimal regression retains 34 attributed files plus an
explicit 40-record adaptation recipe. Original 6.2.0 revision
`3dcbd5c4b48e02263fbf4a3c01e3fe4f81d584d9` and 6.3.0 revision
`b36e0829c6d0140e93cfef2ca599b1b07d4a7797` remain original, separate from local
customizations. The 6.3 SDD definition is independently checked against the nested
original archive member; source identities are not inferred from customized files.

The exercised selection is SDD, its support files, the actual reviewer resource,
and LICENSE. Other workflow skills are declared supplied test context. This is
not a whole-five-skill/plugin replay and does not claim Hermes-reference coverage.
The old Codex guidance's `When using subagent-driven-development, close reviewer
subagents when their review returns.` became a different V1/V2 tool discussion in
6.3. The archived prefix insertion therefore lacks a unique surviving anchor;
that unrelated reference is excluded instead of permitting global replacement.
A wider replay would need a newly reviewed adaptation or explicit resolution.

Each explicit SDD adaptation has unique expected bytes in both pinned originals
and preserves untouched upstream additions. Naming and real upstream reviewer
improvements are asserted. The reproduced text-clean SDD matches the archived
text-merge bytes exactly while containing both the local approval gate and the
upstream unqualified correction instruction. Its behavioral state remains
`not_requested`. The manually reviewed final SDD is compared byte-for-byte and
has SHA-256 `fd62fd73a88814227eb26bc1939662646f2e0b75700644097a46db426c1ec15c`.
Acceptance binds that final digest and retains explicitly supplied findings as
`completed_with_findings`.

Recorded approval scenarios cover plan/spec disagreement holding affected work,
routine naming proceeding, fix caps not bypassing approval, received approval
permitting correction, and reporting a missing spec. They are static archived
review evidence. Python text/hash assertions do not predict model compliance or
establish behavioral reliability.

The archive also exposed a scanner defect: `<R>/5` in a fix-round template was
misread as skill `5`. A focused RED/GREEN regression excludes slash tokens attached
to a closing placeholder while still detecting a genuine `/review` invocation.
Real `/worktree` references remain dependency evidence. Update Result now exposes
`text_conflicts` and `behavioral_review.state` from the actual conflict/preview
records, independently of the full `conflicts` collection.

## Live host checks

The controller ran the following disposable probes on macOS. Test profile/project
paths below are sanitized placeholders. All temporary source/profile/auth copies
were removed; real personal skills and configuration were untouched. The sole
skill instruction requested exactly `FORK_DISCOVERY_PROBE_20260912` and no tools.

| Host/version | Scope and mode | Discovery evidence | Literal invocation | Interactive menu |
| --- | --- | --- | --- | --- |
| codex-cli 0.154.0 | project direct | PASS: explicit skill invocation | PASS, exact literal, exit 0 | **Unverified** |
| codex-cli 0.154.0 | project shared relative link | PASS: explicit skill invocation | PASS, exact literal, exit 0 | **Unverified** |
| codex-cli 0.154.0 | personal direct/shared | **Unverified** | **Unverified** | **Unverified** |
| Claude Code 2.1.270 | project direct/shared | PASS: runtime init skills list, `fork-discovery-probe` | **Unverified**: isolated profile not logged in, exit 1 | **Unverified** |
| Claude Code 2.1.270 | personal direct/shared | PASS: runtime init skills list, `fork-discovery-probe` | **Unverified**: isolated profile not logged in, exit 1 | **Unverified** |
| Pi 0.84.4 | project direct/shared | PASS: RPC command list, `skill:fork-discovery-probe` | **Unverified**: isolated credential provider registry empty; prompt rejected before model invocation | **Unverified** |
| Pi 0.84.4 | personal direct/shared | PASS: RPC command list, `skill:fork-discovery-probe` | **Unverified**: same credential limitation | **Unverified** |
| All three hosts | Linux/Windows | **Unverified** | **Unverified** | **Unverified** |

Codex direct content was `PROJECT/.agents/skills/fork-discovery-probe`; shared
content was a sibling `project-shared-content` bound through that discovery root.
Claude used `PROJECT/.claude/skills/fork-discovery-probe`; isolated personal direct
content was `PROFILE/skills/fork-discovery-probe`, or an external sibling through a
relative link. Pi used `PROJECT/.pi/skills/fork-discovery-probe` and isolated
`PROFILE/skills/fork-discovery-probe`, with analogous shared links.

Recorded command shapes:

```text
codex exec --ignore-user-config --ephemeral --json --skip-git-repo-check --sandbox read-only --disable hooks --disable plugins --disable remote_plugin --disable skill_mcp_dependency_install -C PROJECT '$fork-discovery-probe'
claude --print /fork-discovery-probe --output-format stream-json --verbose --no-session-persistence --strict-mcp-config --settings '{"disableAllHooks":true,"enabledPlugins":{}}' --setting-sources user,project,local --tools Skill,Read --model haiku
pi --mode rpc --approve --no-session --no-extensions --no-prompt-templates --no-themes --no-context-files --no-tools --provider amazon-bedrock --model deepseek.v3.2
```

Runtime init/command lists are **not interactive menu inspection**. Remaining
checks are to inspect `/skills` in fresh Codex/Claude sessions and `/skill:`
completion in Pi, then invoke `$fork-discovery-probe`, `/fork-discovery-probe`, or
`/skill:fork-discovery-probe` and record the exact literal for every direct/shared
project/personal cell. Claude/Pi require a disposable authenticated profile.
Codex personal checks require a supported isolated personal discovery profile:
this session prohibited repurposing HOME/CODEX_HOME and exposed no alternative
isolated personal root, so that cell was left unverified. Repeat on native Linux
and Windows separately; an adapter unit test or an installed version is not host
compatibility evidence.

## Acceptance-criterion coverage map

| Requirement | Enforcing evidence |
| --- | --- |
| Standalone package, static version, CLI, dependency boundaries | `test_packaging.py`, `test_boundaries.py`, installed-wheel lifecycle above |
| Local/Git acquisition, inert helpers, original archives/licenses/modes/links | `test_git.py`, `test_snapshots.py`, attributed experiment manifest and `test_platform.py` |
| Selection, names, dependency evidence, external references | `test_selection.py`, `test_adaptations.py`, `test_operation_guards.py`, exact archived recipe |
| Portable/external state, direct local edits and uncertain adoption | `test_adoption.py`, `test_status.py`, `test_relocation.py` |
| Original three-way merge, both diffs, source mappings and kind/text conflicts | `test_updates.py`, `test_merge.py`, archived and file/directory collision regressions |
| Final-byte validation, optional review, explicit whole-variant acceptance | `test_acceptance.py`, `test_companion_contracts.py`, `test_preparation_races.py`, reviewed full-file hash |
| Single writable shared root and independent copies | `test_installation.py`, project/personal direct/shared relocation cases |
| Locks, drift, failure journals, recovery, monotonic rollback, portable attestation | `test_transactions.py`, `test_rollback.py`, `test_recovery_authority.py`, `test_persistence_guards.py` |
| All eight CLI envelopes, refusal codes, no CLI Git commit/staging | `test_cli_lifecycle.py`, `test_cli_errors.py` |
| Native filesystems and actual host discovery | Platform/host matrix above; unverified cells remain release-evidence gaps |
