"""The refusal taxonomy for workspace resolution and init.

Config raises; content never does. okf-io's never-raise rule governs *concept
content*, and none of this is that — a missing workspace and a manifest at an
unreadable version are configuration, so they follow the line
`code_wiki_okf.ConfigError` and `okf_ext`'s `VocabularyError` / `SchemaError`
already draw.
"""

from __future__ import annotations


class WorkspaceError(ValueError):
    """Base for every refusal this package raises; the message is user-facing."""


class WorkspaceNotFound(WorkspaceError):
    """No `workspace.yaml` at the resolved root, so there is no workspace here."""


class InitError(WorkspaceError):
    """An init refused for a reason that is not bundle content.

    One case: a root that exists and is not a directory. Bundle content is
    never an exception here — a conflicting file is a `WriteFailure` in the
    result, matching both shipped `install_bundle`s.
    """


class ScanError(WorkspaceError):
    """A scan refused before it could read anything.

    Environment, not content: the code graph could not be built or opened. A
    page the pipeline cannot narrate is an entry in `ApplyResult.entity_errors`,
    never this.
    """


class QueryError(WorkspaceError):
    """A query refused before it could produce a candidate set.

    Environment, not content: there is nothing indexed to search, or the index
    on disk cannot be scored against the query. A malformed concept is still a
    concept — it is retrieved, ranked and handed on like any other.
    """


__all__ = ["InitError", "QueryError", "ScanError", "WorkspaceError", "WorkspaceNotFound"]
