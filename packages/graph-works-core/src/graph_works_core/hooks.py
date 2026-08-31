"""graph_works_core.hooks -- the `.claude/settings.local.json` hooks merge/remove primitive.

Typed and sync, and carrying no `graph_wiki`/`graph-wiki` naming. Top-level,
not under `workspace/`: this
merges entries into a repo-level Claude Code settings file, unrelated to the
workspace manifest/layout `workspace/` owns, and never touches a
`WorkspaceLayout`. Per ADR-0013, `repo_root` is a plain argument -- this
module never resolves a workspace itself.
"""

from __future__ import annotations

import json
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from graph_works_core.workspace.errors import WorkspaceError

Feature = Literal["transcript"]
Action = Literal["enable", "disable"]


class HooksError(WorkspaceError):
    """A hooks merge/remove refused: an unknown feature/action, or a missing
    hook script under `scripts_dir` at enable time. Configuration/environment,
    not content -- per ADR-0013 rule 5."""


class HooksSettingsError(HooksError):
    """The existing settings file is malformed JSON or has an unusable shape."""


class HooksIOError(HooksError):
    """The settings file could not be read, written, or verified after writing."""


@dataclass(frozen=True, slots=True)
class HookWiring:
    """One settings.json hook registration: which event, which matcher, which script."""

    event: str
    matcher: str
    script: str

    @staticmethod
    def for_feature(feature: Feature) -> tuple[HookWiring, ...]:
        """The wirings one feature registers.

        A tuple, not a single wiring, because a feature is a *group*: the
        retired `gates` feature registered two scripts across two events, and
        the plural is what let enable/disable stay one code path. `transcript`
        being the only survivor does not make the shape wrong.
        """
        if feature == "transcript":
            return (HookWiring("SessionEnd", "", "session-end-transcript-capture.sh"),)
        raise HooksError(f"unknown hooks feature {feature!r} (valid: transcript)")


@dataclass(frozen=True, slots=True)
class HooksResult:
    """Outcome of one `apply()` call.

    `skipped` is enable-only (scripts already registered). `added`/`removed`
    may repeat a filename -- one entry per matching settings entry, not per
    unique script. Disabling a feature that was never enabled yields all
    three tuples empty and `changed=False` (and writes nothing).
    """

    settings_path: Path
    changed: bool
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()


def _default_scripts_dir() -> Path:
    packaged = Path(__file__).with_name("_hook_scripts")
    if packaged.is_dir():
        return packaged
    for candidate in Path(__file__).resolve().parents:
        examples = candidate / "plugins" / "graph-works" / "hooks" / "examples"
        if examples.is_dir():
            return examples
    raise HooksError("could not locate packaged hook scripts or plugins/graph-works/hooks/examples/")


def _settings_path(repo_root: Path) -> Path:
    return repo_root / ".claude" / "settings.local.json"


def _hook_command(script_path: Path) -> str:
    """Bind a configured hook to the interpreter that installed graph-works."""
    return f"GRAPH_WORKS_PYTHON={shlex.quote(sys.executable)} bash {shlex.quote(str(script_path))}"


def _json_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return type(value).__name__


def _shape_error(path: Path, field: str | None, expected: str, value: object) -> HooksSettingsError:
    label = "top-level JSON value" if field is None else f"`{field}`"
    article = "an" if expected in {"array", "object"} else "a"
    return HooksSettingsError(f"{path}: {label} must be {article} {expected}, got {_json_type(value)}")


