#!/usr/bin/env python3
"""Argument/JSON adapter; the installed core owns discovery, Git proof, writes and cleanup plans."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date
import json
from pathlib import Path


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
    from graph_works_core.workspace.discovery import resolve
    layout = resolve(workspace=args.workspace)
    if args.mode == "cleanup":
        from graph_works_core.workspace.finish import plan_finish_cleanup
        result = plan_finish_cleanup(layout, args.path, runner_cwd=args.runner_cwd or Path.cwd())
        print(json.dumps(asdict(result)))
        return 0 if result.refusal is None else 1
    if args.mode == "inspect":
        from graph_works_core.workspace.finish import inspect_finish
        result = inspect_finish(layout, args.path)
    else:
        from graph_works_core.orchestrate.finish_receipt import run_record_finish
        result = run_record_finish(layout, args.path, repo_name=args.repo, today=date.today())
    print(json.dumps(asdict(result)))
    return 0 if (result.complete if args.mode == "inspect" else result.refusal is None) else 1


if __name__ == "__main__":
    raise SystemExit(main())
