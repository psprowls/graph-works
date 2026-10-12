# AGENTS.md — gw-work

Band 3 application slice. Permitted dependencies: gw-kernel, gw-pipeline and declared lower packages.
Forbidden edges: gw-orchestrate, gw-wiki and interfaces.
See the [root band table](../../AGENTS.md) and the root `pyproject.toml` contracts
"The application band is a tree of five slices beneath the interfaces" and
"Pipeline consumers never import the wiki slice".

Application slice over resolved kernel primitives. May import the declared `gw_pipeline` dependency; no other sibling slice or interface runtime imports, kernel facade imports, workspace discovery, environment root lookup or direct workspace manifest reads. Runtime dependencies are the declarations in pyproject.toml.

## Modules

- `commands`: work filing, routing, display reads, lint, indexes, decision ledgers and path mutations.
- `carried`, `epic_brief`: inherited epic guidance.
- `reconcile`, `obligations`: artifact and obligation operations.
- `path_report`, `affecting`, `artifact_bodies`: path, target and body projections.
- `transactions`: delegation to pipeline transaction APIs; preserve monkeypatch seams.
- `archive.commands`: archival plans and execution.

## Tests

`work/` and `archive/` own the moved vertical tests. `kernel_seams/` owns integration cases invoking work with kernel services; `perf/` owns reparent regression counters. Tests never import sibling slices (`gw_orchestrate`, `gw_wiki`) or kernel test paths. Six installed kernel fixtures are re-exported from root conftest. Local repository support lives in `gitrepo.py` and `workspace_fixture.py`.

`test_work_boundaries.py` scans source and tests, including ancestor aliases, relative imports and TYPE_CHECKING bodies. Run `just check-pkg gw-work` for both mypy arms and the unchanged 95% branch floor.
