#!/usr/bin/env python3
"""Background stage dispatcher for the graph-wiki work pipeline.

Watches every work item in the vault, and for each one whose next pipeline
stage is automatable, dispatches a **fresh** background Claude Code session
(`claude --bg`) running `/graph-wiki:next <slug>`.

This mechanizes the invariant already stated in
`plugins/graph-wiki/skills/workflow/SKILL.md`: *"One stage per invocation, by
design. Never chain stages in a session -- each stage gets a fresh context
window. The work item plus raw/ artifacts are the durable state between
sessions; nothing depends on conversation memory."*

Design notes
------------
* **Fresh sessions, not subagents.** `claude --bg` starts a full Claude Code
  session with its own session id, transcript, plugin load and SessionStart
  hooks. A subagent (`Agent` tool) would share the parent's session and skip
  those hooks, so it is not usable here.
* **Interaction is surfaced, not swallowed.** A backgrounded session that hits
  `AskUserQuestion` does not deadlock -- it transitions to `state: "blocked"`
  in `claude agents --json`. This dispatcher reports those so you can
  `claude attach <id>`, answer, and let the stage continue.
* **`state: "done"` is not trusted as success.** A session can exit having
  accomplished nothing. Completion is confirmed against the durable wiki state
  by re-running `gw work next <slug> --json` and checking the phase actually
  advanced.
* **`claude logs` is deliberately never parsed.** Background sessions run in a
  pty; the log is raw ANSI terminal capture, not a data feed.

Usage
-----
    scripts/gw_dispatch.py --once --dry-run      # see what would be dispatched
    scripts/gw_dispatch.py                       # watch loop
    scripts/gw_dispatch.py --status              # ledger + live session rollup
    scripts/gw_dispatch.py --reset <slug>        # clear a stalled/failed entry
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Phases this dispatcher is willing to run unattended. `design` is excluded on
# purpose -- brainstorming is a conversation, and the user drives it in the
# foreground. `finish` is excluded because finishing-a-development-branch
# presents merge options and requires a typed "discard" confirmation; those
# should block on a human.
DEFAULT_PHASES = ("plan", "execute")

# Work item statuses that are terminal -- `gw work next` only returns blockers
# for these, so skip them before spending a subprocess.
TERMINAL_STATUSES = frozenset({"resolved", "wontfix", "superseded"})

# Ledger states that occupy a slug (no re-dispatch while in one of these).
ACTIVE_STATES = frozenset({"running", "blocked"})

# Ledger states that require an explicit `--reset` before retrying, so a
# systematically failing item cannot burn tokens in a redispatch loop.
STICKY_STATES = frozenset({"stalled", "failed", "lost"})

ISO = "%Y-%m-%dT%H:%M:%SZ"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime(ISO)


# --------------------------------------------------------------------------
# process helpers
# --------------------------------------------------------------------------


def run(argv: list[str], cwd: Path | None = None, timeout: int = 120) -> tuple[int, str, str]:
    """Run argv without a shell. Returns (rc, stdout, stderr)."""
    try:
        p = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s: {' '.join(argv)}"
    except FileNotFoundError as exc:
        return 127, "", str(exc)
    return p.returncode, p.stdout, p.stderr


def resolve_claude_bin(explicit: str | None) -> str:
    """Resolve the real `claude` executable.

    Note: in an interactive zsh `claude` may be a shell *function*; that is
    invisible to subprocess, and `which` correctly yields the real binary.
    """
    cand = explicit or os.environ.get("CLAUDE_BIN") or shutil.which("claude")
    if not cand:
        sys.exit("error: could not find the `claude` executable (try --claude-bin)")
    return cand


def resolve_gw_bin(explicit: str | None) -> list[str]:
    cand = explicit or os.environ.get("GW_BIN") or shutil.which("gw")
    if cand:
        return [cand]
    # Fall back to the uv workspace entry point documented in CLAUDE.md.
    if shutil.which("uv"):
        return ["uv", "run", "--package", "graph-wiki-cli", "gw"]
    sys.exit("error: could not find `gw` (try --gw-bin, or install graph-wiki-cli)")


# --------------------------------------------------------------------------
# ledger
# --------------------------------------------------------------------------


@dataclass
class Ledger:
    path: Path
    runs: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> Ledger:
        if path.exists():
            try:
                data = json.loads(path.read_text())
                return cls(path=path, runs=data.get("runs", {}))
            except (OSError, json.JSONDecodeError) as exc:
                print(f"warn: unreadable ledger {path} ({exc}); starting fresh", file=sys.stderr)
        return cls(path=path)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"updated_at": now_iso(), "runs": self.runs}, indent=2) + "\n")
        tmp.replace(self.path)

    def state_of(self, slug: str) -> str | None:
        entry = self.runs.get(slug)
        return entry.get("state") if entry else None


class EventLog:
    """Append-only record of dispatch lifecycle transitions."""

    def __init__(self, path: Path, notify: bool) -> None:
        self.path = path
        self.notify = notify

    def emit(self, kind: str, slug: str, **fields: Any) -> None:
        rec = {"at": now_iso(), "event": kind, "slug": slug, **fields}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as fh:
                fh.write(json.dumps(rec) + "\n")
        except OSError as exc:
            print(f"warn: could not append event log ({exc})", file=sys.stderr)

        marker = {"blocked": "!!", "failed": "XX", "stalled": "??"}.get(kind, "--")
        print(
            f"[{rec['at']}] {marker} {kind:<10} {slug}"
            + ("  " + " ".join(f"{k}={v}" for k, v in fields.items()) if fields else "")
        )

        if self.notify and kind in ("blocked", "failed", "stalled"):
            self._banner(kind, slug, fields)

    def _banner(self, kind: str, slug: str, fields: dict[str, Any]) -> None:
        if sys.platform != "darwin" or not shutil.which("osascript"):
            return
        sub = f"attach: claude attach {fields['id']}" if fields.get("id") else kind
        # Quote-strip so the AppleScript string literal cannot be broken out of.
        title = f"gw-dispatch: {kind}".replace('"', "")
        body = f"{slug}\n{sub}".replace('"', "")
        run(["osascript", "-e", f'display notification "{body}" with title "{title}"'], timeout=10)


# --------------------------------------------------------------------------
# dispatcher
# --------------------------------------------------------------------------


UNATTENDED_PROMPT = """\
UNATTENDED BACKGROUND RUN (dispatched by scripts/gw_dispatch.py).

