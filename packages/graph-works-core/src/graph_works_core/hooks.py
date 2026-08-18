"""graph_works_core.hooks -- the `.claude/settings.local.json` hooks merge/remove primitive.

Ports `run_config_hooks` (agent-research's `graph_wiki_core/commands/config.py`,
lines 139-307) as a typed, sync module generalized off the legacy
`graph_wiki`/`graph-wiki` naming. Top-level, not under `workspace/`: this
merges entries into a repo-level Claude Code settings file, unrelated to the
workspace manifest/layout `workspace/` owns, and never touches a
`WorkspaceLayout`. Per ADR-0013, `repo_root` is a plain argument -- this
module never resolves a workspace itself.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from graph_works_core.workspace.errors import WorkspaceError

Feature = Literal["gates", "transcript"]
Action = Literal["enable", "disable"]


class HooksError(WorkspaceError):
    """A hooks merge/remove refused: an unknown feature/action, or a missing
    hook script under `scripts_dir` at enable time. Configuration/environment,
    not content -- per ADR-0013 rule 5."""


@dataclass(frozen=True, slots=True)
class HookWiring:
    """One settings.json hook registration: which event, which matcher, which script."""

    event: str
    matcher: str
    script: str

    @staticmethod
    def for_feature(feature: Feature) -> tuple[HookWiring, ...]:
        if feature == "gates":
            return (
                HookWiring("PostToolUse", "TaskUpdate", "post-task-complete-revalidate.sh"),
                HookWiring("Stop", "", "stop-revalidate-user-gates.sh"),
            )
        if feature == "transcript":
            return (HookWiring("SessionEnd", "", "session-end-transcript-capture.sh"),)
        raise HooksError(f"unknown hooks feature {feature!r} (valid: gates, transcript)")


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
    for candidate in Path(__file__).resolve().parents:
        examples = candidate / "plugins" / "graph-works" / "hooks" / "examples"
        if examples.is_dir():
            return examples
    raise HooksError(
        "could not locate plugins/graph-works/hooks/examples/ -- "
        "is graph-works-core running outside the agent-research checkout?"
    )


def _settings_path(repo_root: Path) -> Path:
    return repo_root / ".claude" / "settings.local.json"


def _read_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8")) or {}
    return data


def _write_settings(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    json.loads(path.read_text(encoding="utf-8"))  # confirm the write re-parses


def apply(
    action: Action,
    feature: Feature,
    repo_root: Path,
    *,
    scripts_dir: Path | None = None,
) -> HooksResult:
    """Merge (enable) or remove (disable) a hook feature's registrations in settings.local.json.

    Dedup is by script filename inside the `command` string, so enabling
    twice is a no-op and disable removes exactly what enable added. `gates`
    also manages `permissions.deny: ["EnterPlanMode"]`: enable adds it, and
    disable removes it only when this call actually removed a gates hook --
    a deny entry with no gates hooks present is treated as user-owned and
    left alone. Every write is re-read and re-parsed to confirm the entries
    landed; a parse failure raises. Nothing is written when `apply` raises --
    the write happens once, after the merge loop completes.
    """
    if action not in ("enable", "disable"):
        raise HooksError(f"unknown hooks action {action!r} (valid: enable, disable)")

    wirings = HookWiring.for_feature(feature)
    scripts = scripts_dir if scripts_dir is not None else _default_scripts_dir()
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
            script_path = scripts / wiring.script
            if not script_path.is_file():
                raise HooksError(f"hook script missing: {script_path}")
            arr.append(
                {
                    "matcher": wiring.matcher,
                    "hooks": [{"type": "command", "command": f'bash "{script_path}"'}],
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

    if feature == "gates":
        permissions: dict[str, Any] = data.setdefault("permissions", {})
        deny: list[str] = permissions.setdefault("deny", [])
        if action == "enable" and "EnterPlanMode" not in deny:
            deny.append("EnterPlanMode")
            changed = True
        if action == "disable" and removed and "EnterPlanMode" in deny:
            deny.remove("EnterPlanMode")
            changed = True
        if not deny:
            permissions.pop("deny", None)
        if not permissions:
            data.pop("permissions", None)

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
