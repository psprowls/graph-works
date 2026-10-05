# Workspace task runner — okf-io, okf-ext, code-graph-io, code-wiki-okf,
# work-tracker-okf, config-io, plugin-fork-io, models-io, subagents-io, doc-wiki-okf, repositories-okf,
# graph-works-core, workflow-local, workflow-orca, graph-works-wire, graph-works-cli, graph-works-serve.
# Each recipe is exactly the command a future CI job will call.
#
# okf-io and okf-ext share the root `testpaths` and run under a plain
# `uv run`. code-graph-io resolves its own dependency closure, so it runs
# under `--package` and names its test directory.

default: check

# A tracked filename whose on-disk bytes disagree with git's about Unicode
# normalization form (run early: cheapest check, and the most diagnostic --
# see 2026-08-07-bug-moves-fixture-unicode-normalization).
normalization:
    uv run python scripts/check_filename_normalization.py .

# Host toolchain check: git works, uv is present, uv resolves Python >= 3.12.
# First dependency of every gate so a broken host fails in one second with one
# `TOOLCHAIN PREFLIGHT FAILED (host, not code)` line instead of a red suite.
preflight:
    bash scripts/preflight.sh

# Implicit text-IO defaults in shipped source -- a missing `encoding=` on any
# text read/write, or a missing `newline=` on any text write.
#
# Not a `lint` addition, deliberately: `lint` is ruff, ruff's PLW1514 is
# preview-only and covers `encoding=` alone, and `scripts` and `plugins` are in
# ruff's `exclude`. The half ruff cannot express -- a missing `newline=` -- is
# the half that corrupts data: a CRLF okf document written back through a
# translating writer on Windows becomes CR CR LF, one stray CR per line, and
# non-idempotently. See work/epic-native-windows-support/children/bug-explicit-encoding-newline.
#
# Scope is shipped source (`packages/*/src`, `scripts`) plus the three test
# trees whose assertions are byte-exact (`okf-io`, `okf-ext`, `scripts/tests`);
# `fixtures/` is excluded everywhere. The other nine test trees still rely on a
# suite run (see work/epic-native-windows-support/children/bug-test-fixtures-assume-lf-on-write).
text-io:
    uv run python scripts/check_text_io_explicit.py .

# A tracked file that would check out CRLF under Git for Windows' default
# core.autocrlf=true (see work/epic-native-windows-support/children/bug-enforce-lf-line-endings).
line-endings:
    uv run python scripts/check_line_endings.py .

# A package that imports a POSIX-only module, or reaches a POSIX-only process
# primitive, with no `## Platform` section declaring it -- ADR-0021 rule 3a
# turned into a check (see work/epic-native-windows-support/children/tech-debt-publish-platform-matrix).
platform-declared:
    uv run python scripts/check_platform_declared.py .

# Provision the workspace environment. Idempotent; a no-op once in sync.
#
# A bare `uv run` installs the ROOT's dependencies only — not those declared by
# workspace *members*. `typer` belongs to `code-wiki-okf` and `work-tracker-okf`,
# so without this `mypy --strict` cannot resolve their `@app.command()`
# decorators and reports all 13 commands as untyped. The code is fine; the
# checker is half-blind.
#
# The `--package` recipes below self-provision, so `test` and `cov` install
# `typer` as a side effect — which is why an established `.venv` hides this and a
# clean checkout does not. It stayed invisible until a merge drill ran the gate
# in a fresh worktree. CI is always a fresh worktree.
sync:
    uv sync --all-packages

# Lint and format check — repo-wide, so every package is covered by both.
lint:
    uv run ruff check .
    uv run ruff format --check .

