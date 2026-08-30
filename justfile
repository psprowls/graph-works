# Workspace task runner — okf-io, okf-ext, code-graph-io, code-wiki-okf,
# work-tracker-okf, config-io, models-io, subagents-io, doc-wiki-okf,
# graph-works-core, workflow-local, workflow-orca.
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

# Static types, strict — every package. Depends on `sync`: this is the recipe
# that fails without it.
types: sync
    uv run mypy --strict packages/okf-io/src packages/okf-ext/src
    uv run --package code-graph-io mypy --strict packages/code-graph-io/src
    uv run --package code-wiki-okf mypy --strict packages/code-wiki-okf/src
    uv run --package work-tracker-okf mypy --strict packages/work-tracker-okf/src
    uv run --package config-io mypy --strict packages/config-io/src
    uv run --package models-io --extra bedrock --extra vercel mypy --strict packages/models-io/src
    uv run --package subagents-io mypy --strict packages/subagents-io/src
    uv run --package doc-wiki-okf mypy --strict packages/doc-wiki-okf/src
    uv run --package graph-works-core mypy --strict packages/graph-works-core/src
    uv run --package workflow-local mypy --strict packages/workflow-local/src
    uv run --package workflow-orca mypy --strict packages/workflow-orca/src
    uv run --package graph-works-cli mypy --strict packages/graph-works-cli/src

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
    uv run --package models-io --extra bedrock --extra vercel pytest packages/models-io/tests
    uv run --package subagents-io pytest packages/subagents-io/tests
    uv run --package doc-wiki-okf pytest packages/doc-wiki-okf/tests
    uv run --package graph-works-core pytest packages/graph-works-core/tests
    uv run --package workflow-local pytest packages/workflow-local/tests
    uv run --package workflow-orca pytest packages/workflow-orca/tests
    uv run --package graph-works-cli pytest packages/graph-works-cli/tests

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
    uv run --package models-io --extra bedrock --extra vercel pytest packages/models-io/tests --cov=models_io --cov-branch --cov-report=term-missing --cov-fail-under=95
    uv run --package subagents-io pytest packages/subagents-io/tests --cov=subagents_io --cov-branch --cov-report=term-missing --cov-fail-under=95
    uv run --package doc-wiki-okf pytest packages/doc-wiki-okf/tests --cov=doc_wiki_okf --cov-branch --cov-report=term-missing --cov-fail-under=95
    uv run --package graph-works-core pytest packages/graph-works-core/tests --cov=graph_works_core --cov-branch --cov-report=term-missing --cov-fail-under=95
    uv run --package workflow-local pytest packages/workflow-local/tests --cov=workflow_local --cov-branch --cov-report=term-missing --cov-fail-under=95
    uv run --package workflow-orca pytest packages/workflow-orca/tests --cov=workflow_orca --cov-branch --cov-report=term-missing --cov-fail-under=95
    uv run --package graph-works-cli pytest packages/graph-works-cli/tests --cov=graph_works_cli --cov-branch --cov-report=term-missing --cov-fail-under=95

# The plugin CLI contract — three assertions against
# `wiki/concepts/graph-works-plugin-cli-contract.md`. Deliberately OUTSIDE
# `just check`: a gate that cannot pass yet must not block every unrelated
# change.
#
# `--plugin-tree` is passed here where the upstream recipe leaves it off. That
# recipe was written in a repository where the plugin tree lives elsewhere, so
# A1 and A3 report `skipped (no plugin tree)` rather than passing vacuously.
# In this fork the tree is `plugins/graph-works`, so naming it is what makes
# all three assertions real.
#
# Stale as of `feature-native-plugin-scaffold`: `--plugin-tree` still points at
# `plugins/graph-works`, which no longer holds the seventeen skills moved to
# `plugins/graph-works-native/`. Advisory only, not in `check`, so left as-is
# here -- it now covers only the vendored subtree's remaining skills.
plugin-contract *ARGS:
    uv run python scripts/plugin_contract.py --contract-page "${GRAPH_WIKI_WORKSPACE}/wiki/concepts/graph-works-plugin-cli-contract.md" --plugin-tree plugins/graph-works {{ARGS}}

# Everything CI will run. `cov` runs every suite, so `test` is not repeated.
#
# `sync` is named here as well as on `types`, deliberately. `just` runs a
# dependency at most once per invocation, so it costs nothing — and it means the
# gate is provisioned by its own contract rather than by whichever member recipe
# happens to pull `sync` in today. Without it the gate's result depends on the
# order of this list: `types` before `cov` fails from a clean checkout, `cov`
# before `types` passes, on identical code.
check: sync subtree-base normalization lint types contracts cov test-plugin test-plugin-native

# Subtree merge-base guard -- the `git-subtree-split` note behind
# `plugins/graph-works`.
#
# ENFORCING, and part of `check`. This is the one fork check that belongs in the
# gate: `audit-delta` and `plugin-contract` go legitimately red during
# in-progress work, so gating on them blocks unrelated changes. This cannot. The
# base changes only during an upstream re-base, and `plugins/SYNC.md`'s ritual
# appends the ledger row as step 1 -- there is no window where correct work
# leaves it red.
#
# It is the machine half of SYNC.md hard rule 2. The rule states a property --
# the squash commit stays reachable, recorded and prefix-rooted -- and this
# asserts it, which is what lets the rule name the property rather than banning
# every operation that might break it.
#
# It runs where `just check` runs. That converts a failure discovered one
# upstream release later into a red gate on the next run; it is not a merge gate.
subtree-base:
    python3 scripts/check_subtree_base.py

