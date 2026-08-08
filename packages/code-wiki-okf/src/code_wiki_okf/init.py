"""`init_bundle()` — write an empty-but-valid OKF v0.2 bundle.

Fresh-only: succeeds into an absent or empty directory; any existing file in
the target is a refusal — nothing written, clear error naming the
obstruction. No `--force`, no reconcile. No lane directories — those appear
when the first page lands in them, in a later child.
"""

from __future__ import annotations

import importlib.resources
from dataclasses import dataclass
from datetime import date
from importlib.resources.abc import Traversable
from pathlib import Path

from okf_io import append_log_entry, parse

_SEED_RELATIVE_PATHS: tuple[str, ...] = (
    "_schema/Package.schema.json",
    "_schema/App.schema.json",
    "_schema/Dependency.schema.json",
    "_schema/TestSuite.schema.json",
    "_schema/Repository.schema.json",
    "_schema/AgentPlugin.schema.json",
    "_schema/File.schema.json",
    "_sections/Package.yaml",
    "_sections/App.yaml",
    "_sections/Dependency.yaml",
    "_sections/TestSuite.yaml",
    "_sections/Repository.yaml",
    "_sections/AgentPlugin.yaml",
    "_sections/File.yaml",
    "_tags.yaml",
)


class InitError(ValueError):
    """The target directory refused a fresh init — not absent-or-empty."""


@dataclass(frozen=True, slots=True)
class PlannedFile:
    relative_path: str
    content: str


@dataclass(frozen=True, slots=True)
class BundleInit:
    root: Path
    files: tuple[PlannedFile, ...]

    @property
    def changed(self) -> bool:
        """Always `True` on any successful `init_bundle()` call.

        Unlike `IndexUpdate`/`LogAppend`/`Migration` in okf-io, there is no
        reconciliation step here to make this vary — a fresh-only writer
        always produces the same fixed file list on success. Kept for
        interface symmetry with those sibling writer types.
        """
        return bool(self.files)

    def diff(self) -> str:
        return "\n".join(f"+ {planned.relative_path}" for planned in self.files)


def _assets_root() -> Traversable:
    return importlib.resources.files("code_wiki_okf") / "assets"


def init_bundle(root: str | Path, *, today: date, dry_run: bool = True) -> BundleInit:
    """Write a fresh, empty-but-valid OKF v0.2 bundle at *root*.

    `today` is injected — this module never reads the clock. `dry_run`
    defaults to `True`, matching okf-io's writer convention: the default call
    plans, and only an explicit `dry_run=False` touches disk.
    """
    root = Path(root)
    if root.exists():
        if not root.is_dir():
            raise InitError(f"{root}: exists and is not a directory")
        existing = sorted(p.name for p in root.iterdir())
        if existing:
            raise InitError(f"{root}: not empty (found {existing!r}); init_bundle() is fresh-only, no --force")

    from code_wiki_okf import __version__  # deferred: avoids a circular import at module load

    assets = _assets_root()
    files: list[PlannedFile] = []

    index_text = f"---\nokf_version: 0.2\n---\n\n# {root.name}\n"
    files.append(PlannedFile("index.md", index_text))

    log_seed = parse("")
    log_append = append_log_entry(
        log_seed, f"bundle initialized by code-wiki-okf/{__version__}", today=today, dry_run=True
    )
    files.append(PlannedFile("log.md", log_append.after))

    repositories_template = (assets / "_repositories.yaml").read_text(encoding="utf-8")
    files.append(PlannedFile("_repositories.yaml", repositories_template))

    for relative in _SEED_RELATIVE_PATHS:
        content = (assets / relative).read_text(encoding="utf-8")
        files.append(PlannedFile(relative, content))

    if not dry_run:
        for planned in files:
            target = root / planned.relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(planned.content, encoding="utf-8")

    return BundleInit(root=root, files=tuple(files))
