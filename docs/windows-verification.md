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
git ls-files --eol | grep 'w/crlf' | grep -v 'attr/-text'
```

**Expected:** empty output. Record it in the evidence record.

The `attr/-text` exclusion is load-bearing: this checkpoint asks whether any file
picked up CRLF *accidentally*, and the repo deliberately pins a handful of
byte-exact CRLF regression fixtures with `-text`. Counting raw `w/crlf` lines
would report those as failures — the corpus was 1 file when this page was
written and is 4 today, so a literal `-c ... == 0` expectation records a FAIL on
a correct tree. The cross-check below asserts one of those pins is still CRLF,
so the two halves must agree about the exemption.

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

**Command:** run **bare**, from **Git Bash**, at the clone's root:

```bash
just --version
just sync
echo rc=$?
```

**Expected: `PASS, unmodified`.** The recipes assume a POSIX shell — several chain
with `&&`, and `test-plugin` carries a `#!/usr/bin/env bash` shebang, `cd`s, and
hard-requires `bash`, `node` and `npm` — and from Git Bash they get one. `just`'s
default shell is `sh -cu` on every platform, Windows included; under Git Bash `sh`
resolves to `/usr/bin/sh` and no flags are needed.

**cmd.exe and PowerShell are recorded known-failing launchers.** Do not run this
checkpoint from either. Git for Windows puts only `C:\Program Files\Git\cmd` on
the Windows `PATH`, and that holds `git.exe`, not `sh.exe`, so `just` cannot
resolve a shell and every recipe fails identically before its body runs:

```
error: recipe `sync` could not be run because just could not find the shell `sh`: program not found
```

That is the expected behaviour of the wrong launcher, not a red. It is recorded
here so the next runner recognises it instead of re-deriving it. The root
`README.md`'s `## Checks` section states the same prerequisite for humans.

Record in the evidence one of:

- **PASS, unmodified** — `just sync` completes and `rc=0` with no changes to the
  checkout. This is the expected outcome from Git Bash.
- **PASS, with a documented prerequisite** — bare invocation from Git Bash did
  *not* suffice and some mechanism had to be supplied. Record it verbatim.
- **FAIL** — not invocable by any means you tried. Record what you tried.

A `FAIL` here is a **red with an owner**, not a blocked run: continue to
B1–B9 by invoking each recipe's underlying commands directly from Git Bash
(they are listed verbatim in the `justfile`) and say so in the record.

**If it fails:** Owner: a **new child** under `epic-native-windows-support`. Note
that the shell prerequisite itself is already settled — see the routing table
below — so a red here is a *new* finding, not a rediscovery of that one.

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

## Group C — the transaction tier

Owner: `feature-windows-anchor-and-tier-adr`. Its design lists exactly these as
the cases its POSIX parametrization must leave `xfail`ed — *implemented and
continuously regression-tested for logic, but unverified for platform behavior*.
This group removes that qualifier, or confirms it.

**Gate for the whole group:** `tech-debt-anchor-abstraction` **and**
`feature-windows-anchor-and-tier-adr` must both be `resolved`. Confirm before
starting — note that these two commands read the **graph-works vault**, not the
scratch workspace, so point `--workspace` at the vault checkout on this box:

```bat
gw work next work/epic-native-windows-support/children/tech-debt-anchor-abstraction --workspace <vault> --json
gw work next work/epic-native-windows-support/children/feature-windows-anchor-and-tier-adr --workspace <vault> --json
```

**Expected:** each reports a terminal blocker (`work_status: resolved` or
`phase: done`). If either does not, **stop**: mark C1–C7 `NOT RUN`, reason
"group gate unsatisfied", owner `feature-windows-anchor-and-tier-adr`.

**Everything here goes through public verbs.** No checkpoint imports an anchor
class or calls a preflight function directly. That is deliberate: the anchor's
internal surface is still moving, and a protocol pinned to it would be stale
before this page is next opened. `gw work file` and `gw work advance` are the
stable mutation entry points and each one is a full transaction.

**The mutation used throughout** — call it *the mutation* below — is:

```bat
gw work advance work/scratch-windows-verification --workspace C:\gw-verify\ws --json
```

against the scratch item created in F2. Where a checkpoint needs a *fresh*
workspace on a different volume it says so, and names that workspace explicitly.

#### C1 — `CreateHardLinkW` off NTFS

**Gate:** group gate.

`os.link` is how every file install happens in `_commit_write`. On exFAT, FAT32,
a network share, or across volumes it fails outright — there the Windows tier
does not degrade, **it does not work at all**. That is a tier boundary, and the
tier ADR states it; C1 is what makes the statement measured rather than asserted.

**Command:** create an exFAT volume, then bootstrap a workspace on it. From an
**elevated** cmd.exe:

```bat
mkdir C:\gw-verify
(echo create vdisk file="C:\gw-verify\exfat.vhd" maximum=512 type=expandable
 echo select vdisk file="C:\gw-verify\exfat.vhd"
 echo attach vdisk
 echo create partition primary
 echo format fs=exfat quick label=GWEXFAT
 echo assign letter=E) > C:\gw-verify\mkexfat.txt
diskpart /s C:\gw-verify\mkexfat.txt
fsutil fsinfo volumeinfo E:
```

Confirm `File System Name : exFAT`, then:

```bat
gw bootstrap --topic windows-verification-exfat --workspace E:\ws
gw work file --workspace E:\ws --title "exFAT probe" --kind TechDebt --summary "C1" --json
echo rc=%ERRORLEVEL%
```

**Expected:** a **loud, attributable refusal that names the filesystem
requirement** and a non-zero `rc`, with **no partial commit** — `E:\ws\okf` must
contain no half-written item. Verify:

```bat
dir /s /b E:\ws\okf
```

A refusal here is a **PASS**. A silent success is a FAIL (the tier's stated
boundary is wrong). A crash with an unattributable `OSError` is also a FAIL — the
boundary exists but is not surfaced.

Record **which** of the two commands refused. If `gw bootstrap` itself refuses,
that is a PASS and `gw work file` is moot — say so rather than leaving the
second command's row blank.

**Teardown:**

```bat
(echo select vdisk file="C:\gw-verify\exfat.vhd" & echo detach vdisk) > C:\gw-verify\rmexfat.txt
diskpart /s C:\gw-verify\rmexfat.txt
```