You are a single pipeline stage of the graph-wiki work workflow, running in a
fresh background session with no human watching the terminal. Follow the
graph-wiki:workflow skill exactly as written, with these standing answers so
you do not stall on routine questions:

- Default work item owner is `{owner}`. If a dispatch transition requires
  `--owner` and none is recorded, use that value without asking.
- Run exactly ONE pipeline stage, then stop. Do not chain into the next stage.

Two things you must NOT do silently:

- Do NOT invent an `--effort` value. If the item is blocked waiting on effort,
  stop and report it; a human sizes the item.
- Do NOT guess your way past a genuine design decision. Asking is supported
  here: an AskUserQuestion moves this session to `blocked`, the dispatcher
  surfaces it, and a human attaches to answer. A blocked session is a correct
  outcome. A wrong guess is not.
"""


class Dispatcher:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.claude = resolve_claude_bin(args.claude_bin)
        self.gw = resolve_gw_bin(args.gw_bin)
        self.workspace = Path(args.workspace).expanduser().resolve()
        self.repo = Path(args.repo).expanduser().resolve()
        state_dir = self.workspace / ".graph-wiki" / "dispatch"
        self.ledger = Ledger.load(state_dir / "ledger.json")
        self.events = EventLog(state_dir / "events.jsonl", notify=args.notify)
        self.phases = tuple(p.strip() for p in args.phases.split(",") if p.strip())

    # -- data sources ------------------------------------------------------

    def gw_next(self, slug: str) -> dict[str, Any] | None:
        """Read the dispatch record for a slug.

        `gw work next` exits **rc=1 whenever blockers are present** while still
        writing a complete JSON document to stdout. Parse status, not exit
        status -- gating on rc would silently hide every blocked item.
        """
        rc, out, err = run([*self.gw, "work", "next", slug, "--json"], cwd=self.repo)
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            detail = (err or out).strip()[:200]
            print(f"warn: gw work next {slug} gave no JSON (rc={rc}): {detail}", file=sys.stderr)
            return None

    def work_items(self) -> list[dict[str, Any]]:
        """Slugs from the work index, falling back to a glob of wiki/work/."""
        index = self.workspace / "wiki" / "work-index.json"
        if index.exists():
            try:
                items = json.loads(index.read_text()).get("items", [])
                return [i for i in items if i.get("slug")]
            except (OSError, json.JSONDecodeError) as exc:
                print(f"warn: unreadable {index} ({exc}); falling back to glob", file=sys.stderr)
        work_dir = self.workspace / "wiki" / "work"
        return [
            {"slug": p.stem, "status": None}
            for p in sorted(work_dir.glob("*.md"))
            if p.stem != "index"  # wiki/work/index.md is not a work item
        ]

    def live_sessions(self) -> dict[str, dict[str, Any]]:
        """Background sessions keyed by short id. Empty dict on failure."""
        rc, out, _ = run([self.claude, "agents", "--json", "--all"], timeout=60)
        if rc != 0:
            return {}
        try:
            rows = json.loads(out)
        except json.JSONDecodeError:
            return {}
        return {r["id"]: r for r in rows if r.get("kind") == "background" and r.get("id")}

    # -- lifecycle ---------------------------------------------------------

    def reconcile(self) -> None:
        """Update ledger entries against live session state + durable wiki state."""
        live = self.live_sessions()
        for slug, entry in list(self.ledger.runs.items()):
            if entry.get("state") not in ACTIVE_STATES:
                continue
            sess = live.get(entry.get("id", ""))

            if sess is None:
                # Session is gone from the agents list entirely.
                self._settle(slug, entry, reason="session disappeared")
                continue

            state = sess.get("state")
            if state == "blocked":
                if entry.get("state") != "blocked":
                    entry["state"] = "blocked"
                    entry["blocked_at"] = now_iso()
                    self.events.emit(
                        "blocked",
                        slug,
                        id=entry["id"],
                        hint=f"claude attach {entry['id']}",
                    )
                continue
            if state == "failed":
                entry["state"] = "failed"
                entry["finished_at"] = now_iso()
                self.events.emit("failed", slug, id=entry["id"])
                continue
            if state == "done":
                self._settle(slug, entry, reason="session done")
                continue
            # still working -- if it had been blocked, a human answered it
            if entry.get("state") == "blocked":
                entry["state"] = "running"
                self.events.emit("unblocked", slug, id=entry["id"])
        self.ledger.save()

    def _settle(self, slug: str, entry: dict[str, Any], reason: str) -> None:
        """Confirm a finished run against durable wiki state, not session state."""
        entry["finished_at"] = now_iso()
        info = self.gw_next(slug)
        if info is None:
            entry["state"] = "lost"
            self.events.emit("lost", slug, id=entry.get("id"), reason=reason)
            return

        phase, status = info.get("phase"), info.get("status")
        expected = entry.get("expect_phase")
        advanced = (expected and phase == expected) or phase != entry.get("from_phase") or status in TERMINAL_STATUSES
        entry["end_phase"] = phase
        entry["end_status"] = status
        if advanced:
            entry["state"] = "completed"
            self.events.emit(
                "completed",
                slug,
                id=entry.get("id"),
                phase=f"{entry.get('from_phase')}->{phase}",
            )
        else:
            entry["state"] = "stalled"
            self.events.emit(
                "stalled",
                slug,
                id=entry.get("id"),
                phase=phase,
                reason=f"{reason}; phase did not advance",
            )

    # -- readiness ---------------------------------------------------------

    def ready(self) -> tuple[list[dict[str, Any]], list[tuple[str, str]]]:
        """Return (dispatchable, waiting) where waiting is (slug, reason)."""
        dispatchable: list[dict[str, Any]] = []
        waiting: list[tuple[str, str]] = []

        for item in self.work_items():
            slug = item["slug"]
            if item.get("status") in TERMINAL_STATUSES:
                continue
            led = self.ledger.state_of(slug)
            if led in ACTIVE_STATES:
                continue
            if led in STICKY_STATES:
                waiting.append((slug, f"{led} -- clear with --reset {slug}"))
                continue

            info = self.gw_next(slug)
            if info is None:
                continue
            if info.get("status") in TERMINAL_STATUSES or info.get("phase") == "done":
                continue

            blockers = info.get("blockers") or []
            if blockers:
                # Epics gated on open children are not a problem -- their
                # children are separate slugs and get picked up on their own.
                text = " ".join(str(b) for b in blockers)
                if "waiting on children" not in text:
                    waiting.append((slug, self._blocker_summary(blockers)))
                continue

            phase = info.get("phase")
            skill = (info.get("action") or {}).get("skill")
            if phase not in self.phases or not skill:
                continue

            dispatchable.append(info)

        return dispatchable, waiting

    @staticmethod
    def _blocker_summary(blockers: list[Any]) -> str:
        parts = []
        for b in blockers:
            if isinstance(b, dict):
                parts.append(str(b.get("reason") or b.get("message") or b))
            else:
                parts.append(str(b))
        return "; ".join(parts)[:160]

    # -- dispatch ----------------------------------------------------------

    def dispatch(self, info: dict[str, Any]) -> bool:
        slug = info["slug"]
        phase = info.get("phase") or "?"
        skill = (info.get("action") or {}).get("skill")
        name = f"gw-{phase}-{slug}"[:64]

        argv = [
            self.claude,
            "--bg",
            "-n",
            name,
            "--permission-mode",
            self.args.permission_mode,
            "--add-dir",
            str(self.workspace),
            "--append-system-prompt",
            UNATTENDED_PROMPT.format(owner=self.args.owner),
            f"/graph-wiki:next {slug}",
        ]
        if self.args.model:
            argv[1:1] = ["--model", self.args.model]

        if self.args.dry_run:
            print(f"DRY-RUN would dispatch {slug} (phase={phase} skill={skill})")
            print("        " + " ".join(argv[:1] + ["..."] + argv[-1:]))
            return False

        rc, out, err = run(argv, cwd=self.repo, timeout=180)
        blob = f"{out}\n{err}"

        if "requires accepting the disclaimer" in blob:
            sys.exit(
                "\nerror: bypassPermissions needs a one-time interactive acknowledgement.\n"
                "  Open a terminal and run once:  claude --dangerously-skip-permissions\n"
                "  Accept the disclaimer, quit, then restart this dispatcher.\n"
            )
        if rc != 0:
            self.events.emit("dispatch-error", slug, rc=rc, err=err.strip()[:200])
            return False

        sid = self._extract_id(blob, name)
        if not sid:
            self.events.emit("dispatch-error", slug, rc=rc, err="could not determine session id")
            return False

        self.ledger.runs[slug] = {
            "id": sid,
            "name": name,
            "state": "running",
            "skill": skill,
            "from_phase": phase,
            "from_status": info.get("status"),
            "expect_phase": (info.get("on_complete") or {}).get("phase"),
            "dispatched_at": now_iso(),
        }
        self.ledger.save()
        self.events.emit("dispatched", slug, id=sid, phase=phase, skill=skill)
        return True

    def _extract_id(self, blob: str, name: str) -> str | None:
        # `claude --bg` prints: "backgrounded · <id> · <name>"
        m = re.search(r"backgrounded\s*\W\s*([0-9a-f]{6,})", blob)
        if m:
            return m.group(1)
        # Fall back to locating the session we just named.
        for sid, row in self.live_sessions().items():
            if row.get("name") == name:
                return sid
        return None

    # -- entry points ------------------------------------------------------

    def tick(self) -> None:
        self.reconcile()
        dispatchable, waiting = self.ready()

        active = sum(1 for e in self.ledger.runs.values() if e.get("state") in ACTIVE_STATES)
        slots = max(0, self.args.max_parallel - active)

        for info in dispatchable[:slots] if not self.args.dry_run else dispatchable:
            self.dispatch(info)

        if len(dispatchable) > slots and not self.args.dry_run:
            held = [i["slug"] for i in dispatchable[slots:]]
            print(f"       .. {len(held)} ready, held by --max-parallel {self.args.max_parallel}: " + ", ".join(held))
        for slug, reason in waiting:
            print(f"       .. waiting  {slug}: {reason}")

    def status(self) -> None:
        live = self.live_sessions()
        if not self.ledger.runs:
            print("ledger empty")
        for slug, e in sorted(self.ledger.runs.items()):
            sess = live.get(e.get("id", ""))
            tail = f" live={sess.get('state')}" if sess else ""
            print(
                f"{e.get('state', '?'):<10} {e.get('id', '--'):<10} {slug}"
                f"  [{e.get('from_phase')} -> {e.get('end_phase') or e.get('expect_phase')}]{tail}"
            )
            if e.get("state") == "blocked":
                print(f"           needs input: claude attach {e.get('id')}")

    def reset(self, slugs: list[str]) -> None:
        for slug in slugs:
            if self.ledger.runs.pop(slug, None) is not None:
                print(f"reset {slug}")
            else:
                print(f"no ledger entry for {slug}")
        self.ledger.save()


def main() -> None:
    default_ws = os.environ.get("GRAPH_WIKI_WORKSPACE", "")
    ap = argparse.ArgumentParser(
        description="Dispatch graph-wiki pipeline stages into fresh background Claude sessions.",
    )
    ap.add_argument("--workspace", default=default_ws, help="graph-wiki workspace (default: $GRAPH_WIKI_WORKSPACE)")
    ap.add_argument(
        "--repo", default=os.getcwd(), help="repo checkout used as cwd for dispatched sessions (default: cwd)"
    )
    ap.add_argument(
        "--phases",
        default=",".join(DEFAULT_PHASES),
        help=f"comma-separated phases to automate (default: {','.join(DEFAULT_PHASES)})",
    )
    ap.add_argument(
        "--owner",
        default=os.environ.get("USER", "unassigned"),
        help="default work item owner for the execute dispatch transition",
    )
    ap.add_argument(
        "--permission-mode",
        default="bypassPermissions",
        help="permission mode for dispatched sessions (default: bypassPermissions)",
    )
    ap.add_argument("--model", default=None, help="model override for dispatched sessions")
    ap.add_argument("--max-parallel", type=int, default=2, help="concurrent background stages")
    ap.add_argument("--interval", type=int, default=30, help="watch loop poll interval (seconds)")
    ap.add_argument("--once", action="store_true", help="single pass, then exit")
    ap.add_argument("--dry-run", action="store_true", help="report dispatches without making them")
    ap.add_argument("--notify", action="store_true", help="macOS banner on blocked/failed/stalled")
    ap.add_argument("--status", action="store_true", help="print ledger rollup and exit")
    ap.add_argument("--reset", nargs="+", metavar="SLUG", help="clear ledger entries and exit")
    ap.add_argument("--claude-bin", default=None)
    ap.add_argument("--gw-bin", default=None)
    args = ap.parse_args()

    if not args.workspace:
        sys.exit("error: no workspace. Pass --workspace or set GRAPH_WIKI_WORKSPACE.")

    d = Dispatcher(args)

    if args.reset:
        d.reset(args.reset)
        return
    if args.status:
        d.status()
        return

    if args.once:
        d.tick()
        return

    print(f"gw-dispatch watching {d.workspace}")
    print(f"  repo={d.repo} phases={','.join(d.phases)} max-parallel={args.max_parallel} interval={args.interval}s")
    print("  ctrl-c to stop (running background sessions are NOT stopped)\n")
    try:
        while True:
            d.tick()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nstopped. Background sessions keep running -- `claude agents` to review.")


if __name__ == "__main__":
    main()