def _object(path: Path, field: str | None, value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _shape_error(path, field, "object", value)
    return cast(dict[str, Any], value)


def _array(path: Path, field: str, value: object) -> list[Any]:
    if not isinstance(value, list):
        raise _shape_error(path, field, "array", value)
    return value


def _string(path: Path, field: str, value: object) -> str:
    if not isinstance(value, str):
        raise _shape_error(path, field, "string", value)
    return value


def _validate_settings(path: Path, value: object) -> dict[str, Any]:
    data = _object(path, None, value)
    if "hooks" in data:
        hooks = _object(path, "hooks", data["hooks"])
        for event, raw_entries in hooks.items():
            entries = _array(path, f"hooks.{event}", raw_entries)
            for entry_index, raw_entry in enumerate(entries):
                entry_field = f"hooks.{event}[{entry_index}]"
                entry = _object(path, entry_field, raw_entry)
                if "hooks" not in entry:
                    continue
                hook_entries = _array(path, f"{entry_field}.hooks", entry["hooks"])
                for hook_index, raw_hook in enumerate(hook_entries):
                    hook_field = f"{entry_field}.hooks[{hook_index}]"
                    hook = _object(path, hook_field, raw_hook)
                    if "command" in hook:
                        _string(path, f"{hook_field}.command", hook["command"])
    if "permissions" in data:
        permissions = _object(path, "permissions", data["permissions"])
        if "deny" in permissions:
            deny = _array(path, "permissions.deny", permissions["deny"])
            for index, member in enumerate(deny):
                _string(path, f"permissions.deny[{index}]", member)
    return data


def _read_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise HooksIOError(f"could not read settings {path}: {exc}") from exc
    try:
        value: object = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HooksSettingsError(f"{path}: is not valid JSON: {exc}") from exc
    return _validate_settings(path, value)


def _write_settings(path: Path, data: dict[str, Any]) -> None:
    try:
        rendered = json.dumps(data, indent=2) + "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8")
        json.loads(path.read_text(encoding="utf-8"))  # confirm the write re-parses
    except (OSError, UnicodeError, TypeError, ValueError) as exc:
        raise HooksIOError(f"could not write settings {path}: {exc}") from exc


def apply(
    action: Action,
    feature: Feature,
    repo_root: Path,
    *,
    scripts_dir: Path | None = None,
) -> HooksResult:
    """Merge (enable) or remove (disable) a hook feature's registrations in settings.local.json.

    Dedup is by script filename inside the `command` string, so enabling
    twice is a no-op and disable removes exactly what enable added. No
    feature manages `permissions` any more -- the retired `gates` feature
    was the only one that did -- but `_read_settings` still validates the
    block's shape, because this function rewrites the whole file and must
    not re-serialize a `permissions` value it never checked. Every write is
    re-read and re-parsed to confirm the entries landed; a parse failure
    raises. Nothing is written when `apply` raises -- the write happens
    once, after the merge loop completes.
    """
    if action not in ("enable", "disable"):
        raise HooksError(f"unknown hooks action {action!r} (valid: enable, disable)")

    wirings = HookWiring.for_feature(feature)
    scripts = scripts_dir
    if action == "enable" and scripts is None:
        scripts = _default_scripts_dir()
    target = _settings_path(repo_root)
    data = _read_settings(target)

    added: list[str] = []
    removed: list[str] = []
    skipped: list[str] = []
    changed = False

    hooks_block: dict[str, Any] = data.setdefault("hooks", {})

    for wiring in wirings:
        arr: list[dict[str, Any]] = hooks_block.setdefault(wiring.event, [])
        present = any(wiring.script in h.get("command", "") for entry in arr for h in entry.get("hooks", []))
        if action == "enable":
            if present:
                skipped.append(wiring.script)
                continue
            assert scripts is not None
            script_path = scripts / wiring.script
            if not script_path.is_file():
                raise HooksError(f"hook script missing: {script_path}")
            arr.append(
                {
                    "matcher": wiring.matcher,
                    "hooks": [{"type": "command", "command": _hook_command(script_path)}],
                }
            )
            added.append(wiring.script)
            changed = True
        else:
            kept: list[dict[str, Any]] = []
            for entry in arr:
                original = entry.get("hooks", [])
                entry_hooks = [h for h in original if wiring.script not in h.get("command", "")]
                if len(entry_hooks) < len(original):
                    removed.append(wiring.script)
                    changed = True
                    if entry_hooks:
                        kept.append({**entry, "hooks": entry_hooks})
                else:
                    kept.append(entry)
            if kept:
                hooks_block[wiring.event] = kept
            else:
                hooks_block.pop(wiring.event, None)

    if not hooks_block:
        data.pop("hooks", None)

    result = HooksResult(
        settings_path=target,
        changed=changed,
        added=tuple(added),
        removed=tuple(removed),
        skipped=tuple(skipped),
    )
    if changed:
        _write_settings(target, data)
    return result
