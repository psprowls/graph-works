# AGENTS.md — gw-pipeline

Band 3 application slice. Permitted dependencies: gw-kernel and declared foundation and OKF packages.
Forbidden edges: gw-orchestrate, gw-work, gw-wiki and interfaces.
See the [root band table](../../AGENTS.md) and the root `pyproject.toml` contracts
"The application band is a tree of five slices beneath the interfaces" and
"Pipeline consumers never import the wiki slice".

Application slice over resolved kernel paths. Imports are stdlib, this package,
defining `gw_kernel` modules and the declared lower dependencies:
work-tracker-okf, okf-io, okf-ext, code-graph-io and config-io. Never import kernel
facade APIs, sibling slices (`gw_work`, `gw_orchestrate`, `gw_wiki`) or interfaces,
including in tests and TYPE_CHECKING bodies. There is no sibling dev dependency.
The package root exports no APIs; import each defining module.

## Module ownership

- `transactions`: journaled engine, executor lock, differential counted-multiset
  validation, same-lock baseline capture and anchored delegators. Preserve C3:
  tier/capability wrappers resolve transaction-global names at call time so
  monkeypatches of transactions._TIERS/os/platform continue to control the engine.
- `finish`, `execute_return`, `landed`: finish evidence/cleanup and return journals.
- `gate_config`, `decision_owner`: repository gate settings and owner locks.
- `workspace_branch`, `workspace_placement`, `repo_context`: resolved placement,
  branch and repository observations.
- `guidance/assembly`, `guidance/render`: guidance assembly and rendering;
  affecting overlap remains `gw_kernel.guidance.closure`.
- `transcript_capture`: the only production discovery exemption. It delegates to
  `gw_kernel.workspace.discovery.resolve`; it never reads workspace.yaml or
  implements another root resolver. No other production module may use discovery,
  environment root lookup or direct manifest reads.

Bundle locks/pending commits, anchors, commits, provenance, pipeline definitions,
dispatch configuration, hooks/hatch build and work schemas stay kernel-owned.
Pipeline imports those primitives; kernel never imports pipeline.

## Test support and checks

Tests re-export installed `gw_kernel.testing` fixtures and use generic kernel
support. `testing.forced_tier` patches engine globals; `test_support.finish_repos`
supplies finish-specific scenarios. These installed helpers are test-only;
production modules never import them. Never import another package test tree or
conftest. Scratch fixture authorship through kernel init APIs, injected test roots
and authored manifests is allowed; it grants no production discovery exemption.

`test_pipeline_boundaries.py` scans source and tests, grouped/ancestor/aliased
imports, discovery and direct manifest reads. Kernel's shared source-path guards
also scan this root. Run `just check-pkg gw-pipeline` for both mypy arms and the
unchanged 95% branch floor. Version is static 0.1.0; Python >=3.12.
