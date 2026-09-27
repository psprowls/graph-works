# Workspace task runner — okf-io, okf-ext, code-graph-io, code-wiki-okf,
# work-tracker-okf, config-io, plugin-fork-io, models-io, subagents-io, doc-wiki-okf,
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
types: sync
    uv run mypy --strict --platform linux packages/okf-io/src packages/okf-ext/src
    uv run --package code-graph-io mypy --strict --platform linux packages/code-graph-io/src
    uv run --package code-wiki-okf mypy --strict --platform linux packages/code-wiki-okf/src
    uv run --package work-tracker-okf mypy --strict --platform linux packages/work-tracker-okf/src
    uv run --package config-io mypy --strict --platform linux packages/config-io/src
    uv run --package plugin-fork-io mypy --strict --platform linux packages/plugin-fork-io/src
    uv run --package models-io --extra bedrock --extra vercel mypy --strict --platform linux packages/models-io/src
    uv run --package subagents-io mypy --strict --platform linux packages/subagents-io/src
    uv run --package doc-wiki-okf mypy --strict --platform linux packages/doc-wiki-okf/src
    uv run --package graph-works-core mypy --strict --platform linux packages/graph-works-core/src
    uv run --package workflow-local mypy --strict --platform linux packages/workflow-local/src
    uv run --package workflow-orca mypy --strict --platform linux packages/workflow-orca/src
    uv run --package graph-works-wire mypy --strict --platform linux packages/graph-works-wire/src
    uv run --package graph-works-cli mypy --strict --platform linux packages/graph-works-cli/src
    uv run --package graph-works-serve mypy --strict --platform linux packages/graph-works-serve/src
    uv run mypy --strict --platform win32 packages/okf-io/src packages/okf-ext/src
    uv run --package code-graph-io mypy --strict --platform win32 packages/code-graph-io/src
    uv run --package code-wiki-okf mypy --strict --platform win32 packages/code-wiki-okf/src
    uv run --package work-tracker-okf mypy --strict --platform win32 packages/work-tracker-okf/src
    uv run --package config-io mypy --strict --platform win32 packages/config-io/src
    uv run --package plugin-fork-io mypy --strict --platform win32 packages/plugin-fork-io/src
    uv run --package models-io --extra bedrock --extra vercel mypy --strict --platform win32 packages/models-io/src
    uv run --package subagents-io mypy --strict --platform win32 packages/subagents-io/src
    uv run --package doc-wiki-okf mypy --strict --platform win32 packages/doc-wiki-okf/src
    uv run --package graph-works-core mypy --strict --platform win32 packages/graph-works-core/src
    uv run --package workflow-local mypy --strict --platform win32 packages/workflow-local/src
    uv run --package workflow-orca mypy --strict --platform win32 packages/workflow-orca/src
    uv run --package graph-works-wire mypy --strict --platform win32 packages/graph-works-wire/src
    uv run --package graph-works-cli mypy --strict --platform win32 packages/graph-works-cli/src
    uv run --package graph-works-serve mypy --strict --platform win32 packages/graph-works-serve/src

# Internal package boundaries (okf-ext README, "Boundaries"). Opt-in until CI
# exists: nothing enforces this but the person who runs it.
contracts:
    uv run lint-imports

# Full test suite, every package.
test:
    uv run pytest
    uv run --package code-graph-io pytest packages/code-graph-io/tests
    uv run --package code-wiki-okf pytest packages/code-wiki-okf/tests
    uv run --package work-tracker-okf pytest packages/work-tracker-okf/tests
    uv run --package config-io pytest packages/config-io/tests
    uv run --package plugin-fork-io pytest packages/plugin-fork-io/tests
    uv run --package models-io --extra bedrock --extra vercel pytest packages/models-io/tests
    uv run --package subagents-io pytest packages/subagents-io/tests
    uv run --package doc-wiki-okf pytest packages/doc-wiki-okf/tests
    uv run --package graph-works-core pytest packages/graph-works-core/tests
    uv run --package workflow-local pytest packages/workflow-local/tests
    uv run --package workflow-orca pytest packages/workflow-orca/tests
    uv run --package graph-works-wire pytest packages/graph-works-wire/tests
    uv run --package graph-works-cli pytest packages/graph-works-cli/tests
    uv run --package graph-works-serve pytest packages/graph-works-serve/tests

