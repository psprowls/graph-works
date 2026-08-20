"""Build hooks for graph-works-core distribution resources."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    """Include canonical hook scripts in direct wheel builds.

    Source distributions stage the scripts inside ``src/graph_works_core``;
    wheels built from an sdist therefore include them through the normal
    package traversal and need no forced path.
    """

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        packaged = Path(self.root) / "src" / "graph_works_core" / "_hook_scripts"
        if packaged.is_dir():
            return

        canonical = Path(self.root).parents[1] / "plugins" / "graph-works" / "hooks" / "examples"
        if not canonical.is_dir():
            raise FileNotFoundError(f"canonical hook scripts not found: {canonical}")
        build_data["force_include"][str(canonical)] = "graph_works_core/_hook_scripts"