**If it fails:** Owner: `feature-windows-anchor-and-tier-adr` if the ADR's stated
contract is wrong; a **new child** if the contract is right and the
implementation does not honour it.

#### C2 — `st_ino` stability on NTFS across a rename

**Gate:** group gate.

Child 6 kept `_take_custody`'s `st_dev`/`st_ino` identity checks as written, on
the reading that Python populates both via `GetFileInformationByHandle`, and
explicitly declined to assert it. This is that assertion.

**Command:** from cmd.exe, on the **NTFS** checkout volume:

```bat
python -c "import os, pathlib, tempfile; d = pathlib.Path(tempfile.mkdtemp(dir=r'C:\gw-verify')); p = d / 'a'; p.write_bytes(b'x'); a = os.stat(p); q = d / 'b'; os.replace(p, q); b = os.stat(q); c = os.stat(q); print('before', a.st_dev, a.st_ino); print('after ', b.st_dev, b.st_ino); print('restat', c.st_dev, c.st_ino); print('STABLE' if (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino) == (c.st_dev, c.st_ino) else 'UNSTABLE')"
```

**Expected:** the last line reads `STABLE`, and the three `st_dev`/`st_ino` pairs
printed above it are identical. Record all four lines verbatim — the actual
numbers are the evidence, not the verdict word.

A printed `st_ino` of `0` is a **FAIL even if the line reads `STABLE`**: zero is
what CPython reports when it could not obtain a file index at all, and three
matching zeroes prove nothing about identity.

**If it fails** (`UNSTABLE`, `st_ino` of `0`, or any non-zero `st_ino` changing
across the rename): this is the outcome child 6's design already names. The epic
design's name-plus-type-plus-re-`lstat` fallback becomes necessary. Owner:
**reopens** `feature-windows-anchor-and-tier-adr`.

#### C3 — `MAX_PATH`

**Gate:** group gate.

Bundle paths nest deep — `work/<epic>/children/<child>/references/` — which makes
this the likeliest real-world failure in the group. **Run it both ways.**

**Command:** first record the starting state (the preamble already did; confirm
it has not changed):

```bat
reg query "HKLM\SYSTEM\CurrentControlSet\Control\FileSystem" /v LongPathsEnabled
```

Then, from an **elevated** cmd.exe, for each of `0` and `1`:

```bat
reg add "HKLM\SYSTEM\CurrentControlSet\Control\FileSystem" /v LongPathsEnabled /t REG_DWORD /d 0 /f
```

Reboot (the setting is read at process start, and rebooting removes any doubt
about which processes inherited which value), then, writing `<deep>` for
`C:\gw-verify\aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\bbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\cccccccccccccccccccccccccccccc\dddddddddddddddddddddddddddddd\eeeeeeeeeeeeeeeeeeeeeeeeeeeeee\ffffffffffffffffffffffffffffff\gggggggggggggggggggggggggggggg`:

```bat
mkdir <deep>
gw bootstrap --topic windows-verification-longpath --workspace <deep>\ws
gw work file --workspace <deep>\ws --title "Long path probe with a deliberately verbose title" --kind TechDebt --summary "C3" --name long-path-probe-with-a-deliberately-verbose-name --json
echo rc=%ERRORLEVEL%
```

Then repeat the whole block with `/d 1`, another reboot, and the same commands.

**File this item at the top level, with no `--parent-path`.** The depth this
checkpoint needs comes from the workspace path and the long `--name`, not from a
parent lane. Passing a `--parent-path` naming an item that does not exist in this
fresh workspace is refused `unknown-parent` before any path is ever touched,
which would record a `MAX_PATH` FAIL that has nothing to do with `MAX_PATH`.

**Expected:**

- With `LongPathsEnabled=1`: both commands succeed, `rc=0`, and the item lands on
  disk.
- With `LongPathsEnabled=0`: **an actionable refusal naming the path-length
  limit, raised before any effect lands** — not a mid-commit `FileNotFoundError`
  or `[WinError 3] The system cannot find the path specified` from inside a
  partially applied transaction. Confirm nothing was half-written with
  `dir /s /b <deep>\ws\okf`.

**Restore the starting value from the preamble when done**, and say in the record
which value the box started at.

**If it fails:** Owner: `feature-windows-anchor-and-tier-adr` if the ADR's stated
contract is wrong; a **new child** otherwise.

#### C4 — open handles and AV interference

**Gate:** group gate.

This has no POSIX analogue. Child 6 documents it as a known weak-tier failure
mode rather than solving it; C4 measures whether the rollback claim holds and
whether the error is attributable.

**Command:** in **PowerShell**, hold an exclusive handle on a bundle member:

```powershell
$m = "C:\gw-verify\ws\okf\work\scratch-windows-verification.md"
$h = [System.IO.File]::Open($m, 'Open', 'ReadWrite', 'None')
"handle held on $m"
```

Leave that window open. In a **second** window, record the item's phase, then run
*the mutation*:

```bat
findstr /b "phase:" C:\gw-verify\ws\okf\work\scratch-windows-verification.md
gw work advance work/scratch-windows-verification --workspace C:\gw-verify\ws --json
echo rc=%ERRORLEVEL%
```

Then release the handle in the first window:

```powershell
$h.Close()
```

**Expected:** the mutation either refuses before starting or **rolls back
cleanly**, with an error naming the member it could not touch. After it returns,
the bundle must be exactly as it was — verify with `git status` if the scratch
workspace is under git, or by re-reading the item's `phase:` line:

```bat
findstr /b "phase:" C:\gw-verify\ws\okf\work\scratch-windows-verification.md
```

**Expected:** identical to the reading taken before the mutation.

**FAIL:** a partially applied mutation, or an error that does not name the
locked member.

**If it fails:** Owner: `feature-windows-anchor-and-tier-adr` if the ADR's stated
rollback contract is wrong; a **new child** otherwise.

#### C5 — crash consistency without directory `fsync`

**Gate:** group gate. This is loss **L2** of the Windows tier.

**First, establish which process name to kill.** `gw` is an entry point, and how
it is installed decides its image name. A `uv tool install` / `pipx` layout ships
a native launcher that hosts Python in-process, so the running process is
`gw.exe` and **`taskkill /IM python.exe` never touches it** — the mutation runs
to completion untouched and the checkpoint silently measures nothing. A
`pip install --user` layout can genuinely be `python.exe`. Do not assume; look:

```powershell
Start-Process gw -ArgumentList 'work','next','<item>','--workspace','C:\gw-verify\ws'
Get-Process | Where-Object { $_.ProcessName -match 'gw|python' } | Select-Object Id,ProcessName
```

Record the image name you found; the rest of this checkpoint writes it as
`<gw-image>`.

**Command:** in one window start *the mutation*; in a second, kill it mid-commit:

```bat
taskkill /F /IM <gw-image>
```

Postcondition validation costs several seconds per mutation, so the window is
real, but hitting it may take repeats. Timing the kill by hand is unreliable —
interpreter start-up and imports are a large fraction of a short mutation, and a
kill that lands there creates no transaction directory at all, which looks
identical to not having run the checkpoint. Fire the kill off the transaction
directory appearing instead:

```powershell
$d = "C:\gw-verify\ws\.gw\cache\work-mutations"
$n = (Get-ChildItem $d).Count
while ((Get-ChildItem $d).Count -le $n) { Start-Sleep -Milliseconds 50 }
taskkill /F /IM <gw-image>
```

Start that watcher **first**, then run the mutation in the other window.
**Repeat until the kill lands mid-commit** — confirmed by the transaction
journal lacking a terminal record:

```bat
dir /b /o-d C:\gw-verify\ws\.gw\cache\work-mutations
findstr /c:"\"state\"" C:\gw-verify\ws\.gw\cache\work-mutations\<newest-id>\journal.jsonl
```

A journal whose last record is **not** a completion record is the state this
checkpoint wants. Then reopen the bundle:

```bat
gw work next work/scratch-windows-verification --workspace C:\gw-verify\ws --json
findstr /b "phase:" C:\gw-verify\ws\okf\work\scratch-windows-verification.md
```

**Expected:** the bundle is **either fully pre- or fully post-mutation** — never
half — and `journal.jsonl` records what happened. Paste the whole journal into
the evidence record; it is short and it is the evidence.

`taskkill /F /IM <gw-image>` kills *every* process of that name on the box — and
where `<gw-image>` is `python.exe`, that is every Python process. Close the
PowerShell window from C4 and any editor language server first, and do not run
this checkpoint while B7 is still going.

**FAIL:** a bundle in a mixed state, or a journal that does not explain it.

**If it fails:** Owner: `feature-windows-anchor-and-tier-adr` if L2 is stated
more strongly than the machine delivers; a **new child** otherwise.

#### C6a — `msvcrt.locking`'s ten-second bound and its message

**Gate:** group gate.

POSIX `flock(LOCK_EX)` blocks indefinitely. The Windows branch is
`msvcrt.locking(fd, LK_LOCK, 1)`, which retries once a second for ten tries and
then raises. D-012 declared that bound a **property of the Windows tier**, not
a bug.

A natural two-process CLI race does not hold the lock long enough to exercise
this bound — the winner's critical section is on the order of one second, so
the loser acquires the file lock on its first retry, well inside the ten-try
budget. Observing the bound requires a **deliberate holder** that keeps the
lock past the budget on purpose; that is what this checkpoint does. (C6b below
observes the natural race instead, and expects a sub-ten-second loser.)

**Command:** in one window, hold the bundle lock directly for 40 seconds and
print `HELD` once it is held:

```bat
python -c "from okf_ext.locking import locked; from pathlib import Path; import time; lock = Path(r'C:\gw-verify\ws\okf\.gw-bundle.lock'); print('about to hold'); exec('with locked(lock):\n print(\"HELD\"); time.sleep(40)')"
```

Once `HELD` prints, in a second window run and time:

```bat
gw work advance work/scratch-windows-verification --workspace C:\gw-verify\ws --json & echo rc=%ERRORLEVEL%
```

**Expected:** the second caller **fails after roughly ten seconds** with an
`OSError` whose message **names the lock file path**; `rc` non-zero.

**PASS:** `rc` non-zero, elapsed roughly ten seconds, message names the lock
path. **FAIL:** any other elapsed time, or a message that does not name the
path (the latter reopens `tech-debt-portable-file-lock`, unchanged).

**If it fails:** Owner: `tech-debt-portable-file-lock` if the message does not
name the path; a **new child** if the ten-second bound itself is unworkable
(D-012 names `LockFileEx` via `ctypes` as the upgrade path).

#### C6b — contention under the staleness guard

**Gate:** group gate.

`transactions.apply_mutation` takes the bundle lock **before** it validates
the plan is still current, so the staleness guard's `re-plan` refusal fires
*inside* the locked region, not upstream of it. Two concurrent `gw work
advance` calls on the same item are therefore serialized by the lock, and the
loser is refused for staleness immediately after acquiring it — well before
the ten-second retry budget in C6a is ever touched. **A sub-ten-second loser
here is the expected result and is not evidence the file lock was bypassed —
see C6a for the bound itself.**

**Command:** two cmd.exe windows, started as close together as possible, each
running:

```bat
gw work advance work/scratch-windows-verification --workspace C:\gw-verify\ws --json & echo rc=%ERRORLEVEL%
```

Record both elapsed times.

**Expected:** exactly one caller succeeds, `rc=0`; the other fails, `rc`
non-zero, with a message containing `changed since planning; re-plan`; the
bundle is left in the winner's post-state.

**PASS:** exactly one winner, the loser refused with the `re-plan` message,
bundle left consistent. **FAIL:** both succeed (a lost update), both fail, or
the bundle is left in a mixed state.

**If it fails:** Owner: a **new child** — a lost update or a mixed bundle here
is a transaction-engine defect, not a locking one.

#### C7 — the preflight refusals

**Gate:** group gate. **A refusal here is a PASS.** These are contract statements
of the Windows tier.