# Branch coverage — GATED, per package.
#
# okf-io / okf-ext hold the original 95% floor. The margin there is thin, and
# `models.py` and `document.py` are where the slack went — they carry the debt.
#
# code-graph-io gates at 90% (its parser subtree is folded into this one
# gate, not a second one). A failure here names only a global percentage, so
# start with the lowest-covered module and read `term-missing`.
cov:
    uv run pytest --cov=okf_io --cov=okf_ext --cov-branch --cov-report=term-missing --cov-fail-under=95
    uv run --package code-graph-io pytest packages/code-graph-io/tests --cov=code_graph_io --cov-branch --cov-report=term-missing --cov-fail-under=90
    uv run --package code-wiki-okf pytest packages/code-wiki-okf/tests --cov=code_wiki_okf --cov-branch --cov-report=term-missing --cov-fail-under=95
    uv run --package work-tracker-okf pytest packages/work-tracker-okf/tests --cov=work_tracker_okf --cov-branch --cov-report=term-missing --cov-fail-under=95
    uv run --package config-io pytest packages/config-io/tests --cov=config_io --cov-branch --cov-report=term-missing --cov-fail-under=95
    uv run --package plugin-fork-io pytest packages/plugin-fork-io/tests --cov=plugin_fork_io --cov-branch --cov-report=term-missing --cov-fail-under=95
    uv run --package models-io --extra bedrock --extra vercel pytest packages/models-io/tests --cov=models_io --cov-branch --cov-report=term-missing --cov-fail-under=95
    uv run --package subagents-io pytest packages/subagents-io/tests --cov=subagents_io --cov-branch --cov-report=term-missing --cov-fail-under=95
    uv run --package doc-wiki-okf pytest packages/doc-wiki-okf/tests --cov=doc_wiki_okf --cov-branch --cov-report=term-missing --cov-fail-under=95
    uv run --package graph-works-core pytest packages/graph-works-core/tests --cov=graph_works_core --cov-branch --cov-report=term-missing --cov-fail-under=95
    just cov-workflow-local
    uv run --package workflow-orca pytest packages/workflow-orca/tests --cov=workflow_orca --cov-branch --cov-report=term-missing --cov-fail-under=95
    uv run --package graph-works-wire pytest packages/graph-works-wire/tests --cov=graph_works_wire --cov-branch --cov-report=term-missing --cov-fail-under=95
    uv run --package graph-works-cli pytest packages/graph-works-cli/tests --cov=graph_works_cli --cov-branch --cov-report=term-missing --cov-fail-under=95
    uv run --package graph-works-serve pytest packages/graph-works-serve/tests --cov=graph_works_serve --cov-branch --cov-report=term-missing --cov-fail-under=95

# workflow-local's coverage arm. POSIX-only: the package refuses to construct on
# Windows by design (D-002), so a line-coverage floor there measures a suite that
# is 51 skips wide. `just test` still runs the suite on Windows, where the skips
# and `test_windows_guard.py` are the signal.
[unix]
cov-workflow-local:
    uv run --package workflow-local pytest packages/workflow-local/tests --cov=workflow_local --cov-branch --cov-report=term-missing --cov-fail-under=95

