#!/usr/bin/env python3
"""Convert `<workspace>/.graph-wiki.yaml` v2 into `workspace.yaml` v1 plus `.gw/`.

One direction only. `version: 1` is a **fresh format, not v3** -- there is no
migration path inside `graph_works_core` (`workspace/manifest.py:18-20`), and
`gw bootstrap` never overwrites an existing manifest (`workspace/init.py:326`),
so this script converts only non-dispatch settings. Retired dispatch choices
are refused before writes; remove them explicitly and recreate desired rules
in the new shared/local dispatch documents.

Design notes
------------
* **Dry run by default.** Nothing is created and nothing is run without
  `--write`, matching the writer conventions in `okf_io` and the sibling
  `convert_wikilinks.py`.

* **One command, no subcommands.** The whole conversion is one atomic act over
  one small document, and a partially converted control plane is not a state
  anyone wants.

* **`Disposition` is the deliverable, not an implementation detail.** The
  report an operator reads is the disposition tuple rendered, and the design
  spec's two tables are this module's parametrization -- one row, one
  `Disposition`, one test.

* **The manifest is rendered by hand, not dumped and not through
  `manifest.render_initial`.** That function cannot express
  `layout.bundle_dir`, operational `workflow.auto_drive.*`, or `state_gate`; `manifest.py:475-480` already renders by hand for the same
  reason.

* **Four independent readers validate the result.** They disagree by design:
  the manifest catalog owns top-level validation, and
  `code_wiki_okf.config` explicitly ignores every key it does not name.

* **Runs under `uv run` from the repo root.** It imports four workspace
  packages, so a bare `python scripts/convert_config.py` gets
  `ModuleNotFoundError` -- the same trap `convert-wikilinks.md` documents.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Literal

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from code_wiki_okf.config import ConfigError, load_config
from config_io import StoreValidationError, dotted
from graph_works_core.workspace import manifest
from graph_works_core.workspace.dispatch_config import validate_workspace_dispatch_layers
from graph_works_core.workspace.errors import WorkspaceError, WorkspaceNotFound
from graph_works_core.workspace.init import GITIGNORE_FILENAME
from graph_works_core.workspace.init import _GITIGNORE_HEADER as GITIGNORE_HEADER
from graph_works_core.workspace.layout import WorkspaceLayout, layout_for
from graph_works_core.workspace.pipeline import RELAY_TAIL_SEED

V2_FILENAME = ".graph-wiki.yaml"
V2_LOCAL_FILENAME = ".graph-wiki.local.yaml"
V2_VERSION = 2

#: Every top-level key the three live v2 manifests carry. A key nobody decided
#: is not a key to guess at, so anything outside this set is a refusal.
V2_TOP_LEVEL_KEYS: frozenset[str] = frozenset(
    {
        "version",
        "initialized_at",
        "topic",
        "plugins",
        "plugin",
        "workflow",
        "state_gate",
        "repo-directory",
        "workspace-directory",
    }
)


class ConversionRefused(RuntimeError):
    """A conversion this script will not perform. Exits 1, never partially applied."""


Action = Literal["carry", "re-express", "seed", "drop", "refuse"]


@dataclass(frozen=True, slots=True)
class Disposition:
    """What became of one key. One row of the design spec's tables."""

    source_key: str | None  # dotted v2 key; None for a seeded v1-only key
    target_key: str | None  # dotted v1 key; None for a drop
    action: Action
    value: object | None
    why: str  # one sentence, printed in the report


@dataclass(frozen=True, slots=True)
class Conversion:
    dispositions: tuple[Disposition, ...]
    manifest_text: str
    refusals: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.refusals


def _load_mapping(path: Path) -> dict[str, object]:
    """One YAML document as a mapping, or a refusal naming the file."""
    try:
        raw = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    except (YAMLError, UnicodeDecodeError) as exc:
        raise ConversionRefused(f"{path}: not valid YAML: {exc}") from exc
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConversionRefused(f"{path}: the document must be a mapping at the top level")
    return raw


