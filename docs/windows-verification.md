# Native Windows verification protocol

This is the whole of graph-works' native-Windows signal. There is no CI on this
platform — ADR-0010 stands and the epic's D-003 bought a documented manual run
instead — so what is written here is what anyone later gets to cite.

**Run it whole.** A partial run over a partially-landed tree measures nothing
anyone can cite later; see *Gating* below.

**Record as you go.** Every checkpoint gets a verdict in the run's evidence
record *at the machine*, not from memory afterwards. The record lives in the
graph-works vault at
`okf/work/epic-native-windows-support/children/tech-debt-windows-verification-run/references/03-windows-run-<YYYY-MM-DD>.md`,
one dated file per run, additive — a second run on different hardware is a
comparison, never a replacement.

**A red is a result.** A failing checkpoint is a successful outcome of this
protocol. What is *not* acceptable is a red with no named owner. Every failing
row names the work item that will fix it before the run is called done.

## Verdict vocabulary

Exactly three values, used verbatim:

| Verdict | Meaning |
|---|---|
| `PASS` | The command ran and its output matched **Expected**. |
| `FAIL` | The command ran and its output did not match **Expected**. |
| `NOT RUN` | The checkpoint was not executed. Requires a reason **and** an owner in the same row. |

Nothing else. "Partial", "mostly", and "works but" are not verdicts — split the
checkpoint or record `FAIL` with the observation.

## The machine

A **physical x64 Windows 11 PC**. Not ARM64 (Orca's NSIS build is x64 and would
run under emulation; a wheel that resolves differently on ARM64 is
indistinguishable from a portability defect) and not a cloud VM (the Orca
auto-drive checkpoint would depend on an RDP desktop session).

The checkout and the scratch workspace live on **NTFS** unless a checkpoint says
otherwise. NTFS is a precondition of the Windows durability tier, not a detail —
see C1.

## Preamble — machine identity

Run every command below **first**, and paste the output verbatim into the
evidence record's *Preamble* section. This is recorded fact, not assumption:
under D-003 there is no CI to tell a later reader whether a divergence is a
regression or a different box, so this preamble is what carries that burden.

| Fact | Command | Why it is load-bearing |
|---|---|---|
| Windows edition + build | `winver` (screenshot or transcribe) and `systeminfo` | The baseline a later run is compared against |
| Architecture | `echo %PROCESSOR_ARCHITECTURE%` | The physical-x64 premise, stated rather than trusted |
| Checkout volume filesystem | `fsutil fsinfo volumeinfo C:` | NTFS is a precondition — see C1 |
| Long-path setting | `reg query "HKLM\SYSTEM\CurrentControlSet\Control\FileSystem" /v LongPathsEnabled` | C3 runs both ways and must know which state it started in |
| Symlink privilege | `whoami /priv` and Settings → System → For developers (Developer Mode) | C7's symlink refusal fires on its absence |
| Git for Windows | `git --version` and `git config --get core.autocrlf` | Group A's whole premise is the shipped default `true` |
| Toolchain | `python --version`, `python3 --version`, `uv --version`, `just --version`, `node --version`, `npm --version` | Group B is a toolchain result as much as a code result |
| Checkout revision | `git rev-parse HEAD` and `git status --porcelain` | F5 and F6 compare against what this exact tree declares |
| Orca build | installer filename, and the version shown in-app under Help → About | Group E's subject |

`python3 --version` is in the list deliberately: it is what `just subtree-base`
invokes (B9), and Windows Python installers do not all create that name.

## Gating

A run over a partially-landed tree measures nothing anyone can cite later.

| Group | Gate |
|---|---|
| A | none — runnable |
| B0–B6, B8, B9 | none — runnable |
| B7 (`just cov`) | partial: the suite runs today, but its Windows-anchor parametrization needs `tech-debt-anchor-abstraction` and `feature-windows-anchor-and-tier-adr` |
| C | **gated** on `tech-debt-anchor-abstraction` **and** `feature-windows-anchor-and-tier-adr`, both resolved |
| D | none — runnable |
| E | none — runnable |
| F1–F4, F6, F7 | none — runnable |
| F5 | runnable; the expected values shift as `tech-debt-portable-file-lock` and `feature-windows-anchor-and-tier-adr` land, which is why F5 regenerates them rather than reading them from here |

## Run order

Author order below is A→F. **Execution order is not.** Group A is cheap and
diagnostic and tells you early whether the checkout is even sound; Group E's
install is long-lead and should be started in the background as soon as A
passes; Group C needs the deepest setup (an exFAT volume, a registry toggle, a
held handle, a mid-commit kill) and goes last so a failure there does not strand
everything else.

| Order | Do | Why here |
|---|---|---|
| 1 | Preamble | Everything else is compared against it |
| 2 | Group A | Cheapest, most diagnostic; a bad checkout invalidates every later group |
| 3 | E1 (start the Orca installer, then continue) | Long-lead; runs in the background while B and D proceed |
| 4 | Group B | The static gates; `just cov` is the long pole |
| 5 | Group D | Cheap, and independent of everything else |
| 6 | F1–F4, F6, F7 | The pipeline itself |
| 7 | F5 | Wants a resolved workspace, so it follows F1 |
| 8 | E2–E4 | The installer from step 3 has finished by now |
| 9 | Group C | Deepest setup; last so a failure strands nothing |