# Static types, strict — every package, ONCE PER PLATFORM ARM.
#
# Two passes, not one. A single-platform gate structurally cannot see the arm it
# is not compiled for: the win32 pass is blind to every POSIX branch and the
# linux pass is blind to every Windows branch. Running only the host's arm is
# what let `os.O_DIRECTORY` sit unguarded on the POSIX side and `os.O_BINARY`
# sit unguarded on the Windows side, each invisible to whoever was looking.
# Roughly double the runtime, and strictly more coverage on every machine.
#
# `--platform` overrides `[tool.mypy] platform` in pyproject.toml.
#
# Depends on `sync`: this is the recipe that fails without it.
#
# The 32 unit x arm jobs run concurrently (`GW_TYPES_JOBS`, default: host CPU
# count), each with its own `--cache-dir .mypy_cache/<arm>/<unit>`: a shared
# cache made the two arms invalidate each other every run, and concurrent mypy
# processes must never share one. Each job stays its own `uv run --package`
# so an undeclared import still fails. The unit table is
# `scripts/package_units.py`; output is buffered per job and prefixed
# `[<arm>/<unit>]`.
types: sync
    #!/usr/bin/env bash
    set -euo pipefail
    jobs="${GW_TYPES_JOBS:-$(uv run python -c 'import os; print(os.cpu_count() or 1)')}"
    list=$(mktemp)
    trap 'rm -f "$list"' EXIT
    uv run python scripts/package_units.py mypy-jobs > "$list"
    xargs -P "$jobs" -L 1 bash -c 'label=$1; shift; if out=$("$@" 2>&1); then st=0; else st=$?; fi; printf "%s\n" "$out" | sed "s|^|[$label] |"; exit "$st"' _ < "$list"

# Internal package boundaries (okf-ext README, "Boundaries"). Opt-in until CI
# exists: nothing enforces this but the person who runs it.
contracts:
    uv run lint-imports

# Full test suite, every package -- workers-per-suite (`-n auto`) AND a bounded
# number of packages running at once, because neither axis alone uses a dev
# box's cores well: one package's suite rarely saturates them, and 15 serial
# `uv run` invocations pay 15x process/import startup back to back.
#
# Independent checkouts of the same source with disjoint coverage targets, so
# there is no state shared between the fanned-out recipes below -- but the two
# axes multiply, and BOTH must stay bounded together. A first version ran all
# 15 packages at once via `[parallel]`, each spawning `-n auto` (= host CPU
# count) workers -- 120 pytest-xdist worker processes on an 8-core box. That
# didn't just run slowly: xdist's own worker bookkeeping raced under the
# overload and threw `KeyError: <WorkerController gwN>` mid-session, and a
# separate suite's server-startup test timed out waiting on a CPU-starved host
# -- six failures with no code at fault. `GW_TEST_JOBS` caps how many packages
# run at once (default 3); `PYTEST_XDIST_AUTO_NUM_WORKERS` is set from it so
# `-n auto` in every worker pool divides the host's CPUs across whatever's
# running concurrently, instead of each pool claiming all of them. Raise
# `GW_TEST_JOBS` on a bigger box; lower it (or set it to 1) if a run wedges.
test:
    #!/usr/bin/env bash
    set -euo pipefail
    jobs="${GW_TEST_JOBS:-3}"
    ncpu=$(uv run python -c "import os; print(os.cpu_count() or 1)")
    workers=$(( ncpu / jobs )); [ "$workers" -ge 1 ] || workers=1
    export PYTEST_XDIST_AUTO_NUM_WORKERS="$workers"
    pkgs=(_test-okf _test-code-graph-io _test-code-wiki-okf _test-work-tracker-okf _test-config-io _test-plugin-fork-io _test-models-io _test-subagents-io _test-doc-wiki-okf _test-repositories-okf _test-graph-works-core _test-workflow-local _test-workflow-orca _test-graph-works-wire _test-graph-works-cli _test-graph-works-serve)
    printf '%s\n' "${pkgs[@]}" | xargs -P "$jobs" -I{} just {}

_test-okf:
    uv run pytest -n auto
_test-code-graph-io:
    uv run --package code-graph-io pytest packages/code-graph-io/tests -n auto
_test-code-wiki-okf:
    uv run --package code-wiki-okf pytest packages/code-wiki-okf/tests -n auto
_test-work-tracker-okf:
    uv run --package work-tracker-okf pytest packages/work-tracker-okf/tests -n auto
_test-config-io:
    uv run --package config-io pytest packages/config-io/tests -n auto
_test-plugin-fork-io:
    uv run --package plugin-fork-io pytest packages/plugin-fork-io/tests -n auto
_test-models-io:
    uv run --package models-io --extra bedrock --extra vercel pytest packages/models-io/tests -n auto
_test-subagents-io:
    uv run --package subagents-io pytest packages/subagents-io/tests -n auto
_test-doc-wiki-okf:
    uv run --package doc-wiki-okf pytest packages/doc-wiki-okf/tests -n auto
_test-repositories-okf:
    uv run --package repositories-okf pytest packages/repositories-okf/tests -n auto
