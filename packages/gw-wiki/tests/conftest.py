"""Wiki fixture re-exports and collection-local helper paths."""

from __future__ import annotations

import sys
from pathlib import Path

from gw_kernel.testing import (
    code_layout as code_layout,
)
from gw_kernel.testing import (
    declare_repos as declare_repos,
)
from gw_kernel.testing import (
    empty_graph_dir as empty_graph_dir,
)
from gw_kernel.testing import (
    git_repo as git_repo,
)
from gw_kernel.testing import (
    graph_dir as graph_dir,
)
from gw_kernel.testing import (
    stale_graph_dir as stale_graph_dir,
)

# The lint integration suite shares the repository CLI fixtures with their own tests.
sys.path.insert(0, str(Path(__file__).parent / "repositories"))

# Scan prose-refresh tests share the wiki-owned fake response/LLM helpers.
sys.path.insert(0, str(Path(__file__).parent / "ingest"))