def _check_version(doc: dict[str, object], path: Path) -> None:
    version = doc.get("version")
    # `bool` is an `int`: `version: true` is a broken manifest, not version 1.
    if isinstance(version, bool) or not isinstance(version, int):
        raise ConversionRefused(f"{path}: `version` must be an integer, got {version!r}")
    if version != V2_VERSION:
        raise ConversionRefused(
            f"{path}: this converts version {V2_VERSION} only, got {version}. "
            "`workspace.yaml` version 1 is a fresh format, not a bump -- there is nothing to convert here."
        )


def _check_keys(doc: dict[str, object], path: Path) -> None:
    unknown = sorted(set(doc) - V2_TOP_LEVEL_KEYS)
    if unknown:
        raise ConversionRefused(
            f"{path}: unknown top-level key(s) {unknown!r}. Every key needs a decided disposition; "
            "add one to the tables in the design spec rather than letting this guess."
        )


def read_v2(root: Path) -> dict[str, object]:
    """The workspace's v2 document, with `.graph-wiki.local.yaml` overlaid on top.

    The local file wins over the tracked one, matching legacy precedence.
    """
    tracked = root / V2_FILENAME
    if not tracked.is_file():
        raise ConversionRefused(f"{root}: no {V2_FILENAME} here, so there is nothing to convert.")
    doc = _load_mapping(tracked)
    _check_version(doc, tracked)
    _check_keys(doc, tracked)
    merged = dict(doc)
    local = root / V2_LOCAL_FILENAME
    if local.is_file():
        overlay = _load_mapping(local)
        _check_keys(overlay, local)
        merged.update(overlay)
    return merged


#: What `layout.bundle_dir` is seeded with. Deliberately not
#: `layout.DEFAULT_BUNDLE_DIR` (`okf`): the bundle does not move (ADR 2026-08-20-gw-directory-nests) and
#: the live vault is `wiki/`.
BUNDLE_DIR_SEED = "wiki"

#: What `topic` is retargeted to -- the post-C3 repo name.
TOPIC_SEED = "graph-works"


@dataclass(frozen=True, slots=True)
class Options:
    """The four conversion overrides. Every one has a computed default."""

    topic: str | None = None
    repo_name: str | None = None
    repo_path: str | None = None
    bundle_dir: str = BUNDLE_DIR_SEED


def _scalar(value: object) -> str:
    """One YAML scalar. `json.dumps` is the minimal correct double-quoted form --
    the same call `manifest.render_initial` and `code_wiki_okf.seed_files` make."""
    # `bool` before `int`: `True` is an `int` and would render as `1`.
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return json.dumps(_text(value))


def _text(value: object) -> str:
    """A v2 value as the string v1 should carry.

    The safe loader turns an *unquoted* ISO date into `datetime.date`; the live
    files quote `initialized_at`, but a workspace that did not is still a
    workspace this must convert.
    """
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    return str(value)


def _workspace_relative(value: str, root: Path) -> str:
    """*value* as `repositories.<name>.path` should carry it.

    Resolved in this order:

    1. `expanduser()`.
    2. **Absolute** -- `Path.is_absolute()`, pathlib's judgement on the host platform -- used
       as given.
    3. **Root-anchored but driveless on Windows** (`is_absolute()` is `False` while the value
       starts with a separator, and `PureWindowsPath(value).drive` is empty) -- refused, naming
       the missing drive. This branch cannot fire on POSIX: there the same string is absolute
       and stops at step 2.
    4. **Otherwise relative** -- joined onto *root*, the directory `workspace.yaml` itself lives
       in: every reader resolves a declared repo's relative path against the directory the
       manifest was read from (`code_wiki_okf.config.load_config`, anchored on `config_path`'s
       parent). Same computation as `graph_works_core.workspace.init._workspace_relative`,
       restated here rather than imported because that name is private to a package this script
       only reads through its public surface.

    A cross-drive target raises `ConversionRefused` naming both paths, rather than the raw
    `ValueError` `os.path.relpath` raises.
    """
    expanded = Path(value).expanduser()
    text = str(expanded)
    if expanded.is_absolute():
        target = expanded
    elif text.startswith(("/", "\\")) and not PureWindowsPath(text).drive:
        raise ConversionRefused(
            f"repo-directory: {value!r} is root-anchored but carries no drive, so on Windows it "
            "resolves against whichever drive is current. Give a drive-qualified absolute path "
            "(C:/...) or a path relative to the workspace root."
        )
    else:
        target = root / expanded
    try:
        return Path(os.path.relpath(target, start=root)).as_posix()
    except ValueError as exc:
        raise ConversionRefused(
            f"repo-directory: {target} is on a different drive than the workspace root {root}: {exc}"
        ) from exc