_test-graph-works-core:
    uv run --package graph-works-core pytest packages/graph-works-core/tests -n auto
_test-workflow-local:
    uv run --package workflow-local pytest packages/workflow-local/tests -n auto
_test-workflow-orca:
    uv run --package workflow-orca pytest packages/workflow-orca/tests -n auto
_test-graph-works-wire:
    uv run --package graph-works-wire pytest packages/graph-works-wire/tests -n auto
_test-graph-works-cli:
    uv run --package graph-works-cli pytest packages/graph-works-cli/tests -n auto
_test-graph-works-serve:
    uv run --package graph-works-serve pytest packages/graph-works-serve/tests -n auto

# Branch coverage — GATED, per package. Same bounded two-axis fan-out as
# `test` (`-n auto` per package, `GW_COV_JOBS`-many packages at once, CPUs
# split between them via `PYTEST_XDIST_AUTO_NUM_WORKERS`) -- see `test`'s
# comment for why both axes have to be bounded together, and
# `GW_COV_JOBS`/`GW_TEST_JOBS` are independent knobs only because `cov` and
# `test` are never run in the same invocation.
#
# Each package's coverage recipe sets its own `COVERAGE_FILE` -- coverage.py's
# default data file is `.coverage` in the CWD, and every one of these `uv run`
# invocations shares the same CWD (the workspace root), regardless of which
# package it covers. Serially that was invisible (each run finished, was
# read, and was gone before the next one started); run concurrently, two
# processes writing the same `.coverage` at once produced real, silent data
# corruption -- packages reported as failing their coverage floor with numbers
# that belonged to a different package's source tree entirely. Distinct data
# files per package is the fix, not a smaller `GW_COV_JOBS`.
#
# okf-io / okf-ext hold the original 95% floor. The margin there is thin, and
# `models.py` and `document.py` are where the slack went — they carry the debt.
#
# code-graph-io gates at 90% (its parser subtree is folded into this one
# gate, not a second one). A failure here names only a global percentage, so
# start with the lowest-covered module and read `term-missing`.
cov:
    #!/usr/bin/env bash
    set -euo pipefail
    jobs="${GW_COV_JOBS:-3}"
    ncpu=$(uv run python -c "import os; print(os.cpu_count() or 1)")
    workers=$(( ncpu / jobs )); [ "$workers" -ge 1 ] || workers=1
    export PYTEST_XDIST_AUTO_NUM_WORKERS="$workers"
    pkgs=(_cov-okf _cov-code-graph-io _cov-code-wiki-okf _cov-work-tracker-okf _cov-config-io _cov-plugin-fork-io _cov-models-io _cov-subagents-io _cov-doc-wiki-okf _cov-repositories-okf _cov-graph-works-core _cov-workflow-local _cov-workflow-orca _cov-graph-works-wire _cov-graph-works-cli _cov-graph-works-serve)
    printf '%s\n' "${pkgs[@]}" | xargs -P "$jobs" -I{} just {}

_cov-okf:
    COVERAGE_FILE=.coverage.okf uv run pytest --cov=okf_io --cov=okf_ext --cov-branch --cov-report=term-missing --cov-fail-under=95 -n auto
_cov-code-graph-io:
    COVERAGE_FILE=.coverage.code-graph-io uv run --package code-graph-io pytest packages/code-graph-io/tests --cov=code_graph_io --cov-branch --cov-report=term-missing --cov-fail-under=90 -n auto
_cov-code-wiki-okf:
    COVERAGE_FILE=.coverage.code-wiki-okf uv run --package code-wiki-okf pytest packages/code-wiki-okf/tests --cov=code_wiki_okf --cov-branch --cov-report=term-missing --cov-fail-under=95 -n auto
_cov-work-tracker-okf:
    COVERAGE_FILE=.coverage.work-tracker-okf uv run --package work-tracker-okf pytest packages/work-tracker-okf/tests --cov=work_tracker_okf --cov-branch --cov-report=term-missing --cov-fail-under=95 -n auto
_cov-config-io:
    COVERAGE_FILE=.coverage.config-io uv run --package config-io pytest packages/config-io/tests --cov=config_io --cov-branch --cov-report=term-missing --cov-fail-under=95 -n auto
_cov-plugin-fork-io:
    COVERAGE_FILE=.coverage.plugin-fork-io uv run --package plugin-fork-io pytest packages/plugin-fork-io/tests --cov=plugin_fork_io --cov-branch --cov-report=term-missing --cov-fail-under=95 -n auto
