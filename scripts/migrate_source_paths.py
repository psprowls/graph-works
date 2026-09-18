#!/usr/bin/env python3
"""Point every dead `source_path` in a bundle's `sources/**` at a copy under
`sources/references/`, per the ingest contract.

One-shot repair for C1 (`work/epic-wiki-lint-okf-io-correctness/children/
tech-debt-migrate-unresolvable-source-path-values-in`). Its argument is the
bundle directory; it never resolves a workspace itself.

Design notes
------------
* **Dead means `Bundle.has_member` says no**, after stripping one leading `/`
  -- the exact test `schemas.unresolved-member` applies, so the script and the
  rule can never disagree about which pages need work.
* **Resolution is by rule, never a guess.** An absolute path containing
  `/okf/` resolves to its remainder; a `work/...` path to the unique work item
  whose directory is, or ends with `-`, the date-stripped slug; a `raw/...`
  path to its bytes at `--restore-from` in the git repo holding the bundle.
  Zero or several hits is a refusal, and any refusal means nothing is written.
* **An existing destination is reused, never overwritten**: an earlier ingest
  put it there and it is already the contract destination.
* **One frontmatter line changes per page**, through `Document.set` and its
  splice render. `origin:`, bodies and every other key are untouched.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from okf_io import Document, load_bundle

SOURCES = "sources"
REFERENCES = "sources/references"
_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}-")
_DESIGN_NAMES = ("01-" "design-spec.md", "01-design.md")  # fmt: skip

Action = Literal["copy", "reuse", "restore", "self"]


@dataclass(frozen=True)
class Planned:
    page: str
    old: str
    new: str
    action: Action
    source: Path | None
    blob: bytes | None
    note: str | None


@dataclass(frozen=True)
class Refused:
    page: str
    value: str
    reason: str


class _Refusal(Exception):
    pass


def _target(value: str) -> str:
    return value[1:] if value.startswith("/") else value


def _item_slug(parts: list[str]) -> str:
    """Return the date-stripped work-item segment of a `work/...` value."""
    if "references" in parts:
        segment = parts[parts.index("references") - 1]
    else:
        rest = parts[1:]
        if rest and rest[0] == "_archive":
            rest = rest[1:]
        if not rest:
            raise _Refusal("no work-item segment")
        segment = rest[0]
    return _DATE_PREFIX.sub("", segment)


def _resolve_work(bundle: Path, value: str) -> Path:
    parts = value.split("/")
    slug = _item_slug(parts)
    candidates = sorted(
        directory
        for directory in (bundle / "work").rglob("*")
        if directory.is_dir()
        and "references" not in directory.relative_to(bundle).parts
        and (directory.name == slug or directory.name.endswith(f"-{slug}"))
    )
    if not candidates:
        raise _Refusal(f"no work item matches `{slug}`")
    if len(candidates) > 1:
        names = ", ".join(f"`{candidate.relative_to(bundle).as_posix()}`" for candidate in candidates)
        raise _Refusal(f"{len(candidates)} work items match `{slug}`: {names}")
    item = candidates[0]
    for name in (parts[-1], *_DESIGN_NAMES):
        for directory in (item / "references", item / "references" / "_archive"):
            if (directory / name).is_file():
                return directory / name
    relative = item.relative_to(bundle).as_posix()
    raise _Refusal(f"`{relative}` has no design artifact under references/")


def _restore(bundle: Path, value: str, rev: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(bundle.parent), "show", f"{rev}:./{value}"],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise _Refusal(f"nothing at `{value}` in {rev}")
    return result.stdout


def _plan_one(bundle: Path, page: str, value: str, restore_from: str) -> Planned:
    page_path = bundle / page
    if page_path.parent == bundle / REFERENCES:
        return Planned(page, value, page, "self", None, None, None)
    target = _target(value)
    source: Path | None = None
    blob: bytes | None = None
    if value.startswith("/") and "/okf/" in value:
        source = bundle / value.rsplit("/okf/", 1)[1]
        if not source.is_file():
            relative = source.relative_to(bundle).as_posix()
            raise _Refusal(f"`{relative}` does not exist")
    elif target.startswith("work/"):
        source = _resolve_work(bundle, target)
    elif target.startswith("raw/"):
        blob = _restore(bundle, target, restore_from)
    else:
        raise _Refusal("no resolution rule applies")
    suffix = source.suffix if source is not None else Path(target).suffix
    new = f"{REFERENCES}/{page_path.stem}{suffix}"
    destination = bundle / new
    if destination.exists():
        incoming = source.read_bytes() if source is not None else blob
        note = None
        if incoming != destination.read_bytes():
            origin = source.relative_to(bundle).as_posix() if source is not None else f"{restore_from}:{target}"
            note = f"destination differs from `{origin}`"
        return Planned(page, value, new, "reuse", source, blob, note)
    action: Action = "restore" if blob is not None else "copy"
    return Planned(page, value, new, action, source, blob, None)


def plan(bundle: Path, *, restore_from: str) -> tuple[list[Planned], list[Refused]]:
    loaded = load_bundle(bundle)
    planned: list[Planned] = []
    refused: list[Refused] = []
    for path in sorted((bundle / SOURCES).rglob("*.md")):
        page = path.relative_to(bundle).as_posix()
        value = Document.load(path).fm_data().get("source_path")
        if not isinstance(value, str) or not value.strip():
            continue
        if loaded.has_member(_target(value)):
            continue
        try:
            planned.append(_plan_one(bundle, page, value, restore_from))
        except _Refusal as exc:
            refused.append(Refused(page, value, str(exc)))
    return planned, refused


def apply(bundle: Path, planned: list[Planned]) -> None:
    for item in planned:
        destination = bundle / item.new
        if item.action == "copy" and item.source is not None:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(item.source, destination)
        elif item.action == "restore" and item.blob is not None:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(item.blob)
        document = Document.load(bundle / item.page)
        document.set("source_path", item.new)
        document.save()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("bundle", type=Path, help="the OKF bundle directory (e.g. <workspace>/okf)")
    parser.add_argument("--write", action="store_true", help="apply; the default is a dry run")
    parser.add_argument(
        "--restore-from",
        default="fdbeb856^",
        help="git revision holding deleted raw/ files",
    )
    args = parser.parse_args(argv)
    planned, refused = plan(args.bundle, restore_from=args.restore_from)
    for item in planned:
        note = f"  -- note: {item.note}" if item.note else ""
        print(f"{item.page}: {item.old} → {item.new} [{item.action}]{note}")
    for refusal in refused:
        print(f"REFUSED {refusal.page}: {refusal.value} -- {refusal.reason}")
    result = "written" if args.write and not refused else "dry run"
    print(f"{len(planned)} planned, {len(refused)} refused, {result}")
    if refused:
        return 1
    if args.write:
        apply(args.bundle, planned)
    return 0


if __name__ == "__main__":
    sys.exit(main())
