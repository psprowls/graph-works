"""The graph directory owned by code-graph-io, and how to derive one.

Everything this package writes — ``code.db`` and the builtins cache — lives
under a single directory the caller names. That directory is what the public
entry points take, as ``graph_dir=``: nothing here discovers it, reads the
environment, or looks for a manifest.

Callers organized around a *workspace* (a directory whose graph state lives in
a well-known child) get the conventional derivation from :func:`graph_dir`.
Using it is the sanctioned way to spell that convention — it keeps
``GRAPH_DIRNAME`` a single line here rather than a string frozen across every
downstream call site.
"""

from __future__ import annotations

from pathlib import Path

#: Local machine state only — graph DB and caches. Nothing here is meant to be
#: committed; keeping it out of version control is the caller's job.
GRAPH_DIRNAME = ".agent-workspace"


def graph_dir(workspace: Path) -> Path:
    """The conventional graph directory for *workspace*: ``<workspace>/.agent-workspace``.

    A convenience for workspace-shaped callers, not a requirement — the entry
    points accept any directory, so a caller free to choose its own layout can
    skip this and pass one directly.
    """
    return Path(workspace) / GRAPH_DIRNAME