def dispose(raw: dict[str, object], *, root: Path, options: Options) -> Conversion:
    """One `Disposition` per row of the design spec's two tables, plus the
    rendered `workspace.yaml` body."""
    dispositions: list[Disposition] = []
    refusals: list[str] = []
    add = dispositions.append

    # --- v2 keys, in table order -------------------------------------------
    add(
        Disposition(
            "version",
            "version",
            "re-express",
            1,
            "Not a bump -- `workspace.yaml` version 1 is a fresh format (manifest.py:18-20).",
        )
    )

    initialized_at = _text(raw.get("initialized_at", ""))
    add(
        Disposition(
            "initialized_at",
            "initialized_at",
            "carry",
            initialized_at,
            "Provenance, carried verbatim -- this script never stamps today.",
        )
    )

    topic = options.topic if options.topic is not None else TOPIC_SEED
    add(
        Disposition(
            "topic",
            "topic",
            "re-express",
            topic,
            "Display name only, retargeted to the post-C3 repo name; --topic overrides.",
        )
    )

    if "plugins" in raw:
        add(
            Disposition(
                "plugins",
                None,
                "drop",
                raw["plugins"],
                "No catalog equivalent and no successor field. The installer roster is not "
                "rendered anywhere any more; plugin versions live in "
                "~/.claude/plugins/installed_plugins.json.",
            )
        )

    if "plugin" in raw:
        add(
            Disposition(
                "plugin",
                None,
                "drop",
                raw["plugin"],
                "The plugin backends belong to models-io (manifest.py:6), a different surface.",
            )
        )

    workflow = raw.get("workflow")
    workflow = workflow if isinstance(workflow, dict) else {}

    if "commit_strategy" in workflow:
        add(
            Disposition(
                "workflow.commit_strategy",
                None,
                "drop",
                workflow["commit_strategy"],
                "No catalog equivalent and no consumer anywhere in the rebuild.",
            )
        )

    if "model_routing" in workflow:
        add(
            Disposition(
                "workflow.model_routing",
                None,
                "drop",
                workflow["model_routing"],
                "No catalog equivalent. The tier axis fed legacy hooks the new plugin does not "
                "register; roles.* is a different axis (concrete backend model ids).",
            )
        )

    auto_drive = workflow.get("auto_drive")
    auto_drive = auto_drive if isinstance(auto_drive, dict) else {}
    if "max_parallel" in auto_drive:
        add(
            Disposition(
                "workflow.auto_drive.max_parallel",
                "workflow.auto_drive.max_parallel",
                "carry",
                auto_drive["max_parallel"],
                "In the catalog (manifest.py:231-236).",
            )
        )
    if "supervise_merges" in auto_drive:
        add(
            Disposition(
                "workflow.auto_drive.supervise_merges",
                "workflow.auto_drive.supervise_merges",
                "carry",
                auto_drive["supervise_merges"],
                "Operational merge supervision is preserved.",
            )
        )
    for key in ("models", "overrides", "permission_mode"):
        if key in auto_drive:
            refusals.append(
                f"workflow.auto_drive.{key}: retired dispatch setting; remove it and recreate rules explicitly."
            )
    if "pipeline" in workflow:
        refusals.append("workflow.pipeline: retired dispatch setting; remove it and recreate rules explicitly.")

    state_gate = raw.get("state_gate")
    if isinstance(state_gate, dict):
        if "enabled" in state_gate:
            add(
                Disposition(
                    "state_gate.enabled",
                    "state_gate.enabled",
                    "carry",
                    state_gate["enabled"],
                    "In the catalog (manifest.py:140-145).",
                )
            )
        if "branches" in state_gate:
            add(
                Disposition(
                    "state_gate.branches",
                    "state_gate.branches",
                    "carry",
                    list(state_gate["branches"]),  # type: ignore[arg-type]
                    "In the catalog (manifest.py:146-151). An empty list is valid and intentional.",
                )
            )

    if "workspace-directory" in raw:
        add(
            Disposition(
                "workspace-directory",
                None,
                "drop",
                raw["workspace-directory"],
                "Becomes GRAPH_WORKS_DIR, an env-only catalog entry (manifest.py:243-250). "
                "Nothing is written to the file; the export line is printed instead.",
            )
        )

    # --- v1-only keys ------------------------------------------------------
    bundle_dir = options.bundle_dir
    add(
        Disposition(
            None,
            "layout.bundle_dir",
            "seed",
            bundle_dir,
            "Written explicitly even though it is an override: the projection carries explicit "
            "values only, and skill-doc-routing falls back to `okf`, so an unset key is a hole.",
        )
    )

    raw_repo = options.repo_path if options.repo_path is not None else raw.get("repo-directory")
    if raw_repo is None:
        refusals.append("no repository path: the v2 document carries no `repo-directory` and no --repo-path was given.")
        repo_name = options.repo_name or "unknown"
        repo_relative = ""
    else:
        repo_text = _text(raw_repo)
        if options.repo_path is not None:
            # A `--repo-path` value means what the shell means by it: resolve a relative value
            # against the CWD before handing it to the workspace-root-anchored resolver, rather
            # than letting a manifest's own anchor apply to a value that never came from one.
            cwd_target = Path(repo_text).expanduser()
            if not cwd_target.is_absolute():
                cwd_target = Path.cwd() / cwd_target
            repo_text = str(cwd_target)
        repo_name = options.repo_name or Path(repo_text).expanduser().name
        repo_relative = _workspace_relative(repo_text, root)
        add(
            Disposition(
                "repo-directory",
                f"repositories.{repo_name}.path",
                "re-express",
                repo_relative,
                "A repo root becomes one entry in a mapping of named scan targets -- deliberately "
                "different things (layout.py:10-15). Rewritten relative to the workspace root.",
            )
        )

    add(
        Disposition(
            None,
            "ignore",
            "seed",
            [],
            "NOT layout.scanner_excludes: this workspace is its own git repo, so scanner_excludes "
            "evaluates to ('./**',) and would exclude the entire scan.",
        )
    )

    add(
        Disposition(
            None,
            "workflow.dispatch_rules",
            "seed",
            "dispatch.yaml",
            "Initialize new dispatch defaults; old launch choices are never translated.",
        )
    )

    return Conversion(
        dispositions=tuple(dispositions),
        manifest_text=_render(dispositions),
        refusals=tuple(refusals),
    )


