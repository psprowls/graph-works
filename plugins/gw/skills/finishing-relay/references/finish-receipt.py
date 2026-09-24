#!/usr/bin/env python3
"""Argument/JSON adapter; the installed core owns discovery, Git proof and writes."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date
import json
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("inspect", "record"))
    parser.add_argument("path")
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--repo")
    args = parser.parse_args(argv)
    if args.mode == "record" and not args.repo:
        parser.error("record requires --repo")
    from graph_works_core.workspace.discovery import resolve
    from graph_works_core.workspace.finish import inspect_finish
    from graph_works_core.orchestrate.finish_receipt import run_record_finish
    layout = resolve(workspace=args.workspace)
    result = (inspect_finish(layout, args.path) if args.mode == "inspect" else
              run_record_finish(layout, args.path, repo_name=args.repo, today=date.today()))
    print(json.dumps(asdict(result)))
    return 0 if (result.complete if args.mode == "inspect" else result.refusal is None) else 1


if __name__ == "__main__":
    raise SystemExit(main())
