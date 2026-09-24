#!/usr/bin/env python3
"""Thin core adapter: snapshot before provisioning, guarded stamp afterwards.

Run under ``uv run --package graph-works-core python``. No Orca call runs
inside this process or while core holds its decision-owner lock.
"""
from __future__ import annotations

import argparse
from datetime import date
import json

from graph_works_core.orchestrate.placement import preparation_guard, run_record_placement
from graph_works_core.workspace.discovery import resolve


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("snapshot", "record"))
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--root")
    parser.add_argument("--phase")
    parser.add_argument("--repo")
    parser.add_argument("--worktree")
    parser.add_argument("--branch")
    parser.add_argument("--expected")
    args = parser.parse_args()
    layout = resolve(workspace=args.workspace)
    if args.operation == "snapshot":
        print(json.dumps({"guard": preparation_guard(layout, args.owner)}))
        return
    if not all((args.root == args.owner, args.phase, args.repo, args.worktree, args.branch, args.expected)):
        parser.error("record requires owner root, phase, repo, worktree, branch and expected guard")
    result = run_record_placement(
        layout, args.owner, root=args.root, phase=args.phase, repo=args.repo,
        worktree=args.worktree, branch=args.branch, expected_preparation=args.expected,
        today=date.today(), dry_run=False,
    )
    if result.plan.refusal or (result.plan.changed and not result.written):
        raise SystemExit(f"Preparation stamp refused: {result.plan.detail or result.application}")
    print(json.dumps({"written": result.written, "owner": args.owner, "repo": args.repo}))


if __name__ == "__main__":
    main()