def _render(dispositions: list[Disposition]) -> str:
    """The `workspace.yaml` body.

    Rendered by hand rather than dumped, and deliberately not through
    `manifest.render_initial` -- that function cannot express
    `layout.bundle_dir`, operational `workflow.auto_drive.*`, or `state_gate`. Key order mirrors `render_initial`'s so a converted
    manifest reads like a bootstrapped one.
    """
    values = {d.target_key: d.value for d in dispositions if d.target_key is not None}
    lines: list[str] = []

    lines.append(f"version: {_scalar(values['version'])}")
    if values.get("initialized_at"):
        lines.append(f"initialized_at: {_scalar(values['initialized_at'])}")
    if values.get("topic"):
        lines.append(f"topic: {_scalar(values['topic'])}")

    if "layout.bundle_dir" in values:
        lines.append("layout:")
        lines.append(f"  bundle_dir: {_scalar(values['layout.bundle_dir'])}")

    # One `workflow:` block carrying both nested sub-blocks -- two would be a
    # duplicate key and the second would silently win.
    workflow_lines: list[str] = []
    if "workflow.dispatch_rules" in values:
        workflow_lines.append(f"  dispatch_rules: {_scalar(values['workflow.dispatch_rules'])}")
    auto_drive_lines: list[str] = []
    for key in ("max_parallel", "supervise_merges"):
        dotted_key = f"workflow.auto_drive.{key}"
        if dotted_key in values:
            auto_drive_lines.append(f"    {key}: {_scalar(values[dotted_key])}")
    if auto_drive_lines:
        workflow_lines.append("  auto_drive:")
        workflow_lines.extend(auto_drive_lines)
    if workflow_lines:
        lines.append("workflow:")
        lines.extend(workflow_lines)

    repo_paths = {
        key: value for key, value in values.items() if key.startswith("repositories.") and key.endswith(".path")
    }
    if repo_paths:
        lines.append("repositories:")
        for key, value in repo_paths.items():
            name = key[len("repositories.") : -len(".path")]
            lines.append(f"  {json.dumps(name)}:")
            lines.append(f"    path: {_scalar(value)}")
    else:
        lines.append("repositories: {}")

    ignore = values.get("ignore")
    if isinstance(ignore, list) and ignore:
        lines.append("ignore:")
        lines.extend(f"  - {_scalar(pattern)}" for pattern in ignore)
    else:
        lines.append("ignore: []")

    gate_lines: list[str] = []
    if "state_gate.enabled" in values:
        gate_lines.append(f"  enabled: {_scalar(values['state_gate.enabled'])}")
    branches = values.get("state_gate.branches")
    if isinstance(branches, list):
        if branches:
            gate_lines.append("  branches:")
            gate_lines.extend(f"    - {_scalar(branch)}" for branch in branches)
        else:
            gate_lines.append("  branches: []")
    if gate_lines:
        lines.append("state_gate:")
        lines.extend(gate_lines)

    return "\n".join(lines) + "\n"


