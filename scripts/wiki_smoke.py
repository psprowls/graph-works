#!/usr/bin/env python3
"""One-command smoke run of the code-wiki-okf pipeline against any real repo
checkout: graph-build -> init -> sync, in a single invocation.

No test in `code-wiki-okf` itself exercises this chain end to end against a
real checkout -- only a synthetic four-file `tmp_path` repo. Two failure modes
only show up at real scale or with a real git remote: a `workspace.yaml`
repo name that doesn't match the graph's own repository-node name (the entity
lane silently syncs zero entities for it -- `entities/sync.py`'s
`_resolve_placements` just `continue`s), and the mirror lane writing one page
per tracked file. This script exercises both in one command and turns the
first from silent into a loud exit code.

The graph is built directly at *bundle_root*, not a separate directory:
`code-wiki-okf`'s own `sync`/`validate` commands fix `graph_dir` to
`bundle_root` unconditionally now that the document no longer carries a
`graph_dir` field of its own (`code_wiki_okf.config.load_config`'s own
docstring), so there is no separate location for this standalone CLI to point
at.

Run with:
    uv run --package code-wiki-okf python scripts/wiki_smoke.py <repo> <bundle-root>
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from code_graph_io import open_reader
from code_graph_io.repo_context import repo_context
from code_graph_io.update import run_workspace
from code_wiki_okf.cli import app
from typer.testing import CliRunner


@dataclass(frozen=True)
class SmokeResult:
    repo_name: str
    graph_repo_names: tuple[str, ...]
    mismatch: bool
    init_output: str
    init_exit_code: int
    sync_output: str
    sync_exit_code: int


def run(
    repo: Path,
    bundle_root: Path,
    *,
    repo_name: str | None = None,
    full: bool = True,
) -> SmokeResult:
    """Graph *repo* into *bundle_root*, init *bundle_root* if needed, point it
    at *repo* under *repo_name* (derived from the repo's own git remote when
    not given), and sync. `repo_name` is a deliberate override point: passing
    a name that doesn't match the graph's own is how a caller reproduces the
    silent zero-entities mismatch on purpose, same as a human's typo would.
    """
    repo = repo.resolve()
    bundle_root = bundle_root.resolve()

    resolved_name = repo_name if repo_name is not None else repo_context(repo).repo

    run_workspace([repo], graph_dir=bundle_root, full=full)

    runner = CliRunner()
    init_result = runner.invoke(app, ["init", str(bundle_root)])

    config_path = bundle_root / "workspace.yaml"
    config_path.write_text(
        f"repositories:\n  {resolved_name}:\n    path: {repo}\n",
        encoding="utf-8",
    )

    sync_result = runner.invoke(app, ["sync", str(bundle_root)])

    with open_reader(graph_dir=bundle_root) as reader:
        graph_repo_names = tuple(sorted(node.name for node in reader.list_repositories()))

    return SmokeResult(
        repo_name=resolved_name,
        graph_repo_names=graph_repo_names,
        mismatch=resolved_name not in graph_repo_names,
        init_output=init_result.output,
        init_exit_code=init_result.exit_code,
        sync_output=sync_result.output,
        sync_exit_code=sync_result.exit_code,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0], allow_abbrev=False)
    parser.add_argument("repo", type=Path, help="path to the repo checkout to graph and mirror")
    parser.add_argument("bundle_root", type=Path, help="directory to build the code wiki bundle (and graph) into")
    parser.add_argument(
        "--repo-name",
        default=None,
        help="override the derived repo name -- e.g. to reproduce a name mismatch on purpose",
    )
    parser.add_argument(
        "--no-full", dest="full", action="store_false", help="incremental graph update instead of a full rebuild"
    )
    args = parser.parse_args(argv)

    if not args.repo.is_dir():
        print(f"not a directory: {args.repo}", file=sys.stderr)
        return 2

    result = run(args.repo, args.bundle_root, repo_name=args.repo_name, full=args.full)

    print(result.init_output, end="")
    print(result.sync_output, end="")

    if result.mismatch:
        known = ", ".join(result.graph_repo_names) or "<none>"
        print(
            f"\nWARNING: repo name {result.repo_name!r} matches no repository the graph knows about ({known}) "
            "-- 0 entities will ever sync for it. Check `workspace.yaml`'s `repositories` key against the graph's "
            "own repository name.",
            file=sys.stderr,
        )

    if result.init_exit_code != 0 or result.sync_exit_code != 0 or result.mismatch:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
