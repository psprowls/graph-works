"""A rollback-able set of bundle writes: each path's prior bytes are kept, so a failed apply restores the
bundle exactly."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class WriteLog:
    bundle_dir: Path
    _before: dict[str, bytes | None] = field(default_factory=dict)

    def remember(self, rel: str) -> None:
        """Record *rel*'s current bytes (or absence) before something else writes it. Only the first call per
        path counts."""
        if rel not in self._before:
            path = self.bundle_dir / rel
            self._before[rel] = path.read_bytes() if path.is_file() else None

    def write(self, rel: str, text: str) -> None:
        self.remember(rel)
        path = self.bundle_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))

    def rollback(self) -> None:
        for rel, before in reversed(list(self._before.items())):
            path = self.bundle_dir / rel
            if before is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(before)

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(sorted(self._before))


__all__ = ["WriteLog"]