def validate_manifest(layout: WorkspaceLayout) -> None:
    """Prove a written `workspace.yaml` through four independent readers.

    Four rather than one because they disagree by design: the manifest catalog
    owns top-level validation, and `code_wiki_okf.config` explicitly ignores
    every key it does not name. Raises `ConversionRefused` for the first
    failure, naming which reader rejected it.
    """
    # Reader 1 -- version, parseability, layout overrides resolve.
    try:
        manifest.read(layout.manifest_path)
    except (WorkspaceError, WorkspaceNotFound) as exc:
        raise ConversionRefused(f"manifest.read: {exc}") from exc

    # Reader 2 -- every carried value matches its catalog type/allowed. The same
    # path `gw config list` takes (config_cli/main.py:147).
    try:
        manifest.resolve_checked_all(layout, environ={})
    except (StoreValidationError, WorkspaceError) as exc:
        raise ConversionRefused(f"manifest.resolve_checked_all: {exc}") from exc

    # Reader 3 -- the independent tier-3 reader over repositories/ignore/state_gate.
    # `config_path` is required: the default is `<bundle_root>/workspace.yaml`, and
    # the manifest lives at the workspace root. Every production call site passes it.
    try:
        config = load_config(
            layout.bundle_dir,
            config_path=layout.manifest_path,
            graph_dir=layout.cache_dir,
            declarations_dir=layout.config_dir,
        )
    except (ConfigError, OSError, ValueError) as exc:
        raise ConversionRefused(f"code_wiki_okf.load_config: {exc}") from exc
    # `_resolve` is pure path math -- existence is this script's post-condition,
    # not the reader's. A scan target that does not exist is a silently empty scan.
    for repo in config.repos:
        if not repo.path.is_dir():
            raise ConversionRefused(
                f"repositories.{repo.name}.path: {repo.path} is not a directory -- "
                "the value is resolved against the workspace root."
            )

    # Reader 4 -- validate explicit manifest layers without restoring retired routing.
    store = manifest.workspace_store(layout)
    try:
        validate_workspace_dispatch_layers(
            layout, base=store.read_base_explicit(), overlay=store.read_overlay_explicit()
        )
    except WorkspaceError as exc:
        raise ConversionRefused(f"dispatch configuration: {exc}") from exc