_cov-models-io:
    COVERAGE_FILE=.coverage.models-io uv run --package models-io --extra bedrock --extra vercel pytest packages/models-io/tests --cov=models_io --cov-branch --cov-report=term-missing --cov-fail-under=95 -n auto
_cov-subagents-io:
    COVERAGE_FILE=.coverage.subagents-io uv run --package subagents-io pytest packages/subagents-io/tests --cov=subagents_io --cov-branch --cov-report=term-missing --cov-fail-under=95 -n auto
_cov-doc-wiki-okf:
    COVERAGE_FILE=.coverage.doc-wiki-okf uv run --package doc-wiki-okf pytest packages/doc-wiki-okf/tests --cov=doc_wiki_okf --cov-branch --cov-report=term-missing --cov-fail-under=95 -n auto
_cov-repositories-okf:
    COVERAGE_FILE=.coverage.repositories-okf uv run --package repositories-okf pytest packages/repositories-okf/tests --cov=repositories_okf --cov-branch --cov-report=term-missing --cov-fail-under=95 -n auto
_cov-graph-works-core:
    COVERAGE_FILE=.coverage.graph-works-core uv run --package graph-works-core pytest packages/graph-works-core/tests --cov=graph_works_core --cov-branch --cov-report=term-missing --cov-fail-under=95 -n auto
_cov-workflow-orca:
    COVERAGE_FILE=.coverage.workflow-orca uv run --package workflow-orca pytest packages/workflow-orca/tests --cov=workflow_orca --cov-branch --cov-report=term-missing --cov-fail-under=95 -n auto
_cov-graph-works-wire:
    COVERAGE_FILE=.coverage.graph-works-wire uv run --package graph-works-wire pytest packages/graph-works-wire/tests --cov=graph_works_wire --cov-branch --cov-report=term-missing --cov-fail-under=95 -n auto
_cov-graph-works-cli:
    COVERAGE_FILE=.coverage.graph-works-cli uv run --package graph-works-cli pytest packages/graph-works-cli/tests --cov=graph_works_cli --cov-branch --cov-report=term-missing --cov-fail-under=95 -n auto
_cov-graph-works-serve:
    COVERAGE_FILE=.coverage.graph-works-serve uv run --package graph-works-serve pytest packages/graph-works-serve/tests --cov=graph_works_serve --cov-branch --cov-report=term-missing --cov-fail-under=95 -n auto

# workflow-local's coverage arm. POSIX-only: the package refuses to construct on
# Windows by design (D-002), so a line-coverage floor there measures a suite that
# is 51 skips wide. `just test` still runs the suite on Windows, where the skips
# and `test_windows_guard.py` are the signal.
[unix]
_cov-workflow-local:
    COVERAGE_FILE=.coverage.workflow-local uv run --package workflow-local pytest packages/workflow-local/tests --cov=workflow_local --cov-branch --cov-report=term-missing --cov-fail-under=95 -n auto

[windows]
_cov-workflow-local:
    @echo "workflow-local: coverage gate skipped — POSIX-only backend (D-002); see test_windows_guard.py"

# Scoped gate for ONE package -- lint, types (both platform arms), and the
# cov-gated test run, restricted to that package's own paths. Use this while
# iterating; it does not touch the other 14 packages or `test-plugin`, so it
# runs in a fraction of `just check`'s time.
#
# Not a substitute for `just check` -- it skips normalization/text-io/
# line-endings/platform-declared/contracts/test-plugin entirely, and a change
# can still break another package's types or a repo-wide check this skips. Run
# `just check` once, at the finish gate, before advancing the item.
#
# PKG is the package directory name under `packages/` (e.g. `code-graph-io`).
# `okf-io` and `okf-ext` share one root suite, so either name runs both.
# Units, paths and floors come from scripts/package_units.py; both type arms run concurrently with the same per-arm caches `types` uses.
check-pkg PKG: preflight sync
    just _check-pkg-body {{PKG}}

# `check-pkg`'s body, without `preflight`/`sync`: `check-affected` runs it per unit after one sync.
_check-pkg-body PKG:
    #!/usr/bin/env bash
    set -euo pipefail
    # Assign first, then eval: `eval "$(cmd)"` would swallow cmd's failure.
    vars=$(uv run python scripts/package_units.py env "{{PKG}}")
    eval "$vars"
    echo "--- lint ($LINT)"
    uv run ruff check $LINT
    uv run ruff format --check $LINT
    just _unit-types-cov {{PKG}}