## Scratch workspace

Several checkpoints share one scratch workspace. Create it once, at
`C:\gw-verify\ws`, and reuse it. Note its path in the evidence record — C1 and C3
deliberately create *additional* workspaces elsewhere.

**Every `gw` command that acts on the scratch bundle carries
`--workspace C:\gw-verify\ws` explicitly.** It is written out in every command
below rather than left to the ambient working directory, because a `gw` verb
that silently resolves a *different* workspace would turn a real Windows result
into an unattributable one. Where a command targets a workspace other than the
scratch one — C1's exFAT volume, C3's deep path, and the group gates, which read
this repository's own vault — the command says so.

## How each checkpoint is written

```
#### <ID> — <one-line claim>

**Gate:** <none | what must have landed>

**Command:** <exactly what to run, in exactly which shell>

**Expected:** <exactly what the output must be>

**If it fails:** <what the failure means and who owns it>
```

## Group A — checkout and line endings

Owner: `bug-enforce-lf-line-endings`. Its fix is a `.gitattributes` rule; this
group is where that rule is verified **on the platform the rule exists for**.

The premise is Git for Windows' shipped default `core.autocrlf=true`. Do not
change it before this group — running Group A with `core.autocrlf=false` tests
nothing.

#### A1 — the attributes resolve on the platform that needs them

**Gate:** none.

**Command:** in a fresh directory, with `core.autocrlf` at its Git-for-Windows
default (confirm with `git config --get core.autocrlf` → `true`):

```bash
git clone <repo-url> gw-a1
cd gw-a1
git ls-files --eol | grep -c 'w/crlf'
```

**Expected:** `0`. Record the full `git ls-files --eol | grep 'w/crlf'` output
(empty on a pass) in the evidence record.

Cross-check that the fixture corpus is *deliberately* exempt and did not get
normalized either:

```bash
git ls-files --eol packages/okf-io/tests/fixtures/edge/encoding/crlf.md
```

**Expected:** the line reports `w/crlf` for that one path — it is pinned `-text`
on purpose and is a byte-exact regression fixture. A `w/lf` here is a **FAIL**.

**If it fails:** the root or plugin `.gitattributes` does not cover a path it
must. Owner: reopens `bug-enforce-lf-line-endings`.

#### A2 — the four fork-added extensionless bash scripts execute

**Gate:** none.

**Command:** under **Git Bash**, from the clone's root:

```bash
bash plugins/graph-works/skills/subagent-driven-development/scripts/review-package; echo "rc=$?"
bash plugins/graph-works/skills/subagent-driven-development/scripts/sdd-workspace; echo "rc=$?"
bash plugins/graph-works/skills/subagent-driven-development/scripts/task-brief; echo "rc=$?"
printf '{}' | bash plugins/graph-works/hooks/skill-doc-routing; echo "rc=$?"
```

**Expected:** each of the first three prints its own usage line —
`usage: review-package PLAN_FILE BASE HEAD [OUTFILE]`,
`usage: sdd-workspace PLAN_FILE`,
`usage: task-brief PLAN_FILE TASK_NUMBER [OUTFILE]` — and a non-zero `rc`.
`skill-doc-routing` prints a single JSON object and `rc=0`.

**Nowhere in any of the four outputs may the string `\r` appear**, and in
particular none of them may print `$'\r': command not found` or
`/usr/bin/env: 'bash\r': No such file or directory`. That string is the actual
failure mode this checkpoint exists for.

A non-zero `rc` from the first three is a **PASS** — they are refusing bad
arguments, which is the cheapest way to prove the interpreter line parsed.

**If it fails:** the file checked out CRLF. Note that `skill-doc-routing` fails
*silently* in production — it is fail-open by design — which is why it is checked
by hand here. Owner: reopens `bug-enforce-lf-line-endings`.

#### A3 — `run-hook.cmd` parses under cmd.exe

**Gate:** none. **Nobody in this fork has ever executed this.**

**Command:** from **cmd.exe**, at the clone's root:

```bat
cmd /c plugins\graph-works\hooks\run-hook.cmd session-start
echo rc=%ERRORLEVEL%
```

**Expected:** a single JSON object on stdout beginning
`{` and containing `"hookSpecificOutput"` or `"additionalContext"`, followed by
`rc=0`. That output is `hooks/session-start`'s, which means the batch half
reached its `bash.exe` call and propagated `%ERRORLEVEL%`.

**FAIL** looks like any of: cmd.exe echoing raw script lines; `The syntax of the
command is incorrect.`; `) was unexpected at this time.`; empty output with a
non-zero `rc`.

**If it fails — and this is the standing rule:** the file is a cmd/bash polyglot
pinned `eol=lf`, and it contains four multi-line parenthesised `if … ( … )`
blocks, the construct where cmd.exe's line-at-a-time parser is least reliable
with bare LF. **The fix is to flatten the batch half's `if` blocks into
single-line forms. The fix is never to change the EOL.** The bash half opens
`: << 'CMDBLOCK'` and needs a line reading exactly `CMDBLOCK` to close the
heredoc; under CRLF that line is `CMDBLOCK\r`, the terminator never matches,
bash swallows the rest of the file as heredoc body, and the wrapper executes
nothing at all — silently, on Unix, which is where it is used every day.

Owner: a **new child** under `epic-native-windows-support`, scoped to
restructuring the batch half.