**Command:** stage each offending member in the scratch bundle, then run *the
mutation*. Reserved names cannot be created through the normal Win32 path, so
use the `\\?\` prefix.

**Do not use Python's `open()` for this.** It rejects every `\\?\` path with
`[Errno 22] Invalid argument` before the call reaches Windows -- including an
ordinary `normal.md`, so the failure is Python refusing the prefix, not Windows
refusing the reserved name, and mistaking one for the other records a PASS that
never happened. Measured 2026-09-03 on CPython 3.11 and 3.14. Use PowerShell's
`[System.IO.File]::Create`, which honours the prefix:

```powershell
$w = "C:\gw-verify\ws\okf\work"
foreach ($n in @("CON.md", "COM1.md", "trailing. .md")) {
  $fs = [System.IO.File]::Create("\\?\$w\$n")
  $b = [System.Text.Encoding]::ASCII.GetBytes("---`ntype: TechDebt`n---`n")
  $fs.Write($b, 0, $b.Length); $fs.Close()
}
```

Then, from cmd.exe:

```bat
mklink C:\gw-verify\ws\okf\work\linked.md C:\gw-verify\ws\okf\work\scratch-windows-verification.md
gw work advance work/scratch-windows-verification --workspace C:\gw-verify\ws --json
echo rc=%ERRORLEVEL%
```

**Expected:** a refusal **before any mutation starts**, naming the offending
members — `CON.md`, `COM1.md`, `trailing. .md`, `linked.md`. Confirm nothing
moved:

```bat
findstr /b "phase:" C:\gw-verify\ws\okf\work\scratch-windows-verification.md
```

**Expected:** unchanged.

Two sub-results to record separately:

- If Windows itself refuses to create a reserved-name file even through `\\?\`,
  record that as `PASS (unreachable — Windows refused creation)` with the exact
  error. The tier's boundary holds; it just holds one layer lower.
- If `mklink` fails with `You do not have sufficient privilege`, that is the
  precondition child 6's D-002 preflight refusal keys off. Re-run it from an
  elevated prompt or with Developer Mode on, and record which.

Remove every staged member afterwards — including through `\\?\` for the ones
Explorer and `del` will not touch:

```powershell
$w = "C:\gw-verify\ws\okf\work"
foreach ($n in @("CON.md", "COM1.md", "trailing. .md")) {
  $p = "\\?\$w\$n"
  if ([System.IO.File]::Exists($p)) { [System.IO.File]::Delete($p) }
}
Remove-Item "$w\linked.md" -Force -ErrorAction SilentlyContinue
```

(Same reason as the staging block: `os.remove` on a `\\?\` path fails the same
way `open()` does, so the Python teardown would silently leave every offender in
place and poison the next checkpoint that walks this bundle.)

**If it fails** (a mutation proceeds past any of them): Owner:
`feature-windows-anchor-and-tier-adr`.

## Group D — the byte-level newline and encoding proof

Owner: `bug-explicit-encoding-newline`. Its acceptance property is a **static
guard** (`just text-io`, B2). The **behavioral** proof — these files, on Windows,
byte-for-byte — is here, because it is not reproducible on POSIX: `os.linesep`
translation is compiled into CPython.

The defect being hunted is `CR CR LF` — one stray `CR` per line, added
**non-idempotently**, so a second pass adds another. Every D checkpoint therefore
runs its round-trip **twice**.

Compare bytes with `xxd` under Git Bash, or `certutil -f -encodehex` under
cmd.exe. **The evidence record carries the hex, not a description of it.**

> **POSIX baseline, measured while this protocol was authored.** On macOS at the
> commit that introduced this page, D1 part a prints `pass1 IDENTICAL`,
> `pass2 IDENTICAL`, `crcrlf False` against
> `packages/okf-io/tests/fixtures/edge/encoding/crlf.md` (9 CRLF line endings).
> A `CHANGED` on Windows is therefore unambiguously a platform result and not a
> pre-existing round-trip defect.

#### D1 — a CRLF okf document round-trips byte-identical

**Gate:** none.

**Command, part a — the narrow proof, through `okf_io` directly.** From Git
Bash at the checkout root:

```bash
cp packages/okf-io/tests/fixtures/edge/encoding/crlf.md /c/gw-verify/d1.md
uv run python -c "
from pathlib import Path
from okf_io import Document
p = Path(r'C:\gw-verify\d1.md')
before = p.read_bytes()
Document.load(p).save(p)
once = p.read_bytes()
Document.load(p).save(p)
twice = p.read_bytes()
print('pass1', 'IDENTICAL' if once == before else 'CHANGED')
print('pass2', 'IDENTICAL' if twice == once else 'CHANGED')
print('crcrlf', b'\r\r\n' in twice)
"
xxd /c/gw-verify/d1.md | head -5
```

**Expected:**

```
pass1 IDENTICAL
pass2 IDENTICAL
crcrlf False
```

and the `xxd` output showing `0d0a` (never `0d0d0a`) at every line end. Paste the
`xxd` output.

**Command, part b — the wide proof, through the pipeline.** Convert the scratch
item to CRLF, then mutate it through a real verb:

```bash
python - <<'PY'
from pathlib import Path
p = Path(r'C:\gw-verify\ws\okf\work\scratch-windows-verification.md')
b = p.read_bytes().replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
p.write_bytes(b)
print('crlf lines:', b.count(b'\r\n'), 'bare lf:', b.count(b'\n') - b.count(b'\r\n'))
PY
gw work advance work/scratch-windows-verification --workspace C:\\gw-verify\\ws --json >/dev/null
gw work advance work/scratch-windows-verification --workspace C:\\gw-verify\\ws --json >/dev/null
python -c "from pathlib import Path; b=Path(r'C:\gw-verify\ws\okf\work\scratch-windows-verification.md').read_bytes(); print('CRCRLF' if b'\r\r\n' in b else 'CLEAN'); print('crlf', b.count(b'\r\n'), 'bare lf', b.count(b'\n')-b.count(b'\r\n'))"
```

**Expected:** `CLEAN`, and the CRLF count unchanged from before the two
mutations (only the lines the mutation actually edited may differ, and they must
be `\r\n` too). `bare lf 0`.

Part b consumes two of the scratch item's phase transitions. Run it **after** F4
has recorded the phase sequence it needs, or file a second scratch item for it —
either is fine, but say in the record which you did.

**If it fails:** Owner: **reopens** `bug-explicit-encoding-newline` — its static
guard passed while the behaviour did not, which is exactly the case this run
exists to catch.

#### D2 — pinned-LF machine formats stay LF

**Gate:** none.

The `newline="\n"` sites — JSON and JSONL — must contain **no `\r` at all**,
whatever the platform.

**Command:** after at least one mutation has run in the scratch workspace:

```bash
python -c "
import pathlib
root = pathlib.Path(r'C:\gw-verify\ws\.gw')
bad = [str(p) for p in root.rglob('*') if p.is_file() and p.suffix in {'.json', '.jsonl'} and b'\r' in p.read_bytes()]
print('\n'.join(bad) if bad else 'NO CR IN ANY JSON/JSONL')
print('files checked:', sum(1 for p in root.rglob('*') if p.is_file() and p.suffix in {'.json', '.jsonl'}))
"
```

**Expected:** `NO CR IN ANY JSON/JSONL`, and `files checked:` greater than zero.
A zero count is a **FAIL of the checkpoint's own setup**, not a pass — nothing
was inspected. Run a mutation first.

Then confirm the transaction journal specifically:

```bash
xxd C:/gw-verify/ws/.gw/cache/work-mutations/<newest-id>/journal.jsonl | grep -c '0d0a'
```

**Expected:** `0`.

**If it fails:** Owner: **reopens** `bug-explicit-encoding-newline`.

#### D3 — a non-cp1252 character survives

**Gate:** none.

Windows' default text encoding is cp1252, so a character outside it is the
discriminator. An em dash (U+2014) and a CJK ideograph (U+6F22) are used because
the first is the one that appears throughout this repo's own prose and the second
cannot be represented in cp1252 at all.

**Command:**

```bash
gw work file --workspace C:\\gw-verify\\ws \
  --title "Encoding probe — 漢字 in a title" \
  --kind TechDebt --summary "D3 — 漢字" --name d3-encoding-probe --json
