"""The `pin` a repository page records: the commit its clone is detached at, and where that came from (seed §3
source and lineage facts, D-007)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

_HEX = frozenset("0123456789abcdef")


def is_sha(value: object) -> bool:
    """A full, lower-case, 40-hex object name — the only spelling a `pin` stores."""
    return isinstance(value, str) and len(value) == 40 and set(value) <= _HEX


if TYPE_CHECKING:
    from okf_io import Document


@dataclass(frozen=True, slots=True)
class Generation:
    """The version and scan inputs that generated a managed repository's code graph."""

    gw_version: str
    scan_config_hash: str


@dataclass(frozen=True, slots=True)
class Pin:
    """Source and lineage facts only (D-007). `fetched_at` is an ISO-8601 instant string the caller formats."""

    commit: str
    fetched_at: str
    ref: str | None = None
    describe: str | None = None
    tree: str | None = None
    commit_date: str | None = None
    previous: str | None = None
    generation: Generation | None = None

    def frontmatter(self) -> dict[str, object]:
        """The `pin:` mapping. Absent keys are omitted: the lane schema rejects `null` for them."""
        pairs = (
            ("commit", self.commit),
            ("tree", self.tree),
            ("describe", self.describe),
            ("commit_date", self.commit_date),
            ("ref", self.ref),
            ("fetched_at", self.fetched_at),
            ("previous", self.previous),
        )
        data: dict[str, object] = {key: value for key, value in pairs if value is not None}
        if self.generation is not None:
            data["generation"] = {
                "gw_version": self.generation.gw_version,
                "scan_config_hash": self.generation.scan_config_hash,
            }
        return data


def _text(raw: dict[str, object], key: str) -> str | None:
    value = raw.get(key)
    return value if isinstance(value, str) and value else None


def read_pin(document: Document) -> Pin | None:
    """The page's `pin`, or `None` when it has none a clone could be detached at."""
    raw = document.fm_data(dates="iso").get("pin")
    if not isinstance(raw, dict) or not is_sha(raw.get("commit")):
        return None
    generation = None
    raw_generation = raw.get("generation")
    if isinstance(raw_generation, Mapping):
        version, digest = raw_generation.get("gw_version"), raw_generation.get("scan_config_hash")
        if isinstance(version, str) and version and isinstance(digest, str) and digest:
            generation = Generation(version, digest)
    return Pin(
        commit=str(raw["commit"]),
        fetched_at=_text(raw, "fetched_at") or "",
        ref=_text(raw, "ref"),
        describe=_text(raw, "describe"),
        tree=_text(raw, "tree"),
        commit_date=_text(raw, "commit_date"),
        previous=_text(raw, "previous"),
        generation=generation,
    )


def write_pin(document: Document, pin: Pin) -> None:
    """Replace the page's whole `pin` mapping; every other byte goes through okf-io's splice."""
    document.set("pin", pin.frontmatter())


__all__ = ["Generation", "Pin", "is_sha", "read_pin", "write_pin"]
