"""What `gw util platform` reports: platform, durability tier, dispatch backend,
and which POSIX-only components are unavailable.

The epic's tier decision requires the weaker durability tier be *surfaced at
runtime* rather than inferred from a crash. That requirement is why this is a
provider seam and not a table: each capability is answered by a provider that
asks the machinery that owns it. A hand-maintained table keyed on
`sys.platform` is the pattern this CLI already rejected once, when
`describe-surface` dropped its `json_keys` field — a registry nothing asserts
against real behaviour rots silently, and a platform verb that can claim a
durability tier the engine is not actually running is worse than no verb.

Nothing here reads `sys.platform` except as `build_report`'s default: the
platform is an argument, the same shape okf-io's required `today=` has and the
whole band inherits. That is what lets a POSIX box assert the report a Windows
user would actually see — the strongest reason this verb is built early rather
than last.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import signal
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from graph_works_core.workspace.layout import WorkspaceLayout

#: Bumped when a consumer-visible field changes shape. Follows
#: `describe-surface`'s precedent, for the same reason: scripts read this.
SCHEMA_VERSION = 1

#: `available` — present and usable. `unavailable` — the machinery cannot run
#: here. `degraded` — usable with a weaker guarantee than the strong tier.
#: `unknown` — nothing could be asked, and this report does not guess.
CapabilityStatus = Literal["available", "unavailable", "degraded", "unknown"]

#: The one irreducible platform fact in this module: stdlib modules CPython
#: does not ship on native Windows. Every provider derives from
#: `module_available` rather than restating a per-platform literal of its own —
#: that duplication is exactly what turns a seam back into a table.
POSIX_ONLY_MODULES = frozenset({"fcntl", "grp", "pwd", "termios"})


def module_available(module_name: str, platform_name: str) -> bool:
    """Whether *module_name* is importable on *platform_name*.

    For the running platform this asks the import system. For a foreign one it
    can only answer for the modules whose absence is a fixed property of the
    build — hence `POSIX_ONLY_MODULES`, kept small and named rather than
    inferred, so a reader can see the whole assumption at once.
    """
    if importlib.util.find_spec(module_name) is None:
        return False
    if platform_name == sys.platform:
        return True
    return not (platform_name.startswith("win") and module_name in POSIX_ONLY_MODULES)


@dataclass(frozen=True, slots=True)
class Capability:
    """One declared platform fact and the machinery that answered for it.

    `provider` is the dotted module that produced `value`. It is what makes
    every claim traceable, and what stops the seam quietly degenerating back
    into a table. `guarantees` is the declared contract — for the durability
    tier, the sentences the tier ADR commits to; a tier *name* with no contract
    beside it surfaces nothing a user can act on.
    """

    name: str
    value: str
    status: CapabilityStatus
    detail: str
    guarantees: tuple[str, ...]
    provider: str


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """What a liveness check observed, and whether it matches the declaration.

    Rendered *beside* the declared value, never in place of it: a box where
    `dispatch-backend` declares one thing and probes another is the single most
    useful thing this verb can say, and collapsing the two destroys it.
    """

    capability: str
    status: CapabilityStatus
    detail: str
    agrees_with_declared: bool


@dataclass(frozen=True, slots=True)
class PlatformReport:
    """The whole report. Data, not a printer — the CLI formats this, and a
    future `gw doctor` could carry it as one section."""

    schema_version: int
    platform: str
    python: str
    capabilities: tuple[Capability, ...]
    probes: tuple[ProbeResult, ...]

    @property
    def unavailable(self) -> tuple[str, ...]:
        """Which POSIX-only components are unavailable — the item's fourth
        required field, derived rather than maintained."""
        return tuple(c.name for c in self.capabilities if c.status == "unavailable")


#: The engine whose durability tier this report describes. Named once so the
#: provider's `provider` field and its detail sentence cannot drift apart.
TRANSACTIONS_MODULE = "graph_works_core.workspace.transactions"

#: Why the durability tier has no liveness check. Stated as a constant so a
#: later reader finds the reason next to the refusal.
NOT_PROBEABLE_DETAIL = (
    "not probeable — running a real transaction is not read-only; the tier's "
    "guarantee is bought by a measured run on real Windows hardware, not by a "
    "diagnostic verb asserting it"
)

_STRONG_TIER_GUARANTEES = (
    "an exclusive lock is held across the whole read-mutate-write cycle",
    "every effect lands or the pre-mutation snapshot is restored",
    "recovery evidence is journaled outside the bundle",
)


class Provider(Protocol):
    """One capability, answered by whoever owns the machinery behind it.

    The strict rule, and the whole point of the seam: a provider never
    restates a platform fact it could ask for. When the tier ADR's second
    anchor lands, the answer changes *in the engine* and this verb reports the
    new answer without being edited. A provider that cannot ask — because the
    machinery does not exist yet — says `unresolved` / `unknown` with a detail
    naming what is missing. It does not guess, and it does not anticipate.
    """

    name: str

    def declare(self, platform_name: str) -> Capability:
        """The static answer for *platform_name*. No subprocess, no I/O."""
        ...

    def probe(self, layout: WorkspaceLayout) -> ProbeResult | None:
        """A cheap liveness check, or `None` when this capability has none."""
        ...


class DurabilityTierProvider:
    """Which durability tier the transaction engine actually runs here.

    Derived, not tabulated: the engine imports `fcntl` at module scope, so its
    availability *is* that module's availability. When a selector and a second
    anchor exist, this asks the engine which one it selects instead — and the
    verb's answer changes without the verb changing.
    """

    name = "durability-tier"

    def declare(self, platform_name: str) -> Capability:
        if module_available("fcntl", platform_name):
            return Capability(
                name=self.name,
                value="posix-strong",
                status="available",
                detail="the transaction engine's advisory-lock path is importable here",
                guarantees=_STRONG_TIER_GUARANTEES,
                provider=TRANSACTIONS_MODULE,
            )
        return Capability(
            name=self.name,
            value="unavailable",
            status="unavailable",
            detail=(
                f"{TRANSACTIONS_MODULE} imports `fcntl` at module scope; native "
                f"Windows cannot load it, so no transaction can run at all"
            ),
            guarantees=(),
            provider=TRANSACTIONS_MODULE,
        )

    def probe(self, layout: WorkspaceLayout) -> ProbeResult | None:
        """Deliberately never runs a transaction. See `NOT_PROBEABLE_DETAIL`."""
        return ProbeResult(
            capability=self.name,
            status="unknown",
            detail=NOT_PROBEABLE_DETAIL,
            agrees_with_declared=True,
        )


#: Bounded because a diagnostic verb that hangs while diagnosing is worthless.
ORCA_PROBE_TIMEOUT_SECONDS = 5.0

#: The shell whose docstring states the rule this provider reports. Named as a
#: string rather than imported: `graph_works_core.util` and
#: `graph_works_core.orchestrate` are siblings under an `independence`
#: import-linter contract, so citing it must not become importing it.
ORCHESTRATE_SHELL = "graph_works_core.orchestrate.commands"


class DispatchBackendProvider:
    """Which dispatch backend a run would use — and whether `orca` actually works here.

    Declares `unresolved` because no resolver exists yet: the orchestrate shell
    resolves no backend by design, and nothing imports `workflow_local` or
    `workflow_orca`. When the resolver lands, this asks it. Until then the slot
    reports honestly rather than guessing a default.

    The probe does not need the resolver, and is implemented now: Orca's own
    availability on a given box is a prerequisite to verify, not assume.
    """

    name = "dispatch-backend"

    def __init__(
        self,
        *,
        which: Callable[[str], str | None] = shutil.which,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self._which = which
        self._run = run

    def declare(self, platform_name: str) -> Capability:
        return Capability(
            name=self.name,
            value="unresolved",
            status="unknown",
            detail=(
                f"no backend resolver exists; {ORCHESTRATE_SHELL} resolves no "
                f"backend by design and nothing imports workflow_local or "
                f"workflow_orca — run with --probe to ask whether orca works here"
            ),
            guarantees=(),
            provider=ORCHESTRATE_SHELL,
        )

    def probe(self, layout: WorkspaceLayout) -> ProbeResult | None:
        executable = self._which("orca")
        if executable is None:
            return self._result("unavailable", "orca is not on PATH")
        try:
            completed = self._run(
                [executable, "--version"],
                capture_output=True,
                text=True,
                timeout=ORCA_PROBE_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return self._result("unavailable", f"`orca --version` timed out after {ORCA_PROBE_TIMEOUT_SECONDS}s")
        except OSError as exc:
            return self._result("unavailable", f"`orca --version` could not be launched: {exc}")
        if completed.returncode != 0:
            return self._result("unavailable", f"`orca --version` exited {completed.returncode}")
        return self._result("available", f"orca at {executable}: {completed.stdout.strip() or 'no version reported'}")

    def _result(self, status: CapabilityStatus, detail: str) -> ProbeResult:
        """Always disagrees with the declaration while it reads `unresolved`:
        an observation where the declaration has none is exactly the news this
        verb exists to carry."""
        return ProbeResult(capability=self.name, status=status, detail=detail, agrees_with_declared=False)


#: The three modules that take an advisory file lock today, as `<pkg>/<path>`
#: below `packages/*/src/`. Named rather than discovered so the report cites
#: real files — and so a test can assert each one still imports `fcntl`, which
#: is what keeps this list from rotting once the portable-lock work lands.
LOCK_SITES = (
    "work_tracker_okf/decisions.py",
    "graph_works_core/work/commands.py",
    "okf_ext/logs/__init__.py",
)

#: The probe's own lock file. Deliberately NOT a live decisions-cache lock:
#: contending for a real ledger lock to answer a diagnostic question could
#: stall a concurrent writer.
PROBE_LOCK_NAME = "platform-probe.lock"

#: The package whose README already declares itself POSIX-only.
WORKFLOW_LOCAL_BACKEND = "workflow_local.backend"


class FileLockProvider:
    """Which primitive serializes a read-mutate-write cycle across processes.

    Derived from `fcntl`'s availability, because that is literally what the
    three lock sites import. When a portable `locked(path)` helper exists,
    this asks it which branch it took instead.
    """

    name = "file-lock"

    def declare(self, platform_name: str) -> Capability:
        sites = ", ".join(LOCK_SITES)
        if module_available("fcntl", platform_name):
            return Capability(
                name=self.name,
                value="fcntl.flock",
                status="available",
                detail=f"advisory exclusive locks via fcntl.flock at {sites}",
                guarantees=("an exclusive advisory lock serializes writers across processes",),
                provider=LOCK_SITES[0].replace("/", ".").removesuffix(".py"),
            )
        return Capability(
            name=self.name,
            value="unavailable",
            status="unavailable",
            detail=f"{sites} each import `fcntl`, which native Windows does not provide",
            guarantees=(),
            provider=LOCK_SITES[0].replace("/", ".").removesuffix(".py"),
        )

    def probe(self, layout: WorkspaceLayout) -> ProbeResult | None:
        """Take and immediately release a lock on a dedicated cache path."""
        lock = layout.cache_dir / PROBE_LOCK_NAME
        try:
            import fcntl

            lock.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o644)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)
        except (ImportError, OSError) as exc:
            return ProbeResult(
                capability=self.name,
                status="unavailable",
                detail=f"could not take an advisory lock at {lock}: {exc}",
                agrees_with_declared=False,
            )
        return ProbeResult(
            capability=self.name,
            status="available",
            detail=f"took and released an exclusive advisory lock at {lock}",
            agrees_with_declared=True,
        )


class ProcessControlProvider:
    """Whether worker liveness and teardown can run here.

    Derived from `signal.SIGKILL`, the attribute CPython omits on Windows —
    the backend's teardown polls SIGTERM then SIGKILL, and its liveness check
    is `os.kill(pid, 0)`. Asking for the attribute is asking the machinery.
    """

    name = "process-control"

    def declare(self, platform_name: str) -> Capability:
        posix_signals = module_available("fcntl", platform_name) and hasattr(signal, "SIGKILL")
        if posix_signals:
            return Capability(
                name=self.name,
                value="workflow-local",
                status="available",
                detail=f"{WORKFLOW_LOCAL_BACKEND} uses os.kill(pid, 0) for liveness and SIGTERM/SIGKILL for teardown",
                guarantees=("a worker's liveness is observable and its teardown is enforceable",),
                provider=WORKFLOW_LOCAL_BACKEND,
            )
        return Capability(
            name=self.name,
            value="unavailable",
            status="unavailable",
            detail=(
                f"{WORKFLOW_LOCAL_BACKEND} polls SIGTERM then SIGKILL for teardown and calls "
                f"os.kill(pid, 0) for liveness; native Windows provides no SIGKILL"
            ),
            guarantees=(),
            provider=WORKFLOW_LOCAL_BACKEND,
        )

    def probe(self, layout: WorkspaceLayout) -> ProbeResult | None:
        """None: sending a signal to prove signalling works is not read-only."""
        return None