# Fork ledger drift check -- `plugins/PATCHES.md` against the tree.
#
# ADVISORY, and deliberately not in `check`. An enforcing gate fails on
# legitimate in-progress work: the child that applies the audit's dispositions
# would run its whole execution against a red gate until its last ledger entry
# landed, and every future patch would be blocked until documented. The two
# moments that matter -- the post-merge checklist in `plugins/SYNC.md`, and the
# merge drill -- invoke it explicitly.
audit-delta:
    python3 scripts/audit_delta.py

# Vendored upstream plugin suites -- the offline subset that executes code.
#
# ENFORCING, and part of `check`. This departs from how `audit-delta` and
# `plugin-contract` are wired, deliberately: both of those go red during
# legitimate in-progress work, so gating on them would block unrelated changes.
# These do not. Once repaired they are green, and they go red only when a patch
# actually breaks a hook -- which is the exposure the gate exists to close.
#
# What is NOT here, and why, is written down in `plugins/SYNC.md`. The two
# documentation-grep suites stay unrun (they assert our own skill prose
# verbatim); the live-model suites stay unrun (non-deterministic, 10-30 minutes,
# billed per run); `windows-lifecycle` moves to `test-plugin-slow`.
#
# It invokes the files directly rather than delegating to upstream's own
# `tests/claude-code/run-skill-tests.sh`, which names only three files, two of
# which need a live model. That runner was never a route to the offline set.
#
# `tests/pi` is here because our one added test in it -- coverage for the
# extension injecting nothing when the bundled skill is unreadable -- was
# unreachable by any runner: upstream's `package.json` declares no scripts, and
# nothing else named the directory. A divergence nothing executes is not
# coverage. It needs Node's built-in TypeScript stripping to import
# `.pi/extensions/superpowers.ts` (Node 22+; developed against v24).
#
# node and npm are hard requirements. A gate that silently skips 134 assertions
# when a toolchain is missing reports green while covering nothing.
#
# Eight pcvelz-only `tests/claude-code/test-*.sh` suites were drop-list rows
# (spike D2) and were removed with the dormant hook surface they covered.
# Their names were pruned from the list below by C2, 2026-08-17 — the gate
# still runs every suite that survives, and nothing is skipped silently.
test-plugin:
    #!/usr/bin/env bash
    set -euo pipefail
    cd plugins/graph-works
    for t in test-sdd-workspace; do
        echo "--- claude-code/$t"
        bash "tests/claude-code/$t.sh"
    done
    echo "--- hooks/test-session-start"
    bash tests/hooks/test-session-start.sh
    echo "--- shell-lint/test-lint-shell"
    bash tests/shell-lint/test-lint-shell.sh
    echo "--- systematic-debugging/test-find-polluter"
    bash tests/systematic-debugging/test-find-polluter.sh
    echo "--- pi/test-pi-extension (7 node)"
    node --test tests/pi/test-pi-extension.mjs
    echo "--- brainstorm-server (7 node + 2 bash)"
    cd tests/brainstorm-server
    [ -d node_modules ] || npm ci
    npm test

# The Graph Works-native plugin's own suites -- `plugins/graph-works-native/`.
#
# ENFORCING, and part of `check`, for `test-plugin`'s reason: these are green
# once written and go red only when a change actually breaks a hook or a
# guard. Five suites, not six: the parent design's list counted
# `test-sdd-workspace`, which is upstream's and stays with the subtree.
#
# Child 9 deletes `test-plugin` and renames this recipe to `test-plugin`.
test-plugin-native:
    #!/usr/bin/env bash
    set -euo pipefail
    cd plugins/graph-works-native
    echo "--- hooks/test-session-start"
    bash tests/hooks/test-session-start.sh
    echo "--- hooks/test-skill-doc-routing"
    bash tests/hooks/test-skill-doc-routing.sh
    echo "--- test-doc-layout-claims"
    bash tests/test-doc-layout-claims.sh
    echo "--- test-entry-point-skills"
    bash tests/test-entry-point-skills.sh
    echo "--- skills/shared/resolve-workspace"
    bash skills/shared/resolve-workspace.test.sh

# The vendored suite deliberately kept out of `check`.
#
# `windows-lifecycle.test.sh` costs ~150s in hard `sleep 75` calls and skips 3
# of its 12 checks off Windows. Shortening those sleeps behind an env-overridable
# window was considered and rejected: patching upstream test *timing logic* is a
# behavioral divergence, not an identity rename, and a larger maintenance
# liability than the 150 seconds it saves. It runs intact, on demand -- from
# `plugins/SYNC.md`'s post-merge checklist and the merge drill.
test-plugin-slow:
    cd plugins/graph-works/tests/brainstorm-server && bash windows-lifecycle.test.sh