python -c "
from pathlib import Path
p = Path(r'C:\gw-verify\ws\okf\work\d3-encoding-probe.md')
b = p.read_bytes()
print('utf8 em dash', b'\xe2\x80\x94' in b)
print('utf8 CJK    ', b'\xe6\xbc\xa2\xe5\xad\x97' in b)
print('cp1252 dash ', '\x97' in b.decode('utf-8', 'replace'))
print('decodes     ', bool(b.decode('utf-8')))
"
```

**Expected:**

```
utf8 em dash True
utf8 CJK     True
cp1252 dash  False
decodes      True
```

**The `cp1252 dash` line tests the decoded text, not raw bytes, and must stay
that way.** A cp1252 em dash that survived into the file appears as the control
character `U+0097` once decoded, which is what this looks for. Scanning the raw
bytes for `0x97` instead — as this page did until 2026-09-02 — false-positives
on the CJK half of the very title chosen to defeat cp1252: `漢` is `U+6F22`,
UTF-8 `E5 AD 97`, whose third byte *is* `0x97`. That reports a mangled encoding
on a perfectly encoded file.

The shell must hand those characters through unmangled before the checkpoint
means anything. If the cmd.exe code page mangles them at the prompt (a `?` in the
title on disk is the tell), re-run from Git Bash, or `chcp 65001` first, and
record which shell produced the result. A mangled title from a cp1252 console is
a finding about the console, not about `gw` — do not record it as a `FAIL`
without saying so.

Then mutate it twice and re-check the same four lines are unchanged:

```bash
gw work advance work/d3-encoding-probe --workspace C:\\gw-verify\\ws --json >/dev/null
gw work advance work/d3-encoding-probe --workspace C:\\gw-verify\\ws --json >/dev/null
```

**Expected:** identical output. Paste
`certutil -f -encodehex C:\gw-verify\ws\okf\work\d3-encoding-probe.md CON 4` (or
`xxd`) for the title line into the record.

A `UnicodeDecodeError` or `UnicodeEncodeError` anywhere in this checkpoint is a
**FAIL**, including from the `gw` command itself.

**If it fails:** Owner: **reopens** `bug-explicit-encoding-newline`.

## Group E — Orca as the dispatch backend

Owner: `tech-debt-guard-workflow-local-verify-orca`.

**Availability is already settled and is not what is being checked here.** D-004
established, from Orca's own source tree rather than a local install, that a
native Windows build ships: a `build:win` script driving electron-builder with
NSIS packaging, three Windows-specific e2e suites, a WSL hook-relay reattach
benchmark, and an `electron.vite.config.ts` note about Windows NSIS deploying
`app.asar` before external resources. What remains is D-002's *verify, not
assume* — a run, not a question.

**Start E1 early** (run order step 3) and come back for E2–E4 (step 8).

#### E1 — install Orca's Windows (NSIS) build

**Gate:** none.

**Command:** download the Windows NSIS installer and run it. Record the exact
installer filename and the version shown in-app under Help → About.

**Expected:** the installer completes and Orca launches to its main window.

**If it fails:** record the installer error verbatim. Owner: fires
`tech-debt-port-workflow-local-process-control`.

#### E2 — `orca status` shows a reachable runtime

**Gate:** E1.

This is the same precondition `plugins/graph-works/skills/auto-drive/SKILL.md`
checks before every auto-drive run.

**Command:**

```bat
orca status
echo rc=%ERRORLEVEL%
```

**Expected:** `rc=0` and output naming a reachable runtime. Paste it verbatim.
`'orca' is not recognized` is a FAIL — the NSIS installer did not put the CLI on
`PATH`, which is itself the finding.

**If it fails:** Owner: fires `tech-debt-port-workflow-local-process-control`.

#### E3 — `orca orchestration run-current --json` answers

**Gate:** E2.

This is the orchestration surface the auto-drive skill depends on.

**Command:**

```bat
orca orchestration run-current --json
echo rc=%ERRORLEVEL%
```

**Expected:** well-formed JSON on stdout and `rc=0`. A JSON body reporting *no
current run* is a **PASS** — the surface answered. A non-zero exit, a stack
trace, or a hang is a FAIL.

**If it fails:** Owner: fires `tech-debt-port-workflow-local-process-control`.

#### E4 — one real auto-drive stage, end to end

**Gate:** E3, and the scratch item from F2.

**Command:** dispatch one auto-drive stage against the scratch item and watch it
settle:

```bat
gw work orchestrate work/scratch-windows-verification --workspace C:\gw-verify\ws --json
```

then drive that stage through Orca as a real dispatched worker, exactly as a
macOS run would, and observe the worker settle with a `worker_done` through the
same channel.

**Expected:** the worker starts, does its stage, and settles with `worker_done`.
Record: the dispatch id, the worker's terminal handle, the elapsed time, and
whether the settle arrived through the same channel a macOS run uses. A worker
that starts and never settles is a **FAIL**, and the elapsed time before you
called it is part of the evidence.

**A `worker_done` is the checkpoint; the stage's own product is not.** If the
worker settles with `--outcome failed` for a reason of its own — a stage that had
nothing to do, a plan it declined to write — that is still a **PASS** here: the
dispatch channel carried a worker from start to settle on Windows, which is the
only claim E4 makes. Record the outcome value either way.

**If it fails:** this is the trigger. Owner: fires
`tech-debt-port-workflow-local-process-control` — D-002's filed contingency,
currently unfired.

**Record the outcome either way.** A pass is what makes *"workflow-orca is the
native-Windows auto-drive backend"* a verified statement rather than an inherited
assumption, and that sentence is the whole reason this group exists.

## Group F — the end-to-end pipeline run

The item's headline, and the part no single child hands over: **does the tool
this epic exists to ship actually drive its own pipeline on Windows?**

F1 and F2 create the scratch workspace and item that C4–C7, D1b and E4 reuse, so
run them before those.

**First, make `gw` be the tree under test.** Every checkpoint in this group runs
the *installed* `gw`, not this checkout. A `uv tool install` / `pipx` layout
copies the sources at install time, so an install made before the commit you are
measuring silently measures that older build — and the whole group's verdicts
then describe a tree nobody is shipping, while the record names the commit you
are sitting on. Reinstall from this checkout, then prove the copy matches:

```bash
uv tool install --force --reinstall packages/graph-works-cli
SP="$APPDATA/uv/tools/graph-works-cli/Lib/site-packages"
for m in okf_io okf_ext work_tracker_okf graph_works_core graph_works_cli; do
  d="packages/$(echo $m | tr _ -)/src/$m"
  [ -d "$d" ] || continue
  echo "=== $m"; diff -rq "$d" "$SP/$m" 2>&1 | grep -v __pycache__