def create_control_plane(layout: WorkspaceLayout) -> list[Path]:
    """The four directories and the gitignore anchor. Returns what it created.

    **Deliberately not `init.apply_init`.** That also runs `plan_scaffold` and
    the three `install_bundle`s (`init.py:24-26`, `INSTALLERS` at
    `init.py:120-124`), and that is C4 **phase 3**, not phase 1. This creates
    the same values `plan_init` would compute and stops; phase 3's
    `gw bootstrap` is idempotent over them and skips the manifest
    (`init.py:326`).
    """
    created: list[Path] = []
    for directory in layout.directories:
        if not directory.exists():
            directory.mkdir(parents=True, exist_ok=True)
            created.append(directory)

    path = layout.config_dir / GITIGNORE_FILENAME
    entries = layout.gitignore_entries
    body = "".join(f"{entry}\n" for entry in entries)
    if not path.exists():
        # The header is imported rather than restated so a converted workspace's
        # gitignore is byte-identical to a bootstrapped one, and phase 3's
        # `gw bootstrap` sees nothing to append.
        path.write_text(GITIGNORE_HEADER + body, encoding="utf-8", newline="")
        created.append(path)
    else:
        present = {line.strip() for line in path.read_text(encoding="utf-8").splitlines()}
        missing = "".join(f"{entry}\n" for entry in entries if entry not in present)
        if missing:
            with path.open("a", encoding="utf-8", newline="") as handle:
                handle.write(missing)
            created.append(path)
    shared = layout.root / "dispatch.yaml"
    if not shared.exists():
        shared.write_text(
            "pipeline:\n  rules:\n  - match: {variant: branch}\n    prompt_tail: " + json.dumps(RELAY_TAIL_SEED) + "\n",
            encoding="utf-8",
            newline="",
        )
        created.append(shared)
    root_ignore = layout.root / GITIGNORE_FILENAME
    present = root_ignore.read_text(encoding="utf-8").splitlines() if root_ignore.exists() else []
    missing = [entry for entry in ("workspace.local.yaml", "/dispatch.local.yaml") if entry not in present]
    if missing:
        with root_ignore.open("a", encoding="utf-8", newline="") as handle:
            if root_ignore.stat().st_size and not root_ignore.read_bytes().endswith(b"\n"):
                handle.write("\n")
            handle.write("".join(entry + "\n" for entry in missing))
        created.append(root_ignore)
    return created


#: The CLI invocation. The bare `gw` on PATH is the **legacy** binary until
#: `2026-08-21-bug-gw-path-resolves-donor-cli` lands, so the default names the
#: package explicitly. `--gw` overrides it.
DEFAULT_GW = "uv run --package graph-works-cli gw"

PROJECTION_BASENAME = "config.json"