[windows]
cov-workflow-local:
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
check-pkg PKG: sync
    #!/usr/bin/env bash
    set -euo pipefail
    case "{{PKG}}" in
      okf-io|okf-ext)
        MODULES="--cov=okf_io --cov=okf_ext"; SRC="packages/okf-io/src packages/okf-ext/src"
        TESTPATH=""; PKGFLAG=""; FLOOR=95; EXTRA="" ;;
      code-graph-io)
        MODULES="--cov=code_graph_io"; SRC="packages/code-graph-io/src"
        TESTPATH="packages/code-graph-io/tests"; PKGFLAG="--package code-graph-io"; FLOOR=90; EXTRA="" ;;
      code-wiki-okf)
        MODULES="--cov=code_wiki_okf"; SRC="packages/code-wiki-okf/src"
        TESTPATH="packages/code-wiki-okf/tests"; PKGFLAG="--package code-wiki-okf"; FLOOR=95; EXTRA="" ;;
      work-tracker-okf)
        MODULES="--cov=work_tracker_okf"; SRC="packages/work-tracker-okf/src"
        TESTPATH="packages/work-tracker-okf/tests"; PKGFLAG="--package work-tracker-okf"; FLOOR=95; EXTRA="" ;;
      config-io)
        MODULES="--cov=config_io"; SRC="packages/config-io/src"
        TESTPATH="packages/config-io/tests"; PKGFLAG="--package config-io"; FLOOR=95; EXTRA="" ;;
      plugin-fork-io)
        MODULES="--cov=plugin_fork_io"; SRC="packages/plugin-fork-io/src"
        TESTPATH="packages/plugin-fork-io/tests"; PKGFLAG="--package plugin-fork-io"; FLOOR=95; EXTRA="" ;;
      models-io)
        MODULES="--cov=models_io"; SRC="packages/models-io/src"
        TESTPATH="packages/models-io/tests"; PKGFLAG="--package models-io"; FLOOR=95; EXTRA="--extra bedrock --extra vercel" ;;
      subagents-io)
        MODULES="--cov=subagents_io"; SRC="packages/subagents-io/src"
        TESTPATH="packages/subagents-io/tests"; PKGFLAG="--package subagents-io"; FLOOR=95; EXTRA="" ;;
      doc-wiki-okf)
        MODULES="--cov=doc_wiki_okf"; SRC="packages/doc-wiki-okf/src"
        TESTPATH="packages/doc-wiki-okf/tests"; PKGFLAG="--package doc-wiki-okf"; FLOOR=95; EXTRA="" ;;
      graph-works-core)
        MODULES="--cov=graph_works_core"; SRC="packages/graph-works-core/src"
        TESTPATH="packages/graph-works-core/tests"; PKGFLAG="--package graph-works-core"; FLOOR=95; EXTRA="" ;;
      workflow-local)
        MODULES="--cov=workflow_local"; SRC="packages/workflow-local/src"
        TESTPATH="packages/workflow-local/tests"; PKGFLAG="--package workflow-local"; FLOOR=95; EXTRA="" ;;
      workflow-orca)
        MODULES="--cov=workflow_orca"; SRC="packages/workflow-orca/src"
        TESTPATH="packages/workflow-orca/tests"; PKGFLAG="--package workflow-orca"; FLOOR=95; EXTRA="" ;;
      graph-works-wire)
        MODULES="--cov=graph_works_wire"; SRC="packages/graph-works-wire/src"
        TESTPATH="packages/graph-works-wire/tests"; PKGFLAG="--package graph-works-wire"; FLOOR=95; EXTRA="" ;;
      graph-works-cli)
        MODULES="--cov=graph_works_cli"; SRC="packages/graph-works-cli/src"
        TESTPATH="packages/graph-works-cli/tests"; PKGFLAG="--package graph-works-cli"; FLOOR=95; EXTRA="" ;;
      graph-works-serve)
        MODULES="--cov=graph_works_serve"; SRC="packages/graph-works-serve/src"
        TESTPATH="packages/graph-works-serve/tests"; PKGFLAG="--package graph-works-serve"; FLOOR=95; EXTRA="" ;;
      *)
        echo "unknown package '{{PKG}}' -- see packages/ for valid names" >&2
        exit 1 ;;
    esac
    LINT_PATH="packages/{{PKG}}"
    echo "--- lint ($LINT_PATH)"
    uv run ruff check "$LINT_PATH"
    uv run ruff format --check "$LINT_PATH"
    echo "--- types (linux)"
    uv run $PKGFLAG $EXTRA mypy --strict --platform linux $SRC
    echo "--- types (win32)"
    uv run $PKGFLAG $EXTRA mypy --strict --platform win32 $SRC
    echo "--- cov"
    uv run $PKGFLAG $EXTRA pytest $TESTPATH $MODULES --cov-branch --cov-report=term-missing --cov-fail-under=$FLOOR

# Everything CI will run. `cov` runs every suite, so `test` is not repeated.
#
# `sync` is named here as well as on `types`, deliberately. `just` runs a
# dependency at most once per invocation, so it costs nothing — and it means the
# gate is provisioned by its own contract rather than by whichever member recipe
# happens to pull `sync` in today. Without it the gate's result depends on the
# order of this list: `types` before `cov` fails from a clean checkout, `cov`
# before `types` passes, on identical code.
check: sync normalization text-io line-endings platform-declared lint types contracts cov test-plugin

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
test-plugin:
    #!/usr/bin/env bash
    set -euo pipefail
    cd plugins/gw
    echo "--- test-dispatch-profile-contract"
    bash tests/test-dispatch-profile-contract.sh
    echo "--- test_launch_placement"
    python3 tests/test_launch_placement.py
    echo "--- test_attend_cards"
    python3 tests/test_attend_cards.py
    echo "--- test_classify_lifecycle"
    python3 tests/test_classify_lifecycle.py
    echo "--- test_reader_pinning"
    python3 tests/test_reader_pinning.py
    python3 tests/test_finish_receipt.py
    echo "--- codex/test-marketplace-manifest"
    bash tests/codex/test-marketplace-manifest.sh
    echo "--- codex/test-package-codex-plugin"
    bash tests/codex/test-package-codex-plugin.sh
    echo "--- hooks/test-session-start"
    bash tests/hooks/test-session-start.sh
    echo "--- hooks/test-skill-doc-routing"
    bash tests/hooks/test-skill-doc-routing.sh
    echo "--- hooks/test-dispatch-prompt-guard"
    python3 tests/hooks/test-dispatch-prompt-guard.py
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
