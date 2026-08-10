"""code-wiki-okf: generates and updates a standalone OKF v0.2 bundle from the
shared code graph.

This package (epic child 1) ships the package skeleton, `_repositories.yaml`
config, `install_bundle()`, and the shared plumbing later children write
through: find-by-resource (`resources.py`), provenance stamping
(`provenance.py`), and a `git_state` port with the state gate.
"""

from __future__ import annotations

__version__ = "0.2.0"

from code_wiki_okf.config import (
    Config,
    ConfigError,
    RepoConfig,
    StateGateConfig,
    load_config,
)
from code_wiki_okf.git_state import (
    StateGate,
    changed_files_since,
    compute_state_gate,
    head_commit,
    is_clean_on_branches,
    ls_files,
)
from code_wiki_okf.init import BundleInstall, InitError, install_bundle, plan_install
from code_wiki_okf.provenance import (
    generated_value,
    last_updated_commit_value,
    tokens_value,
)
from code_wiki_okf.resources import (
    ResourceEntry,
    ResourceIndex,
    resource_index,
)

__all__ = [
    "BundleInstall",
    "Config",
    "ConfigError",
    "InitError",
    "RepoConfig",
    "ResourceEntry",
    "ResourceIndex",
    "StateGate",
    "StateGateConfig",
    "__version__",
    "changed_files_since",
    "compute_state_gate",
    "generated_value",
    "head_commit",
    "install_bundle",
    "is_clean_on_branches",
    "last_updated_commit_value",
    "load_config",
    "ls_files",
    "plan_install",
    "resource_index",
    "tokens_value",
]