done
```

**Expected:** no output per module except `Only in ...: _hook_scripts`, which is
generated at build time. **Record that you ran this and what it reported** — it
is a precondition of every F verdict, not a convenience. On 2026-09-02 skipping
it produced a false `FAIL` on F7 that reversed to `PASS` at the very same commit
once the install was refreshed.

#### F1 — `gw bootstrap` a scratch workspace

**Gate:** none.

**Command:**

```bat
gw bootstrap --topic windows-verification --workspace C:\gw-verify\ws --json
echo rc=%ERRORLEVEL%
dir /b C:\gw-verify\ws
dir /b C:\gw-verify\ws\.gw
```

**Expected:** `rc=0`; `C:\gw-verify\ws` contains `.gw` and `okf`; `.gw` contains
`cache` (and the config members bootstrap creates). Paste both listings.

**If it fails:** Owner: a **new child** scoped to `gw bootstrap` on Windows.

#### F2 — file a scratch work item

**Gate:** F1.

**Command:**

```bat
gw work file --workspace C:\gw-verify\ws --title "Scratch windows verification" --kind TechDebt --summary "Scratch item for the Windows verification run" --name scratch-windows-verification --effort small --json
echo rc=%ERRORLEVEL%
dir /b /s C:\gw-verify\ws\okf\work
```

**Expected:** `rc=0`; the JSON reports the canonical path
`work/tech-debt-scratch-windows-verification` — filing prefixes the kind, so the
unprefixed form this page writes elsewhere is shorthand for that same item,
not a second one; on disk there is both
`C:\gw-verify\ws\okf\work\scratch-windows-verification.md` **and** the owned
directory `C:\gw-verify\ws\okf\work\scratch-windows-verification\references\`,
containing a `.gitkeep`. The owned directory existing is the checkpoint — the
invariant is that a work item's owned directory is created at filing time, and a
Windows path bug that skipped it would be invisible until the design stage. The
`.gitkeep` is what proves the directory was *created by the filing*, not left
behind by `dir` or an editor.

**If it fails:** Owner: a **new child** scoped to `gw work file` on Windows.

#### F3 — `gw next` returns a well-formed envelope

**Gate:** F2.

**Command:**

```bat
gw next work/scratch-windows-verification --workspace C:\gw-verify\ws --json
echo rc=%ERRORLEVEL%
```

**Expected:** `rc=0` and a JSON object carrying at least the keys
`requested_path`, `selected_path`, `work_status`, `kind`, `phase`, `action`,
`artifact`, `on_dispatch`, `on_complete`, `blockers`. Paste it whole.

**If it fails:** Owner: a **new child** scoped to `gw next` on Windows.

#### F4 — drive the scratch item design → plan → execute → finish

**Gate:** F3.

Every one of these is a bundle mutation through the transaction engine, so **F4
is also Group C's integration case.**

**Command:** after each `advance`, read the phase back off disk rather than
trusting the command's own report:

```bat
gw work advance work/scratch-windows-verification --workspace C:\gw-verify\ws --json
findstr /b "phase:" C:\gw-verify\ws\okf\work\scratch-windows-verification.md
```

Repeat until the item reaches `finish`, supplying whatever flags `gw next`'s
`on_dispatch.requires` names — `--owner <handle>` at the execute transition and
`--resolved-in <ref>` at the finish transition.

**Expected:** the on-disk `phase:` line changes at every step, in order, and
never disagrees with what the command reported. Record the phase sequence
observed and the flags each step required.

**If it fails:** Owner: a **new child** scoped to `gw work advance` on Windows;
if the failure is inside the transaction engine, reopens
`feature-windows-anchor-and-tier-adr` instead.

#### F5 — the differential platform report

**Gate:** none, but the expected values shift as `tech-debt-portable-file-lock`
and `feature-windows-anchor-and-tier-adr` land. **This checkpoint therefore
generates its own expected value rather than reading one from this page.**

The comparison is the checkpoint, not the output. A disagreement between what
the code *declares* a Windows box gets and what the box *actually reports* is
precisely what `agrees_with_declared` was built to surface — and `durability-tier`
is deliberately not probeable, naming this run as its evidence instead.

**Command:**

```bat
git rev-parse HEAD > C:\gw-verify\f5-head.txt
gw util platform --json > C:\gw-verify\f5-actual.json
gw util platform --probe --workspace C:\gw-verify\ws --json > C:\gw-verify\f5-actual-probed.json
uv run python -c "import json; from graph_works_core.util.platform import build_report; from graph_works_cli.util_cli.platform import _payload; open(r'C:\gw-verify\f5-declared.json','w',encoding='utf-8',newline='\n').write(json.dumps(_payload(build_report(platform_name='win32')), indent=2))"
```

Run all four from the **checkout root**, so `uv run` resolves this tree's
`graph_works_core` and not an unrelated install — the comparison is only
meaningful if both sides come from the commit in `f5-head.txt`.

Then diff, under Git Bash:

```bash
diff /c/gw-verify/f5-declared.json /c/gw-verify/f5-actual.json
```

**Expected:** the two agree on `platform` (`win32`) and on every capability's
`name`, `value` and `status`. `python` may differ only if the interpreter
running `gw` differs from the one running the simulation — if so, say which.

The declared side is written with `indent=2`; if `gw util platform --json` emits
a different indentation the raw `diff` will be noisy without a real disagreement.
Compare the parsed structures instead when that happens, and say in the record
that you did:

```bash
python -c "
import json
a = json.load(open('/c/gw-verify/f5-declared.json'))
b = json.load(open('/c/gw-verify/f5-actual.json'))
print('platform', a['platform'], b['platform'])
ka = {c['name']: (c['value'], c['status']) for c in a['capabilities']}
kb = {c['name']: (c['value'], c['status']) for c in b['capabilities']}
for n in sorted(set(ka) | set(kb)):
    print('AGREE ' if ka.get(n) == kb.get(n) else 'DIFFER', n, ka.get(n), kb.get(n))