# Both mypy arms then the cov-gated suite for one unit (no lint, no sync).
_unit-types-cov PKG:
    #!/usr/bin/env bash
    set -euo pipefail
    vars=$(uv run python scripts/package_units.py env "{{PKG}}")
    eval "$vars"
    tmp=$(mktemp -d)
    trap 'rm -rf "$tmp"' EXIT
    pids=()
    for arm in linux win32; do
      uv run $UVFLAGS mypy --strict --platform "$arm" --cache-dir ".mypy_cache/$arm/$UNIT" $SRC > "$tmp/$arm.log" 2>&1 &
      pids+=("$!")
    done
    status=0
    for i in 0 1; do
      arm=$([ "$i" = 0 ] && echo linux || echo win32)
      if wait "${pids[$i]}"; then rc=0; else rc=$?; fi
      echo "--- types ($arm)"
      cat "$tmp/$arm.log"
      [ "$rc" = 0 ] || status=1
    done
    [ "$status" = 0 ] || exit 1
    echo "--- cov"
    COVERAGE_FILE=".coverage.$UNIT" uv run $UVFLAGS pytest $TESTPATH $MODULES --cov-branch --cov-report=term-missing --cov-fail-under=$FLOOR -n auto

# The repo-wide half of `check`, as the gw gate's repo_wide step.
gate-repo-wide: preflight sync normalization text-io line-endings platform-declared lint contracts

# One gw gate unit; the plugin unit uses `test-plugin` directly.
_gate-unit UNIT:
    just _unit-types-cov {{UNIT}}

# Change-scoped gate -- what a branch touched, and everything that depends on it.
#
# `scripts/affected_packages.py` maps `git diff <base>` plus untracked files to
# package units and their reverse-dependency closure; BASE defaults to
# `git merge-base HEAD main`. A path outside `packages/`, `plugins/` and
# non-package `*.md` (justfile, scripts/, root pyproject, uv.lock, ...) runs
# the full `just check`. Otherwise: the cheap repo-wide checks always,
# `test-plugin` when `plugins/` changed, and `check-pkg` for each selected unit
# (`GW_COV_JOBS` at once, CPUs split as in `cov`).
#
# Use this while iterating. The stage-boundary gate is still `gw work gate`
# running the full `just check`.
check-affected BASE="": preflight
    #!/usr/bin/env bash
    set -euo pipefail
    BASE="{{BASE}}"
    sel=$(uv run python scripts/affected_packages.py ${BASE:+--base "$BASE"})
    printf '%s\n' "$sel"
    mode=$(printf '%s\n' "$sel" | sed -n 's/^mode: //p')
    plugin=$(printf '%s\n' "$sel" | sed -n 's/^plugin: //p')
    units=$(printf '%s\n' "$sel" | sed -n 's/^unit: //p')
    case "$mode" in
      none) echo "nothing changed"; exit 0 ;;
      full) exec just check ;;
    esac
    just sync normalization text-io line-endings platform-declared contracts
    if [ "$plugin" = yes ]; then just test-plugin; fi
    if [ -n "$units" ]; then
      jobs="${GW_COV_JOBS:-3}"
      ncpu=$(uv run python -c "import os; print(os.cpu_count() or 1)")
      workers=$(( ncpu / jobs )); [ "$workers" -ge 1 ] || workers=1
      export PYTEST_XDIST_AUTO_NUM_WORKERS="$workers"
      # The body, not `check-pkg`: one exact sync up front; per-unit syncs would race extras.
      printf '%s\n' "$units" | xargs -P "$jobs" -I{} just _check-pkg-body {}
    fi

# Everything CI will run. `cov` runs every suite, so `test` is not repeated.
#
# `sync` is named here as well as on `types`, deliberately. `just` runs a
# dependency at most once per invocation, so it costs nothing — and it means the
# gate is provisioned by its own contract rather than by whichever member recipe
# happens to pull `sync` in today. Without it the gate's result depends on the
# order of this list: `types` before `cov` fails from a clean checkout, `cov`
# before `types` passes, on identical code.
check: preflight sync normalization text-io line-endings platform-declared lint types contracts cov test-plugin

