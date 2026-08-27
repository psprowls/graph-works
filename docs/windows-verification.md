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

## Group B — the static gates, on Windows

Every recipe in the `justfile` is exactly what a future CI job would call. This
group runs each one natively and records its exit code.

**Read this before recording a `FAIL` for an "unexpected" line.** `just` echoes
each recipe's command line to **stderr** before running it. That echo is not
output from the check — it is `just` narrating. Every **Expected** below is
therefore stated as *what appears on stdout*, plus the exit code. Capture the two
streams separately (`2>` to a file, or `2>/dev/null` under Git Bash) rather than
eyeballing a merged transcript.

#### B0 — is `just check` natively invocable at all?

**Gate:** none.

**Command:** from **cmd.exe**, at the clone's root:

```bat
just --version
just sync
echo rc=%ERRORLEVEL%
```

**Expected — and read this carefully: the answer is itself the finding.** The
recipes assume a POSIX shell: several chain with `&&`, and `test-plugin` carries
a `#!/usr/bin/env bash` shebang, `cd`s, and hard-requires `bash`, `node` and
`npm`. `just` on Windows defaults to `cmd.exe` unless told otherwise.

Record in the evidence one of:

- **PASS, unmodified** — `just sync` completes and `rc=0` with no changes to the
  checkout.
- **PASS, with a documented prerequisite** — it works once `just` is pointed at
  Git Bash. Record the exact mechanism used, verbatim: the `--shell` flags, or
  the `set windows-shell := [...]` line that would have to be added to the
  `justfile`. **Do not commit that line as part of this run** — recording what
  it would be is this checkpoint's deliverable; adding it is a separate child's.
- **FAIL** — not invocable by any means you tried. Record what you tried.

A `FAIL` here is a **red with an owner**, not a blocked run: continue to
B1–B9 by invoking each recipe's underlying commands directly from Git Bash
(they are listed verbatim in the `justfile`) and say so in the record.

**If it fails:** Owner: a **new child** under `epic-native-windows-support` —
either a `set windows-shell` line in the `justfile` or a documented prerequisite
in the root README.

#### B1–B9 — recipe by recipe

**Gate:** none, except B7 — see below.

**Command:** run each in turn, from whichever shell B0 established, recording
the exit code of each:

| # | Command | Expected on stdout | Exit |
|---|---|---|---|
| B1 | `just normalization` | **nothing** | 0 |
| B2 | `just text-io` | **nothing** | 0 |
| B3 | `just line-endings` | **nothing** | 0 |
| B4 | `just lint` | `All checks passed!` from `ruff check`, then `N files already formatted` from `ruff format --check` | 0 |
| B5 | `just types` | one `Success: no issues found in N source files` line per `mypy --strict` invocation | 0 |
| B6 | `just contracts` | one `KEPT` line per contract, then `Contracts: N kept, 0 broken.` | 0 |
| B7 | `just cov` | every per-package `pytest --cov` run reports `Required test coverage of N% reached` | 0 |
| B8 | `just test-plugin` | the `--- ` banner lines print in order and the run ends with npm's test summary | 0 |
| B9 | `just subtree-base` | the four-line `subtree-base -- plugins/graph-works` report, ending `ok -- the subtree merge base is reachable, recorded, and prefix-rooted` | 0 |

Record the exit code for each. On any failure, record **the first failing
assertion verbatim** — not a summary of it. For B7 that means the first failing
test's node id and its assertion output; for B4/B5/B6 the first reported
violation.

**B1, B2 and B3 print nothing on stdout when they pass.** Empty stdout plus exit
0 is the pass; any stdout at all is a failure and gets pasted. Their guards
report by *printing the offending paths*, so silence is the whole signal. The
`uv run python scripts/check_*.py .` line you will see is `just`'s stderr echo,
not the guard's output.

**B9 prints a report, not nothing.** It is the one recipe in this group whose
success is verbose: it names the recorded squash commit, the upstream commit,
the `plugins/SYNC.md` ledger row, and the prefix-rooted tree check, and closes
with the `ok -- ` line above. The commit hashes will differ from any transcribed
here; what must match is the shape and the final `ok -- ` line.

**B7 is the real payload.** It runs every package suite, including
`packages/graph-works-core/tests/test_transactions.py` — the asset D-003's
rationale named, and the only thing that turns "a weaker Windows tier" from a
claim into a tier. Its Windows-anchor parametrization is gated on
`tech-debt-anchor-abstraction` and `feature-windows-anchor-and-tier-adr`; if a
Windows-anchor case reports `xfail`, record which, because that is precisely the
qualifier this run exists to remove.

**B9 is not in the design's checkpoint list — it was added at plan time.**
`just check` depends on `subtree-base`, which invokes `python3 scripts/check_subtree_base.py`.
Windows Python installers do not all create a `python3` name. If B9 fails with
`'python3' is not recognized`, that is a **real finding about `just check` on
Windows**, not an environment complaint — record it as such.

Note what `just text-io` deliberately does **not** cover: child 2 scoped its
guard to shipped source, because test trees write to `tmp_path`. The instrument
for those is a suite run on Windows — which is B7.

**If any fails:** B1–B3, B9 → the guard scripts themselves; owner is a **new
child**. B4–B6 → owner is a **new child** scoped to the failing check. B7 →
route by what failed: a transaction/anchor failure reopens
`feature-windows-anchor-and-tier-adr`, anything else is a **new child** scoped to
the failing suite. B8 → a **new child**; `test-plugin` hard-requires `bash`,
`node` and `npm`, so record which was missing if that is the cause.
