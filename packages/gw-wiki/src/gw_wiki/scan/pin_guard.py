"""Scan's pin guard (D-205): a lane clone whose HEAD disagrees with its page's `pin.commit` is not scanned.

The lane page travels with the workspace; the clone does not. A machine that pulled the page after
`gw repo advance` ran elsewhere would otherwise plan against the stale tree and delete or recreate
every page the newer commits added or renamed.
"""

from __future__ import annotations

from dataclasses import dataclass

from code_wiki_okf.config import Config
from code_wiki_okf.git_state import head_commit
from gw_kernel.workspace.bundle import load_workspace_bundle
from gw_kernel.workspace.layout import WorkspaceLayout
from gw_kernel.workspace.repos import in_bundle_clone
from repositories_okf.lifecycle import lane_pages
from repositories_okf.pin import read_pin

_ABBREVIATED = 7


@dataclass(frozen=True, slots=True)
class PinDrift:
    """One lane repository whose clone HEAD is not the commit its page pins."""

    name: str
    head: str
    pin: str

    def detail(self) -> str:
        return (
            f"repository {self.name!r}: clone HEAD {self.head[:_ABBREVIATED]} differs from pin "
            f"{self.pin[:_ABBREVIATED]}; run gw repo restore {self.name}"
        )


def pin_drift(layout: WorkspaceLayout, config: Config, repos: frozenset[str] | None) -> tuple[PinDrift, ...]:
    """The in-bundle clones (restricted to *repos* when given) provably off their pin.

    A missing page, a page without a pin, or a clone that is not a git checkout yields nothing:
    those have their own warnings, and this guard claims only what it can prove.
    """
    clones = [entry for entry in config.repos if in_bundle_clone(layout, entry.path)]
    if repos is not None:
        clones = [entry for entry in clones if entry.name in repos]
    if not clones:
        return ()
    pages = lane_pages(load_workspace_bundle(layout))
    drifts: list[PinDrift] = []
    for entry in clones:
        lane = entry.path.resolve().relative_to(layout.bundle_dir.resolve()).parts[1]
        page = pages.get(lane)
        pin = read_pin(page) if page is not None else None
        head = head_commit(entry.path)
        if pin is not None and head is not None and head != pin.commit:
            drifts.append(PinDrift(lane, head, pin.commit))
    return tuple(drifts)


__all__ = ["PinDrift", "pin_drift"]