"
```

Then read the probed report:

```bash
python -c "
import json
r = json.load(open('/c/gw-verify/f5-actual-probed.json'))
for p in r['probes']:
    print(p['capability'], p['status'], p['agrees_with_declared'], '|', p['detail'])
print('unavailable:', r['unavailable'])
"
```

**Expected — record all of it, and treat any disagreement as the finding:**

- `durability-tier` probes `unknown` with the *not probeable* detail and
  `agrees_with_declared: true`. That is by design: the tier's guarantee is bought
  by **this run**, not by a diagnostic verb asserting it.
- `file-lock` and `process-control`: whatever the box says, compared against the
  declaration. Any `agrees_with_declared: false` is the checkpoint's whole point
  — paste the `detail` verbatim.
- `dispatch-backend`: a probe disagreeing with `unresolved` is expected while no
  resolver exists; record the probed detail.

**Record with the verdict:** the `git rev-parse HEAD` from `f5-head.txt`, and
which of `tech-debt-portable-file-lock` / `feature-windows-anchor-and-tier-adr`
had landed in that tree. Without those two facts the comparison cannot be
reproduced later.

**If it fails** (declared and actual disagree on any capability's `value` or
`status`): Owner: `feature-gw-util-platform` if the provider's derivation is
wrong; the capability's own owning child if the machinery is.

#### F6 — `gw util describe-surface --json` matches the committed golden

**Gate:** none.

Following ADR-0019 Q3's precedent, where a byte-for-byte match was what made
"the pipeline ran" a measurement rather than an impression. The repo already
carries the reference — `packages/graph-works-cli/tests/fixtures/surface.golden.json` —
so there is nothing to hand-carry from a macOS box.

**Command:** from Git Bash at the checkout root:

```bash
gw util describe-surface --json > /c/gw-verify/f6-actual.json
diff packages/graph-works-cli/tests/fixtures/surface.golden.json /c/gw-verify/f6-actual.json && echo IDENTICAL
```

**Expected:** `IDENTICAL`, with no diff output. Ordering is a plain sort on
`path`, so the output is byte-stable across runs and across platforms — which is
exactly what makes a difference here meaningful. This equality was measured on
macOS at the commit that introduced this page, so a difference on Windows is a
platform result rather than a stale golden.

If the only difference is the `version` field, record it and treat it as a
**PASS with a note**: the golden was frozen at a different package version.
Anything else — a missing command, a reordered list, a changed help string — is a
**FAIL**.

**If it fails:** Owner: a **new child** scoped to the differing part of the
surface.

#### F7 — `gw work archive` and `gw work regen-index`

**Gate:** F4 (the scratch item must have reached a terminal state).

These are the mutation verbs the earlier steps did not exercise, and each is a
full transaction.

**Command:**

```bat
gw work archive work/scratch-windows-verification --workspace C:\gw-verify\ws --json
echo rc=%ERRORLEVEL%
dir /b /s C:\gw-verify\ws\okf\work\_archive
gw work regen-index --workspace C:\gw-verify\ws --json
echo rc=%ERRORLEVEL%
type C:\gw-verify\ws\okf\work\index.md
```

**Expected:** `rc=0` from both; the item now lives under
`okf\work\_archive\`; `work\index.md` no longer lists it and the second
`regen-index` reports no further change. Paste the index.

**If it fails:** Owner: a **new child** scoped to the failing verb.

## Writing up the run

**Write this at the machine, not from memory afterwards.** That is the entire
reason the shape is pinned here.

Create
`okf/work/epic-native-windows-support/children/tech-debt-windows-verification-run/references/03-windows-run-<YYYY-MM-DD>.md`
in the graph-works vault, with this frontmatter and these four sections:

````markdown
---
type: Concept
title: "Native Windows verification run — <YYYY-MM-DD>"
description: Recorded verdicts for every checkpoint of docs/windows-verification.md, executed on <machine>, at repo <short-sha>.
category: work
status: stable
updated: <YYYY-MM-DD>
tags: [graph-works, windows, portability, verification, evidence]
---

# Native Windows verification run — <YYYY-MM-DD>

Executed against [`docs/windows-verification.md`](...) at repo `<full-sha>`.
Protocol version: the revision of that file at `<full-sha>`.

## Preamble

<every row of the protocol's machine-identity table, output pasted verbatim>

Which siblings had landed in `<full-sha>`:

| Child | Resolved in this tree? |
|---|---|
| bug-enforce-lf-line-endings | |
| bug-explicit-encoding-newline | |
| tech-debt-portable-file-lock | |
| tech-debt-guard-workflow-local-verify-orca | |
| tech-debt-anchor-abstraction | |
| feature-windows-anchor-and-tier-adr | |
| feature-gw-util-platform | |
| tech-debt-publish-platform-matrix | |

## Verdicts

| Checkpoint | Verdict | Observed | Owner |
|---|---|---|---|
| A1 | | | |
| A2 | | | |
| A3 | | | |
| B0 | | | |
| B1 | | | |
| B2 | | | |
| B3 | | | |
| B4 | | | |
| B5 | | | |
| B6 | | | |
| B7 | | | |
| B8 | | | |
| B9 | | | |
| C1 | **PASS** | 2026-09-04, real exFAT VHD mounted as `E:` (`fsutil` confirms `File System Name : exFAT`). At `5ad4ff22`, `gw bootstrap --workspace E:\ws` exits **rc=1** and refuses: *"the windows-revalidated tier requires hard link support: every file is installed by `CreateHardLinkW` (`transactions._commit_write`), and E:\ws is on a filesystem that does not implement it -- exFAT, FAT32 and some network shares do not. Move the workspace to an NTFS or ReFS volume, or run under WSL for the posix-strong tier."* **Nothing is left on disk** -- `E:\ws` does not exist and the volume holds only `System Volume Information`. `gw work file` is moot: bootstrap refuses first. Probe verified directly: `hard_links_supported()` is `False` on `E:` and `True` on `C:`. Prior reading at `012d5aff` was FAIL (attribution): bootstrap **succeeded** on exFAT and only `gw work file` failed, with an unattributable `[WinError 1] Incorrect function` -- and that error came from `os.link` in `transactions._commit_write`, not from `okf_ext.logs.atomic_replace` as originally recorded. | [bug-atomic-replace-exfat-unattributable](/work/epic-native-windows-support/children/bug-atomic-replace-exfat-unattributable.md) |
| C2 | | | |
| C3 | | | |
| C4 | PASS (fixed) | Rollback half held; the reason half was FAIL (`unknown-path`, no member named) until [An unreadable bundle member refuses as unknown-path](/work/epic-native-windows-support/children/bug-locked-member-reported-unknown-path.md) landed `unreadable-member` in `work_tracker_okf.advance` and `graph_works_core.work.commands.run_next`. Pinned unattended by `packages/graph-works-cli/tests/test_work_cli_writes.py::test_advance_names_a_locked_member_instead_of_collapsing_into_unknown_path` and `packages/graph-works-core/tests/work/test_run_next.py::test_a_locked_member_is_named_instead_of_reported_unknown` (both Windows-only, real exclusive handle). | bug-locked-member-reported-unknown-path |
| C5 | | | |
| C6a | | | |
| C6b | | | |
| C7 | | | |
| D1 | | | |
| D2 | | | |
| D3 | | | |
| E1 | | | |
| E2 | | | |
| E3 | | | |
| E4 | | | |
| F1 | | | |
| F2 | | | |
| F3 | | | |
| F4 | | | |
| F5 | | | |
| F6 | | | |
| F7 | | | |

Rules for this table, and they are not negotiable:

- **Every row has a verdict.** No blanks. `NOT RUN` is a verdict; it needs a
  reason in **Observed** and an owner in **Owner**.
- **Owner is required on every non-`PASS` row.** A red or `NOT RUN` row with an
  empty Owner cell is an incomplete write-up, not a finished run.
- **Owner is empty on `PASS` rows.** Nothing to own.
- **Observed carries output, not adjectives.** Exit codes, hex, the first failing
  assertion. "Worked fine" is not an observation.

## Raw output

<the pasted transcripts the Verdicts table's Observed column points at —
xxd/certutil hex for Group D, the journal for C5, both platform reports for F5>

## Summary

<three to six sentences: what passed, what went red, what each red now owns.
This is what gets copied into the work item body and the epic ledger.>
````

Before committing the record, check it mechanically — 35 rows, every verdict in
the vocabulary, every non-`PASS` row owned:

```bash
python3 - <<'PY'
import re, pathlib, sys
p = pathlib.Path('okf/work/epic-native-windows-support/children/tech-debt-windows-verification-run/references/03-windows-run-<DATE>.md')
rows = [l for l in p.read_text(encoding='utf-8').splitlines() if re.match(r'^\| [A-F]\d+[a-z]? \|', l)]
print('rows:', len(rows))
bad = [r for r in rows if r.split('|')[2].strip() not in {'PASS', 'FAIL', 'NOT RUN'}]
missing_owner = [r for r in rows if r.split('|')[2].strip() in {'FAIL', 'NOT RUN'} and not r.split('|')[4].strip()]
print('bad verdict:', *bad, sep='\n')
print('missing owner:', *missing_owner, sep='\n')
sys.exit(1 if bad or missing_owner or len(rows) != 35 else 0)
PY
```

**Expected:** `rows: 35`, nothing under `bad verdict:` or `missing owner:`, exit 0.

## Red → owner routing

Fill the **Owner** column from this table. Where it says *reopens*, the child
already names this outcome in its own design — reopen it rather than filing a
duplicate.

| Red | Owner |
|---|---|
| A1, A2 | reopens `bug-enforce-lf-line-endings` |
| A3 (`run-hook.cmd` mis-parse) | **new child** — flatten the batch half's `if` blocks, keep LF |
| B0 (`just check` not natively invocable) | **settled** by `tech-debt-just-windows-shell-prerequisite` (D-022): the shell is a documented prerequisite — run bare from Git Bash; the `justfile` deliberately does not change. A red here now means something *other* than shell resolution, and is a **new child** |
| B1–B3, B9 | **new child** scoped to the failing guard script |
| B4–B6, B8 | **new child** scoped to the failing check |
| B7 | transaction/anchor failure reopens `feature-windows-anchor-and-tier-adr`; anything else is a **new child** |
| C1, C3–C5, C7 | `feature-windows-anchor-and-tier-adr` if the ADR's stated contract is wrong; a **new child** if the contract is right and the implementation is not |
| C2 (`st_ino` unstable on NTFS) | **reopens** `feature-windows-anchor-and-tier-adr` — its design already names this outcome |
| C6a | `tech-debt-portable-file-lock` if the message does not name the lock path; a **new child** if the 10s bound itself is unworkable (D-012 names `LockFileEx` via `ctypes` as the upgrade path) |
| C6b | **new child** — a lost update or a mixed bundle is a transaction-engine defect, not a locking one |
| D1–D3 | **reopens** `bug-explicit-encoding-newline` — its guard passed while the behaviour did not |
| E1–E4 | fires `tech-debt-port-workflow-local-process-control`, D-002's filed contingency |
| F1–F4, F7 | **new child** scoped to the failing verb |
| F5 | `feature-gw-util-platform` if the provider's derivation is wrong; the capability's owning child if the machinery is |
| F6 | **new child** scoped to the differing part of the surface |

## What this run does not settle

- **The reds.** This protocol measures; the owners above fix.
- **CI.** ADR-0010 stands. No `.github/`.
- **WSL.** Unaffected throughout — it is the supported channel and nothing in
  this epic changes it.
- **ADR-0027 case-insensitivity and ADR-0036 on-disk shape on NTFS.** Scoped out
  deliberately: settling them could force a member-identity change affecting
  every platform. Do not add them as checkpoints.
