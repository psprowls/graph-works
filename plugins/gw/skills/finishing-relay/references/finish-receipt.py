#!/usr/bin/env python3
"""Argument/JSON adapter; the installed core owns discovery, Git proof, writes and the dry-run removal engine."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import date
from pathlib import Path


def cleanup_rows(layout, path, runner_cwd):
    """One dry-run `gw work place --remove` row per repository the resolved item finished in; never executes."""
    from gw_kernel.workspace.bundle import load_work_bundle
    from gw_kernel.workspace.errors import WorkspaceError
    from gw_kernel.workspace.provenance import GitFailure, gate_git
    from gw_pipeline.finish import read_finish_receipt
    from gw_pipeline.place import PlaceContext, remove
    from gw_pipeline.placement import NullNotifier
    from gw_pipeline.placement_resolve import item_placement
    from work_tracker_okf.items import load_items

    by_path = load_items(load_work_bundle(layout)).by_path
    item = by_path.get(path)
    if item is None:
        return [], f"{path}: unknown work item"
    if item.work_status != "resolved":
        return [], f"{path}: not resolved"
    _receipt, entries, error = read_finish_receipt(layout, path)
    if error is not None:
        return [], error
    if not entries:
        return [], f"{path}: no finish receipt"
    git = gate_git(layout)
    if isinstance(git, GitFailure):
        return [], f"{git.cause}: {git.detail}"
    ctx = PlaceContext(layout, git, NullNotifier(), date.today(), dry_run=True)
    runner = runner_cwd.resolve()
    rows = []
    for repo in dict.fromkeys(entry.repo for entry in entries):
        command = f"gw work place {path} --remove --repo-name {repo}"
        try:
            placement = item_placement(layout, item, by_path, repo_name=repo)
        except WorkspaceError as exc:
            rows.append(_row(repo, "", "", "skip", str(exc), command))
            continue
        if placement is None:
            rows.append(_row(repo, "", "", "skip", "no-placement", command))
            continue
        worktree, branch = str(placement.worktree), placement.branch
        if runner.is_relative_to(placement.worktree.resolve()):
            rows.append(_row(repo, worktree, branch, "deferred", "runner", command))
            continue
        result = remove(ctx, placement)
        step = result.chain[0] if result.chain else None
        if result.refusal is not None:
            rows.append(_row(repo, worktree, branch, "skip", result.refusal.kind, command))
        elif step is not None and step.action == "removed":
            rows.append(_row(repo, worktree, branch, "remove", "", command))
        else:
            rows.append(_row(repo, worktree, branch, "skip", (step.detail if step else None) or "kept", command))
    return rows, None


def _row(repo, worktree, branch, action, reason, command):
    return {"repo": repo, "worktree": worktree, "branch": branch, "action": action, "reason": reason, "command": command}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("inspect", "record", "cleanup"))
    parser.add_argument("path")
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--repo")
    parser.add_argument("--runner-cwd", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.mode == "record" and not args.repo:
        parser.error("record requires --repo")
    from gw_kernel.workspace.discovery import resolve

    layout = resolve(workspace=args.workspace)
    if args.mode == "cleanup":
        rows, refusal = cleanup_rows(layout, args.path, args.runner_cwd or Path.cwd())
        print(json.dumps({"rows": rows, "refusal": refusal}))
        return 0 if refusal is None else 1
    if args.mode == "inspect":
        from gw_pipeline.finish import inspect_finish

        result = inspect_finish(layout, args.path)
    else:
        from gw_orchestrate.finish_receipt import run_record_finish

        result = run_record_finish(layout, args.path, repo_name=args.repo, today=date.today())
    print(json.dumps(asdict(result)))
    return 0 if (result.complete if args.mode == "inspect" else result.refusal is None) else 1


if __name__ == "__main__":
    raise SystemExit(main())
