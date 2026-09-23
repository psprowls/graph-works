"""Plan and apply date-free, path-native work-item filing."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

from okf_ext.sections import render_skeleton
from okf_ext.shape import SectionSet
from okf_io import Document, parse

from work_tracker_okf._selection import path_index
from work_tracker_okf.dependencies import DependencyEdge, serialize_dependencies, validate_dependencies
from work_tracker_okf.hierarchy import unknown_depends_on
from work_tracker_okf.items import WorkItem
from work_tracker_okf.paths import child_lane, item_page, owned_dir, references_dir
from work_tracker_okf.vocabulary import (
    BLAST_RADII,
    EFFORTS,
    PARENT_TYPES,
    ROOT_ONLY_TYPES,
    SLUG_PREFIXES,
    TERMINAL_STATUSES,
)

FilingRefusal = Literal[
    "page-exists",
    "directory-exists",
    "unknown-type",
    "unknown-parent",
    "invalid-parent-type",
    "root-only-child",
    "inactive-parent",
    "unknown-dependency",
    "invalid-dependency",
    "invalid-effort",
    "invalid-blast-radius",
    "invalid-release-field",
]

_NAME_RE = re.compile(r"[^a-z0-9]+")
_MAX_WORDS_CLEAN = 4
_MAX_WORDS_HARD_CAP = 6
_KEY_ORDER: tuple[str, ...] = (
    "type",
    "title",
    "description",
    "tags",
    "status",
    "work_status",
    "opened",
    "updated",
    "effort",
    "blast_radius",
    "version",
    "target_date",
    "owner",
    "repo",
    "affects",
    "depends_on",
)


@dataclass(frozen=True, slots=True)
class FilingSeed:
    type: str
    title: str
    description: str
    on: date
    name: str | None = None
    parent_path: str | None = None
    depends_on: tuple[DependencyEdge, ...] = ()
    version: str | None = None
    target_date: date | None = None
    effort: str | None = None
    blast_radius: str | None = None
    owner: str | None = None
    affects: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    repo: str | None = None


@dataclass(frozen=True, slots=True)
class FilingPlan:
    seed: FilingSeed
    path: str
    target: Path
    owned_directory: Path
    required_directories: tuple[str, ...]
    required_indexes: tuple[str, ...]
    frontmatter: Mapping[str, Any]
    body: str
    warnings: tuple[str, ...]
    refusal: FilingRefusal | None
    detail: str

    @property
    def changed(self) -> bool:
        return self.refusal is None

    def diff(self) -> str:
        if self.refusal is not None:
            return f"{self.path}: refused ({self.refusal}) -- {self.detail}"
        lines = [f"+ {self.target}"]
        lines.extend(f"+ {directory}/" for directory in self.required_directories)
        lines.extend(f"  {key}: {value!r}" for key, value in self.frontmatter.items())
        return "\n".join(lines)


def slugify(value: str) -> str:
    """Return a safe lowercase basename fragment."""
    return _NAME_RE.sub("-", value.lower()).strip("-") or "untitled"


def compose_basename(type: str, name: str) -> tuple[str, tuple[str, ...]]:
    """Derive ``<type-prefix>-<name>`` without embedding a date."""
    prefix = SLUG_PREFIXES.get(type)
    if prefix is None:
        raise ValueError(f"unknown type {type!r}; expected one of {sorted(SLUG_PREFIXES)}")
    tokens = slugify(name).split("-")
    warnings: list[str] = []
    if len(tokens) > _MAX_WORDS_HARD_CAP:
        kept = "-".join(tokens[:_MAX_WORDS_HARD_CAP])
        warnings.append(f"name words: {len(tokens)} words truncated to {_MAX_WORDS_HARD_CAP} ({kept})")
        tokens = tokens[:_MAX_WORDS_HARD_CAP]
    elif len(tokens) > _MAX_WORDS_CLEAN:
        warnings.append(f"name words: {len(tokens)} words kept (recommended 1-{_MAX_WORDS_CLEAN})")
    return f"{prefix}-{'-'.join(tokens)}", tuple(warnings)


def _freeze_yaml(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_yaml(nested) for key, nested in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_yaml(nested) for nested in value)
    return value


def _freeze_frontmatter(values: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType({key: _freeze_yaml(value) for key, value in values.items()})


def _materialize_yaml(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _materialize_yaml(nested) for key, nested in value.items()}
    if isinstance(value, tuple):
        return [_materialize_yaml(nested) for nested in value]
    return value


def _materialize_frontmatter(values: Mapping[str, object]) -> dict[str, object]:
    return {key: _materialize_yaml(value) for key, value in values.items()}


def _frontmatter(seed: FilingSeed) -> Mapping[str, object]:
    values: dict[str, object] = {
        "type": seed.type,
        "title": seed.title,
        "description": seed.description,
        "status": "draft",
        "work_status": "open",
        "opened": seed.on,
        "updated": date(seed.on.year, seed.on.month, seed.on.day),
    }
    for key, value in (
        ("effort", seed.effort),
        ("blast_radius", seed.blast_radius),
        ("version", seed.version),
        ("target_date", seed.target_date),
        ("owner", seed.owner),
        ("repo", seed.repo),
    ):
        if value is not None:
            values[key] = value
    if seed.tags:
        values["tags"] = list(seed.tags)
    if seed.affects:
        values["affects"] = list(seed.affects)
    if seed.depends_on:
        values["depends_on"] = list(serialize_dependencies(seed.depends_on))
    return _freeze_frontmatter(values)


def _parent_note(parent: WorkItem | None) -> str:
    return "- Seeded for design.\n" if parent is None else f"- Filed under {parent.path}.\n"


def _refused(
    seed: FilingSeed,
    path: str,
    target: Path,
    owned_directory: Path,
    refusal: FilingRefusal,
    detail: str,
) -> FilingPlan:
    return FilingPlan(
        seed=seed,
        path=path,
        target=target,
        owned_directory=owned_directory,
        required_directories=(),
        required_indexes=(),
        frontmatter=MappingProxyType({}),
        body="",
        warnings=(),
        refusal=refusal,
        detail=detail,
    )


def plan_filing(root: Path, items: Sequence[WorkItem], seed: FilingSeed, section_set: SectionSet) -> FilingPlan:
    """Plan a canonical item path and its owned scaffolding without writing."""
    if seed.type not in SLUG_PREFIXES:
        return _refused(seed, "", root, root / "work", "unknown-type", f"unknown type {seed.type!r}")
    if seed.effort is not None and seed.effort not in EFFORTS:
        return _refused(seed, "", root, root / "work", "invalid-effort", f"invalid effort {seed.effort!r}")
    if seed.blast_radius is not None and seed.blast_radius not in BLAST_RADII:
        return _refused(
            seed,
            "",
            root,
            root / "work",
            "invalid-blast-radius",
            f"invalid blast radius {seed.blast_radius!r}",
        )

    release_only = [
        name for name, value in (("version", seed.version), ("target_date", seed.target_date)) if value is not None
    ]
    if release_only and seed.type != "Release":
        return _refused(
            seed,
            "",
            root,
            root / "work",
            "invalid-release-field",
            f"{' and '.join(release_only)} apply only to a Release, not to a {seed.type}",
        )

    by_path = path_index(items)
    parent: WorkItem | None = None
    if seed.parent_path is not None:
        if seed.type in ROOT_ONLY_TYPES:
            return _refused(
                seed,
                "",
                root,
                root / "work",
                "root-only-child",
                f"{seed.type} items must be filed at the root",
            )
        parent = by_path.get(seed.parent_path)
        if parent is None:
            return _refused(seed, "", root, root / "work", "unknown-parent", f"unknown parent {seed.parent_path!r}")
        if parent.type not in PARENT_TYPES:
            return _refused(
                seed,
                "",
                root,
                root / "work",
                "invalid-parent-type",
                f"parent {parent.path!r} has type {parent.type!r}",
            )
        if parent.archived or parent.work_status in TERMINAL_STATUSES:
            return _refused(seed, "", root, root / "work", "inactive-parent", f"parent {parent.path!r} is inactive")

    basename, warnings = compose_basename(seed.type, seed.title if seed.name is None else seed.name)
    lane = "work" if parent is None else child_lane(parent.path)
    path = f"{lane}/{basename}"
    target = item_page(path).path(root)
    owner = owned_dir(path).path(root)

    dependency_issues = validate_dependencies(
        seed.depends_on,
        parent_path=parent.path if parent is not None else None,
        self_path=path,
    )
    if dependency_issues:
        detail = "; ".join(f"{issue.code}: {issue.detail}" for issue in dependency_issues)
        return _refused(seed, path, target, owner, "invalid-dependency", detail)
    unknown = unknown_depends_on(items, seed.depends_on)
    if unknown:
        dependency_path = next(iter(unknown))
        return _refused(
            seed,
            path,
            target,
            owner,
            "unknown-dependency",
            f"unknown dependency {dependency_path!r}",
        )
    if target.exists():
        return _refused(seed, path, target, owner, "page-exists", f"{target}: a page already exists here")
    if owner.exists():
        return _refused(
            seed,
            path,
            target,
            owner,
            "directory-exists",
            f"{owner}: an owned directory already exists here",
        )

    required_directories = [references_dir(path).rel]
    required_indexes: list[str] = []
    if seed.type in PARENT_TYPES:
        active_lane = child_lane(path)
        archived_lane = child_lane(path, archived=True)
        required_directories.extend((active_lane, archived_lane))
        required_indexes.extend((f"{active_lane}/index.md", f"{archived_lane}/index.md"))
    return FilingPlan(
        seed=seed,
        path=path,
        target=target,
        owned_directory=owner,
        required_directories=tuple(required_directories),
        required_indexes=tuple(required_indexes),
        frontmatter=_frontmatter(seed),
        body=render_skeleton(section_set.types[seed.type]) + _parent_note(parent),
        warnings=warnings,
        refusal=None,
        detail=f"file {path} as {seed.type}",
    )


def _empty_index() -> str:
    return ""


def _document(plan: FilingPlan) -> Document:
    document = parse("")
    frontmatter = _materialize_frontmatter(plan.frontmatter)
    for key in _KEY_ORDER:
        if key in frontmatter:
            document.set(key, frontmatter[key])
    document.set_body(plan.body)
    return document


def apply(plan: FilingPlan) -> Path:
    """Create the page, owned lanes/indexes, and references scaffold."""
    if plan.refusal is not None:
        raise ValueError(f"refused plan ({plan.refusal}) must not be applied: {plan.detail}")
    if plan.target.exists():
        raise FileExistsError(f"{plan.target}: a page already exists here")
    if plan.owned_directory.exists():
        raise FileExistsError(f"{plan.owned_directory}: an owned directory already exists here")

    document = _document(plan)
    plan.target.parent.mkdir(parents=True, exist_ok=True)
    with plan.target.open("x", encoding="utf-8", newline="") as stream:
        stream.write(document.serialize())
    root = plan.target
    for _ in Path(plan.path).parts:
        root = root.parent
    for relative in plan.required_directories:
        (root / relative).mkdir(parents=True, exist_ok=False)
    (plan.owned_directory / "references" / ".gitkeep").touch(exist_ok=False)
    for relative in plan.required_indexes:
        (root / relative).write_text(_empty_index(), encoding="utf-8", newline="")
    return plan.target


__all__ = [
    "FilingPlan",
    "FilingRefusal",
    "FilingSeed",
    "apply",
    "compose_basename",
    "plan_filing",
    "slugify",
]