def _assert_projection_fresh(layout: WorkspaceLayout) -> Path:
    """The three post-conditions finding (5) and (6) turn into checks."""
    projection = layout.cache_dir / PROJECTION_BASENAME
    if not projection.is_file():
        raise ConversionRefused(
            f"{projection}: `gw config sync` reported success but wrote no projection. "
            "Every hook reads this file; without it they all fall back to defaults silently."
        )
    try:
        payload = json.loads(projection.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConversionRefused(f"{projection}: not readable JSON: {exc}") from exc

    recorded = (payload.get("_meta") or {}).get("source_sha256")
    actual = hashlib.sha256(layout.manifest_path.read_bytes()).hexdigest()
    if recorded != actual:
        raise ConversionRefused(
            f"{projection}: _meta.source_sha256 {recorded!r} does not match sha256(workspace.yaml) "
            f"{actual!r}. The sync must be the last write -- that comparison is what "
            "skill-doc-routing:145-152 makes to decide the projection is stale."
        )

    layout_block = payload.get("layout")
    if not isinstance(layout_block, dict) or "bundle_dir" not in layout_block:
        raise ConversionRefused(
            f"{projection}: carries no `layout.bundle_dir`. The projection is explicit-only "
            "(projection.py:34-37) and skill-doc-routing:130 falls back to `okf`, so an unwritten "
            "key routes every workflow artifact into <workspace>/okf/."
        )
    return projection


def sync_projection(layout: WorkspaceLayout, *, gw: str) -> Path:
    """`<gw> config sync --workspace <root>`, then the post-conditions.

    The last write of the whole conversion, so the hook's sha256 comparison is
    clean. `gw` is a shell-quoted command string rather than a path because the
    default is a `uv run --package …` invocation, and it inherits this process's
    cwd -- run the script from the repo root or `uv run` cannot resolve the package.
    """
    command = shlex.split(gw) + ["config", "sync", "--workspace", str(layout.root)]
    try:
        result = subprocess.run(command, capture_output=True, text=True)
    except OSError as exc:
        raise ConversionRefused(f"{' '.join(command)}: {exc}") from exc
    if result.returncode != 0:
        raise ConversionRefused(
            f"`{' '.join(command)}` exited {result.returncode}:\n{result.stderr.strip() or result.stdout.strip()}"
        )
    return _assert_projection_fresh(layout)


@dataclass(frozen=True, slots=True)
class Applied:
    """What `--write` actually did. Rendered by `report`."""

    manifest_written: bool
    manifest_unchanged: bool
    created: tuple[Path, ...]
    projection: Path | None
    synced: bool


def convert(
    root: Path,
    *,
    options: Options,
    write: bool,
    sync: bool,
    gw: str,
) -> tuple[Conversion, Applied | None]:
    """The seven acts. Returns the conversion and, when `write`, what was applied.

    Act order is load-bearing: the sync is last so the projection's sha256 of
    `workspace.yaml` is current, which is the freshness comparison every hook makes.
    """
    # Reject each explicit source before the legacy shallow overlay can hide it.
    for filename in (V2_FILENAME, V2_LOCAL_FILENAME):
        source = root / filename
        if not source.is_file():
            continue
        explicit = _load_mapping(source)
        for key in (
            "workflow.pipeline",
            "workflow.auto_drive.models",
            "workflow.auto_drive.overrides",
            "workflow.auto_drive.permission_mode",
        ):
            if dotted.has(explicit, key):
                raise ConversionRefused(
                    f"{source}: {key}: retired dispatch setting; remove it and recreate rules explicitly."
                )
    conversion = dispose(read_v2(root), root=root, options=options)  # acts 1-2
    if not conversion.ok:
        raise ConversionRefused("; ".join(conversion.refusals))
    if not write:  # act 3 is the caller's `report`
        return conversion, None

    layout = layout_for(root, bundle_dir=options.bundle_dir)

    # Act 4 -- written only when absent, or byte-identical. A present-and-different
    # manifest is a refusal, never an overwrite.
    manifest_path = layout.manifest_path
    created_manifest = False
    unchanged = False
    if manifest_path.exists():
        existing = manifest_path.read_text(encoding="utf-8")
        if existing != conversion.manifest_text:
            raise ConversionRefused(
                f"{manifest_path}: already exists and differs from what this would write. "
                "Move it aside and re-run, or reconcile it by hand -- this never overwrites."
            )
        unchanged = True
    else:
        manifest_path.write_text(conversion.manifest_text, encoding="utf-8", newline="")
        created_manifest = True

    # Act 5 -- four readers. A failure unlinks a manifest *this run* created and
    # leaves one it did not: an unconvertible pre-existing file is the operator's
    # to look at, not this script's to delete.
    try:
        validate_manifest(layout)
    except ConversionRefused:
        if created_manifest:
            manifest_path.unlink(missing_ok=True)
        raise

    created = tuple(create_control_plane(layout))  # act 6
    projection = sync_projection(layout, gw=gw) if sync else None  # act 7

    return conversion, Applied(
        manifest_written=created_manifest,
        manifest_unchanged=unchanged,
        created=created,
        projection=projection,
        synced=sync,
    )


def report(conversion: Conversion, applied: Applied | None, *, root: Path, write: bool) -> None:
    """Act 3. The disposition tuple rendered -- what an operator reads at C4 phase 1."""
    width = max((len(d.source_key or d.target_key or "") for d in conversion.dispositions), default=0)
    print(f"{root} -- {V2_FILENAME} v{V2_VERSION} to workspace.yaml v1\n")
    print("dispositions:")
    for d in conversion.dispositions:
        name = d.source_key or f"(none) -> {d.target_key}"
        target = d.target_key or "(dropped)"
        print(f"  {name:<{width}}  {d.action:<10}  -> {target}")
        print(f"  {'':<{width}}  {'':<10}     {d.why}")
    print("\nworkspace.yaml:")
    for line in conversion.manifest_text.splitlines():
        print(f"  {line}")

    # Reported, never written: `workspace.dir` is an env-only catalog entry
    # (manifest.py:243-250), and the plugin's pure-bash resolver walks up for a
    # `.git` and then looks for `<repo>/.works` -- which this workspace has not got.
    print("\nnot written, for the operator to act on:")
    print(f"  export GRAPH_WORKS_DIR={root}")

    print()
    if not write:
        print("Dry run. Nothing written; pass --write to apply.")
        return
    assert applied is not None
    if applied.manifest_unchanged:
        print("workspace.yaml already matched -- no rewrite.")
    else:
        print("WROTE workspace.yaml.")
    for path in applied.created:
        print(f"  created {path}")
    if applied.projection is not None:
        print(f"  synced  {applied.projection}")
    elif not applied.synced:
        print("  --no-sync: no .gw/cache/config.json was written. The conversion is HALF DONE --")
        print("             every hook reads that file. Run `gw config sync --workspace <root>` to finish.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0], allow_abbrev=False)
    parser.add_argument("workspace", type=Path, help="directory holding .graph-wiki.yaml")
    parser.add_argument("--write", action="store_true", help="apply (default: report only)")
    parser.add_argument("--topic", default=None, help="value for `topic:` (default: the post-C3 repo name)")
    parser.add_argument("--repo-name", default=None, help="key under `repositories:` (default: repo basename)")
    parser.add_argument("--repo-path", default=None, help="override the scan target (default: from repo-directory)")
    parser.add_argument("--bundle-dir", default=BUNDLE_DIR_SEED, help="value for `layout.bundle_dir`")
    parser.add_argument(
        "--no-sync",
        dest="sync",
        action="store_false",
        help="skip the `gw config sync` shell-out, for a workspace whose CLI is not yet swapped",
    )
    parser.add_argument("--gw", default=DEFAULT_GW, help="the CLI invocation used for `config sync`")
    args = parser.parse_args(argv)

    root = args.workspace
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2

    options = Options(
        topic=args.topic,
        repo_name=args.repo_name,
        repo_path=args.repo_path,
        bundle_dir=args.bundle_dir,
    )
    try:
        conversion, applied = convert(root, options=options, write=args.write, sync=args.sync, gw=args.gw)
    except ConversionRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    report(conversion, applied, root=root, write=args.write)
    return 0


if __name__ == "__main__":  # pragma: no cover -- exercised through `main` in tests
    raise SystemExit(main())
