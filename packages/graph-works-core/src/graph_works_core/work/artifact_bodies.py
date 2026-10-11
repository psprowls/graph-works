"""Artifact bodies for stamp titles, served from an open read session (epic D-005, D-007, D-010).

`ReadSession.content` returns a hash-verified body slice without a parse for an
exact unchanged row; the title scanner stays `work_tracker_okf.compose`'s, so
queue titles equal `run_next`'s. Anything the session cannot serve -- not a
member (created after open, non-Markdown) or unreadable -- returns `None`, and
`stamp_for` falls through to the authoritative file read, error behavior
included. The memo lives as long as the returned supplier: one queue pass.
"""

from __future__ import annotations

from work_tracker_okf.compose import ArtifactBody
from work_tracker_okf.paths import ArtifactRef

from graph_works_core.read_session import PageContent, ReadSession


def session_bodies(session: ReadSession) -> ArtifactBody:
    """A per-pass `ArtifactBody` over *session*'s verified content."""
    served: dict[str, str | None] = {}

    def body_of(ref: ArtifactRef) -> str | None:
        if ref.rel not in served:
            content = session.content(ref.rel)
            served[ref.rel] = content.body if isinstance(content, PageContent) else None
        return served[ref.rel]

    return body_of