# Orca's coordinator->worker reply path -- P1 static (offline), P2 live round trip.
#
# ADVISORY, and deliberately not in `check`: the gate must stay offline and
# Orca-free. P1 would be safe there, but P2 needs a live Dispatch and a second
# party to answer, and a gate whose second half is permanently `skipped` in CI
# reports green while covering the half that matters.
#
# The filed correlation defect does NOT reproduce on orca 1.4.191 -- that was proved
# live at design time. This is the standing guard on that fact, so the next
# regression is found here rather than through a lost human answer.
#
# P2 runs only when `--from` and `--dispatch-capability` are passed through, from an
# agent's own dispatch preamble:
#   just orca-reply-probe --from term_... --dispatch-capability dcap_...
orca-reply-probe *ARGS:
    uv run python scripts/orca_reply_probe.py {{ARGS}}

# The gw plugin's own suites -- `plugins/gw/`.
#
# ENFORCING, and part of `check`: these are green once written and go red only
# when a change actually breaks a hook or a guard.
#
# Every suite under the plugin tree is named here explicitly. A gate that
# silently skips a suite when nothing invokes it reports green while covering
# nothing -- that is the failure mode this list exists to prevent, so a new
# suite added to the tree gets a line here in the same change.
#
# `tests/pi` needs Node's built-in TypeScript stripping to import
# `.pi/extensions/graph-works.ts` (Node 23.6+, where it is unflagged; developed
# against v24), so node
# remains a hard requirement of `just check`. A gate that silently skips a
# suite when a toolchain is missing reports green while covering nothing.
test-plugin: preflight
    #!/usr/bin/env bash
    set -euo pipefail
    cd plugins/gw
    # The bash suites shell out to the unversioned interpreter name; shadow it with uv's pinned
    # interpreter so a host whose first interpreter is 3.9 cannot fail them.
    shim=$(mktemp -d)
    trap 'rm -rf "$shim"' EXIT
    ln -s "$(uv run python -c 'import sys; print(sys.executable)')" "$shim/python3"
    export PATH="$shim:$PATH"
    echo "--- test-dispatch-profile-contract"
    bash tests/test-dispatch-profile-contract.sh
    echo "--- test-workspace-commit-authority"
    bash tests/test-workspace-commit-authority.sh
    echo "--- test-workspace-branch-docs"
    bash tests/test-workspace-branch-docs.sh
    echo "--- test-carried-context-docs"
    bash tests/test-carried-context-docs.sh
    echo "--- test_launch_placement"
    uv run python tests/test_launch_placement.py
    echo "--- test_attend_cards"
    uv run python tests/test_attend_cards.py
    echo "--- test_classify_lifecycle"
    uv run python tests/test_classify_lifecycle.py
    echo "--- test_reader_pinning"
    uv run python tests/test_reader_pinning.py
    uv run python tests/test_finish_receipt.py
    echo "--- test-finish-cleanup-contract"
    bash tests/test-finish-cleanup-contract.sh
    echo "--- test-finish-strategy-contract"
    bash tests/test-finish-strategy-contract.sh
    echo "--- test-finish-obligations-docs"
    bash tests/test-finish-obligations-docs.sh
    echo "--- test-execute-return-docs"
    bash tests/test-execute-return-docs.sh
    echo "--- test-no-stage-artifact-literals"
    bash tests/test-no-stage-artifact-literals.sh
    echo "--- codex/test-marketplace-manifest"
    bash tests/codex/test-marketplace-manifest.sh
    echo "--- codex/test-package-codex-plugin"
    bash tests/codex/test-package-codex-plugin.sh
    echo "--- hooks/test-session-start"
    bash tests/hooks/test-session-start.sh
    echo "--- hooks/test-skill-doc-routing"
    bash tests/hooks/test-skill-doc-routing.sh
    echo "--- hooks/test-dispatch-prompt-guard"
    uv run python tests/hooks/test-dispatch-prompt-guard.py
    echo "--- test-doc-layout-claims"
    bash tests/test-doc-layout-claims.sh
    echo "--- test_log_recipe"
    uv run --package graph-works-cli python tests/test_log_recipe.py
    echo "--- test-entry-point-skills"
    bash tests/test-entry-point-skills.sh
    echo "--- skills/shared/resolve-workspace"
    bash skills/shared/resolve-workspace.test.sh
    echo "--- pi/test-pi-extension (node)"
    node --test tests/pi/test-pi-extension.mjs
    echo "--- lint-shell"
    bash scripts/lint-shell.sh --all --strict
